"""
Cross-Encoder Reranker（ms-marco-MiniLM 系列）。

支持启动预热：若 RAG_RERANK_ENABLED=true，后端启动时会在后台线程自动加载模型，
避免首次请求出现长达数秒的等待。
模型默认为 cross-encoder/ms-marco-MiniLM-L-6-v2（~100 MB），也可通过
RAG_RERANK_MODEL 配置为中文增强版 BAAI/bge-reranker-base（~280 MB）。

用法：
    from app.services.reranker import rerank, warmup
    warmup()          # 启动时预热（可选，main.py lifespan 已调用）
    results = rerank(query, candidates, top_k=8)
"""
import logging
import threading
import time
from typing import Any

_lock = threading.Lock()
_encoder: Any = None  # CrossEncoder 实例，首次调用后缓存
_loaded_model: str = ""


def _get_encoder(model_name: str) -> Any:
    """惰性加载 CrossEncoder，线程安全。"""
    global _encoder, _loaded_model
    if _encoder is not None and _loaded_model == model_name:
        return _encoder
    with _lock:
        if _encoder is not None and _loaded_model == model_name:
            return _encoder
        try:
            from sentence_transformers import CrossEncoder  # noqa: PLC0415
        except ImportError as e:
            raise RuntimeError(
                "sentence-transformers 未安装，请运行 pip install sentence-transformers"
            ) from e
        logging.info("[Reranker] 正在加载模型 %s …", model_name)
        t0 = time.perf_counter()
        _encoder = CrossEncoder(model_name, max_length=512)
        _loaded_model = model_name
        logging.info("[Reranker] 模型加载完成，耗时 %.1f s", time.perf_counter() - t0)
        return _encoder


def warmup(model_name: str | None = None) -> None:
    """启动预热：在后台提前加载 CrossEncoder 模型，消除首次请求的冷启动延迟。

    若未传入 model_name，则从 settings 读取配置；若 RAG_RERANK_ENABLED=false 则跳过。
    """
    from app.config import settings  # noqa: PLC0415

    if not getattr(settings, "rag_rerank_enabled", False):
        logging.info("[Reranker] 未启用，跳过预热")
        return

    name = model_name or str(getattr(settings, "rag_rerank_model",
                                      "cross-encoder/ms-marco-MiniLM-L-6-v2"))
    try:
        _get_encoder(name)
    except Exception as e:
        logging.warning("[Reranker] 预热失败（不影响服务启动）: %s", e)


def rerank(
    query: str,
    candidates: list[dict[str, Any]],
    *,
    model_name: str,
    top_k: int,
    score_field: str = "rerank_score",
) -> list[dict[str, Any]]:
    """对候选列表重新打分并返回 top_k 条，按 rerank_score 降序。

    Args:
        query:       用户原始问题。
        candidates:  search_chunks() 返回的字典列表，每条必须有 full_content 字段。
        model_name:  CrossEncoder 模型名称（HuggingFace Hub 或本地路径）。
        top_k:       最终保留条数。
        score_field: 写入结果字典的字段名。

    Returns:
        候选列表的子集（≤ top_k 条），按 rerank_score 降序。
        每条字典额外含 score_field 键（float）。
    """
    if not candidates:
        return candidates

    t0 = time.perf_counter()
    try:
        encoder = _get_encoder(model_name)
        pairs = [(query, c.get("full_content", c.get("snippet", ""))[:512]) for c in candidates]
        scores: list[float] = encoder.predict(pairs).tolist()
    except Exception as e:
        logging.warning("[Reranker] rerank 失败，回落到原始顺序: %s", e)
        return candidates[:top_k]

    elapsed = (time.perf_counter() - t0) * 1000
    logging.info(
        "[Reranker] %d 候选 → top %d，耗时 %.0f ms（模型: %s）",
        len(candidates), top_k, elapsed, model_name,
    )

    ranked = sorted(
        zip(candidates, scores),
        key=lambda x: x[1],
        reverse=True,
    )
    import math  # noqa: PLC0415

    def _sigmoid(x: float) -> float:
        """把 CrossEncoder 原始 logit 映射到 (0, 1)，便于前端以百分比展示。"""
        return 1.0 / (1.0 + math.exp(-x))

    result = []
    for item, sc in ranked[:top_k]:
        out = dict(item)
        out[score_field] = round(float(sc), 6)          # 原始 logit，供调试
        # sigmoid 归一化到 0–1，前端 ×100 后显示为合理百分比
        out["score"] = round(_sigmoid(float(sc)), 4)
        result.append(out)
    return result
