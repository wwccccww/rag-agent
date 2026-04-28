from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import Base, engine, ensure_extensions
from app.routers import chat, docs, health, ingest, memory, sessions


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_extensions()
    Base.metadata.create_all(bind=engine)
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
