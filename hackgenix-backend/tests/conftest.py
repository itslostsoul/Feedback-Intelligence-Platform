import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.api.dependencies import get_embedding_provider, get_llm_provider
from app.db.database import engine, get_db
from app.main import app
from tests.fakes import FakeEmbeddingProvider, FakeLLMProvider


@pytest.fixture()
def db_session():
    """A session bound to a single connection + outer transaction that is
    always rolled back, so Phase 2 tests never leave rows behind in the
    real database. A SAVEPOINT is restarted after every commit so that
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
