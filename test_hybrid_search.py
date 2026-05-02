"""
混合检索 vs 纯向量检索 对比测试
用法：
  cd d:\1study\study\python\rag-agent\backend
  .\.venv\Scripts\Activate.ps1
  cd ..
  python test_hybrid_search.py
"""

import sys
import os

# 加入 backend 到 Python 路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

from app.database import SessionLocal
from app.kb import sanitize_doc_types_list
from app.services.rag import search_chunks
from app.services.ollama import OllamaClient
from app.config import settings


def run_search(query: str, top_k: int = 5, hybrid: bool = True):
    """运行检索并返回结果列表"""
    db = SessionLocal()
    client = OllamaClient()
    try:
        # 临时切换混合检索开关
        original = settings.hybrid_search
        settings.hybrid_search = hybrid
        kb = (os.environ.get("EVAL_KB_COLLECTION") or "").strip() or None
        dt_raw = os.environ.get("EVAL_DOC_TYPES") or ""
        doc_types = (
            sanitize_doc_types_list([x.strip() for x in dt_raw.split(",") if x.strip()])
            if dt_raw.strip()
            else None
        )
        results = search_chunks(db, client, query, top_k, kb, doc_types)
        settings.hybrid_search = original
        return results
    finally:
        client.close()
        db.close()


def print_results(results: list, label: str):
    print(f"\n{'='*60}")
    print(f"  {label}  （共 {len(results)} 条）")
    print(f"{'='*60}")
    for i, r in enumerate(results, 1):
        print(f"\n[{i}] 来源: {r.get('source', '?')}  | 评分: {r.get('score', 0):.4f}")
        print(f"    {r.get('snippet', '')[:120]}…")


def compare(query: str, top_k: int = 5):
    print(f"\n{'#'*60}")
    print(f"  查询: 「{query}」")
    print(f"{'#'*60}")

    print("\n⏳ 运行纯向量检索…")
    vec_results = run_search(query, top_k, hybrid=False)
    print_results(vec_results, "纯向量检索（pgvector cosine）")

    print("\n⏳ 运行混合检索（向量 + pg_trgm RRF）…")
    hyb_results = run_search(query, top_k, hybrid=True)
    print_results(hyb_results, "混合检索（Hybrid Search + RRF）")

    # 比较差异
    vec_ids = [str(r["chunk_id"]) for r in vec_results]
    hyb_ids = [str(r["chunk_id"]) for r in hyb_results]
    new_in_hybrid = [hid for hid in hyb_ids if hid not in vec_ids]
    print(f"\n📊 差异分析：混合检索召回了 {len(new_in_hybrid)} 个纯向量未命中的片段")
    if new_in_hybrid:
        print("   新召回的 chunk_id：", new_in_hybrid)


if __name__ == "__main__":
    # 你可以修改下面的查询词来测试不同场景
    # 建议先在 UI 上传一个文档，再用文档里出现的具体词汇测试
    queries = [
        "向量数据库",          # 测试语义匹配
        "pgvector 使用方法",   # 混合：有精确词也有语义
        "如何安装",            # 纯关键词
    ]

    if len(sys.argv) > 1:
        # 支持命令行传入查询词：python test_hybrid_search.py "你的问题"
        queries = [" ".join(sys.argv[1:])]

    for q in queries:
        try:
            compare(q, top_k=5)
        except Exception as e:
            print(f"\n❌ 查询「{q}」出错: {e}")
            print("   请确认：1) 后端 DB 可连接  2) 已上传过至少一个文档")

    print("\n✅ 测试完成")
