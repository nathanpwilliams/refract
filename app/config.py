"""Environment configuration. Copy .env.example to .env and fill in values."""
from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Supabase
    supabase_url: str                      # https://<project-ref>.supabase.co
    supabase_service_role_key: str         # server-side only; NEVER ship to frontend
    supabase_jwt_secret: str               # Settings -> API -> JWT secret
    database_url: str                      # postgres connection string (pooler, port 6543)

    # Anthropic
    anthropic_api_key: str
    adaptation_model: str = "claude-sonnet-4-6"
    max_output_tokens: int = 8000

    # Pipeline behavior
    rewrite_temperature: float = 0.3
    verify_temperature: float = 0.0
    llm_max_retries: int = 2               # retries on malformed JSON output
    worker_poll_seconds: int = 5

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()
