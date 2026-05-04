# RAG Agent（Tool Calling Agent + 流式对话 + RAG + 分层记忆）

偏后端作品：FastAPI 提供 SSE 流式对话与文档入库；Postgres + pgvector 存向量；Ollama 本地 `qwen2.5:7b` 生成、`nomic-embed-text` 向量化；Next.js（App Router）作为 BFF 代理 SSE，便于演示。

**核心亮点：Tool Calling Agent**——对话界面可切换到 Agent 模式，LLM 自主决策是否调用 `search_knowledge_base`、`recall_user_memory`、`get_current_datetime`、`web_search` 等工具，实现真正的 ReAct 推理循环，而非无脑检索。Agent 每步决策均会捕获 LLM 的推理文本（Thought）并持久化轨迹，刷新页面后可恢复。

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


| 端点                           | 说明                                                                        |
| ---------------------------- | ------------------------------------------------------------------------- |
| `GET /v1/health`             | DB / pgvector / Ollama / 模型探测                                             |
| `GET /v1/metrics`            | 进程内性能指标快照（TTFT / p50/p95 / tok/s 等）                                       |
| `POST /v1/ingest`            | 文件上传（`.txt/.md/.pdf`）、URL 抓取、纯文本入库；Form 可选 `kb_collection`、`doc_type`（**自定义**：`1–32` 位小写 `[a-z0-9_-]`，空格等会规范为连字符；常见预设 `tutorial`/`api`/`requirements`/`general`） |
| `POST /v1/chat/stream`       | SSE 流式对话；JSON 可选 `kb_collection`、`doc_types`（字符串数组，最多 8 个，规则同 `doc_type`）                         |
| `POST /v1/chat/agent/stream` | **Agent 模式**；知识库检索范围由请求的 `kb_collection`/`doc_types` 固定，工具仅传 `query`            |
| `GET /v1/documents`          | 文档列表；可选查询参数 `kb_collection`、`doc_type` 筛选                                       |
| `GET /v1/documents/catalog/doc-types` | 去重后的 `doc_type` 列表；可选 `kb_collection` 仅统计该分区（对话侧栏与当前分区一致时用于快捷展示；路径避免与 `/documents/{id}` 冲突） |
| `PATCH /v1/documents/{id}`   | 入库后修改该文档的 `kb_collection` 与/或 `doc_type`（JSON 至少其一）；同步各 chunk 的 `meta`；若目标分区已有相同 `content_sha256` 则 **409** |
| `PATCH /v1/documents/batch`  | 同上批量，请求体 `document_ids`（≤100）+ 可选 `kb_collection` / `doc_type`（至少其一）；任一失败整批回滚                         |
| `POST /v1/memory`            | 手动写入长期记忆（自动向量化）                                                           |
| `GET /v1/memory?user_id=`    | 列出记忆                                                                      |
| `DELETE /v1/memory/{id}`     | 删除记忆                                                                      |
| `GET /v1/sessions?user_id=`  | 列出会话                                                                      |
| `PATCH /v1/sessions/{id}`    | 重命名会话                                                                     |
| `DELETE /v1/sessions/{id}`   | 删除会话及其所有消息                                                                |
| `GET /v1/stats?user_id=`     | 系统统计（文档/片段/会话/消息/记忆数量）                                                    |


## 核心功能 & 测试方法

---

### 0. Tool Calling Agent（核心亮点）

