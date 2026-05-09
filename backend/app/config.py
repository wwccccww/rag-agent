from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://rag:ragpass@192.168.116.130:5432/rag"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_chat_model: str = "qwen2.5:7b"
    ollama_embed_model: str = "nomic-embed-text:latest"
    embed_dim: int = 768  # nomic-embed-text 768
    # 对话生成最大 token 数（Ollama: options.num_predict）。用于收敛端到端长尾。
    # 设为 0 表示不限制（不推荐）。
    ollama_num_predict: int = 512
    # Embedding 调用预算（ms）。用于治理偶发长尾；超时会触发降级（如 RAG 走 trgm-only）。
    # 设为 0 表示不启用 budget。
    ollama_embed_budget_ms: int = 1200
    rag_top_k: int = 8
    chat_history_turns: int = 12
    chunk_max_chars: int = 720
    chunk_overlap: int = 90
    # Markdown / 含 ## 标题的文本：按标题分节再切块，单块语义更纯、减轻知识库混杂时的向量漂移
    chunk_markdown_by_heading: bool = True
    # Markdown 节内：识别 ``` 围栏代码块，整块不切；超长围栏仅在换行处切分（避免 XML/代码拦腰断）
    chunk_markdown_fence_aware: bool = True
    # 紧邻围栏前的纯文字若不超过此长度（且不含围栏），与围栏合并为一个切块，避免「例如：」单独成块检索不到代码
    chunk_merge_intro_before_fence_max_chars: int = 320
    # 超长围栏按行切为多块时，从第 2 块起在正文前加「[节：… · 续]」（需能解析出节标题）
    chunk_fence_continuation_prefix: bool = True
    # 续块前缀中节名的最大字符数（避免过长占满 chunk）
    chunk_continuation_title_max_chars: int = 72
    # 会话消息超过此数量时触发自动摘要（每 10 条触发一次）
    summary_threshold: int = 20
    # 混合检索：向量权重 vs 三元组文本权重（RRF 已自动平衡，此参数保留供将来调参）
    hybrid_search: bool = True
    # 查询改写：对话前用 LLM 生成 2 个备选查询，多路召回合并（增加召回率，会多一次 LLM 调用）
    query_rewrite: bool = True
    # 查询改写的延迟预算（ms）。超过预算则放弃改写并降级为仅用原始 query 检索。
    # 设为 0 表示不启用预算（不推荐，容易出现长尾）。
    query_rewrite_budget_ms: int = 1200
    # 仅在「首次检索 0 命中」时才触发改写（更稳、更省时延）。
    query_rewrite_only_on_empty: bool = True
    # 改写结果缓存 TTL（秒）。命中缓存可避免重复调用 LLM 改写。
    query_rewrite_cache_ttl_s: int = 600
    # 上传文件大小上限（MB），防止超大文件导致内存耗尽
    max_upload_mb: int = 50
    # 请求未传 kb_collection 时使用的知识库分区名
    default_kb_collection: str = "default"
    # True：仅允许访问 user_kb_collections 表中授权的分区（企业内部知识库隔离）；False：兼容旧行为（不信任边界在客户端）
    kb_acl_enabled: bool = True
    # 向量检索相关性阈值：余弦距离超过此值的片段视为"不相关"直接丢弃
    # cosine_distance ∈ [0,2]，0=完全相同，1=正交，2=相反
    # 略收紧可减轻「勉强相关」的跨文档噪声（过严会降低召回，可按库调参）
    vector_distance_threshold: float = 0.38
    # pg_trgm word_similarity 下限（混合检索文本路）；过低易召回页脚、无关长文
    trgm_word_similarity_min: float = 0.27
    # 仅由文本路命中、无向量分时，要求更高的 word_similarity，抑制弱子串匹配污染
    rag_trgm_only_min_similarity: float = 0.32
    # 两路都有分时：若向量相似度与 trgm 同时偏弱则丢弃（减轻 RRF 把「双弱」拼进 Top-K）
    rag_dual_weak_filter: bool = True
    rag_dual_weak_max_vec: float = 0.46
    rag_dual_weak_max_trgm: float = 0.23
    # 门控后候选不足 top_k*2 时，是否用「未过门控」的 RRF 顺序补齐（提高召回，易混入弱相关片段）
    rag_gate_relax_fill: bool = False
    # 流式回复落库前：移除与正文无足够短语重叠的 [Sk] 引用（减轻模型乱标引用）
    rag_citation_verify: bool = True
    rag_citation_min_hits: int = 2
    rag_citation_min_term_frac: float = 0.02
    rag_citation_max_source_terms: int = 100
    # 混合检索：word_similarity 同时对正文与 meta.section_heading（面包屑）取 max，利于「管理员 / Vue」等在标题、小节名中的词
    rag_trgm_include_section_heading: bool = True
    # 向量+文本各自召回的候选条数 = top_k * 本系数（略大有利于同一需求文档多小节进入 RRF）
    rag_candidate_top_k_multiplier: int = 5
    # 单次检索同一文档最多返回几条片段（原 3；放宽利于同一需求文档多命中）
    rag_max_chunks_per_doc: int = 4
    # 若某文档已在 Top-K 中有「较强向量命中」，从 prefetch 队列再换入同文档尚未进 Top-K 的片段数上限（挤掉他文档弱 RRF 项）
    rag_same_doc_prefetch_extra: int = 2
    # 触发同文档补位：该文档在 Top-K 中已有 chunk 的向量相似度至少达到此值（0–1）
    rag_same_doc_expand_min_vec: float = 0.32

    # ── Multi-hop RAG（两跳检索编排）──────────────────────────────────────────
    # 开启后：RAG 模式在首跳检索后，用 LLM 基于命中片段生成下一跳 query，再检索一次并合并证据。
    rag_multihop_enabled: bool = True
    # 最大跳数（当前实现支持 1 或 2；>2 会按 2 处理）
    rag_multihop_max_hops: int = 2

    # ── Parent-Child 分块 ─────────────────────────────────────────────────────
    # 开启后：按 Markdown 层级先切「父块」（200–1500 字），再将每个父块内部切成更小的「子块」
    # 子块参与向量/文本检索，命中后用父块喂给模型，兼顾检索精度与上下文完整性
    chunk_parent_child: bool = True
    # 父块最小字符数：若单节内容 >= 此值则单独成父块，不与邻节合并
    chunk_parent_min_chars: int = 200
    # 父块最大字符数：多个小节合并的父块总字符数上限
    chunk_parent_max_chars: int = 1500

    # ── Cross-Encoder Reranker ────────────────────────────────────────────────
    # 开启后：RRF 召回 rag_top_k * rag_rerank_candidate_k 候选，再用 CrossEncoder 精排取 top_k
    # 首次请求时自动从 HuggingFace 下载模型（约 100 MB），之后缓存在进程内
    rag_rerank_enabled: bool = False
    # 可选模型：
    #   cross-encoder/ms-marco-MiniLM-L-6-v2  （英文，~100 MB，快）
    #   BAAI/bge-reranker-base                 （中英双语，~280 MB，中文更好）
    rag_rerank_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    # Reranker 候选倍数：实际召回 top_k * 本值 条后精排（越大效果越好但越慢）
    rag_rerank_candidate_k: int = 3

    # Web 搜索后端（可选，国内环境 DuckDuckGo 可能被屏蔽）
    # 优先级：searxng_url > tavily_api_key > duckduckgo（fallback）
    # SearXNG：自建实例（免费，支持代理），如 http://localhost:8888
    searxng_url: str | None = None
    # Tavily：https://tavily.com 申请免费 API Key（1000次/月）
    tavily_api_key: str | None = None
    # 搜索超时（秒），防止网络不通时长时间阻塞
    web_search_timeout: int = 8

    # ── Agent 扩展工具 ─────────────────────────────────────────────────────────
    # python_repl：Python 代码沙箱执行（子进程隔离）
    # 执行超时（秒）；超时后强制终止子进程
    python_repl_timeout: int = 15
    # 最大输出字符数；超出部分截断
    python_repl_max_output_chars: int = 2000

    # fetch_url：网页正文抓取
    # 请求超时（秒）
    fetch_url_timeout: int = 15
    # 提取正文最大字符数；超出部分截断（避免超长网页占满上下文）
    fetch_url_max_chars: int = 4000

    # ── Plan & Execute 模式 ────────────────────────────────────────────────────
    # 计划最大步骤数（超出部分丢弃，避免无限步骤）
    plan_max_steps: int = 6

    # ── Agent 推理策略 ─────────────────────────────────────────────────────────
    # 强制 CoT 格式：在系统提示中要求 LLM 每次工具调用前输出 "Thought: ..." 推理
    agent_cot_enabled: bool = True
    # Self-Ask：ReAct 循环前额外调用一次 LLM 将问题拆解为子问题（会增加一次 LLM 调用耗时）
    agent_self_ask_enabled: bool = True
    # Self-Ask 触发最小字符数：问题短于此值时跳过分解（简单问题无需拆解）
    agent_self_ask_min_chars: int = 20
    # Reflection：每轮工具执行后评估信息是否充足，充足则提前终止循环（会增加一次 LLM 调用耗时）
    agent_reflection_enabled: bool = True
    # Reflection 评估的延迟预算（ms）。超过预算则跳过反思并默认视为“信息已足够”，避免长尾阻塞。
    # 设为 0 表示不启用预算（不推荐，可能出现 300s 长尾）。
    agent_reflection_budget_ms: int = 1500

    # ── 边界治理：权限分级 / 失败兜底 / 审计 ──────────────────────────────────
    # 工具策略级别：
    #   low    - 本地开发：允许全部工具
    #   medium - 默认：禁止 python_repl；允许 fetch_url；web_search 可选
    #   high   - 保守：仅允许离线工具（kb/memory/datetime/calculate）
    tool_policy_level: str = "medium"
    # medium 下是否允许 web_search（联网搜索）。默认关闭，避免网络不稳定/审计风险。
    web_search_enabled: bool = False
    # Multi-Agent（档2）：retriever worker 是否允许 web_search（联网搜索）。
    # 注意：仍受全局 web_search_enabled 与 tool_policy_level 约束；该开关仅用于“在 retriever allowlist 中放行”。
    multi_retriever_web_search_enabled: bool = False
    # 工具调用最大次数（跨多轮 tool_calls 累计）；超过会被拒绝并审计。
    tool_max_calls: int = 12
    # 工具结果预览存入审计日志的最大字符数
    tool_audit_preview_chars: int = 800

    # ── 知识图谱（轻量级，存储于 PostgreSQL）──────────────────────────────────
    # 总开关：False 时跳过所有 KG 操作（search_memories 退化为纯向量搜索）
    kg_enabled: bool = True
    # 三元组提取：写入记忆时是否同步提取实体关系（会额外增加一次 LLM 调用）
    kg_triple_extract_enabled: bool = True
    # 向量检索实体去重阈值（余弦距离）：距离 < 此值视为同一实体
    kg_entity_dedup_threshold: float = 0.15
    # 图谱展开跳数：从种子实体出发展开几跳邻域（1-3 跳，跳数越多上下文越丰富但越慢）
    kg_graph_hops: int = 2
    # 图谱实体向量检索相关性阈值（余弦距离）：超过此值的实体不作为展开种子
    kg_entity_distance_threshold: float = 0.5

    # ── Sentry（线上错误率/异常聚合）──────────────────────────────────────────
    # DSN 为空则不启用 Sentry
    sentry_dsn: str | None = None
    # 环境：dev/staging/prod（默认 dev）
    sentry_environment: str = "dev"
    # 采样率：错误事件采样（0-1，默认 1.0）；性能追踪可后续再加
    sentry_sample_rate: float = 1.0

    # 问答轮次审计：每次 RAG/Agent/Plan/Multi 成功结束写入 qa_audit_logs
    qa_audit_enabled: bool = True
    # 非空时除 /、/docs、/openapi.json、/redoc、/v1/health 外需携带 X-API-Key 或 Authorization: Bearer
    api_key: str | None = None

    # ── MDN Web Docs（方案A：从 GitHub Markdown 源仓库批量导入）───────────────
    # 仓库 URL：英文为 mdn/content；中文为 mdn/translated-content
    mdn_repo_url: str = "https://github.com/mdn/content.git"
    # 本地仓库存放目录（建议放在 backend/data/ 或 data/ 下，避免污染代码目录）
    mdn_repo_dir: str = "data/mdn_repo"
    # 分支/标签（通常 main）
    mdn_repo_ref: str = "main"
    # 文档语言目录：英文 en-us；中文 zh-cn（仅 translated-content 仓库中存在）
    mdn_lang: str = "en-us"
    # 仅导入这些子目录（逗号分隔；为空表示导入该语言下全部）
    # 例如：files/en-us/web/javascript,files/en-us/web/css
    mdn_include_paths: str | None = None
    # 排除路径（逗号分隔的 glob；相对仓库根目录匹配）
    # 注意：MDN 的正文通常就在各目录的 index.md，不要默认排除它，否则会导入 0 篇。
    mdn_exclude_globs: str = "**/index.yaml,**/_sidebar.md,**/_redirects.md"
    # 最大导入文件数（0 表示不限制；用于防止一次性导入过大）
    mdn_max_files: int = 0


settings = Settings()
