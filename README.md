# RAG Agent（流式对话 + RAG + 分层记忆）

偏后端作品：FastAPI 提供 SSE 流式对话与文档入库；Postgres + pgvector 存向量；Ollama 本地 `qwen2.5:7b` 生成、`nomic-embed-text` 向量化；Next.js（App Router）作为 BFF 代理 SSE，便于演示。

## 架构

- **Backend**：`backend/`（FastAPI）
- **Frontend**：`frontend/`（Next.js）
- **DB**：Postgres + pgvector（虚拟机 `192.168.116.130:5432`）
- **LLM**：Windows 本机 Ollama `http://127.0.0.1:11434`

## 1) 准备数据库

确保数据库里已 `CREATE EXTENSION vector;`（当前为 `vector 0.8.2`）。  
`pg_trgm` 扩展、GIN 索引以及 HNSW 向量索引会在后端首次启动时自动创建，无需手动操作。

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
INFO:root:[DB] HNSW index ready: idx_chunks_embedding_hnsw
INFO:root:[DB] HNSW index ready: idx_memories_embedding_hnsw
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
| `GET /v1/stats?user_id=` | 系统统计（文档/片段/会话/消息/记忆数量） |

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

### 6. Markdown 渲染

AI 回复自动解析并渲染 Markdown 格式，流式输出时实时渲染：

| 格式 | 效果 |
|------|------|
| `# 标题` / `**加粗**` / `*斜体*` | 标准 Markdown 排版 |
| 代码块 ` ```python ``` ` | 带语法高亮（oneDark 主题） |
| 行内代码 `` `code` `` | 蓝色等宽字体显示 |
| 表格 / 列表 / 引用块 | 完整 GFM 支持 |

**测试步骤：**
1. 在对话中提问需要代码回答的问题，如「写一个 Python 冒泡排序」
2. 观察 AI 回复是否以格式化方式显示代码块（带语法高亮、行背景）
3. 提问「列出三个 REST API 设计原则」，观察列表是否正常渲染

**预期效果：** 代码块有深色背景和语言标识，列表有缩进和项目符号，标题有加粗层级。

---

### 7. 对话导出

当前会话有消息且不在流式生成中时，顶栏显示「↓ 导出 MD」按钮，点击自动下载 `.md` 文件。

导出格式（含来源引用）：
```markdown
# 会话名称
> 导出时间：2026/4/28 16:30:00

**用户**

用户的问题内容

---

**助手**

助手的回答内容

**参考来源：**
- [S1] document.pdf 第3页 (92%)
  > 片段摘要内容前120字…
- [S2] article.txt (85%)

---
```

**测试步骤：**
1. 在任意会话中发送几条消息，等待回复完成
2. 点击顶栏「↓ 导出 MD」按钮
3. 检查下载的 `.md` 文件，助手回答后附有来源引用列表

---

### 8. Embedding LRU 缓存

进程级 LRU 缓存（512 条，线程安全），相同文本的 Embedding 请求直接返回缓存结果，跳过 Ollama 网络调用。

**命中场景：** 查询改写启用后，同一次对话会对原始查询文本发起多次 embed（改写前后各一次），缓存可消除重复调用。

**测试方法：** 查看后端日志，重复查询同一文本时出现以下行：
```
DEBUG:root:[Ollama] embed cache hit (32 chars)
```

> 注意：Debug 级别日志默认不显示，需在启动命令中加 `--log-level debug` 或修改 Python logging 级别才能看到。

---

### 9. Word / Excel 文档支持 & 批量入库

入库页面支持 `.docx` 和 `.xlsx` 格式，并支持**批量多文件上传**：

| 格式 | 提取内容 |
|------|---------|
| `.docx` | 所有段落文本 + 表格单元格（以 `\|` 分隔） |
| `.xlsx` | 所有 Sheet 的行数据（Sheet 名作标题） |
| `.md` | 纯文本，自动提取首个 `# H1` 作为文档标题 |

**批量上传说明：**
- 点击或拖拽可同时选择多个文件
- 每个文件独立显示状态（⏳待上传 / ⬆️上传中 / ✅成功 / ⚠️重复 / ❌失败）
- 顺序逐文件入库，失败不影响其余文件

**测试步骤：**
1. 打开 `http://localhost:3000/ingest`
2. 按住 Ctrl 多选多个 `.pdf` / `.docx` / `.txt` 文件，或批量拖拽
3. 点击「上传 N 个文件」，观察每个文件逐条更新状态
4. 点击「清除已完成」后可继续追加新文件

**预期日志：**
```
INFO:root:[Extract] docx → 3240 chars, 18 paragraphs
INFO:root:[RAG] ingested report.docx → 12 chunks (filtered short chunks)
```

---

### 10. 消息复制 & 停止生成

- **复制按钮**：鼠标悬停到任意助手回复时，右下角出现「复制」按钮，点击后变为「✓ 已复制」，持续 2 秒
- **停止生成**：流式输出过程中，发送按钮变为红色 `■` 停止按钮；点击立即中断请求，输入框重新激活

---

### 11. 引用内联高亮

AI 回复中包含 `[S1]` `[S2]` 等引用标记时，会渲染为蓝色可点击徽章：

- **悬浮**：显示来源文件名和页码
- **点击**：自动展开源片段列表，目标卡片高亮闪烁 2 秒

**测试步骤：**
1. 上传文档后提问相关内容
2. 如果 AI 回复中包含「（见[S1]）」或「[S2]」，观察其是否渲染为蓝色徽章
3. 点击徽章，观察源片段列表展开并对应卡片高亮

---

### 12. 统计面板（`/stats`）

`GET /v1/stats?user_id=demo` 返回系统整体数据量统计。前端 `/stats` 页以卡片 + 进度条形式展示：

| 指标 | 说明 |
|------|------|
| 文档总数 | 已入库的原始文件数 |
| 向量片段 | 总切块数量，及平均片/文档 |
| 会话数 | 指定 user_id 的会话数 |
| 消息总数 | 用户+助手消息合计 |
| 长期记忆 | 指定 user_id 的记忆条数 |

**测试步骤：**
1. 打开 `http://localhost:3000/stats`
2. 修改 user_id 后点击「刷新」，各指标实时更新
3. 验证文档/片段数与入库操作结果一致

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
| `MAX_UPLOAD_MB` | `50` | 上传文件大小上限（MB），超出返回 413 |

## 简历描述

基于 FastAPI + SSE 实现本地 RAG 流式对话服务；设计三阶段检索管线（查询改写 → 混合召回 → RRF 重排 → 来源多样性过滤），pgvector 余弦向量检索结合 pg_trgm 三元组文本检索，HNSW 索引加速大规模向量查询；短 chunk 自动过滤与 Markdown H1 标题提取提升入库质量；分层记忆（短期滑动窗口 + 长期向量化去重 + 会话自动摘要）支持跨会话上下文感知；支持批量多文件入库（txt/md/pdf/docx/xlsx/URL/文本，50 MB 防护），pg_trgm/HNSW 索引健康检测；前端 SSE 在 final 事件后立即解锁 UI，停止生成、消息复制、时间戳、批量上传进度、含来源的 Markdown 导出等完整交互体验。
