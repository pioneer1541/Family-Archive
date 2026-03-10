from functools import lru_cache
from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FAMILY_VAULT_", extra="ignore")

    app_name: str = "Family Knowledge Vault API"
    api_prefix: str = "/v1"
    version: str = "0.0.1"

    database_url: str = "sqlite:///./family_vault.db"
    redis_url: str = "redis://redis:6379/0"
    celery_task_always_eager: bool = True
    auto_create_schema: bool = True
    ingestion_retry_max_retries: int = 2
    ingestion_retry_base_delay_sec: int = 5
    sqlite_busy_timeout_ms: int = 30000
    pg_pool_pre_ping: bool = True
    pg_pool_recycle: int = 1800
    pg_pool_size: int = 20
    pg_max_overflow: int = 30

    qdrant_url: str = "http://qdrant:6333"
    qdrant_collection: str = "fkv_docs_v1"
    qdrant_vector_size: int = 1024
    qdrant_enable: bool = False
    qdrant_embed_batch_enable: bool = True
    qdrant_embed_batch_size: int = 16
    qdrant_upsert_batch_size: int = 64

    ollama_base_url: str = "http://ollama:11434"
    planner_model: str = "qwen3:1.7b"
    synthesizer_model: str = "qwen3:4b-instruct"
    embed_model: str = "qwen3-embedding:0.6b"
    summary_model: str = "lfm2:latest"
    category_model: str = "qwen3:4b-instruct"
    friendly_name_model: str = "lfm2:latest"
    vl_extract_model: str = "qwen3-vl:2b"
    google_redirect_uri: str = "http://localhost:18181/gmail/callback"
    summary_timeout_sec: int = 12
    agent_synth_timeout_sec: int = 25
    agent_context_mode: str = "smart_followup"
    agent_conversation_max_turns: int = 2
    agent_graph_enabled: bool = False
    agent_graph_shadow_enabled: bool = False
    agent_graph_fail_open: bool = True
    agent_graph_loop_budget: int = 2
    agent_graph_max_context_chunks: int = 12
    agent_graph_max_context_chunks_recovery: int = 16
    agent_graph_llm_router_assist_enabled: bool = True
    agent_graph_llm_router_assist_trigger_mode: str = "low_confidence"
    agent_graph_llm_router_assist_model: str = "qwen3:4b-instruct"
    agent_graph_llm_router_assist_timeout_ms: int = 4000
    agent_graph_llm_router_assist_max_categories: int = 12
    agent_graph_llm_router_assist_top_k: int = 2
    agent_graph_llm_router_assist_cache_ttl_sec: int = 600
    agent_graph_llm_router_assist_confidence_threshold: float = 0.65
    vl_timeout_sec: int = 20

    ingestion_chunk_target_tokens: int = 320
    ingestion_chunk_overlap_tokens: int = 48
    ingestion_allowed_extensions: List[str] = ["pdf", "docx", "txt", "md", "xlsx"]
    ingestion_scan_exclude_dirs: List[str] = [
        ".git",
        "@eadir",
        "#recycle",
        "$recycle.bin",
        "node_modules",
        "__pycache__",
        "mail_attachments",
        "email_attachments",
    ]
    ingestion_scan_max_files_per_run: int = 5000
    ingestion_ocr_fallback_enabled: bool = True
    ingestion_vl_fallback_enabled: bool = True
    ingestion_ocr_pdf_max_pages: int = 8
    ingestion_ocr_render_dpi: int = 180
    ingestion_metadata_fallback_enabled: bool = True
    photo_file_extensions: List[str] = [
        "jpg",
        "jpeg",
        "png",
        "webp",
        "tif",
        "tiff",
        "heic",
    ]
    photo_max_size_mb: int = 20
    ingestion_phash_dedup_enabled: bool = True
    ingestion_phash_hamming_threshold: int = 8
    max_search_hits: int = 20

    source_type: str = "local"
    local_source_dir: str = ""
    nas_host: str = ""
    nas_path: str = ""
    nas_default_source_dir: str = ""
    nas_allowed_extensions: List[str] = [
        "pdf",
        "doc",
        "docx",
        "xls",
        "xlsx",
        "jpg",
        "jpeg",
        "png",
        "webp",
        "tif",
        "tiff",
        "heic",
    ]
    nas_auto_scan_enabled: bool = False
    nas_scan_interval_sec: int = 900
    sync_run_async_enabled: bool = True

    mail_poll_enabled: bool = False
    mail_poll_interval_sec: int = 300
    mail_query: str = "has:attachment newer_than:30d"
    mail_max_results: int = 50
    mail_credentials_path: str = "/app/secrets/gmail/credentials.json"
    mail_token_path: str = "/app/secrets/gmail/token.json"
    mail_attachment_root: str = "/app/data/mail_attachments"
    mail_attachment_subdir: str = "email_attachments"
    mail_allowed_extensions: List[str] = [
        "pdf",
        "doc",
        "docx",
        "xls",
        "xlsx",
        "jpg",
        "jpeg",
        "png",
        "webp",
        "tif",
        "tiff",
        "heic",
    ]
    mail_require_attachment_disposition: bool = True
    mail_skip_inline_images: bool = True
    mail_inline_name_patterns: str = r"image\d{3,4}|logo|signature|smime"
    tag_rules_path: str = "/app/services/kb-worker/config/tag_rules.json"

    longdoc_page_hard_limit: int = 180
    longdoc_sample_trigger_pages: int = 120
    longdoc_final_section_max: int = 18
    longdoc_final_semantic_max: int = 6
    summary_parallel_workers: int = 4

    cookie_secure: bool = False  # Set to True when serving over HTTPS

    allowed_origins: List[str] = ["http://localhost:18181"]
    log_level: str = "INFO"
    lexical_candidate_limit: int = 1500
    celery_worker_concurrency: int = 2

    @field_validator(
        "allowed_origins",
        "ingestion_allowed_extensions",
        "ingestion_scan_exclude_dirs",
        "photo_file_extensions",
        "nas_allowed_extensions",
        "mail_allowed_extensions",
        mode="before",
    )
    @classmethod
    def _parse_allowed_origins(cls, value):
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
