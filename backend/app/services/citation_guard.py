"""对话回复中的 RAG 引用 [S1]… 与检索片段的事实对齐校验。"""
from __future__ import annotations

import re
from math import ceil
from typing import Any

_CITATION_RE = re.compile(r"\[S(\d+)\]", re.IGNORECASE)
_FENCE_RE = re.compile(r"```[\s\S]*?```", re.MULTILINE)


def _strip_fences(text: str) -> str:
    return _FENCE_RE.sub(" ", text)


def _significant_terms(text: str, *, max_terms: int) -> list[str]:
    """从文本抽取用于「答案—片段」重叠的短语（中文连续字、英文标识符）。"""
    t = _strip_fences(text)
    t = _CITATION_RE.sub(" ", t)
    found: list[str] = []
    found.extend(re.findall(r"[\u4e00-\u9fff]{2,}", t))
    found.extend(re.findall(r"[a-zA-Z][a-zA-Z0-9_]{2,}", t))
    uniq = sorted(set(found), key=len, reverse=True)
    out: list[str] = []
    for w in uniq:
        if len(w) < 2:
            continue
        out.append(w)
        if len(out) >= max_terms:
            break
    return out


def _source_supports_answer(
    answer: str,
    source: dict[str, Any],
    *,
    min_hits: int,
    min_term_frac: float,
    max_source_terms: int,
) -> bool:
    """若答案正文与片段共享足够多「可核验短语」，则认为该 [Sk] 引用成立。"""
    body = (answer or "").strip()
    src_text = (source.get("full_content") or source.get("snippet") or "").strip()
    if not body or not src_text:
        return False
    src_terms = _significant_terms(src_text, max_terms=max_source_terms)
    if not src_terms:
        return True
    hits = sum(1 for term in src_terms if term in body)
    need = max(min_hits, int(ceil(len(src_terms) * min_term_frac)))
    need = min(need, len(src_terms))
    return hits >= max(need, 1)


def sanitize_assistant_citations(
    text: str,
    sources: list[dict[str, Any]],
    *,
    enabled: bool,
    min_hits: int,
    min_term_frac: float,
    max_source_terms: int,
) -> tuple[str, list[int]]:
    """移除与正文事实对齐不足的 [Sk] 标记。返回 (新正文, 被移除的 1-based 编号列表)。"""
    if not enabled or not text or not sources:
        return text, []
    n = len(sources)
    cited = {int(m.group(1)) for m in _CITATION_RE.finditer(text)}
    if not cited:
        return text, []
    removed: list[int] = []
    for idx in sorted(cited):
        if idx < 1 or idx > n:
            removed.append(idx)
            continue
        if not _source_supports_answer(
            text,
            sources[idx - 1],
            min_hits=min_hits,
            min_term_frac=min_term_frac,
            max_source_terms=max_source_terms,
        ):
            removed.append(idx)
    if not removed:
        return text, []
    out = text
    for idx in sorted(set(removed), reverse=True):
        out = re.sub(rf"\[S{idx}\]", "", out, flags=re.IGNORECASE)
    out = _cleanup_citation_artifacts(out)
    return out, sorted(set(removed))


def _cleanup_citation_artifacts(text: str) -> str:
    """去掉因删引用产生的多余顿号、空括号等。"""
    text = re.sub(r"引用[：:]\s*[、，,]+", "引用：", text)
    text = re.sub(r"[、，,]{2,}", "、", text)
    text = re.sub(r"引用[：:]\s*(?=\n|$)", "", text)
    text = re.sub(r"（\s*）", "", text)
    text = re.sub(r"\(\s*\)", "", text)
    text = re.sub(r"\[\s*\]", "", text)
    return text
