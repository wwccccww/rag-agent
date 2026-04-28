from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://rag:ragpass@192.168.116.130:5432/rag"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_chat_model: str = "qwen2.5:7b"
    ollama_embed_model: str = "nomic-embed-text:latest"
    embed_dim: int = 768  # nomic-embed-text 768
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
    # 上传文件大小上限（MB），防止超大文件导致内存耗尽
    max_upload_mb: int = 50
    # 向量检索相关性阈值：余弦距离超过此值的片段视为"不相关"直接丢弃
    # cosine_distance ∈ [0,2]，0=完全相同，1=正交，2=相反
    # 0.5 对应余弦相似度 ≈ 0.75，经验上是"有一定相关性"的下限
    vector_distance_threshold: float = 0.4


settings = Settings()
