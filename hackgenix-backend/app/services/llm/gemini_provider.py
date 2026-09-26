import json
import logging

from google import genai
from google.genai import types as genai_types
from pydantic import ValidationError

from app.core.errors import LLMOutputValidationError, LLMProviderError
from app.db.models.enums import FeedbackCategory
from app.schemas.analysis import FeedbackAnalysisResult
from app.schemas.analytics_insight import AnalyticsInsightLLMOutput, AnalyticsSynthesisInput
from app.schemas.decision_brief import DecisionBriefLLMOutput, DecisionSynthesisInput

logger = logging.getLogger(__name__)

# Built from explicit `types.Schema` objects rather than generated from the
# Pydantic model: `FeedbackAnalysisResult`'s `extra="forbid"` config makes
# pydantic emit an `additionalProperties` field, which the live Gemini API
# rejects with "Unknown name additional_properties" even though the SDK's
# own Schema type accepts it locally. Only the fields Gemini actually
# supports are set here.
_RESPONSE_SCHEMA = genai_types.Schema(
    type=genai_types.Type.OBJECT,
    properties={
        "category": genai_types.Schema(
            type=genai_types.Type.STRING, enum=[c.value for c in FeedbackCategory]
        ),
        "affected_feature": genai_types.Schema(type=genai_types.Type.STRING),
        "severity": genai_types.Schema(type=genai_types.Type.INTEGER, minimum=1, maximum=10),
        "confidence": genai_types.Schema(type=genai_types.Type.NUMBER, minimum=0, maximum=1),
        "summary": genai_types.Schema(type=genai_types.Type.STRING),
    },
    required=["category", "affected_feature", "severity", "confidence", "summary"],
)

_ANALYSIS_PROMPT = """You are analyzing a single piece of raw product feedback.

Return ONLY a JSON object with these fields, nothing else:
- category: one of {categories}
- affected_feature: the product feature/module this feedback concerns, in a few words
- severity: an integer 1-10, how severe this individual item is
- confidence: a float 0-1, your confidence in this specific classification
- summary: one short, actionable sentence summarizing this feedback item

Do NOT estimate frequency, trend, priority across other feedback, how many
users are affected, or overall product health — you are only interpreting
this one item in isolation.

Feedback:
\"\"\"
{raw_text}
\"\"\"
"""

# --- Phase 4B: grounded Decision Brief synthesis -----------------------------
# Same hand-built-Schema approach as _RESPONSE_SCHEMA above, for the same
# reason: DecisionBriefLLMOutput's extra="forbid" would otherwise emit an
# additionalProperties field the live Gemini API rejects.
_NARRATIVE_CLAIM_SCHEMA = genai_types.Schema(
    type=genai_types.Type.OBJECT,
    properties={
        "text": genai_types.Schema(type=genai_types.Type.STRING),
        "citation_ids": genai_types.Schema(
            type=genai_types.Type.ARRAY, items=genai_types.Schema(type=genai_types.Type.STRING)
        ),
    },
    required=["text", "citation_ids"],
)

_BRIEF_RESPONSE_SCHEMA = genai_types.Schema(
    type=genai_types.Type.OBJECT,
    properties={
        "headline": genai_types.Schema(type=genai_types.Type.STRING),
        "supporting_narrative": genai_types.Schema(type=genai_types.Type.ARRAY, items=_NARRATIVE_CLAIM_SCHEMA),
        "counter_narrative": genai_types.Schema(type=genai_types.Type.ARRAY, items=_NARRATIVE_CLAIM_SCHEMA),
        "gap_narrative": genai_types.Schema(type=genai_types.Type.ARRAY, items=_NARRATIVE_CLAIM_SCHEMA),
    },
    required=["headline", "supporting_narrative", "counter_narrative", "gap_narrative"],
)

