from pydantic import BaseModel


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    """Shape of every error JSON body returned by the API.

    Mirrors the envelope built by app.core.errors._error_response so the
    OpenAPI schema documents what clients actually receive.
    """

    error: ErrorDetail
