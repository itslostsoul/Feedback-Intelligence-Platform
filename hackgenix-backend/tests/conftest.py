"""Pytest configuration and fixtures.

Test isolation: this suite runs against a DEDICATED PostgreSQL database
(`<DATABASE_URL's db>_test` on the same server, e.g. `hackgenix_test`) —
never the developer's normal `hackgenix` database. Tests that assert
absolute/global counts (e.g. "GET /issues returns total == 1") would
otherwise be coupled to whatever demo/seed data happens to already be
loaded in the shared dev database, since a fresh SAVEPOINT-per-test
transaction still starts from whatever was already committed there.

`app.core.config.get_settings()` is `@lru_cache`d, and `app.db.database`
builds its module-level `engine` from `get_settings().DATABASE_URL` at
*import time* — so the only way to point the whole app at the isolated test
database, with zero changes to application source, is to set the
`DATABASE_URL` environment variable to the test database's URL before
`get_settings()` is ever called by anything, anywhere in this process. That
must happen here, at the very top of conftest.py, before any `from app...`
import (including transitive ones pulled in by creating/migrating the test
database below) — pytest always imports conftest.py before any test module,
so this ordering is reliable.
"""

import os
import re
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def _load_base_database_url() -> str:
    """Reads DATABASE_URL from the real environment or `.env` directly —
    deliberately NOT via app.core.config.get_settings(), which must not be
    constructed (and therefore cached) until after DATABASE_URL below has
    been pointed at the isolated test database."""
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    value = dotenv_values(Path(__file__).resolve().parent.parent / ".env").get("DATABASE_URL")
    if not value:
        raise RuntimeError(
            "DATABASE_URL is not set in the environment or .env — cannot derive an isolated test database URL."
        )
    return value


def _safe_database_name(name: str | None) -> str:
    if not name or not re.fullmatch(r"[A-Za-z0-9_]+", name):
        raise RuntimeError(f"Refusing to operate on unsafe/empty database name {name!r}.")
    return name


def _derive_test_database_url(base_url: str) -> str:
    """An explicit TEST_DATABASE_URL always wins (e.g. for CI running its own
    disposable Postgres); otherwise the suite reuses the app's own
    DATABASE_URL server/credentials but a separate `<name>_test` database —
    same server, same PostgreSQL/pgvector setup, zero risk of touching the
    developer's real data."""
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        return explicit
    url = make_url(base_url)
    # NOT str(url) — SQLAlchemy's URL.__str__ renders the password masked
    # ("***") by default for safe logging, which would bake that literal
    # masked placeholder into the connection string used below instead of
    # the real credentials. render_as_string(hide_password=False) is the
    # explicit "I actually need to connect with this" form.
    return url.set(database=f"{_safe_database_name(url.database)}_test").render_as_string(hide_password=False)


_BASE_DATABASE_URL = _load_base_database_url()
TEST_DATABASE_URL = _derive_test_database_url(_BASE_DATABASE_URL)

# Everything imported below this line — directly or transitively — must see
# the isolated test database as DATABASE_URL.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL


def _ensure_test_database_exists() -> None:
    """Creates the isolated test database on the same PostgreSQL server if
    it doesn't exist yet. The only database this ever runs DDL against is
    `<name>_test`; the only *other* database it ever connects to is
    Postgres's own always-present `postgres` maintenance database (required
    because `CREATE DATABASE` cannot run inside a transaction block, and
    Postgres has no `CREATE DATABASE IF NOT EXISTS`) — the developer's real
    `hackgenix` database is never opened by this function at all."""
    test_url = make_url(TEST_DATABASE_URL)
    test_db_name = _safe_database_name(test_url.database)
    maintenance_url = test_url.set(database="postgres")

    maintenance_engine = create_engine(maintenance_url, future=True)
    try:
        with maintenance_engine.connect() as conn:
            conn = conn.execution_options(isolation_level="AUTOCOMMIT")
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": test_db_name}
            ).scalar_one_or_none()
            if exists is None:
                conn.execute(text(f'CREATE DATABASE "{test_db_name}"'))
    finally:
        maintenance_engine.dispose()


def _migrate_test_database() -> None:
    """Applies the real Alembic migrations (never `Base.metadata.create_all`)
    to the test database, so it gets the exact same schema — including the
    pgvector extension the first migration creates — that `alembic upgrade
    head` would give the real database. Idempotent: a no-op once already at
    head, so this is safe to run on every test session."""
    from alembic import command
    from alembic.config import Config

    backend_root = Path(__file__).resolve().parent.parent
    cfg = Config(str(backend_root / "alembic.ini"))
    command.upgrade(cfg, "head")


_ensure_test_database_exists()
_migrate_test_database()


import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import event  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.api.dependencies import get_embedding_provider, get_llm_provider  # noqa: E402
from app.db.database import engine, get_db  # noqa: E402
from app.main import app  # noqa: E402
from tests.fakes import FakeEmbeddingProvider, FakeLLMProvider  # noqa: E402


@pytest.fixture()
def db_session():
    """A session bound to a single connection + outer transaction that is
    always rolled back, so tests never leave rows behind — now in the
    isolated test database (see module docstring above), not the developer's
    real one. A SAVEPOINT is restarted after every commit so that
    application code calling `db.commit()` (as the ingestion service does)
    doesn't end the outer transaction early.
    """
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, future=True)

    nested = connection.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def _restart_savepoint(sess, trans):
        nonlocal nested
        if not nested.is_active:
            nested = connection.begin_nested()

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture()
def client(db_session):
    """TestClient with the DB dependency overridden (see `db_session`) and
    the LLM/embedding providers defaulted to successful fakes — no test
    using this fixture makes a real Gemini call. Individual tests may still
    reassign `app.dependency_overrides[get_llm_provider]` or
    `[get_embedding_provider]` to exercise different provider behavior
    (failure, bad output, etc.); the fixture teardown always pops both
    afterward regardless of the last value.
    """

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_llm_provider] = lambda: FakeLLMProvider()
    app.dependency_overrides[get_embedding_provider] = lambda: FakeEmbeddingProvider()
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_llm_provider, None)
        app.dependency_overrides.pop(get_embedding_provider, None)
