from typing import Protocol

from app.schemas.analysis import FeedbackAnalysisResult
from app.schemas.decision_brief import DecisionBriefLLMOutput, DecisionSynthesisInput


class LLMProvider(Protocol):
    """Everything the rest of the app knows about an LLM provider.

    The application depends on this interface only — never on a specific
    provider SDK. `GeminiProvider` is the first implementation; an OpenAI or
    Ollama provider is a matter of adding another class that satisfies this
    same shape plus a branch in `app.services.llm.factory`.
    """

    def analyze_feedback(self, raw_text: str) -> FeedbackAnalysisResult:
        """Analyze one feedback item's text.

        Raises `app.core.errors.LLMProviderError` if the provider call
        itself fails (timeout, rate limit, upstream error), or
        `app.core.errors.LLMOutputValidationError` if the provider responded
        but the output doesn't satisfy `FeedbackAnalysisResult`.
        """
        ...

    def synthesize_decision_brief(self, synthesis_input: DecisionSynthesisInput) -> DecisionBriefLLMOutput:
        """Synthesize a grounded Decision Brief narrative from an
        already-computed evidence bundle (Phase 4B) — never raw feedback,
        never a new aggregate calculation.

        Raises `app.core.errors.LLMProviderError` if the provider call
        itself fails, or `app.core.errors.LLMOutputValidationError` if the
        provider responded but the output doesn't satisfy
        `DecisionBriefLLMOutput`. Grounding validation (citation_ids must
        reference `synthesis_input.citation_registry()`) happens in
        `app.services.decision_brief`, not here — a provider only needs to
        return schema-valid output; it isn't responsible for confirming its
        own citations resolve.
        """
        ...
