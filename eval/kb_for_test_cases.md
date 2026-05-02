# RAG Agent 评测用知识库（与 eval/test_cases.json 对齐）

本文档专供离线评测入库：内容与 `test_cases.json` 中各题的 `expected_keywords` 一致，上传至知识库后运行 `eval/eval_rag.py` 或 `eval/deepeval_rag.py` 即可验证召回与回答。

---

## 1. 文件上传与支持的格式

本系统在入库页面支持多种文件格式上传与解析：

- **PDF**：便携式文档，适合论文与扫描件。
- **DOCX**：Word 文档，提取段落与表格单元格。
- **TXT**：纯文本。
- **Markdown**：扩展名 `.md`，可从首个 `#` 标题自动提取文档标题。
- **Excel**：扩展名 `.xlsx`（Microsoft Excel 表格），按 Sheet 导出为表格文本。

单文件大小受环境变量 `MAX_UPLOAD_MB` 限制（默认 50 MB）。重复文件通过内容 SHA256 去重，不会重复入库。

---

## 2. 长期记忆（memory）的查询方式

用户的长期偏好与身份类信息经向量化后写入 `memories` 表。查询接口为 REST API：

- **HTTP 方法**：`GET`
- **路径**：`/v1/memory`
- **查询参数**：`user_id`（必填语义上由业务传入，例如 `demo`）

返回该 `user_id` 下的记忆列表，每条含文本内容与向量字段，供前端展示或管理。对话前系统会按语义相似度从记忆库中检索相关片段注入上下文。因此「如何查询用户的长期记忆」在实现上即：**携带 `user_id` 调用上述 memory 列表接口**，并结合向量相似度在对话中自动召回。

---

## 3. 混合检索的实现原理

混合检索（Hybrid Search）在本项目中指：**向量语义检索**与**关键词式文本检索**并行，再用 **RRF（Reciprocal Rank Fusion）** 融合排序。

- **向量路径**：使用 **pgvector** 存储 chunk 的 embedding，按余弦距离排序，适合同义改写与语义相近的片段。
- **关键词路径**：使用 PostgreSQL 扩展 **pg_trgm** 的 `word_similarity`，对短查询与长文档中的精确词（如 API 路径、参数名）更稳。
- **融合**：对两路结果分别排名后，按 RRF 公式合并得分，再经来源多样性过滤（单文档最多贡献有限条）得到最终 Top-K。

当 `pg_trgm` 不可用或配置关闭时，可降级为纯向量检索。环境变量 `HYBRID_SEARCH` 控制是否启用混合检索。

---

## 4. HNSW 向量索引及其优势

向量片段表上的近似索引采用 **HNSW**（Hierarchical Navigable Small World）结构（由 pgvector 提供，具体以库内迁移脚本为准）。

**优势简述**：

- **查询延迟低**：在高维向量空间中以图结构做**近似**最近邻搜索，避免全表暴力比对。
- **适合大规模向量**：随数据量增长仍可保持较好的检索吞吐。
- **与业务向量维度绑定**：本项目的 embedding 维度由 `EMBED_DIM` 与所选 Ollama 嵌入模型一致。

后端启动时会尝试创建 HNSW 索引（日志中可见 `idx_chunks_embedding_hnsw` 等）。调参需在 DBA 指导下调整 `m`、`ef_construction` 等（若版本支持）。

---

## 5. 会话摘要（session summary）如何生成

当同一会话中消息条数超过阈值时，系统会触发**自动摘要**：

- **阈值**：由环境变量 `SUMMARY_THRESHOLD` 控制（默认约 20 条消息量级，以 `README` 与 `app.config` 为准）。
- **机制**：调用 **LLM** 对较早的多轮对话进行压缩，生成一段 `session.summary` 文本写入数据库。
- **用途**：后续请求的 system prompt 会附带该 **summary**，使模型在窗口有限时仍能「记得」会话早期要点。

摘要与「长期记忆」不同：前者是会话级、随消息增长周期性更新；后者是用户级、按触发词写入 `memories` 表。

---

## 使用说明

1. 在前端入库页或通过 `POST /v1/ingest` 上传本文件（或复制全文到「粘贴文本」）。
2. 再运行：`python eval/eval_rag.py --top-k 5` 与 `python eval/deepeval_rag.py --top-k 5`。

若仍出现召回偏差，可适当提高本文档块内关键词密度或减小同库无关文档比例。
