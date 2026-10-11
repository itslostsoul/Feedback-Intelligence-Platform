"""Manual, explicitly-optional smoke test for the real embedding provider.

This is NOT run by pytest (filename doesn't match test_*.py) and makes a
real network call using whatever EMBEDDING_PROVIDER/EMBEDDING_MODEL/
GEMINI_API_KEY are configured in the environment/.env. Run it by hand to
sanity-check real credentials and confirm the vector dimension still
matches FEEDBACK_EMBEDDING_DIMENSION:

    python scripts/manual_embedding_check.py "The app crashes when I upload a large photo."
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings  # noqa: E402
from app.db.models.feedback_embedding import FEEDBACK_EMBEDDING_DIMENSION  # noqa: E402
from app.services.embedding.factory import build_embedding_provider  # noqa: E402


def main() -> int:
    text = sys.argv[1] if len(sys.argv) > 1 else "The checkout page is very slow on mobile."

    settings = get_settings()
    print(f"provider={settings.EMBEDDING_PROVIDER!r} model={settings.EMBEDDING_MODEL!r}")

    provider = build_embedding_provider(settings)
    result = provider.embed_feedback(text)

    print("dimension:", result.dimension)
    print("expected FEEDBACK_EMBEDDING_DIMENSION:", FEEDBACK_EMBEDDING_DIMENSION)
    print("matches column dimension:", result.dimension == FEEDBACK_EMBEDDING_DIMENSION)
    print("first 5 values:", result.vector[:5])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
