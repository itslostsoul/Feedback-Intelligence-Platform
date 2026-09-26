import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_settings_loads_required_and_default_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pass@localhost:5432/testdb")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("DEBUG", raising=False)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.DATABASE_URL == "postgresql+psycopg://user:pass@localhost:5432/testdb"
    assert settings.APP_ENV == "development"
    assert settings.DEBUG is False
    assert settings.is_production is False


def test_settings_requires_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_is_production_reflects_app_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pass@localhost:5432/testdb")
    monkeypatch.setenv("APP_ENV", "production")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.is_production is True
