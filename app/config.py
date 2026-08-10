"""应用配置。"""

from typing import Any

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "SuperBizAgent"
    app_version: str = "1.0.0"
    debug: bool = False
    host: str = "127.0.0.1"
    port: int = 9900

    cors_origins: str = ""
    cors_allow_credentials: bool = False

    dashscope_api_key: str = ""
    dashscope_model: str = "qwen-max"
    dashscope_embedding_model: str = "text-embedding-v4"

    milvus_host: str = "localhost"
    milvus_port: int = 19530
    milvus_timeout: int = 10000

    rag_top_k: int = 3
    rag_candidate_k: int = 8
    rag_rrf_k: int = 60
    rag_keyword_corpus_limit: int = 2000
    rag_model: str = "qwen-max"
    max_memory_sessions: int = 500
    max_session_messages: int = 100
    conversation_summary_trigger_tokens: int = 6000
    conversation_summary_trigger_messages: int = 12
    conversation_summary_keep_messages: int = 10
    conversation_window_turns: int = 6

    chunk_max_size: int = 800
    chunk_overlap: int = 100

    mcp_cls_transport: str = "streamable-http"
    mcp_cls_url: str = "http://localhost:8003/mcp"
    mcp_monitor_transport: str = "streamable-http"
    mcp_monitor_url: str = "http://localhost:8004/mcp"

    prometheus_base_url: str = "http://127.0.0.1:9090"
    prometheus_request_timeout: float = 10.0

    fault_lab_base_url: str = "http://127.0.0.1:9910"
    fault_lab_request_timeout: float = 10.0
    fault_lab_enabled: bool = True
    fault_lab_control_token: SecretStr | None = None
    log_data_backend: str = "live_local"

    incident_memory_db_path: str = "volumes/incident-memory.db"
    incident_memory_top_k: int = 3

    @property
    def cors_origin_list(self) -> list[str]:
        origins = [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]
        if "*" in origins and self.cors_allow_credentials:
            raise ValueError("通配跨域来源不能与凭证模式同时启用")
        return origins

    @property
    def dashscope_api_secret(self) -> SecretStr | None:
        return SecretStr(self.dashscope_api_key) if self.dashscope_api_key else None

    @property
    def mcp_servers(self) -> dict[str, dict[str, Any]]:
        return {
            "cls": {
                "transport": self.mcp_cls_transport,
                "url": self.mcp_cls_url,
            },
            "monitor": {
                "transport": self.mcp_monitor_transport,
                "url": self.mcp_monitor_url,
            },
        }


config = Settings()
