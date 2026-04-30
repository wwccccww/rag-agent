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
    chunk_max_chars: int = 800
    chunk_overlap: int = 100
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
    # 向量检索相关性阈值：余弦距离超过此值的片段视为"不相关"直接丢弃
    # cosine_distance ∈ [0,2]，0=完全相同，1=正交，2=相反
    # 0.5 对应余弦相似度 ≈ 0.75，经验上是"有一定相关性"的下限
    vector_distance_threshold: float = 0.4

    # Web 搜索后端（可选，国内环境 DuckDuckGo 可能被屏蔽）
    # 优先级：searxng_url > tavily_api_key > duckduckgo（fallback）
    # SearXNG：自建实例（免费，支持代理），如 http://localhost:8888
    searxng_url: str | None = None
    # Tavily：https://tavily.com 申请免费 API Key（1000次/月）
    tavily_api_key: str | None = None
    # 搜索超时（秒），防止网络不通时长时间阻塞
    web_search_timeout: int = 8


settings = Settings()
