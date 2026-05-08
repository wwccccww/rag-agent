#!/usr/bin/env python3
"""
RAG 评估脚本：对比混合检索 vs 纯向量检索的 Recall@k，
并使用 LLM-as-Judge 评分回答的忠实度（Faithfulness）。

用法：
  cd d:/1study/study/python/rag-agent
  python eval/eval_rag.py [--top-k 5] [--cases eval/test_cases.json] [--output eval/report.md]

依赖：已激活 backend/.venv，后端数据库可访问。
"""
import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any
from contextlib import contextmanager

# ── 把 backend 加入 Python 路径 ──────────────────────────────────────────────
BACKEND_DIR = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))
REPO_DIR = Path(__file__).parent.parent.resolve()

# 设置必要环境变量（.env 会由 pydantic-settings 读取）
import os
os.chdir(BACKEND_DIR)

from app.database import SessionLocal, init_db
from app.kb import sanitize_doc_types_list
from app.services.ollama import OllamaClient
from app.services.rag import multi_query_search, search_chunks
from app.config import settings


# ── 纯向量检索（不做 query rewrite，直接 1 次 embedding 搜索）────────────────
@contextmanager
def _temp_setting(name: str, value: object):
    """临时覆盖 settings 的单个字段，用于评测对照实验，退出时恢复。"""
    old = getattr(settings, name)
    setattr(settings, name, value)
    try:
        yield
    finally:
        setattr(settings, name, old)


def vector_only_search(
    db,
    ollama: OllamaClient,
    query: str,
    top_k: int,
    kb_collection: str | None,
    doc_types: list[str] | None,
) -> list[dict[str, Any]]:
    # 关键点：search_chunks() 默认包含「向量 + pg_trgm + RRF」混合检索逻辑。
    # 为了做真实的 vector-only 对照，这里在调用期间临时关闭 hybrid_search，
    # 确保不会执行 pg_trgm 那一路，也不会进行双路 RRF 融合（退化为纯向量排序）。
    with _temp_setting("hybrid_search", False):
        return search_chunks(db, ollama, query, top_k, kb_collection, doc_types)


# ── Recall@k 计算 ────────────────────────────────────────────────────────────
def recall_at_k(results: list[dict], expected_keywords: list[str], k: int) -> float:
    """在 top-k 结果的文本中，至少命中 1 个关键词则 recall=1，否则=0。"""
    if not expected_keywords:
        return 1.0  # 无法验证时默认通过
    top = results[:k]
    all_text = " ".join(r.get("full_content", "") + " " + r.get("snippet", "") for r in top).lower()
    hit = any(kw.lower() in all_text for kw in expected_keywords)
    return 1.0 if hit else 0.0


# ── LLM-as-Judge：忠实度评分（0~1）─────────────────────────────────────────
def faithfulness_score(ollama: OllamaClient, question: str, context: str, answer: str) -> float:
    """
    要求 LLM 给出忠实度评分（0/0.5/1）：
      1.0 = 回答完全基于提供的上下文
      0.5 = 回答部分基于上下文，部分使用了模型自身知识
      0.0 = 回答与上下文无关，完全依赖模型训练知识
    """
    prompt = f"""你是一个评估 RAG 系统质量的评委。
    
【问题】{question}

【检索到的上下文片段】
{context[:2000]}

【模型的回答】
{answer}

请评估"回答"对"上下文片段"的忠实度，只返回一个数字：
- 1.0：回答完全基于提供的上下文内容
- 0.5：回答部分基于上下文，部分补充了模型自身知识
- 0.0：回答基本不依赖上下文，使用了模型训练知识

只回复数字，不要任何解释。"""
    try:
        resp = ollama.chat_complete([{"role": "user", "content": prompt}], temperature=0.0)
        text = resp.strip()
        for val in ["1.0", "0.5", "0.0", "1", "0"]:
            if val in text:
                return float(val)
        return 0.5
    except Exception as e:
        print(f"  [Judge] LLM 打分失败: {e}")
        return -1.0  # -1 表示评估失败，结果中会标注


