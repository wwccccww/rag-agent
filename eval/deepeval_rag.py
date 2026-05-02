#!/usr/bin/env python3
"""
使用 DeepEval 对 RAG 检索 + 生成做离线评测。

指标（均通过本地 Ollama 作为 LLM-as-Judge）：
  - Faithfulness：回答是否可由检索片段支撑（仅非拒答用例；拒答时跳过，避免「无矛盾即满分」虚高）
  - Answer Relevancy：回答与问题的相关性（仅非拒答；拒答时 DeepEval 常误判为 0）
  - Contextual Relevancy：检索片段与问题的相关性（拒答时更要看此项：召回是否偏题）

用法：
  cd d:/1study/study/python/rag-agent
  python eval/deepeval_rag.py [--cases eval/test_cases.json] [--top-k 5] [--threshold 0.5]
      [--judge-chunk-chars 2000] [--quiet] [--no-reason]

依赖：已安装 backend/requirements.txt（含 deepeval）；数据库可连；Ollama 已拉取评判模型。

可选环境变量：
  DEEPEVAL_JUDGE_MODEL — 覆盖评判用模型名，默认与 OLLAMA_CHAT_MODEL 相同。
  使用更小模型（如 qwen2.5:3b）可明显加快评测。

说明：
  --judge-chunk-chars 仅截断送入 DeepEval 评委的检索文本（不影响 RAG 生成用的全文）。
  全库文档混杂时 Contextual Relevancy 会持续偏低，属预期；请配合 README 入库评测集或 eval_rag.py 的 Recall@k。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


def _utf8_stdio() -> None:
    """避免 Windows 默认 GBK 下 Rich/DeepEval 打印 ✓ 等字符时崩溃。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


_utf8_stdio()

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))
os.chdir(BACKEND_DIR)

from deepeval import evaluate  # noqa: E402
from deepeval.evaluate.configs import AsyncConfig, DisplayConfig  # noqa: E402
from deepeval.metrics import (  # noqa: E402
    AnswerRelevancyMetric,
    ContextualRelevancyMetric,
    FaithfulnessMetric,
)
from deepeval.models import OllamaModel  # noqa: E402
from deepeval.test_case import LLMTestCase  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal, init_db  # noqa: E402
from app.kb import sanitize_doc_types_list  # noqa: E402
from app.services.ollama import OllamaClient  # noqa: E402
from app.services.rag import multi_query_search  # noqa: E402


def _judge_model() -> OllamaModel:
    name = os.getenv("DEEPEVAL_JUDGE_MODEL", "").strip() or settings.ollama_chat_model
    return OllamaModel(
        model=name,
        base_url=settings.ollama_base_url.rstrip("/"),
        temperature=0.0,
    )


def _truncate_chunks(chunks: list[str], max_chars_per_chunk: int) -> list[str]:
    """缩短评委输入，避免单题多轮 LLM 调用耗时数分钟。"""
    if max_chars_per_chunk <= 0:
        return list(chunks)
    out: list[str] = []
    for c in chunks:
        c = c or ""
        if len(c) <= max_chars_per_chunk:
            out.append(c)
        else:
            out.append(c[:max_chars_per_chunk] + "…")
    return out


def _metrics_for_case(
    judge: OllamaModel,
    threshold: float,
    retrieval_only: bool,
    include_reason: bool,
) -> list[Any]:
    if retrieval_only:
        return [
            ContextualRelevancyMetric(
                threshold=threshold,
                model=judge,
                include_reason=include_reason,
                async_mode=False,
            ),
        ]
    return [
        FaithfulnessMetric(
            threshold=threshold, model=judge, include_reason=include_reason, async_mode=False
        ),
        AnswerRelevancyMetric(
            threshold=threshold, model=judge, include_reason=include_reason, async_mode=False
        ),
        ContextualRelevancyMetric(
            threshold=threshold, model=judge, include_reason=include_reason, async_mode=False
        ),
    ]


def _is_abstention(answer: str) -> bool:
    """与入库评测用的拒答话术一致；此类输出不跑 Faithfulness / AnswerRelevancy。"""
    t = answer.strip().replace(" ", "")
    markers = (
        "知识库中没有找到相关内容",
        "知识库中未找到相关内容",
        "未检索到相关内容",
    )
    return any(m in t for m in markers)


def get_rag_answer(ollama: OllamaClient, question: str, sources: list[dict]) -> str:
    if not sources:
        return "知识库中未找到相关内容，无法回答。"
    ctx = "\n\n".join(
        f"[S{i}] {r.get('full_content', r.get('snippet', ''))}"
        for i, r in enumerate(sources[:5], 1)
    )
    system_prompt = (
        "你是一个严格的文档问答助手，只能基于提供的文档片段回答问题。"
        "如果片段中没有相关内容，只回答「知识库中没有找到相关内容」。"
    )
    user_prompt = f"【知识库片段】\n{ctx}\n\n【问题】{question}"
    return ollama.chat_complete(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.1,
    )


