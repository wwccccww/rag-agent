"""知识库分区名与文档类型校验。"""

import re

from app.config import settings

_KB_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

DOC_TYPES_FROZEN = frozenset({"tutorial", "api", "requirements", "general"})


def validate_kb_collection_optional(raw: str | None) -> str | None:
    """用于请求体：空/空白返回 None；非空须合法，否则抛 ValueError。"""
    if raw is None or not str(raw).strip():
        return None
    s = str(raw).strip()[:64]
    if not _KB_RE.match(s):
        raise ValueError(
            f"非法 kb_collection: {raw!r}，仅允许 1–64 位字母数字、下划线、连字符"
        )
    return s


def resolve_kb_collection(raw: str | None) -> str:
    """空则 default_kb_collection；非空须匹配 [a-zA-Z0-9_-]{1,64}。"""
    if raw is None or not str(raw).strip():
        base = (settings.default_kb_collection or "default").strip()
        if not _KB_RE.match(base):
            return "default"
        return base[:64]
    s = str(raw).strip()[:64]
    if not _KB_RE.match(s):
        raise ValueError(
            f"非法 kb_collection: {raw!r}，仅允许 1–64 位字母数字、下划线、连字符"
        )
    return s


def normalize_doc_type(raw: str | None) -> str:
    if raw is None or not str(raw).strip():
        return "general"
    t = str(raw).strip().lower()
    if t in DOC_TYPES_FROZEN:
        return t
    return "general"


def sanitize_doc_types_list(items: list[str] | None) -> list[str] | None:
    """去重、只保留合法枚举；空列表视为 None（不过滤）。"""
    if not items:
        return None
    out: list[str] = []
    for x in items:
        n = normalize_doc_type(x)
        if n not in out:
            out.append(n)
    return out or None
