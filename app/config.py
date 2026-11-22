"""Environment configuration. Copy .env.example to .env and fill in values."""
from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Supabase
    supabase_url: str                      # https://<project-ref>.supabase.co; JWKS issuer
    database_url: str                      # postgres connection string (pooler, port 6543)

    # Anthropic
    anthropic_api_key: str
    adaptation_model: str = "claude-sonnet-4-6"   # PLAN / REWRITE / DISPOSITION
    max_output_tokens: int = 8000

    # Cross-model verification (pipeline v2). Two independent VERIFY
    # passes; a 'blocker' from EITHER one halts the version. Values may
    # carry a provider prefix ("anthropic:claude-haiku-4-5") so a second
    # vendor can be added by env alone — see app/services/verifiers.py.
    verify_model_a: str = ""                      # "" => adaptation_model
    verify_model_b: str = "claude-haiku-4-5"

    # Pipeline behavior
    plan_temperature: float = 0.0          # PLAN selects blocks; not creative work
    rewrite_temperature: float = 0.3
    verify_temperature: float = 0.0
    disposition_temperature: float = 0.0
    llm_max_retries: int = 2               # retries on malformed JSON output
    worker_poll_seconds: int = 5

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()
