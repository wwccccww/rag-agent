import os
from importlib.metadata import PackageNotFoundError, version

from app.config import settings


def init_sentry() -> bool:
    """
    初始化 Sentry（若未配置 DSN 则跳过）。
    返回：是否启用。
    """
    dsn = (getattr(settings, "sentry_dsn", None) or "").strip()
    if not dsn:
        return False

    try:
        import sentry_sdk  # type: ignore
        from sentry_sdk.integrations.fastapi import FastApiIntegration  # type: ignore
        from sentry_sdk.integrations.logging import LoggingIntegration  # type: ignore
    except Exception:
        # 依赖缺失时不阻塞启动
        return False

    try:
        release = version("rag-agent")
    except PackageNotFoundError:
        # 未打包安装时用 git sha / build id（可选）
        release = os.getenv("APP_VERSION") or os.getenv("GIT_SHA") or "dev"

    sentry_sdk.init(
        dsn=dsn,
        environment=getattr(settings, "sentry_environment", "dev") or "dev",
        release=release,
        sample_rate=float(getattr(settings, "sentry_sample_rate", 1.0) or 1.0),
        integrations=[
            FastApiIntegration(),
            LoggingIntegration(event_level=None),  # 不把普通 error log 当成事件，避免噪音
        ],
        # 避免上报敏感请求体（必要时可进一步自定义 before_send）
        send_default_pii=False,
    )
    return True