_BRIEF_SYNTHESIS_INSTRUCTIONS = """You are writing a short, evidence-grounded brief for a product decision.

STRICT RULES — follow every one of these exactly:
- Use ONLY the facts explicitly listed below. Do not use outside knowledge about this product, this feature, or software products in general.
- Do not invent numbers, percentages, customer counts, customer segments, or sources that are not explicitly present in the facts below.
- Do not infer or claim that this evidence is statistically representative of the whole customer population — no population denominator is available anywhere in this system.
- match_score is a deterministic relevance/match-strength ranking, not a probability or confidence level — never describe or imply it as a probability, likelihood, or confidence.
- Do not create a priority score, numeric ranking, or any score of your own.
- Do not make a recommendation or state whether this issue should be prioritized — describe only what the evidence shows.
- If the evidence is insufficient to support a claim, say so plainly in gap_narrative rather than filling the gap with a guess or assumption.
- Every claim in supporting_narrative, counter_narrative, and gap_narrative MUST include at least one citation_id from the list below, and MUST NOT include any citation_id that is not listed below — never invent a citation_id.
- Each issue's title, category, and affected_feature are contextual labels only, not evidence — a title mentioning a problem does NOT by itself establish frequency, severity, or customer impact; only the explicitly listed facts (each with their own citation_id) may back a quantitative or evidentiary claim.

Decision question: "{query_text}"
Decision keywords: {keywords}

{issues_section}
{decision_level_gaps_section}
Return ONLY a JSON object with these fields, nothing else:
- headline: one or two sentences neutrally summarizing what the evidence shows — not a recommendation
- supporting_narrative: list of {{text, citation_ids}} — claims that strengthen the case, each citing supplied fact(s)
- counter_narrative: list of {{text, citation_ids}} — claims that weaken confidence or reliability, each citing supplied fact(s)
- gap_narrative: list of {{text, citation_ids}} — claims describing missing or insufficient evidence, each citing supplied fact(s)
"""


def _render_fact(fact) -> str:
    return (
        f"    - citation_id: {fact.citation_id}\n"
        f"      label: {fact.label}\n"
        f"      value: {json.dumps(fact.value)}\n"
        f"      threshold: {json.dumps(fact.threshold)}"
    )


def _render_facts(facts: list) -> str:
    if not facts:
        return "    (none)"
    return "\n".join(_render_fact(f) for f in facts)


def _render_issue(issue) -> str:
    return (
        f"Issue {issue.product_issue_id} — contextual label only, NOT evidence:\n"
        f"  title: {issue.title!r}\n"
        f"  category: {issue.category.value if issue.category else 'unknown'}\n"
        f"  affected_feature: {issue.affected_feature!r}\n"
        f"  match_method: {issue.match_method.value} (match_score={issue.match_score} — a relevance ranking, NOT a probability)\n"
        f"  Supporting facts (evidence FOR):\n{_render_facts(issue.supporting)}\n"
        f"  Counter facts (evidence AGAINST / weakening confidence):\n{_render_facts(issue.counter)}\n"
        f"  Gap facts (missing/insufficient evidence):\n{_render_facts(issue.gaps)}"
    )


def _render_synthesis_prompt(synthesis_input: DecisionSynthesisInput) -> str:
    issues_section = (
        "\n\n".join(_render_issue(issue) for issue in synthesis_input.issues)
        if synthesis_input.issues
        else "No ProductIssues matched this decision."
    )
    decision_level_gaps_section = (
        f"\nDecision-level gaps (apply to the decision as a whole, not any one issue):\n"
        f"{_render_facts(synthesis_input.decision_level_gaps)}\n"
        if synthesis_input.decision_level_gaps
        else ""
    )
    return _BRIEF_SYNTHESIS_INSTRUCTIONS.format(
        query_text=synthesis_input.query_text,
        keywords=", ".join(synthesis_input.keywords),
        issues_section=issues_section,
        decision_level_gaps_section=decision_level_gaps_section,
    )


# --- Analytics AI Insight: grounded interpretation of a deterministic ----------
# analytics evidence bundle (app.services.analytics_insight). Same
# hand-built-Schema approach as above, for the same reason.
_INSIGHT_CLAIM_SCHEMA = genai_types.Schema(
    type=genai_types.Type.OBJECT,
    properties={
        "text": genai_types.Schema(type=genai_types.Type.STRING),
        "evidence_ids": genai_types.Schema(
            type=genai_types.Type.ARRAY, items=genai_types.Schema(type=genai_types.Type.STRING)
        ),
    },
    required=["text", "evidence_ids"],
)

