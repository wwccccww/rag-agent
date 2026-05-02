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
    """创建混合检索所需索引（幂等）：GIN 三元组 + HNSW 向量索引"""
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

    # 为 messages 表添加 extra JSONB 列（用于存储 agent 轨迹等扩展信息）
    try:
        with engine.connect() as conn:
            conn.execute(text(
                "ALTER TABLE messages ADD COLUMN IF NOT EXISTS extra JSONB DEFAULT NULL"
            ))
            conn.commit()
        logging.info("[DB] messages.extra column ready")
    except Exception as e:
        logging.warning("[DB] alter messages.extra failed: %s", e)

    # documents：知识库分区 + 文档类型（幂等迁移，兼容旧库）
    try:
        with engine.connect() as conn:
            conn.execute(
                text(
                    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS kb_collection "
                    "VARCHAR(64) NOT NULL DEFAULT 'default'"
                )
            )
            conn.execute(
                text(
                    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS doc_type "
                    "VARCHAR(32) NOT NULL DEFAULT 'general'"
                )
            )
            conn.commit()
        logging.info("[DB] documents.kb_collection / doc_type columns ready")
    except Exception as e:
        logging.warning("[DB] alter documents kb/doc_type failed: %s", e)

    try:
        with engine.connect() as conn:
            conn.execute(
                text("CREATE INDEX IF NOT EXISTS idx_documents_kb_collection ON documents (kb_collection)")
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_documents_kb_doc_type "
                    "ON documents (kb_collection, doc_type)"
                )
            )
            conn.commit()
        logging.info("[DB] documents kb/doc_type indexes ready")
    except Exception as e:
        logging.warning("[DB] documents kb indexes failed: %s", e)

    # HNSW 向量索引：大规模场景下比 IVFFlat 更稳定，查询时不需要预先 probe 调参
    # m=16 ef_construction=64 是官方推荐的均衡参数
    for table, col in [("chunks", "embedding"), ("memories", "embedding")]:
        idx_name = f"idx_{table}_{col}_hnsw"
        try:
            with engine.connect() as conn:
                conn.execute(text(
                    f"CREATE INDEX IF NOT EXISTS {idx_name} "
                    f"ON {table} USING hnsw ({col} vector_cosine_ops) "
                    f"WITH (m = 16, ef_construction = 64)"
                ))
                conn.commit()
            logging.info("[DB] HNSW index ready: %s", idx_name)
        except Exception as e:
            logging.warning("[DB] HNSW index failed for %s: %s", idx_name, e)


def init_db() -> None:
    """建表 + 启用扩展 + 创建索引，失败只记日志不崩溃"""
    ensure_extensions()
    try:
        Base.metadata.create_all(bind=engine)
        logging.info("[DB] tables ready")
    except Exception as e:
        logging.warning(f"[DB] create_all failed: {e}")
    ensure_indexes()
