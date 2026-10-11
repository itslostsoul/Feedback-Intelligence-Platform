from sqlalchemy.orm import Session

from app.db.database import Base, get_db
from app.db.models import Feedback


def test_get_db_yields_and_closes_a_session() -> None:
    """Session creation is lazy (no real connection is made), so this is
    safe to run without a reachable Postgres instance."""
    generator = get_db()
    db = next(generator)

    assert isinstance(db, Session)
    assert db.is_active

    generator.close()  # runs the dependency's `finally: db.close()`


def test_feedback_model_is_registered_on_metadata() -> None:
    assert "feedback" in Base.metadata.tables
    columns = Base.metadata.tables["feedback"].columns
    assert {"id", "source", "raw_text", "processing_status"}.issubset(columns.keys())
    assert Feedback.__tablename__ == "feedback"
