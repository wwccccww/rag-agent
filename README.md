# RAG Agent（流式对话 + RAG + 分层记忆）

偏后端作品：FastAPI 提供 SSE 流式对话与文档入库；Postgres + pgvector 存向量；Ollama 本地 `qwen2.5:7b` 生成、`nomic-embed-text` 向量化；Next.js（App Router）作为 BFF 代理 SSE，便于演示。

## 架构

- **Backend**：`backend/`（FastAPI）
- **Frontend**：`frontend/`（Next.js）
- **DB**：Postgres + pgvector（虚拟机 `192.168.116.130:5432`）
- **LLM**：Windows 本机 Ollama `http://127.0.0.1:11434`

## 1) 准备数据库

确保数据库里已 `CREATE EXTENSION vector;`（当前为 `vector 0.8.2`）。  
`pg_trgm` 扩展和 GIN 索引会在后端首次启动时自动创建，无需手动操作。

## 2) 启动后端（Windows）

```powershell
cd d:\1study\study\python\rag-agent\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# 按需编辑 .env 中的 DATABASE_URL
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

后端就绪后日志会输出：
```
INFO:root:[DB] extensions ready (vector + pg_trgm)
INFO:root:[DB] tables ready
INFO:root:[DB] GIN trgm index ready
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
- `GET /v1/sessions?user_id=demo`：列出会话
- `PATCH /v1/sessions/{id}`：重命名会话
- `DELETE /v1/sessions/{id}`：删除会话及其消息

## 核心功能说明

### 混合检索（Hybrid Search + RRF）

每次对话检索知识库时，同时走两条召回路径再融合排序：

| 路径 | 实现 | 优势 |
|------|------|------|
| 向量检索 | pgvector 余弦距离 | 语义相近的片段，即使措辞不同也能命中 |
| 文本检索 | pg_trgm `word_similarity` | 精确词汇（路径、参数名、专有名词）不因语义漂移而漏掉 |

两路结果通过 **RRF（Reciprocal Rank Fusion）** 公式 `1/(60 + rank)` 合并，自动平衡两路权重。  
`pg_trgm` 不可用时自动降级为纯向量检索，不影响系统运行。

**验证混合检索效果（先上传至少一个文档）：**

```powershell
cd d:\1study\study\python\rag-agent\backend
.\.venv\Scripts\Activate.ps1
cd ..
# 用文档里实际出现的词测试，如精确路径或参数名
python test_hybrid_search.py "application/json"
python test_hybrid_search.py "/user PUT"
```

输出示例：
```
📊 差异分析：混合检索召回了 2 个纯向量未命中的片段
   新召回的 chunk_id： ['6c2de24d-...', 'ae879ad7-...']
```

`差异 > 0` 说明三元组文本检索补充了向量漏掉的片段，混合检索生效。  
纯语义查询（如「如何验证用户身份」）两路结果可能重合，差异为 0 属正常。

### 分层记忆

- **短期记忆**：当前会话最近 `chat_history_turns × 2` 条消息（默认 12 轮）
- **长期记忆**：跨会话的用户事实，向量化存入 `memories` 表，对话前按语义相似度注入  
  - 自动触发：输入含「记住 / 我是 / 我叫 / 我喜欢 / 我在做 / 我擅长」等关键词  
  - 去重合并：新记忆与已有记忆余弦距离 < 0.15（相似度 > 85%）时更新而非重复写入
- **会话摘要**：消息数超过 20 条后每 10 条自动压缩早期对话为摘要，注入 system prompt，防止 context 溢出

### 自动摘要触发条件

| 消息总数 | 行为 |
|---------|------|
| < 20 条 | 不触发，完整保留历史 |
| 20 条时 | 首次生成摘要（压缩前 14 条） |
| 30、40… 条时 | 每 10 条更新一次摘要 |

## 简历一句话

基于 FastAPI + SSE 实现本地 RAG 流式对话服务；pgvector 向量检索结合 pg_trgm 三元组文本检索，RRF 融合排序提升召回率；分层记忆（短期窗口 + 长期向量化 + 会话自动摘要）支持跨会话上下文感知；Next.js BFF 代理流式响应并提供可演示 Web UI。
