from app.core.errors import (
    EmbeddingOutputValidationError,
    EmbeddingProviderError,
    LLMOutputValidationError,
    LLMProviderError,
)
from app.db.models.enums import FeedbackCategory
from app.db.models.feedback_embedding import FEEDBACK_EMBEDDING_DIMENSION
from app.schemas.analysis import FeedbackAnalysisResult
from app.schemas.analytics_insight import AnalyticsInsightLLMOutput, AnalyticsSynthesisInput, InsightClaim
from app.schemas.decision_brief import DecisionBriefLLMOutput, DecisionSynthesisInput, NarrativeClaim
from app.schemas.embedding import EmbeddingResult


class FakeLLMProvider:
    """Deterministic stand-in for a real LLMProvider, used in tests so none
    of them require a real Gemini API call."""

    def __init__(
        self,
        result: FeedbackAnalysisResult | None = None,
        *,
        raise_provider_error: bool = False,
        raise_output_error: bool = False,
        brief_result: DecisionBriefLLMOutput | None = None,
        brief_raise_provider_error: bool = False,
        brief_raise_output_error: bool = False,
        brief_unknown_citation_id: str | None = None,
        insight_result: AnalyticsInsightLLMOutput | None = None,
        insight_raise_provider_error: bool = False,
        insight_raise_output_error: bool = False,
        insight_unknown_evidence_id: str | None = None,
    ) -> None:
        self._result = result or FeedbackAnalysisResult(
            category=FeedbackCategory.BUG,
            affected_feature="upload",
            severity=7,
            confidence=0.9,
            summary="Upload fails when the photo exceeds 5MB.",
        )
        self._raise_provider_error = raise_provider_error
        self._raise_output_error = raise_output_error

        self._brief_result = brief_result
        self._brief_raise_provider_error = brief_raise_provider_error
        self._brief_raise_output_error = brief_raise_output_error
        # When set, the fake ignores synthesis_input's real citation_ids and
        # cites this (deliberately nonexistent) one instead — used to test
        # grounding validation (LLMUngroundedOutputError).
        self._brief_unknown_citation_id = brief_unknown_citation_id

        self._insight_result = insight_result
        self._insight_raise_provider_error = insight_raise_provider_error
        self._insight_raise_output_error = insight_raise_output_error
        self._insight_unknown_evidence_id = insight_unknown_evidence_id

    def analyze_feedback(self, raw_text: str) -> FeedbackAnalysisResult:
        if self._raise_provider_error:
            raise LLMProviderError("Simulated provider failure.")
        if self._raise_output_error:
            raise LLMOutputValidationError("Simulated malformed output.")
        return self._result

    def synthesize_decision_brief(self, synthesis_input: DecisionSynthesisInput) -> DecisionBriefLLMOutput:
        if self._brief_raise_provider_error:
            raise LLMProviderError("Simulated provider failure.")
        if self._brief_raise_output_error:
            raise LLMOutputValidationError("Simulated malformed output.")

        if self._brief_result is not None:
            return self._brief_result

        if self._brief_unknown_citation_id is not None:
            citation_id = self._brief_unknown_citation_id
        else:
            registry = synthesis_input.citation_registry()
            citation_id = next(iter(registry), None)
            if citation_id is None:
                # No real facts to cite (e.g. NO_MATCHES) — still must cite
                # something real if any registry entries exist; with none at
                # all, fall back to a placeholder so the fake stays usable
                # in that edge case without special-casing every caller.
                citation_id = "decision:no_matching_issues_found"

        claim = NarrativeClaim(text="A grounded, deterministic test claim.", citation_ids=[citation_id])
        return DecisionBriefLLMOutput(
            headline="A neutral, evidence-based summary for testing.",
            supporting_narrative=[claim],
            counter_narrative=[],
            gap_narrative=[],
        )

    def synthesize_analytics_insights(self, synthesis_input: AnalyticsSynthesisInput) -> AnalyticsInsightLLMOutput:
        if self._insight_raise_provider_error:
            raise LLMProviderError("Simulated provider failure.")
        if self._insight_raise_output_error:
            raise LLMOutputValidationError("Simulated malformed output.")

        if self._insight_result is not None:
            return self._insight_result

        if self._insight_unknown_evidence_id is not None:
            evidence_id = self._insight_unknown_evidence_id
        else:
            registry = synthesis_input.citation_registry()
            evidence_id = next(iter(registry), None)
            if evidence_id is None:
                # No real facts to cite (e.g. an empty database) — still must
                # cite something real if any registry entries exist; with
                # none at all, fall back to a placeholder so the fake stays
                # usable in that edge case without special-casing every caller.
                evidence_id = "analytics:feedback_volume"

        claim = InsightClaim(text="A grounded, deterministic test finding.", evidence_ids=[evidence_id])
        return AnalyticsInsightLLMOutput(
            headline="A neutral, evidence-based analytics summary for testing.",
            key_findings=[claim],
            emerging_patterns=[],
            areas_to_investigate=[],
            caveats=[],
        )


class FakeEmbeddingProvider:
    """Deterministic stand-in for a real EmbeddingProvider, used in tests so
    none of them require a real Gemini API call."""

    def __init__(
        self,
        vector: list[float] | None = None,
        *,
        raise_provider_error: bool = False,
        raise_output_error: bool = False,
    ) -> None:
        self._vector = vector if vector is not None else [0.001 * i for i in range(FEEDBACK_EMBEDDING_DIMENSION)]
        self._raise_provider_error = raise_provider_error
        self._raise_output_error = raise_output_error
        self.call_count = 0

    def embed_feedback(self, raw_text: str) -> EmbeddingResult:
        self.call_count += 1
        if self._raise_provider_error:
            raise EmbeddingProviderError("Simulated provider failure.")
        if self._raise_output_error:
            raise EmbeddingOutputValidationError("Simulated malformed embedding output.")
        return EmbeddingResult(vector=self._vector)
