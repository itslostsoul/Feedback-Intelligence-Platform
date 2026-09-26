import logging
import sys

from app.core.config import get_settings


def configure_logging() -> None:
    """Configure application-wide logging.

    Uses a plain structured-ish text format so log lines carry enough
    context (logger name / module) to trace an operation without a
    dedicated log pipeline. Never log secrets or raw sensitive payloads.
    """
    settings = get_settings()

    logging.basicConfig(
        level=settings.LOG_LEVEL.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
        force=True,
    )

    # Quiet noisy third-party loggers unless we're debugging.
    if not settings.DEBUG:
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
