from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import DatabaseUnavailableError
from app.db.database import get_db
from app.schemas.health import DatabaseHealthStatus, HealthStatus

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthStatus)
def health() -> HealthStatus:
    return HealthStatus(status="ok")


@router.get("/health/db", response_model=DatabaseHealthStatus)
def health_db(db: Session = Depends(get_db)) -> DatabaseHealthStatus:
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise DatabaseUnavailableError("Database health check failed.") from exc
    return DatabaseHealthStatus(status="ok")
