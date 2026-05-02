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

依赖：已安装 backend/requirements.txt（含 deepeval）；数据库可连；Ollama 已拉取评判模型。

可选环境变量：
  DEEPEVAL_JUDGE_MODEL — 覆盖评判用模型名，默认与 OLLAMA_CHAT_MODEL 相同。
  使用更小模型（如 qwen2.5:3b）可明显加快评测。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

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
from app.services.ollama import OllamaClient  # noqa: E402
from app.services.rag import multi_query_search  # noqa: E402


def _judge_model() -> OllamaModel:
    name = os.getenv("DEEPEVAL_JUDGE_MODEL", "").strip() or settings.ollama_chat_model
    return OllamaModel(
        model=name,
        base_url=settings.ollama_base_url.rstrip("/"),
        temperature=0.0,
    )


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
    full_metrics = [
        FaithfulnessMetric(threshold=threshold, model=judge, include_reason=True, async_mode=False),
        AnswerRelevancyMetric(threshold=threshold, model=judge, include_reason=True, async_mode=False),
        ContextualRelevancyMetric(threshold=threshold, model=judge, include_reason=True, async_mode=False),
    ]
    retrieval_only_metrics = [
        ContextualRelevancyMetric(threshold=threshold, model=judge, include_reason=True, async_mode=False),
    ]

    init_db()
    db = SessionLocal()
    ollama = OllamaClient()
    work_items: list[tuple[LLMTestCase, list[Any], str, bool]] = []

    try:
        for case in cases:
            q = case["question"]
            case_id = case.get("id", "?")
            sources = multi_query_search(db, ollama, q, top_k)
            answer = get_rag_answer(ollama, q, sources)
            retrieval = [r.get("full_content", r.get("snippet", "")) for r in sources if r]
            if not retrieval:
                print(f"[SKIP] [{case_id}] 检索无命中，跳过 DeepEval 指标（避免空上下文）")
                continue
            abstain = _is_abstention(answer)
            tc = LLMTestCase(
                name=str(case_id),
                input=q,
                actual_output=answer,
                retrieval_context=retrieval,
            )
            mets = retrieval_only_metrics if abstain else full_metrics
            work_items.append((tc, mets, str(case_id), abstain))
            if abstain:
                print(
                    f"[NOTE] [{case_id}] 模型拒答 → 仅评测 Contextual Relevancy（检索是否贴题）；"
                    "跳过 Faithfulness / Answer Relevancy，避免指标误导。"
                )
    finally:
        ollama.close()
        db.close()

    if not work_items:
        print("[ERROR] 没有可评测的用例（可能知识库为空或与用例无关）")
        sys.exit(1)

    print(
        f"\n[INFO] DeepEval 逐条评测：{len(work_items)} 条 | top_k={top_k} | threshold={threshold}\n"
        f"   评判模型: {os.getenv('DEEPEVAL_JUDGE_MODEL', '') or settings.ollama_chat_model}\n"
    )

    for tc, mets, case_id, abstain in work_items:
        label = f"{case_id} (retrieval-only)" if abstain else case_id
        print(f"\n---------- {label} ----------")
        evaluate(
            [tc],
            mets,
            async_config=AsyncConfig(run_async=False, max_concurrent=1),
            display_config=DisplayConfig(show_indicator=True, print_results=True),
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DeepEval RAG 离线评测")
    parser.add_argument("--cases", default="eval/test_cases.json", help="测试用例 JSON")
    parser.add_argument("--top-k", type=int, default=5, help="检索条数")
    parser.add_argument("--threshold", type=float, default=0.5, help="各指标通过阈值")
    parser.add_argument("--max-cases", type=int, default=None, help="只跑前 N 条（调试）")
    args = parser.parse_args()
    run(
        cases_path=args.cases,
        top_k=args.top_k,
        threshold=args.threshold,
        max_cases=args.max_cases,
    )
