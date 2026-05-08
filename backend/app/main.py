import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import Base, engine, init_db
from app.routers import audit, chat, docs, health, ingest, kb_access, memory, metrics, multi_agent, sessions, stats
from app.services.reranker import warmup as warmup_reranker
from app.services.sentry import init_sentry

logging.basicConfig(level=logging.INFO)

# 可选：启用 Sentry 错误率/异常聚合（未配置 DSN 时自动跳过）
init_sentry()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 在后台线程做 DB 初始化，不阻塞主进程启动
    t_db = threading.Thread(target=init_db, daemon=True)
    t_db.start()
    # 若 RAG_RERANK_ENABLED=true，后台提前加载 CrossEncoder 模型
    t_rerank = threading.Thread(target=warmup_reranker, daemon=True)
    t_rerank.start()
    yield


app = FastAPI(title="RAG Agent API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:3000", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(metrics.router)
app.include_router(kb_access.router)
app.include_router(ingest.router)
app.include_router(chat.router)
app.include_router(multi_agent.router)
app.include_router(memory.router)
app.include_router(audit.router)
app.include_router(docs.router)
app.include_router(sessions.router)
app.include_router(stats.router)


@app.get("/")
def root() -> dict:
    return {"service": "rag-agent", "docs": "/docs"}
