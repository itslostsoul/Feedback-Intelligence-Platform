from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration, loaded from environment variables / .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_NAME: str = "Hackgenix Feedback Intelligence API"
    APP_ENV: str = "development"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    DATABASE_URL: str

    # LLM provider abstraction (Phase 3A). LLM_PROVIDER selects the backend
    # ("gemini" for now); each provider has its own API key variable so
    # adding OpenAI/Ollama later doesn't reuse or overload this one.
    LLM_PROVIDER: str | None = None
    LLM_MODEL: str | None = None
    GEMINI_API_KEY: str | None = None

    # Below this confidence, a successful analysis is routed to NEEDS_REVIEW
    # instead of ANALYZED rather than being trusted outright.
    LLM_CONFIDENCE_REVIEW_THRESHOLD: float = 0.5
    LLM_TIMEOUT_SECONDS: float = 30.0

    # Embedding provider abstraction (Phase 3B-1). Deliberately reuses
    # GEMINI_API_KEY above rather than a separate embedding-specific key —
    # it's the same Gemini account/credential, just a different model.
    EMBEDDING_PROVIDER: str | None = None
    EMBEDDING_MODEL: str | None = None

    # Comma-separated allowed origins for the frontend (CORS). Defaults to
    # the common local dev server ports (Vite, Create React App/Next.js) so
    # a frontend developer gets working CORS with zero configuration; set
    # explicitly in .env for anything else. Never a bare "*" — configured
    # origins only, per this project's security posture.
    CORS_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.APP_ENV.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
