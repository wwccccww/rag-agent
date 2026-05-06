import hashlib
import json
from dataclasses import dataclass

from app.config import settings


@dataclass(frozen=True)
class ToolPolicy:
    level: str
    allowed_tools: frozenset[str]


def get_tool_policy() -> ToolPolicy:
    """
    权限分级（最小可用版本）：
      - low:    全开
      - medium: 禁 python_repl；fetch_url 允许；web_search 由 web_search_enabled 控制
      - high:   仅离线工具
    """
    level = (getattr(settings, "tool_policy_level", "medium") or "medium").strip().lower()
    base_offline = frozenset([
        "search_knowledge_base",
        "recall_user_memory",
        "get_current_datetime",
        "calculate",
    ])
    if level == "high":
        return ToolPolicy(level="high", allowed_tools=base_offline)

    if level == "medium":
        allowed = set(base_offline)
        allowed.add("fetch_url")
        if bool(getattr(settings, "web_search_enabled", False)):
            allowed.add("web_search")
        # python_repl 默认禁用
        return ToolPolicy(level="medium", allowed_tools=frozenset(allowed))

    # low（默认）：全开
    return ToolPolicy(level="low", allowed_tools=frozenset([
        *list(base_offline),
        "web_search",
        "python_repl",
        "fetch_url",
    ]))


def sha256_json(obj: object) -> str:
    data = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()