# ── 获取 RAG 回答（非流式）──────────────────────────────────────────────────
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
    try:
        return ollama.chat_complete(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
        )
    except Exception as e:
        return f"LLM 回答失败: {e}"


# ── 主评估流程 ───────────────────────────────────────────────────────────────
def run_eval(
    cases_path: str,
    top_k: int,
    output_path: str | None,
    judge: bool,
    kb_collection: str | None,
    doc_types: list[str] | None,
) -> None:
    # 注意：脚本会 chdir 到 backend/，因此这里对相对路径按仓库根目录解析（Windows 更不易踩坑）
    cases_p = Path(cases_path)
    if not cases_p.is_absolute():
        cases_p = (REPO_DIR / cases_p).resolve()
    if output_path:
        out_p = Path(output_path)
        if not out_p.is_absolute():
            out_p = (REPO_DIR / out_p).resolve()
        output_path = str(out_p)

    with open(cases_p, encoding="utf-8") as f:
        data = json.load(f)
    cases = data.get("cases", [])
    if not cases:
        print("❌ test_cases.json 中没有测试用例，请先填写。")
        return

    kb_note = kb_collection or "(默认分区)"
    dt_note = doc_types or "(不按类型过滤)"
    print(
        f"📋 共 {len(cases)} 条测试用例，top_k={top_k}，kb_collection={kb_note}，doc_types={dt_note}，"
        f"LLM-Judge={'开启' if judge else '关闭'}"
    )
    print("=" * 72)

    init_db()
    db = SessionLocal()
    ollama = OllamaClient()

    rows: list[dict] = []

    for case in cases:
        q = case["question"]
        expected_kw = case.get("expected_keywords", [])
        case_id = case.get("id", "?")

        print(f"\n[{case_id}] {q}")

        # 混合检索
        t0 = time.perf_counter()
        hybrid_results = multi_query_search(db, ollama, q, top_k, kb_collection, doc_types)
        hybrid_ms = int((time.perf_counter() - t0) * 1000)

        # 纯向量检索
        t0 = time.perf_counter()
        vec_results = vector_only_search(db, ollama, q, top_k, kb_collection, doc_types)
        vec_ms = int((time.perf_counter() - t0) * 1000)

        hybrid_recall = recall_at_k(hybrid_results, expected_kw, top_k)
        vec_recall = recall_at_k(vec_results, expected_kw, top_k)

        print(f"  混合检索: Recall@{top_k}={hybrid_recall:.1f}  ({hybrid_ms}ms,  {len(hybrid_results)} 片段)")
        print(f"  纯向量:   Recall@{top_k}={vec_recall:.1f}  ({vec_ms}ms,  {len(vec_results)} 片段)")

        hybrid_faith = -2.0
        vec_faith = -2.0

        if judge:
            hybrid_answer = get_rag_answer(ollama, q, hybrid_results)
            vec_answer = get_rag_answer(ollama, q, vec_results)

            hybrid_ctx = "\n".join(r.get("full_content", "") for r in hybrid_results[:3])
            vec_ctx = "\n".join(r.get("full_content", "") for r in vec_results[:3])

            hybrid_faith = faithfulness_score(ollama, q, hybrid_ctx, hybrid_answer)
            vec_faith = faithfulness_score(ollama, q, vec_ctx, vec_answer)
            print(f"  忠实度(混合): {hybrid_faith:.1f}   忠实度(纯向量): {vec_faith:.1f}")

        rows.append({
            "id": case_id,
            "question": q[:50] + ("…" if len(q) > 50 else ""),
            "hybrid_recall": hybrid_recall,
            "vec_recall": vec_recall,
            "hybrid_ms": hybrid_ms,
            "vec_ms": vec_ms,
            "hybrid_faith": hybrid_faith,
            "vec_faith": vec_faith,
        })

    db.close()

    # ── 汇总 ──────────────────────────────────────────────────────────────
    n = len(rows)
    avg_hybrid_recall = sum(r["hybrid_recall"] for r in rows) / n
    avg_vec_recall = sum(r["vec_recall"] for r in rows) / n
    faith_rows = [r for r in rows if r["hybrid_faith"] >= 0]
    avg_hybrid_faith = sum(r["hybrid_faith"] for r in faith_rows) / len(faith_rows) if faith_rows else float("nan")
    avg_vec_faith = sum(r["vec_faith"] for r in faith_rows) / len(faith_rows) if faith_rows else float("nan")

    print("\n" + "=" * 72)
    print(f"📊 汇总 (n={n})")
    print(f"  混合检索  Recall@{top_k}: {avg_hybrid_recall:.2f}   纯向量 Recall@{top_k}: {avg_vec_recall:.2f}")
    if faith_rows:
        print(f"  混合检索  Faithfulness:  {avg_hybrid_faith:.2f}   纯向量 Faithfulness:  {avg_vec_faith:.2f}")  # noqa: E501

    # ── Markdown 报告 ─────────────────────────────────────────────────────
    lines: list[str] = [
        "# RAG 评估报告",
        "",
        f"**评估时间**：{time.strftime('%Y-%m-%d %H:%M:%S')}  ",
        f"**Top-K**：{top_k}  ",
        f"**测试用例数**：{n}  ",
        "",
        "## 指标汇总",
        "",
        f"| 方法 | Recall@{top_k} | Faithfulness |",
        "|------|------------|-------------|",
        f"| 混合检索（Hybrid + RRF） | **{avg_hybrid_recall:.2f}** | {f'{avg_hybrid_faith:.2f}' if faith_rows else 'N/A'} |",
        f"| 纯向量检索 | {avg_vec_recall:.2f} | {f'{avg_vec_faith:.2f}' if faith_rows else 'N/A'} |",
        "",
        "## 逐题结果",
        "",
        f"| ID | 问题 | 混合 Recall | 向量 Recall | 混合耗时 | 向量耗时 |",
        "|----|------|------------|------------|---------|---------|",
    ]
    for r in rows:
        lines.append(
            f"| {r['id']} | {r['question']} | {r['hybrid_recall']:.1f} | {r['vec_recall']:.1f} | {r['hybrid_ms']}ms | {r['vec_ms']}ms |"
        )

    report = "\n".join(lines)
    if output_path:
        Path(output_path).write_text(report, encoding="utf-8")
        print(f"\n✅ 报告已写入: {output_path}")
    else:
        print("\n" + report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RAG 评估脚本")
    parser.add_argument("--top-k", type=int, default=5, help="检索 top-k（默认 5）")
    parser.add_argument("--cases", default="eval/test_cases.json", help="测试用例 JSON 文件路径")
    parser.add_argument("--output", default=None, help="Markdown 报告输出路径（默认打印到终端）")
    parser.add_argument("--judge", action="store_true", help="开启 LLM-as-Judge 忠实度评分（较慢）")
    parser.add_argument(
        "--kb-collection",
        default=None,
        help="知识库分区；也可用环境变量 EVAL_KB_COLLECTION",
    )
    parser.add_argument(
        "--doc-types",
        default=None,
        help="逗号分隔文档类型：tutorial,api,requirements,general；也可用 EVAL_DOC_TYPES",
    )
    args = parser.parse_args()

    kb = (args.kb_collection or os.environ.get("EVAL_KB_COLLECTION") or "").strip() or None
    dt_raw = args.doc_types or os.environ.get("EVAL_DOC_TYPES") or ""
    doc_types = sanitize_doc_types_list([x.strip() for x in dt_raw.split(",") if x.strip()]) if dt_raw.strip() else None

    run_eval(
        cases_path=args.cases,
        top_k=args.top_k,
        output_path=args.output,
        judge=args.judge,
        kb_collection=kb,
        doc_types=doc_types,
    )
