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
    # 向量检索相关性阈值：余弦距离超过此值的片段视为"不相关"直接丢弃
    # cosine_distance ∈ [0,2]，0=完全相同，1=正交，2=相反
    # 略收紧可减轻「勉强相关」的跨文档噪声（过严会降低召回，可按库调参）
    vector_distance_threshold: float = 0.38
    # pg_trgm word_similarity 下限（混合检索文本路）；过低易召回页脚、无关长文
    trgm_word_similarity_min: float = 0.26
    # 仅由文本路命中、无向量分时，要求更高的 word_similarity，抑制弱子串匹配污染
    rag_trgm_only_min_similarity: float = 0.32
    # 两路都有分时：若向量相似度与 trgm 同时偏弱则丢弃（减轻 RRF 把「双弱」拼进 Top-K）
    rag_dual_weak_filter: bool = True
    rag_dual_weak_max_vec: float = 0.46
    rag_dual_weak_max_trgm: float = 0.24

    # Web 搜索后端（可选，国内环境 DuckDuckGo 可能被屏蔽）
    # 优先级：searxng_url > tavily_api_key > duckduckgo（fallback）
    # SearXNG：自建实例（免费，支持代理），如 http://localhost:8888
    searxng_url: str | None = None
    # Tavily：https://tavily.com 申请免费 API Key（1000次/月）
    tavily_api_key: str | None = None
    # 搜索超时（秒），防止网络不通时长时间阻塞
    web_search_timeout: int = 8


settings = Settings()