对话界面：顶栏有「📚 文档库」入口；侧栏「会话」旁有 **「清空」**（一键删除当前 `user_id` 下全部会话及服务器记录，需确认）与 **「↻」**（从服务器同步会话列表）；「检索文档类型」侧栏仅保留摘要按钮，点开后在**居中弹窗**内配置：打开弹窗时会请求 **`GET /v1/documents/catalog/doc-types`**（若侧栏填了 `kb_collection` 则带同名查询参数），把**知识库里已出现过的 doc_type**（如 `knowledge`）与四个预设一起显示为快捷芯片；你在「自定义」里添加且库中尚未出现的类型会额外记入 `localStorage` 键 `rag_doc_type_shortcuts_<userId>`（最多 16 个，虚线边框芯片）。当前勾选保存在 `rag_doc_types_<userId>`。文档列表的**类型筛选**与**批量目标类型**同样通过弹窗操作；打开任一弹窗时会请求 **`GET /v1/documents/catalog/doc-types`**（与当前页「分区」筛选一致时带 `kb_collection`）并**合并读取** `rag_doc_type_shortcuts_<userId>`，因此聊天页「自定义」里加的 slug 会出现在芯片里，且**不会因当前按类型筛选列表变窄而丢失**其它已在库中出现的类型（例如选中 `api` 后仍可见 `knowledge`）。入库页「选择文档类型」弹窗打开时同样请求 **catalog** 并合并 **`rag_doc_type_shortcuts_<userId>`**，快捷区与对话/文档列表语义一致（虚线=仅本地、灰边=库中已有）。topbar 另有「⚡ Agent 模式」开关，开启后走 `/v1/chat/agent/stream` 端点：

```
用户消息
  ↓
LLM 决策（chat_with_tools，不流式）
  ├── 💭 推理文本（Thought）→ 实时展示为 reasoning 标注
  ├── 调用 search_knowledge_base  → 执行混合检索 → 结果注入上下文
  ├── 调用 recall_user_memory     → 查询向量记忆 → 结果注入上下文
  ├── 调用 get_current_datetime   → 获取当前时间 → 结果注入上下文
  ├── 调用 web_search             → DuckDuckGo 搜索 → 结果注入上下文
  └── 无工具调用 → 直接回答（普通闲聊自动跳过检索）
  ↓（最多 4 轮工具决策）
流式生成最终回复（chat_stream）
↓
轨迹持久化（steps_trace 存入 Message.extra）→ 刷新后可恢复
```

前端实时展示每个工具调用步骤（图标 + reasoning + 耗时 + 片段数），历史会话恢复后 Agent 轨迹同步还原。

**测试步骤：**

