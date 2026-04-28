import logging
from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker, declarative_base

from app.config import settings

# connect_timeout=5 写进 URL，对 psycopg3 最可靠
_url = settings.database_url
if "connect_timeout" not in _url:
    sep = "&" if "?" in _url else "?"
    _url = f"{_url}{sep}connect_timeout=5"

engine = create_engine(
    _url,
    pool_pre_ping=True,
    echo=False,
    pool_timeout=10,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_extensions() -> None:
    try:
        with engine.connect() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            conn.commit()
        logging.info("[DB] extensions ready (vector + pg_trgm)")
    except Exception as e:
        logging.warning(f"[DB] ensure_extensions failed: {e}")


def ensure_indexes() -> None:
    """创建混合检索所需的 GIN 三元组索引（幂等）"""
    try:
        with engine.connect() as conn:
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_chunks_content_trgm "
                "ON chunks USING GIN (content gin_trgm_ops)"
            ))
            conn.commit()
        logging.info("[DB] GIN trgm index ready")
    except Exception as e:
        logging.warning(f"[DB] ensure_indexes failed (pg_trgm may be unavailable): {e}")


def init_db() -> None:
    """建表 + 启用扩展 + 创建索引，失败只记日志不崩溃"""
    ensure_extensions()
    try:
        Base.metadata.create_all(bind=engine)
        logging.info("[DB] tables ready")
    except Exception as e:
        logging.warning(f"[DB] create_all failed: {e}")
    ensure_indexes()
