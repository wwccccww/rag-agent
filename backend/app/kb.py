"""知识库分区名与文档类型校验。"""

import re

from app.config import settings

_KB_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_DOC_TYPE_RE = re.compile(r"^[a-z0-9_-]{1,32}$")

# 仅作 UI 预设提示；任意符合 slug 规则的字符串均可作为 doc_type 入库或检索过滤
PRESET_DOC_TYPES = ("tutorial", "api", "requirements", "general")


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


def slugify_doc_type(raw: str) -> str | None:
    """将用户输入规范为 doc_type slug；无法得到合法值时返回 None。"""
    s = str(raw).strip().lower()
    if not s:
        return None
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^a-z0-9_-]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-_")
    if not s:
        return None
    s = s[:32].strip("-_")
    if not s or not _DOC_TYPE_RE.match(s):
        return None
    return s


def normalize_doc_type(raw: str | None) -> str:
    """写入路径：空视为 general；否则须能 slug 为合法标识，否则 ValueError。"""
    if raw is None or not str(raw).strip():
        return "general"
    t = slugify_doc_type(str(raw))
    if t is None:
        raise ValueError(
            "非法 doc_type：需为 1–32 位小写字母、数字、下划线、连字符（空格等会转为连字符）；"
            "当前输入无法得到合法标识。"
        )
    return t


def sanitize_doc_types_list(items: list[str] | None) -> list[str] | None:
    """去重；非法项静默跳过（用于检索过滤、评测脚本）。"""
    if not items:
        return None
    out: list[str] = []
    for x in items:
        t = slugify_doc_type(str(x))
        if t and t not in out:
            out.append(t)
    return out or None