_ANALYTICS_INSIGHT_RESPONSE_SCHEMA = genai_types.Schema(
    type=genai_types.Type.OBJECT,
    properties={
        "headline": genai_types.Schema(type=genai_types.Type.STRING),
        "key_findings": genai_types.Schema(type=genai_types.Type.ARRAY, items=_INSIGHT_CLAIM_SCHEMA),
        "emerging_patterns": genai_types.Schema(type=genai_types.Type.ARRAY, items=_INSIGHT_CLAIM_SCHEMA),
        "areas_to_investigate": genai_types.Schema(type=genai_types.Type.ARRAY, items=_INSIGHT_CLAIM_SCHEMA),
        "caveats": genai_types.Schema(type=genai_types.Type.ARRAY, items=_INSIGHT_CLAIM_SCHEMA),
    },
    required=["headline", "key_findings", "emerging_patterns", "areas_to_investigate", "caveats"],
)

_ANALYTICS_INSIGHT_INSTRUCTIONS = """You are an analytics interpreter for a product feedback intelligence system. You are NOT a decision-maker and you must NOT act like one.

STRICT RULES — follow every one of these exactly:
- Use ONLY the aggregate facts explicitly listed below. Do not use outside knowledge about this product or software products in general.
- Do not invent metrics, numbers, percentages, customer counts, or customer complaints that are not explicitly present in the facts below.
- Do not invent or reference any product issue, category, or source that is not explicitly listed below.
- Do not claim causation between any two facts — only describe what the aggregate data shows, never why it happened.
- Do not recommend a product roadmap decision, priority, or course of action of any kind — describe patterns in the evidence only.
- Do not produce any numerical claim that isn't a direct restatement of a supplied fact's value.
- If the evidence is insufficient or ambiguous, say so plainly in caveats rather than filling the gap with a guess or assumption.
- Every claim in key_findings, emerging_patterns, areas_to_investigate, and caveats MUST include at least one evidence_id from the list below, and MUST NOT include any evidence_id that is not listed below — never invent an evidence_id.
- Each list may legitimately be empty if there is nothing genuine to report in that category — never pad a list with a weak or unsupported claim just to fill it.

Evidence facts (each with its own evidence_id):
{facts_section}

Return ONLY a JSON object with these fields, nothing else:
- headline: one or two neutral sentences summarizing what the aggregate evidence shows — not a recommendation
- key_findings: list of {{text, evidence_ids}} — the clearest, best-supported observations
- emerging_patterns: list of {{text, evidence_ids}} — trends or correlations visible only across multiple facts (e.g. volume + trend + category together), still never asserting causation
- areas_to_investigate: list of {{text, evidence_ids}} — where the evidence suggests a closer look is warranted, phrased as a question or observation, never a directive
- caveats: list of {{text, evidence_ids}} — data-quality or coverage limitations visible in the evidence (e.g. low analysis coverage, single evaluation, thin history)
"""


def _render_fact_line(fact) -> str:
    return f"    - evidence_id: {fact.citation_id}\n      label: {fact.label}\n      value: {json.dumps(fact.value)}"


def _render_analytics_prompt(synthesis_input: AnalyticsSynthesisInput) -> str:
    facts_section = (
        "\n".join(_render_fact_line(fact) for fact in synthesis_input.facts)
        if synthesis_input.facts
        else "    (no facts available)"
    )
    return _ANALYTICS_INSIGHT_INSTRUCTIONS.format(facts_section=facts_section)


