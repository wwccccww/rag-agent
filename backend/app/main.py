import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import Base, engine, init_db
from app.routers import chat, docs, health, ingest, memory, sessions

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 在后台线程做 DB 初始化，不阻塞主进程启动
    t = threading.Thread(target=init_db, daemon=True)
    t.start()
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
app.include_router(ingest.router)
app.include_router(chat.router)
app.include_router(memory.router)
app.include_router(docs.router)
app.include_router(sessions.router)


@app.get("/")
def root() -> dict:
    return {"service": "rag-agent", "docs": "/docs"}
