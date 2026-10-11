from pydantic import BaseModel


class HealthStatus(BaseModel):
    status: str


class DatabaseHealthStatus(BaseModel):
    status: str
    detail: str | None = None