1. 启动前后端，确保已有文档入库
2. 打开 [http://localhost:3000，点击](http://localhost:3000，点击) topbar 右侧「⚡ Agent 模式」按钮（变为金色表示已开启）
3. 发送知识性问题（如「RAG 的原理是什么」），观察消息气泡上方出现工具调用步骤面板
4. 发送闲聊（如「你好」），观察 LLM 不调用任何工具，直接回答
5. 发送「现在最流行的 LLM 有哪些」，观察调用 `web_search` 工具获取实时信息
6. 刷新页面，切换到刚才的会话，Agent 步骤面板应自动还原

**预期输出：**

```
🔍 搜索知识库  "RAG 的原理"  5 个片段  ●（绿点）  128ms
🌐 网络搜索    "最流行的LLM" 我需要搜索互联网...  ●（绿点）  840ms
[流式生成回答...]
```

---

### 1. 混合检索（Hybrid Search + RRF）

每次对话同时走两条召回路径后 RRF 融合：


| 路径   | 实现                        | 优势                    |
| ---- | ------------------------- | --------------------- |
| 向量检索 | pgvector 余弦距离             | 语义相近的片段，措辞不同也能命中      |
| 文本检索 | pg_trgm `word_similarity` | 精确词汇（路径、参数名）不因语义漂移而漏掉 |


RRF 公式：`score = 1/(60 + 向量排名) + 1/(60 + 文本排名)`，`pg_trgm` 不可用时自动降级纯向量。

**减轻「知识库污染」与弱相关 Top-K：** RRF 融合后增加一层**相关性门控**：若某片段**同时**具有向量分与 `word_similarity` 且二者都偏弱则丢弃；若**仅由文本路**命中（向量未进候选），则要求更高的 `word_similarity`，避免页脚、泛化长文被弱子串拉进结果。仍不足 `top_k` 时按原 RRF 顺序补齐。详见配置项 `RAG_DUAL_WEAK_*`、`TRGM_WORD_SIMILARITY_MIN`、`RAG_TRGM_ONLY_MIN_SIMILARITY`。

**入库分块：** `.md` 或含 `##` 标题的正文默认**按标题分节**；节内识别 **Markdown `` ``` `` 围栏代码块**：围栏内不按句号/短窗切碎，整块尽量保留；仅当单块围栏仍超过 `CHUNK_MAX_CHARS` 时，在**换行边界**切为多段（子块之间不叠加以避免行内半截标签）。**短引言合并**：若紧邻围栏前的纯文字（不含 `` ``` ``）长度不超过 `CHUNK_MERGE_INTRO_BEFORE_FENCE_MAX_CHARS`（默认 320），会与下一围栏**合并为同一切块**（例如「五、一对多查询…例如：」与后面 `` ```xml `` 同块），减轻检索只命中标题、没有示例代码的情况。每节首标题写入 `meta.section_heading`，检索结果与对话 `[S*]` 中会带「节：…」；纯代码子块用节标题回落。**围栏续块前缀**：超长围栏按行切成多块时，**从第 2 块起**在正文前加一行 ``[节：节名 · 续]``（节名来自本分节首标题，长度上限见 `CHUNK_CONTINUATION_TITLE_MAX_CHARS`），便于只命中后续代码块时仍能关联小节；`CHUNK_FENCE_CONTINUATION_PREFIX=false` 可关。可调 `CHUNK_MAX_CHARS`、`CHUNK_OVERLAP`、`CHUNK_MARKDOWN_BY_HEADING`、`CHUNK_MARKDOWN_FENCE_AWARE`、`CHUNK_MERGE_INTRO_BEFORE_FENCE_MAX_CHARS`（`0` 关闭引言合并）、`CHUNK_FENCE_CONTINUATION_PREFIX`、`CHUNK_CONTINUATION_TITLE_MAX_CHARS`。

**测试步骤（Markdown 围栏分块）：**

1. 在仓库 `backend` 目录执行：`python -m unittest tests.test_text_chunk -v`。
2. 可选：将含大段 `` ```xml `` 的 `.md` 重新入库后，在文档库中打开该文档的片段列表，确认超长 XML 仅在行边界断开、不出现 `artifactId>` 等半截标签行（需 `CHUNK_MARKDOWN_FENCE_AWARE=true`，默认已开）。

**预期输出：** 步骤 1 显示 `Ran 5 tests ... OK`；步骤 2 中各片段内容在代码行语义上完整可读。

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

### 1.1 知识库分区（`kb_collection`）与文档类型（`doc_type`）

- **`kb_collection`**：硬分区，检索与列表只在该分区内进行；未传请求字段时使用环境变量 `DEFAULT_KB_COLLECTION`（默认 `default`）。分区名仅允许 `a-zA-Z0-9_-`，长度 1–64。同一文件内容可在不同分区各入库一份（去重键为「内容 SHA256 + 分区」）。
- **`doc_type` / `doc_types`**：文档级类型标签，入库时写入 `Document.doc_type`；对话请求可传 `doc_types` 数组（最多 8 个），仅检索所列类型（不传则不过滤）。**不限于四个预设**：任意符合 `^[a-z0-9_-]{1,32}$` 的 slug 均可（提交前会转小写并将空白转为连字符）；纯符号/中文等无法得到合法 slug 时接口返回 **400**。检索过滤列表中的非法项会被静默忽略。
- **入库后改分区/类型**：无需重新向量化。调用 `PATCH /v1/documents/{id}` 或 `PATCH /v1/documents/batch` 更新 `Document` 行并同步各 chunk 的 `meta` 中的 `kb_collection`/`doc_type`。若将文档移入某分区而该分区已存在**相同内容 SHA** 的另一文档，返回 **409**（与入库去重 `(sha256, kb_collection)` 一致）。

**测试步骤：**

1. 重启后端，确认日志出现 `documents.kb_collection / doc_type columns ready`（旧库自动迁移）。
2. 在入库页将 `kb_collection` 设为 `rag_demo`，`doc_type` 选 `tutorial`，上传一段纯 RAG 介绍文本；再将 `kb_collection` 改为 `api_demo`，`doc_type` 选 `api`，上传另一段接口说明。
3. 打开文档列表页，分别用筛选 `kb_collection=rag_demo` / `api_demo` 确认列表互不交叉。
4. 在对话页侧栏 `kb_collection` 填 `rag_demo`，在「检索文档类型」中点选 `tutorial`（或输入自定义 slug 后添加），提问与教程相关的问题，引用中不应出现 `api_demo` 分区下的接口文档。
5. **批量改元数据**：在文档列表页勾选若干文档，填写目标分区与/或类型后点「批量应用」；或直接用 curl：`PATCH /v1/documents/batch`，JSON 示例：`{"document_ids":["<uuid1>","<uuid2>"],"kb_collection":"moved_demo","doc_type":"general"}`。

**预期输出：** 步骤 4 的 `sources` 事件里 `snippet` 来源均为 `rag_demo` 且文档类型为教程；若去掉类型勾选并仍选 `rag_demo`，行为与「仅分区、不按类型过滤」一致。步骤 5 成功后接口返回 `{"ok":true,"updated":N,...}`，刷新列表后所选文档的 badge 与筛选结果与目标分区/类型一致；若目标分区已有同内容文档则响应 **409** 且列表不变。

评测脚本可选：`python eval/eval_rag.py --kb-collection rag_demo --doc-types tutorial`；环境变量 `EVAL_KB_COLLECTION`、`EVAL_DOC_TYPES`（逗号分隔类型）同样生效。`python test_hybrid_search.py` 会读取上述环境变量。

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


| Tab       | 用途                     |
| --------- | ---------------------- |
| 📄 上传文件   | 本地 `.txt / .md / .pdf` |
| 🌐 网页 URL | 填入链接，后端自动抓取正文          |
| 📝 粘贴文本   | 直接粘贴内容                 |


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

**补充：指标快照 API（/v1/metrics）**

`GET /v1/metrics` 会返回最近一段时间（滚动窗口）的分位数指标（p50/p95/max），便于你写简历数字、做回归对比。

**测试步骤：**

1. 启动后端并进行几次对话（普通或 Agent 均可）
2. 在浏览器打开 `http://127.0.0.1:8000/v1/metrics`，或用命令行请求：

```powershell
curl http://127.0.0.1:8000/v1/metrics
```

**预期输出：** 返回 JSON，包含 `ollama.stream.ttft_ms.p50/p95`、`ollama.stream.total_ms.p50/p95`、`tokens_per_sec_overall` 等字段。

**补充：一键跑数脚本（Benchmark）**

仓库提供 `eval/bench_chat.py` 用于对 SSE 对话做基准测试，输出 TTFT/总耗时的 p50/p95。

**测试步骤：**

```powershell
cd d:\1study\study\python\rag-agent\backend
.\.venv\Scripts\Activate.ps1
cd ..

# 普通对话（/v1/chat/stream）
python eval/bench_chat.py --n 20 --api-base http://127.0.0.1:8000

# Agent 模式（/v1/chat/agent/stream）
python eval/bench_chat.py --n 20 --agent --api-base http://127.0.0.1:8000
```

**预期输出：**

```
[01/20] ttft=...ms total=...ms
...
TTFT(ms):  p50=... p95=...
TOTAL(ms): p50=... p95=...
```

---

### 5. 分层记忆


| 层次   | 实现                                       | 触发条件                      |
| ---- | ---------------------------------------- | ------------------------- |
| 短期记忆 | 最近 `chat_history_turns × 2` 条消息（默认 12 轮） | 每次对话自动                    |
| 长期记忆 | 向量化存入 `memories` 表，对话前按语义相似度注入           | 输入含「记住/我是/我叫/我喜欢/我擅长」等触发词 |
| 去重合并 | 余弦距离 < 0.15（相似度 > 85%）时更新已有记忆            | 写入新记忆时自动检查                |
| 会话摘要 | LLM 压缩早期对话存入 `session.summary`，注入 prompt | 消息数超 20 条后每 10 条触发        |


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


| 格式                         | 效果                |
| -------------------------- | ----------------- |
| `# 标题` / `**加粗`** / `*斜体*` | 标准 Markdown 排版    |
| 代码块 ````python ````        | 带语法高亮（oneDark 主题） |
| 行内代码 ``code``              | 蓝色等宽字体显示          |
| 表格 / 列表 / 引用块              | 完整 GFM 支持         |


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


| 格式      | 提取内容                      |
| ------- | ------------------------- |
| `.docx` | 所有段落文本 + 表格单元格（以 `|` 分隔）  |
| `.xlsx` | 所有 Sheet 的行数据（Sheet 名作标题） |
| `.md`   | 纯文本，自动提取首个 `# H1` 作为文档标题  |


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


| 指标   | 说明               |
| ---- | ---------------- |
| 文档总数 | 已入库的原始文件数        |
| 向量片段 | 总切块数量，及平均片/文档    |
| 会话数  | 指定 user_id 的会话数  |
| 消息总数 | 用户+助手消息合计        |
| 长期记忆 | 指定 user_id 的记忆条数 |


**测试步骤：**

1. 打开 `http://localhost:3000/stats`
2. 修改 user_id 后点击「刷新」，各指标实时更新
3. 验证文档/片段数与入库操作结果一致

---

## 配置项（`backend/.env`）


| 变量                            | 默认值                                    | 说明                                                                   |
| ----------------------------- | -------------------------------------- | -------------------------------------------------------------------- |
| `DATABASE_URL`                | `postgresql+psycopg://rag:ragpass@...` | PostgreSQL 连接串                                                       |
| `OLLAMA_BASE_URL`             | `http://127.0.0.1:11434`               | Ollama 服务地址                                                          |
| `OLLAMA_CHAT_MODEL`           | `qwen2.5:7b`                           | 对话模型                                                                 |
| `OLLAMA_EMBED_MODEL`          | `nomic-embed-text:latest`              | Embedding 模型                                                         |
| `OLLAMA_NUM_PREDICT`          | `512`                                  | 对话生成最大 token 数（Ollama: `num_predict`）；用于收敛生成侧长尾                      |
| `OLLAMA_EMBED_BUDGET_MS`      | `1200`                                 | **仅 RAG 检索**时对单次 `/api/embeddings` 的读超时（ms）；超时走降级（如 trgm-only）。入库与记忆写入不使用该预算，避免大块/冷启动被误杀 |
| `QUERY_REWRITE`               | `true`                                 | 查询改写开关（关闭可减少约 2s 延迟）                                                 |
| `QUERY_REWRITE_BUDGET_MS`     | `1200`                                 | 查询改写延迟预算（ms）；超时则跳过改写，避免长尾                                            |
| `QUERY_REWRITE_ONLY_ON_EMPTY` | `true`                                 | 仅当首次检索 0 命中时触发改写（稳定优先）                                               |
| `QUERY_REWRITE_CACHE_TTL_S`   | `600`                                  | 改写结果缓存 TTL（秒）；命中缓存可减少改写 LLM 调用                                       |
| `HYBRID_SEARCH`               | `true`                                 | 混合检索开关                                                               |
| `VECTOR_DISTANCE_THRESHOLD`   | `0.38`                                 | 向量余弦距离上限，超过视为不相关（越小越严；相似度约等于 1 减该阈值）                                  |
| `TRGM_WORD_SIMILARITY_MIN`    | `0.26`                                 | 混合检索文本路 `word_similarity` 下限（过低易噪声）                                      |
| `RAG_TRGM_ONLY_MIN_SIMILARITY`| `0.32`                                 | 仅文本路命中、无向量分时的 `word_similarity` 下限                                       |
| `RAG_DUAL_WEAK_FILTER`        | `true`                                 | 是否启用「双路分数同时偏弱则丢弃」门控                                                   |
| `RAG_DUAL_WEAK_MAX_VEC`       | `0.46`                                 | 门控用：向量相似度低于此且 trgm 低于下一项时丢弃（两条件同时成立）                                   |
| `RAG_DUAL_WEAK_MAX_TRGM`      | `0.24`                                 | 门控用：`word_similarity` 上项配对阈值                                            |
| `CHUNK_MAX_CHARS`             | `720`                                  | 单片段最大字符数（略小有利于嵌入更聚焦）                                                   |
| `CHUNK_OVERLAP`               | `90`                                   | 相邻片段重叠字符数                                                                |
| `CHUNK_MARKDOWN_BY_HEADING`   | `true`                                 | Markdown / 含 `##` 标题时按标题分节再切块                                              |
| `CHUNK_MARKDOWN_FENCE_AWARE`  | `true`                                 | 与上项同时为真时，节内 `` ``` `` 围栏整块不切，超长围栏仅在换行处切（子块不重叠）                         |
| `CHUNK_MERGE_INTRO_BEFORE_FENCE_MAX_CHARS` | `320`                     | 紧邻围栏前的说明文字若不超过此字符数则与围栏合并切块；`0` 关闭                                   |
| `CHUNK_FENCE_CONTINUATION_PREFIX` | `true`                          | 超长围栏多块时，第 2 块起正文前加 ``[节：… · 续]``（需节内能解析出标题）                         |
| `CHUNK_CONTINUATION_TITLE_MAX_CHARS` | `72`                         | 续块前缀中节名的最大字符数（超出截断加省略号）                                            |
| `SUMMARY_THRESHOLD`           | `20`                                   | 触发会话摘要的消息数阈值                                                         |
| `RAG_TOP_K`                   | `8`                                    | 每次检索返回的片段数                                                           |
| `MAX_UPLOAD_MB`               | `50`                                   | 上传文件大小上限（MB），超出返回 413                                                |
| `DEFAULT_KB_COLLECTION`       | `default`                              | 请求未传 `kb_collection` 时使用的分区名                                            |
| `TAVILY_API_KEY`              | *(空)*                                  | Tavily 搜索 API Key（[免费申请](https://tavily.com)，1000次/月）；国内推荐           |
| `SEARXNG_URL`                 | *(空)*                                  | 自建 SearXNG 实例地址（`http://localhost:8888`），免费无限量                       |
| `WEB_SEARCH_TIMEOUT`          | `8`                                    | Web 搜索超时秒数，网络不通时快速失败                                                 |
| `DEEPEVAL_JUDGE_MODEL`        | *(空)*                                  | 运行 `eval/deepeval_rag.py` 时指定评判用 Ollama 模型；为空则使用 `OLLAMA_CHAT_MODEL` |
| `EVAL_KB_COLLECTION`          | *(空)*                                  | 离线评测检索分区；也可用 `--kb-collection`                                         |
| `EVAL_DOC_TYPES`              | *(空)*                                  | 逗号分隔，如 `tutorial,api`；也可用 `--doc-types`                                    |


> **国内环境说明**：DuckDuckGo 在中国大陆被屏蔽，`web_search` 工具默认会超时失败并优雅降级（提示 LLM 用自身知识作答）。  
> 推荐配置方式（二选一）：
>
> - **Tavily**（推荐）：注册 [tavily.com](https://tavily.com)，设置 `TAVILY_API_KEY=tvly-xxx`
> - **SearXNG**：`docker run -d -p 8888:8080 searxng/searxng`，设置 `SEARXNG_URL=http://localhost:8888`

---

### 13. 评估框架（Eval）

`eval/` 目录提供离线评估脚本，用于量化混合检索 vs 纯向量检索的效果，便于写入简历数字指标。

与 **`eval/test_cases.json`** 中五道评测题对齐的金标知识库正文见 **`eval/kb_for_test_cases.md`**（Markdown）。请先将其**上传入库**（或复制全文到入库页「粘贴文本」），再运行下方脚本，否则 Recall / DeepEval 会因语料无关而偏低。

**测试步骤：**

```powershell
# 0.（推荐）将 eval/kb_for_test_cases.md 在前端 /ingest 上传，或复制全文入库
# 1. 先根据你已入库的文档，编辑 eval/test_cases.json，填入问题和预期关键词
# 2. 运行评估（仅计算 Recall@5，速度较快）
cd d:\1study\study\python\rag-agent\backend
.\.venv\Scripts\Activate.ps1
cd ..
python eval/eval_rag.py --top-k 5 --output eval/report.md

# 指定分区与文档类型（或与 EVAL_KB_COLLECTION / EVAL_DOC_TYPES 环境变量配合）
python eval/eval_rag.py --top-k 5 --kb-collection default --doc-types tutorial --output eval/report.md

# 3. 开启 LLM-as-Judge 忠实度评分（较慢，每题需额外 LLM 调用）
python eval/eval_rag.py --top-k 5 --judge --output eval/report.md
```

**预期输出：**

```
📋 共 5 条测试用例，top_k=5，LLM-Judge=关闭
========================================================================

[tc_001] 系统支持哪些文件格式的上传？
  混合检索: Recall@5=1.0  (312ms,  5 片段)
  纯向量:   Recall@5=1.0  (98ms,   5 片段)

...（逐题结果）...

📊 汇总 (n=5)
  混合检索  Recall@5: 0.80   纯向量 Recall@5: 0.60

✅ 报告已写入: eval/report.md
```

#### 13.1 DeepEval（Faithfulness / Answer Relevancy / Contextual Relevancy）

依赖已写入 `backend/requirements.txt`（`deepeval`）。脚本 `eval/deepeval_rag.py` 对每条用例执行：`multi_query_search` → 非流式 RAG 回答 → 用 **本地 Ollama** 作为评判模型跑 DeepEval 指标（与手写 `eval_rag.py --judge` 互补：指标更标准化、可对比社区基准）。检索 0 命中的用例会跳过，避免空 `retrieval_context` 触发无效评测。

**拒答时的指标策略（重要）**：若生成结果为「知识库中没有找到相关内容」等拒答话术，脚本**只评测 Contextual Relevancy**（检索片段与问题是否相关）。原因：在噪声检索下 Faithfulness 易虚高（拒答与乱片段无「矛盾」）；Answer Relevancy 常把合规拒答打成低分。此时应优先看 **Contextual Relevancy** 与 `eval_rag.py` 的 **Recall@k（expected_keywords）** 判断召回是否失败。

**测试步骤：**

1. 确保已 `pip install -r requirements.txt`，知识库中有与 `eval/test_cases.json` 相关的文档。
2. Ollama 已安装并拉取 `OLLAMA_CHAT_MODEL`（及可选的 `DEEPEVAL_JUDGE_MODEL`）。
3. 在仓库根目录执行：

```powershell
cd d:\1study\study\python\rag-agent\backend
.\.venv\Scripts\Activate.ps1
cd ..
python eval/deepeval_rag.py --top-k 5 --threshold 0.5

# 调试时只跑前 1 条用例：
python eval/deepeval_rag.py --max-cases 1

# 评委侧截断每条检索片段（默认 2000 字），避免单题评测耗时数分钟：
python eval/deepeval_rag.py --judge-chunk-chars 2000

# 少打印 DeepEval 长报告，只看末尾 [SUMMARY] 表：
python eval/deepeval_rag.py --quiet

# 建议命令（日常扫一眼看结果）
python eval/deepeval_rag.py --top-k 5 --threshold 0.5 --quiet --judge-chunk-chars 2000
```

**预期输出：** 默认逐条打印 DeepEval 详情；开头一行汇总「拒答用例」id；**末尾 `[SUMMARY]`** 给出每题各指标分数与是否过阈值，以及 **Contextual Relevancy 平均分** 与简短解读。非拒答用例含 Faithfulness、Answer Relevancy、Contextual Relevancy；拒答用例仅评 Contextual Relevancy。

---

## 简历描述

基于 FastAPI + SSE 实现本地 RAG 流式对话服务，核心是 Tool Calling Agent 模式——LLM 通过 Ollama Function Calling API 自主决策工具调用（知识库检索/记忆查询/时间获取/联网搜索），完整实现 ReAct 推理循环，Agent 决策轨迹（含 Thought 推理文本）持久化到数据库并在历史会话中恢复；设计三阶段检索管线（查询改写 → 混合召回 → RRF 重排），pgvector 余弦检索结合 pg_trgm 三元组文本检索，HNSW 索引加速查询，SHA256 幂等去重防止重复入库；分层记忆（短期滑动窗口 + 长期向量化 + 会话摘要）支持跨会话感知；eval/ 评估框架量化 Recall@k 与 LLM-as-Judge 忠实度，实测混合检索 Recall@5 优于纯向量约 20pp；前端 Next.js App Router + SSE 实现流式对话、引用徽章、Agent 步骤面板等完整交互。