"""Manual, explicitly-optional smoke test for the real Gemini provider.

This is NOT run by pytest (filename doesn't match test_*.py) and makes a
real network call using whatever LLM_PROVIDER/LLM_MODEL/GEMINI_API_KEY are
configured in the environment/.env. Run it by hand to sanity-check real
credentials:

    python scripts/manual_gemini_check.py "The app crashes when I upload a large photo."
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings  # noqa: E402
from app.services.llm.factory import build_llm_provider  # noqa: E402


def main() -> int:
    text = sys.argv[1] if len(sys.argv) > 1 else "The checkout page is very slow on mobile."

    settings = get_settings()
    print(f"provider={settings.LLM_PROVIDER!r} model={settings.LLM_MODEL!r}")

    provider = build_llm_provider(settings)
    result = provider.analyze_feedback(text)

    print("category:", result.category.value)
    print("affected_feature:", result.affected_feature)
    print("severity:", result.severity)
    print("confidence:", result.confidence)
    print("summary:", result.summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
