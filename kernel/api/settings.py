from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    aigent_tenant_id: str
    ollama_base_url: str
    ollama_model: str
    ollama_context_window: int
    ollama_max_response_tokens: int
    chat_db_path: str
    default_agent_id: str


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


def get_settings() -> Settings:
    return Settings(
        aigent_tenant_id=os.getenv("AIGENT_TENANT_ID", "default"),
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434"),
        ollama_model=os.getenv("OLLAMA_MODEL", "llama3.2:3b"),
        ollama_context_window=_int_env("OLLAMA_CONTEXT_WINDOW", 4096, min_value=256),
        ollama_max_response_tokens=_int_env("OLLAMA_MAX_RESPONSE_TOKENS", 512, min_value=16),
        chat_db_path=os.getenv("CHAT_DB_PATH", "/app/models-local/chat.db"),
        default_agent_id=os.getenv("AIGENT_AGENT_ID", "basic"),
    )