class GeminiProvider:
    """LLMProvider implementation backed by Google's Gemini API.

    Never logs the API key or raw feedback text — only outcome metadata
    (error type, category, severity/confidence) is logged.
    """

    def __init__(self, api_key: str, model: str, timeout_seconds: float = 30.0) -> None:
        self._model = model
        self._client = genai.Client(
            api_key=api_key,
            http_options=genai_types.HttpOptions(timeout=int(timeout_seconds * 1000)),
        )

    def analyze_feedback(self, raw_text: str) -> FeedbackAnalysisResult:
        prompt = _ANALYSIS_PROMPT.format(
            categories=", ".join(c.value for c in FeedbackCategory), raw_text=raw_text
        )

        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=_RESPONSE_SCHEMA,
                    temperature=0,
                ),
            )
        except Exception as exc:
            logger.warning("gemini_request_failed error_type=%s", type(exc).__name__)
            raise LLMProviderError("The LLM provider request failed.") from exc

        raw_output = response.text
        if not raw_output or not raw_output.strip():
            raise LLMOutputValidationError("The LLM provider returned an empty response.")

        try:
            data = json.loads(raw_output)
        except json.JSONDecodeError as exc:
            logger.warning("gemini_output_not_json")
            raise LLMOutputValidationError("The LLM provider did not return valid JSON.") from exc

        try:
            return FeedbackAnalysisResult.model_validate(data)
        except ValidationError as exc:
            logger.warning("gemini_output_schema_invalid")
            raise LLMOutputValidationError(
                "The LLM provider's output did not match the required analysis schema."
            ) from exc

    def synthesize_decision_brief(self, synthesis_input: DecisionSynthesisInput) -> DecisionBriefLLMOutput:
        """Never sends raw feedback text or embedding vectors — the prompt
        is built entirely from `synthesis_input`, which is itself built
        entirely from already-persisted Phase 4A structured facts (plus
        read-only ProductIssue title/category/affected_feature). Grounding
        (citation_ids actually resolving) is validated by the caller
        (app.services.decision_brief), not here."""
        prompt = _render_synthesis_prompt(synthesis_input)

        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=_BRIEF_RESPONSE_SCHEMA,
                    temperature=0,
                ),
            )
        except Exception as exc:
            logger.warning("gemini_brief_request_failed error_type=%s", type(exc).__name__)
            raise LLMProviderError("The LLM provider request failed.") from exc

        raw_output = response.text
        if not raw_output or not raw_output.strip():
            raise LLMOutputValidationError("The LLM provider returned an empty response.")

        try:
            data = json.loads(raw_output)
        except json.JSONDecodeError as exc:
            logger.warning("gemini_brief_output_not_json")
            raise LLMOutputValidationError("The LLM provider did not return valid JSON.") from exc

        try:
            return DecisionBriefLLMOutput.model_validate(data)
        except ValidationError as exc:
            logger.warning("gemini_brief_output_schema_invalid")
            raise LLMOutputValidationError(
                "The LLM provider's output did not match the required decision brief schema."
            ) from exc

    def synthesize_analytics_insights(self, synthesis_input: AnalyticsSynthesisInput) -> AnalyticsInsightLLMOutput:
        """Never sends raw feedback text — the prompt is built entirely from
        `synthesis_input`'s already-computed aggregate facts (see
        app.services.analytics_insight). Grounding (evidence_ids actually
        resolving) is validated by the caller, not here."""
        prompt = _render_analytics_prompt(synthesis_input)

        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=_ANALYTICS_INSIGHT_RESPONSE_SCHEMA,
                    temperature=0,
                ),
            )
        except Exception as exc:
            logger.warning("gemini_analytics_insight_request_failed error_type=%s", type(exc).__name__)
            raise LLMProviderError("The LLM provider request failed.") from exc

        raw_output = response.text
        if not raw_output or not raw_output.strip():
            raise LLMOutputValidationError("The LLM provider returned an empty response.")

        try:
            data = json.loads(raw_output)
        except json.JSONDecodeError as exc:
            logger.warning("gemini_analytics_insight_output_not_json")
            raise LLMOutputValidationError("The LLM provider did not return valid JSON.") from exc

        try:
            return AnalyticsInsightLLMOutput.model_validate(data)
        except ValidationError as exc:
            logger.warning("gemini_analytics_insight_output_schema_invalid")
            raise LLMOutputValidationError(
                "The LLM provider's output did not match the required analytics insight schema."
            ) from exc
