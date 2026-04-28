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

| 端点 | 说明 |
|------|------|
| `GET /v1/health` | DB / pgvector / Ollama / 模型探测 |
| `POST /v1/ingest` | 文件上传（`.txt/.md/.pdf`）、URL 抓取、纯文本入库 |
| `POST /v1/chat/stream` | SSE 流式对话（`sources` → `token` → `final`） |
| `POST /v1/memory` | 手动写入长期记忆（自动向量化） |
| `GET /v1/memory?user_id=` | 列出记忆 |
| `DELETE /v1/memory/{id}` | 删除记忆 |
| `GET /v1/sessions?user_id=` | 列出会话 |
| `PATCH /v1/sessions/{id}` | 重命名会话 |
| `DELETE /v1/sessions/{id}` | 删除会话及其所有消息 |

## 核心功能 & 测试方法

---

### 1. 混合检索（Hybrid Search + RRF）

每次对话同时走两条召回路径后 RRF 融合：

| 路径 | 实现 | 优势 |
|------|------|------|
| 向量检索 | pgvector 余弦距离 | 语义相近的片段，措辞不同也能命中 |
| 文本检索 | pg_trgm `word_similarity` | 精确词汇（路径、参数名）不因语义漂移而漏掉 |

RRF 公式：`score = 1/(60 + 向量排名) + 1/(60 + 文本排名)`，`pg_trgm` 不可用时自动降级纯向量。

**测试步骤：**

```powershell
# 1. 确认后端启动日志出现以下两行
#    INFO:root:[DB] extensions ready (vector + pg_trgm)
#    INFO:root:[DB] GIN trgm index ready

# 2. 先在前端上传至少一个文档

# 3. 运行对比脚本（用文档里实际出现的词）
cd d:\1study\study\python\rag-agent\backend
.\.venv\Scripts\Activate.ps1
cd ..
python test_hybrid_search.py "application/json"
python test_hybrid_search.py "/user PUT"
```

**预期输出：**
```
📊 差异分析：混合检索召回了 2 个纯向量未命中的片段
   新召回的 chunk_id： ['6c2de24d-...', 'ae879ad7-...']
```

`差异 > 0` 说明文本检索补充了向量漏掉的片段。纯语义查询（如「如何验证用户身份」）差异为 0 属正常，两路结果重合。

---

### 2. 查询改写（Query Rewriting）

对话前用一次轻量 LLM 调用把用户问题扩写成 3 个角度，多路检索后按最高分去重合并：

```
用户输入：「如何查看用户信息」
改写后：["如何获取用户详情", "用户信息查询接口"]
→ 3 路混合检索 → 去重合并 → top_k 结果
```

**测试方法：** 查看后端日志，对话时出现如下行说明查询改写生效：
```
INFO:root:[RAG] query rewrite: 如何查看用户信息 → ['如何获取用户详情', '用户信息查询接口']
```

**开关：** 在 `.env` 中设置 `QUERY_REWRITE=false` 可关闭（减少约 2s 延迟）。

---

### 3. URL 网页入库

前端入库页（`/ingest`）新增三个 Tab：

| Tab | 用途 |
|-----|------|
| 📄 上传文件 | 本地 `.txt / .md / .pdf` |
| 🌐 网页 URL | 填入链接，后端自动抓取正文 |
| 📝 粘贴文本 | 直接粘贴内容 |

URL 抓取流程：`httpx 请求 → BeautifulSoup 解析 → 过滤 script/nav/footer → 提取正文 → 分块向量化`

**测试步骤：**
1. 打开 `http://localhost:3000/ingest`
2. 点击「🌐 网页 URL」Tab
3. 填入任意可访问的网页地址（如技术文档、博客）
4. 点击「抓取并入库」，等待完成提示
5. 回到主页对话，提问该网页的内容

**验证：** 后端日志出现以下行说明抓取成功：
```
INFO:root:[URL] fetched https://... → 12480 chars (title: 页面标题)
```

---

### 4. 耗时追踪（Observability）

后端对所有 Ollama 调用自动记录延迟和 token 数，无需任何操作，运行时即可在终端看到：

```
# 流式对话
INFO:root:[Ollama] first token 1823ms          ← 首 token 延迟（TTFT）
INFO:root:[Ollama] stream done 8421ms | tokens=156 18.5 tok/s  ← 总耗时 & 吞吐量

# 非流式调用（查询改写、自动摘要、记忆提取）
INFO:root:[Ollama] chat_complete 2341ms | prompt_tokens=48 eval_tokens=32
```

这些指标用于评估和调优本地模型性能。

---

### 5. 分层记忆

| 层次 | 实现 | 触发条件 |
|------|------|---------|
| 短期记忆 | 最近 `chat_history_turns × 2` 条消息（默认 12 轮） | 每次对话自动 |
| 长期记忆 | 向量化存入 `memories` 表，对话前按语义相似度注入 | 输入含「记住/我是/我叫/我喜欢/我擅长」等触发词 |
| 去重合并 | 余弦距离 < 0.15（相似度 > 85%）时更新已有记忆 | 写入新记忆时自动检查 |
| 会话摘要 | LLM 压缩早期对话存入 `session.summary`，注入 prompt | 消息数超 20 条后每 10 条触发 |

**测试长期记忆：**
1. 对话中输入「我是 Java 后端开发者，我擅长 Spring Boot」
2. 右上角出现「💾 已记住：Java 后端开发者…」Toast
3. 开启新会话，提问「你知道我是谁」，AI 应能从记忆中回答
4. 访问 `/memory?user_id=demo` 可查看和管理所有记忆

**测试会话摘要：**
1. 在同一会话中持续对话超过 20 条消息
2. 后端日志出现：`INFO:root:[Chat] session xxxx summarized (20 msgs)`
3. 后续对话的 system prompt 自动包含历史摘要

---

## 配置项（`backend/.env`）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DATABASE_URL` | `postgresql+psycopg://rag:ragpass@...` | PostgreSQL 连接串 |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Ollama 服务地址 |
| `OLLAMA_CHAT_MODEL` | `qwen2.5:7b` | 对话模型 |
| `OLLAMA_EMBED_MODEL` | `nomic-embed-text:latest` | Embedding 模型 |
| `QUERY_REWRITE` | `true` | 查询改写开关（关闭可减少约 2s 延迟） |
| `HYBRID_SEARCH` | `true` | 混合检索开关 |
| `SUMMARY_THRESHOLD` | `20` | 触发会话摘要的消息数阈值 |
| `RAG_TOP_K` | `8` | 每次检索返回的片段数 |

## 简历描述

基于 FastAPI + SSE 实现本地 RAG 流式对话服务；设计三阶段检索管线（查询改写 → 混合召回 → RRF 重排），pgvector 向量检索结合 pg_trgm 三元组文本检索提升召回率；分层记忆（短期滑动窗口 + 长期向量化去重 + 会话自动摘要）支持跨会话上下文感知；支持文件/URL/文本多入口入库，Ollama 调用全链路耗时追踪；Next.js BFF 代理 SSE 并提供可演示 Web UI。