def run(
    cases_path: str,
    top_k: int,
    threshold: float,
    max_cases: int | None,
    judge_chunk_chars: int,
    quiet: bool,
    include_reason: bool,
    kb_collection: str | None,
    doc_types: list[str] | None,
) -> None:
    path = Path(cases_path)
    if not path.is_file():
        path = REPO_ROOT / cases_path
    if not path.is_file():
        print(f"[ERROR] 找不到用例文件: {path}")
        sys.exit(1)

    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    cases = data.get("cases", [])
    if not cases:
        print("[ERROR] JSON 中没有 cases")
        sys.exit(1)
    if max_cases is not None:
        cases = cases[: max_cases]

    judge = _judge_model()

    init_db()
    db = SessionLocal()
    ollama = OllamaClient()
    work_items: list[tuple[LLMTestCase, str, bool]] = []
    abstain_ids: list[str] = []

    try:
        for case in cases:
            q = case["question"]
            case_id = case.get("id", "?")
            sources = multi_query_search(db, ollama, q, top_k, kb_collection, doc_types)
            answer = get_rag_answer(ollama, q, sources)
            retrieval = [r.get("full_content", r.get("snippet", "")) for r in sources if r]
            if not retrieval:
                print(f"[SKIP] [{case_id}] 检索无命中，跳过 DeepEval 指标（避免空上下文）")
                continue
            abstain = _is_abstention(answer)
            if abstain:
                abstain_ids.append(str(case_id))
            eval_chunks = _truncate_chunks(retrieval, judge_chunk_chars)
            tc = LLMTestCase(
                name=str(case_id),
                input=q,
                actual_output=answer,
                retrieval_context=eval_chunks,
            )
            work_items.append((tc, str(case_id), abstain))
    finally:
        ollama.close()
        db.close()

    if not work_items:
        print("[ERROR] 没有可评测的用例（可能知识库为空或与用例无关）")
        sys.exit(1)

    print(
        f"\n[INFO] DeepEval 逐条评测：{len(work_items)} 条 | top_k={top_k} | threshold={threshold}"
        f" | judge_chunk_chars={judge_chunk_chars}"
        f" | kb_collection={kb_collection or '(默认)'} | doc_types={doc_types or '(不过滤)'}\n"
        f"   评判模型: {os.getenv('DEEPEVAL_JUDGE_MODEL', '') or settings.ollama_chat_model}"
    )
    if abstain_ids:
        print(
            f"   拒答用例(仅评 Contextual Relevancy): {', '.join(abstain_ids)}"
        )
    print()

    disp = DisplayConfig(
        show_indicator=not quiet,
        print_results=not quiet,
    )
    summary_rows: list[tuple[str, bool, str, float | None, bool]] = []

    for tc, case_id, abstain in work_items:
        mets = _metrics_for_case(judge, threshold, abstain, include_reason)
        if not quiet:
            label = f"{case_id} (retrieval-only)" if abstain else case_id
            print(f"\n---------- {label} ----------")
        result = evaluate(
            [tc],
            mets,
            async_config=AsyncConfig(run_async=False, max_concurrent=1),
            display_config=disp,
        )
        tr = result.test_results[0]
        for md in tr.metrics_data or []:
            summary_rows.append(
                (case_id, abstain, md.name, md.score, md.success),
            )

    # 文本汇总（不依赖 Rich/emoji，便于 Windows 控制台）
    print("\n[SUMMARY]")
    print(f"{'case_id':<12} {'abstain':<8} {'metric':<28} {'score':>8} {'pass':>5}")
    for case_id, abstain, name, score, ok in summary_rows:
        sc = f"{score:.4f}" if score is not None else "n/a"
        print(f"{case_id:<12} {str(abstain):<8} {name:<28} {sc:>8} {str(ok):>5}")
    ctx_scores = [s for _, _, n, s, _ in summary_rows if n == "Contextual Relevancy" and s is not None]
    if ctx_scores:
        avg = sum(ctx_scores) / len(ctx_scores)
        print(f"\nContextual Relevancy 平均分: {avg:.4f} (n={len(ctx_scores)})")
    print(
        "\n解读: 若多数题拒答且该项偏低，说明 Top-K 片段与问题不匹配（语料杂或检索需调参）。"
        "与 test_cases 对应的文档应入库到同一知识库再评。"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DeepEval RAG 离线评测")
    parser.add_argument("--cases", default="eval/test_cases.json", help="测试用例 JSON")
    parser.add_argument("--top-k", type=int, default=5, help="检索条数")
    parser.add_argument("--threshold", type=float, default=0.5, help="各指标通过阈值")
    parser.add_argument("--max-cases", type=int, default=None, help="只跑前 N 条（调试）")
    parser.add_argument(
        "--judge-chunk-chars",
        type=int,
        default=2000,
        help="每条检索片段送入 DeepEval 评委的最大字符数（默认 2000，0 表示不截断）",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="少打印 DeepEval 详细块，仅保留末尾 [SUMMARY] 表",
    )
    parser.add_argument(
        "--no-reason",
        action="store_true",
        help="评委不生成 reason，略快",
    )
    parser.add_argument("--kb-collection", default=None, help="知识库分区；或 EVAL_KB_COLLECTION")
    parser.add_argument(
        "--doc-types",
        default=None,
        help="逗号分隔类型 tutorial,api,...；或 EVAL_DOC_TYPES",
    )
    args = parser.parse_args()
    kb = (args.kb_collection or os.environ.get("EVAL_KB_COLLECTION") or "").strip() or None
    dt_raw = args.doc_types or os.environ.get("EVAL_DOC_TYPES") or ""
    doc_types = (
        sanitize_doc_types_list([x.strip() for x in dt_raw.split(",") if x.strip()])
        if dt_raw.strip()
        else None
    )
    run(
        cases_path=args.cases,
        top_k=args.top_k,
        threshold=args.threshold,
        max_cases=args.max_cases,
        judge_chunk_chars=args.judge_chunk_chars,
        quiet=args.quiet,
        include_reason=not args.no_reason,
        kb_collection=kb,
        doc_types=doc_types,
    )
