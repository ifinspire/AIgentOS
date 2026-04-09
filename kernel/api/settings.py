from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    aigent_version: str
    aigent_tenant_id: str
    ollama_base_url: str
    ollama_model: str
    embedding_base_url: str
    embedding_model: str
    ollama_context_window: int
    ollama_max_response_tokens: int
    chat_db_path: str
    default_agent_id: str
    worker_poll_interval_ms: int
    memory_chunk_limit: int
    memory_compaction_batch_size: int
    uploads_dir: str
    mcp_timeout_seconds: int
    markitdown_mcp_enabled: bool
    markitdown_mcp_url: str
    markitdown_mcp_uploads_dir: str
    markitdown_mcp_tool_name: str


def _int_env(name: str, default: int, min_value: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    if value < min_value:
        return default
    return value


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def get_settings() -> Settings:
    return Settings(
        aigent_version=os.getenv("AIGENT_VERSION", "0.3.0-oss"),
        aigent_tenant_id=os.getenv("AIGENT_TENANT_ID", "default"),
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434"),
        ollama_model=os.getenv("OLLAMA_MODEL", "llama3.2:3b"),
        embedding_base_url=os.getenv("EMBEDDING_BASE_URL", os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")),
        embedding_model=os.getenv("EMBEDDING_MODEL", "nomic-embed-text"),
        ollama_context_window=_int_env("OLLAMA_CONTEXT_WINDOW", 4096, min_value=256),
        ollama_max_response_tokens=_int_env("OLLAMA_MAX_RESPONSE_TOKENS", 1024, min_value=16),
        chat_db_path=os.getenv("CHAT_DB_PATH", "/app/models-local/chat.db"),
        default_agent_id=os.getenv("AIGENT_AGENT_ID", "basic"),
        worker_poll_interval_ms=_int_env("WORKER_POLL_INTERVAL_MS", 750, min_value=50),
        memory_chunk_limit=_int_env("MEMORY_CHUNK_LIMIT", 160, min_value=20),
        memory_compaction_batch_size=_int_env("MEMORY_COMPACTION_BATCH_SIZE", 24, min_value=4),
        uploads_dir=os.getenv("UPLOADS_DIR", "/app/models-local/uploads"),
        mcp_timeout_seconds=_int_env("MCP_TIMEOUT_SECONDS", 15, min_value=2),
        markitdown_mcp_enabled=_bool_env("MARKITDOWN_MCP_ENABLED", True),
        markitdown_mcp_url=os.getenv("MARKITDOWN_MCP_URL", "http://markitdown-mcp:3001/mcp/"),
        markitdown_mcp_uploads_dir=os.getenv("MARKITDOWN_MCP_UPLOADS_DIR", "/data/uploads"),
        markitdown_mcp_tool_name=os.getenv("MARKITDOWN_MCP_TOOL_NAME", "convert_to_markdown"),
    )
