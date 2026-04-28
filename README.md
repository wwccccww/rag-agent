# RAG Agent（流式对话 + RAG + 分层记忆）

偏后端作品：FastAPI 提供 SSE 流式对话与文档入库；Postgres + pgvector 存向量；Ollama 本地 `qwen2.5:7b` 生成、`nomic-embed-text` 向量化；Next.js（App Router）作为 BFF 代理 SSE，便于演示。

## 架构

- **Backend**：`backend/`（FastAPI）
- **Frontend**：`frontend/`（Next.js）
- **DB**：Postgres + pgvector（你已在虚拟机 `192.168.116.130:5432` 部署）
- **LLM**：Windows 本机 Ollama `http://127.0.0.1:11434`

## 1) 准备数据库

确保数据库里已 `CREATE EXTENSION vector;`（你当前为 `vector 0.8.2`）。

## 2) 启动后端（Windows）

```powershell
cd d:\1study\study\python\rag-agent\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# 按需编辑 .env 中的 DATABASE_URL
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

打开 `http://127.0.0.1:8000/docs` 可调试 API。

## 3) 启动前端（Windows）

```powershell
cd d:\1study\study\python\rag-agent\frontend
npm install
copy .env.example .env.local
npm run dev
```

打开 `http://localhost:3000`。

## 主要 API

- `GET /v1/health`：DB / pgvector / Ollama / 模型探测
- `POST /v1/ingest`：multipart 上传文件（`.txt/.md/.pdf`）
- `POST /v1/chat/stream`：`text/event-stream`（`sources` → `token` → `final`）
- `POST /v1/memory`：手动写入长期记忆（会向量化）
- `GET /v1/memory?user_id=demo`：列出记忆
- `DELETE /v1/memory/{id}?user_id=demo`：删除记忆

## 自动长期记忆（轻量规则）

当用户输入包含类似「记住 / 我是 / 我叫 / 我喜欢 / 我在做」等触发词时，后端会用一次非流式小调用抽取 JSON，并写入 `memories` 表；对话前会按相似度检索注入。

## 简历一句话

实现基于 FastAPI + SSE 的本地 RAG 对话服务，pgvector 存储与检索文档向量，Ollama（Qwen2.5 + nomic-embed）完成生成与嵌入，Next.js BFF 代理流式响应并提供可演示 Web UI。
