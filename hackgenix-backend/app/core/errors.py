import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base class for application-raised errors with a stable error code.

    Later phases (processing errors, external AI provider errors, etc.)
    should subclass this rather than raising bare exceptions, so every
    error surfaces through the same predictable JSON envelope.
    """

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    error_code: str = "internal_error"

    def __init__(self, message: str, *, error_code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if error_code:
            self.error_code = error_code


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    error_code = "not_found"


class ValidationAppError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    error_code = "validation_error"


class DatabaseUnavailableError(AppError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    error_code = "database_unavailable"


class LLMConfigurationError(AppError):
    """Missing API key / unsupported provider — a setup problem, not a
    per-item outcome."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    error_code = "llm_configuration_error"


class LLMProviderError(AppError):
    """The provider call itself failed: timeout, rate limit, or any other
    upstream failure."""

    status_code = status.HTTP_502_BAD_GATEWAY
    error_code = "llm_provider_error"


class LLMOutputValidationError(AppError):
    """The provider responded, but the output didn't satisfy the structured
    analysis schema."""

    status_code = status.HTTP_502_BAD_GATEWAY
    error_code = "llm_output_validation_error"


class EmbeddingConfigurationError(AppError):
    """Missing API key / unsupported provider for the embedding layer — a
    setup problem, not a per-item outcome."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    error_code = "embedding_configuration_error"


class EmbeddingProviderError(AppError):
    """The embedding provider call itself failed: timeout, rate limit, or
    any other upstream failure."""

    status_code = status.HTTP_502_BAD_GATEWAY
    error_code = "embedding_provider_error"


class EmbeddingOutputValidationError(AppError):
    """The provider responded, but didn't return a well-formed vector."""

    status_code = status.HTTP_502_BAD_GATEWAY
    error_code = "embedding_output_validation_error"


class EmbeddingMissingError(AppError):
    """The feedback item exists but has no embedding yet — distinct from
    "feedback not found" so API consumers can tell the two apart (e.g. to
    call POST /embed first, rather than treating the item as nonexistent)."""

    status_code = status.HTTP_404_NOT_FOUND
    error_code = "embedding_missing"


class AnalysisMissingError(AppError):
    """The feedback item exists but has not been analyzed yet — distinct
    from "feedback not found" so API consumers can tell the two apart (e.g.
    to call POST /analyze first, rather than treating the item as
    nonexistent)."""

    status_code = status.HTTP_404_NOT_FOUND
    error_code = "analysis_missing"


class LLMUngroundedOutputError(AppError):
    """The provider returned well-formed, schema-valid JSON, but it cited a
    citation_id that doesn't exist in the evidence supplied to it (Phase
    4B) — distinct from `LLMOutputValidationError` (which means the output
    wasn't even parseable/schema-valid) for observability: this specifically
    means the model fabricated a reference to evidence it wasn't given."""

    status_code = status.HTTP_502_BAD_GATEWAY
    error_code = "llm_ungrounded_output"


class SignalsMissingError(AppError):
    """The ProductIssue exists but signals haven't been calculated for it
    yet — distinct from "issue not found" so API consumers can tell the two
    apart (e.g. to call POST .../signals/recalculate first)."""

    status_code = status.HTTP_404_NOT_FOUND
    error_code = "signals_missing"


def _error_response(status_code: int, error_code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": error_code, "message": message}},
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        logger.warning("app_error endpoint=%s code=%s message=%s", request.url.path, exc.error_code, exc.message)
        return _error_response(exc.status_code, exc.error_code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        logger.info("validation_error endpoint=%s", request.url.path)
        return _error_response(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "validation_error",
            "Request validation failed.",
        )

    @app.exception_handler(SQLAlchemyError)
    async def handle_database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        logger.error("database_error endpoint=%s", request.url.path, exc_info=True)
        return _error_response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "database_error",
            "A database error occurred.",
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled_error endpoint=%s", request.url.path, exc_info=True)
        return _error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "internal_error",
            "An unexpected error occurred.",
        )
