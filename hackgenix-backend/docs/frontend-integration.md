# Frontend Integration Guide

This is the contract for building a UI against the Hackgenix backend. It's
written so you never need to read the backend source to understand what's
available — `GET /docs` (interactive OpenAPI) is the live, authoritative
version of everything described here; this document is the guided tour and
the "how do I actually build the demo" walkthrough.

The product is **evidence-backed product decision intelligence** — not a
chatbot that summarizes feedback. Every number the UI shows should be
traceable back to real feedback; every LLM-written sentence should carry a
citation; every "what changed" claim should be a structured, factual event,
never an invented recommendation. Keep that distinction visible in the UI.

---

## 1. Backend base URL

Local development: **`http://localhost:8000`**. All API routes are under
`/api/v1`. Interactive docs at `http://localhost:8000/docs`.

## 2. How to start the backend

```bash
python -m venv venv
venv\Scripts\activate                 # Windows; `source venv/bin/activate` elsewhere
pip install -r requirements.txt

copy .env.example .env                # edit DATABASE_URL at minimum
alembic upgrade head                  # requires PostgreSQL + pgvector — see README.md
uvicorn main:app --reload             # http://localhost:8000
```

`GEMINI_API_KEY`/`LLM_PROVIDER`/`LLM_MODEL`/`EMBEDDING_PROVIDER`/
`EMBEDDING_MODEL` are only needed for `/analyze`, `/embed`, and
`POST /decisions/{id}/brief` — everything else works without them. No test
requires them either (see README.md).

**Health checks:**
- `GET /` — liveness (`{"status": "AI Backend Operational"}`)
- `GET /api/v1/health` — `{"status": "ok"}`
- `GET /api/v1/health/db` — actually queries the database; `200` or a
  `503` with the standard error envelope if unreachable

## 3. Main API groups

| Prefix | Covers |
|---|---|
| `/api/v1/feedback` | Ingestion, listing, analysis, embedding, semantic relationships |
| `/api/v1/issues` | Discovered Product Issues, their deterministic signals |
| `/api/v1/decisions` | Decisions, evaluations, grounded briefs, stability, Challenge My Decision |

## 4. Feedback workflow

```
POST /api/v1/feedback                          create one item (201, or 200 if it's a duplicate)
POST /api/v1/feedback/bulk                     multipart CSV upload
GET  /api/v1/feedback?limit=&offset=&source=&source_type=&status=   paginated, filterable list
GET  /api/v1/feedback/{id}                     one item
GET  /api/v1/feedback/{id}/analysis            read existing analysis (404 analysis_missing if none yet)
POST /api/v1/feedback/{id}/analyze             run LLM analysis (needs Gemini configured)
POST /api/v1/feedback/{id}/embed               generate/reuse an embedding (never returns the raw vector)
GET  /api/v1/feedback/{id}/similar             preview semantic neighbors, persists nothing
POST /api/v1/feedback/{id}/semantic-analysis   preview + persist relationship rows
```

`processing_status` on a feedback item tells you where it is in the
pipeline: `received -> analyzing -> analyzed | needs_review | failed`.
There's no separate "embedding status" field — call
`GET /feedback/{id}/analysis` (404 if not yet analyzed) to check
analysis, and treat embedding as an internal step that powers clustering
(the UI generally doesn't need to show embedding state directly).

Example create:
```json
POST /api/v1/feedback
{
  "source": "app_store",
  "source_type": "review",
  "raw_text": "Checkout fails every time I try to pay.",
  "customer_id": "cust-123",
  "customer_tier": "enterprise",
  "external_id": "optional-idempotency-key"
}
```

## 5. Product issue workflow

```
POST /api/v1/issues/discover                          cluster feedback_relationship rows into issues (explicit, never automatic)
GET  /api/v1/issues?limit=&offset=&status=&category=   paginated list
GET  /api/v1/issues/{id}                               full detail, includes feedback_ids (traceability)
POST /api/v1/issues/{id}/signals/recalculate           compute deterministic signals for one issue
GET  /api/v1/issues/{id}/signals                       read the last-calculated signal snapshot (404 signals_missing if never calculated)
POST /api/v1/issues/signals/recalculate                recalculate signals for every issue
```

`GET /issues/{id}` gives you `title`, `category`, `affected_feature`,
`status`, `clustering_version`, timestamps, and `feedback_ids` (the raw
traceability list — fetch `GET /feedback/{id}` per id for full detail, or
`GET /feedback/{id}/analysis` for its analysis).

`GET /issues/{id}/signals` gives you the full deterministic signal bundle:
`feedback_count`, `severity` (full distribution + buckets, never just one
number), `recency`, `trend` (with an explicit `insufficient_data` state —
never a guessed direction), `customer_reach` (known vs. anonymous, never
conflated), `source_diversity`, `segment_coverage`, `duplicate_ratio`,
`time_distribution`, and `evidence_quality` (transparent component flags,
never a blended score).

## 6. Decision workflow

```
POST /api/v1/decisions                        create ({query_text, keywords, category?, affected_feature_hint?})
GET  /api/v1/decisions?limit=&offset=          paginated list
GET  /api/v1/decisions/{id}                    one decision
POST /api/v1/decisions/{id}/evaluate           run deterministic matching + evidence classification (no LLM) — always appends a NEW evaluation
GET  /api/v1/decisions/{id}/evaluations?limit=&offset=   history of evaluations (lightweight summaries)
GET  /api/v1/decisions/{id}/evaluations/latest           full detail of the most recent evaluation
GET  /api/v1/decisions/{id}/evaluations/{evaluation_id}  full detail of one specific historical evaluation
```

`keywords` are **explicit tokens you supply** — the backend never runs NLP
extraction on `query_text` to guess them. For a "Should we prioritize
fixing checkout?" question, pass `keywords: ["checkout"]`.

An evaluation's `issues[]` array (each a matched `ProductIssue`) includes
`issue_title`/`issue_category`/`issue_affected_feature`/`issue_status`
inline (enriched for display — read live from the issue, not historically
pinned) plus `match_method`/`match_score`/`matched_terms` (why it matched
— `match_score` is a **relevance ranking, never a probability or
confidence**) and the full `supporting`/`counter`/`gaps` fact lists, each
fact shaped `{code, label, value, threshold, source_signal_path}`.

**Evaluations are append-only** — re-evaluating never overwrites history;
it creates a new evaluation you can always compare against old ones.

## 7. Decision brief workflow

```
POST /api/v1/decisions/{id}/brief              generate a grounded brief from the LATEST evaluation (needs Gemini configured)
GET  /api/v1/decisions/{id}/brief               latest COMPLETED brief (404 if none yet)
GET  /api/v1/decisions/{id}/brief/history?limit=&offset=   all attempts, including FAILED ones
GET  /api/v1/decisions/{id}/brief/{brief_id}    one specific historical brief
```

The LLM only ever explains the evidence already computed by `/evaluate` —
it never analyzes raw feedback, never invents a number, and never issues a
priority score or recommendation. Every claim (`supporting_narrative`/
`counter_narrative`/`gap_narrative`, each a list of
`{text, citation_ids}`) cites at least one `citation_id`, and every
`citation_id` resolves in the response's `citations` map — render a
clickable citation using that map directly, no extra API call needed:

```json
{
  "headline": "...",
  "supporting_narrative": [{"text": "...", "citation_ids": ["<issue-id>:high_frequency"]}],
  "citations": {
    "<issue-id>:high_frequency": {
      "citation_id": "<issue-id>:high_frequency",
      "product_issue_id": "<issue-id>",
      "issue_title": "Checkout fails during payment",
      "code": "high_frequency",
      "label": "High feedback frequency",
      "value": {"feedback_count": 8},
      "threshold": 5,
      "source_signal_path": "feedback_count",
      "category": "supporting"
    }
  },
  "evidence_sufficiency": "sufficient"
}
```

`evidence_sufficiency` (`no_matches` | `insufficient` | `sufficient`) is
**computed by the backend before the LLM ever runs** — trust this field
over the LLM's prose if they ever seem to disagree; the flag is
authoritative by design.

If Gemini isn't configured (or the call fails), `POST .../brief` returns a
`502`/`503` with the standard error envelope — build a "brief unavailable"
UI state for this; the rest of the product (evidence, stability, challenge)
works regardless.

## 8. Stability workflow

```
GET /api/v1/decisions/{id}/stability                                       compare the two most recent evaluations
GET /api/v1/decisions/{id}/evaluations/{from_id}/compare/{to_id}           compare any two specific evaluations
```

Returns `200` (never an error) even with fewer than 2 evaluations —
`status: "insufficient_history"` and `previous_evaluation_id: null` in
that case; a real "nothing changed" result instead has `status: "stable"`
and `events: []` with both evaluation ids populated. Check
`previous_evaluation_id` for `null` to distinguish the two, not just the
`status` string.

`status` (`insufficient_history` | `stable` | `strengthened` | `weakened` |
`mixed`) is a **convenience summary only** — `events` is always the
complete, authoritative account of what changed; never build UI that
shows `status` without also surfacing `events`. **`stable` means the
evidence pattern hasn't changed — it is never a judgment that the decision
is correct.**

Each event: `{type, product_issue_id, fact_code, bucket, previous_value,
current_value, source_signal_path, interpretation}` — both `type` and
`interpretation` are fixed vocabularies (never free text). Also watch
`evaluation_version_mismatch` (an apparent change might just be a rule
update, not real evidence drift) and `reference_at_tie`.

## 9. Challenge My Decision workflow

```
GET /api/v1/decisions/{id}/challenge
```

**Not a recommendation engine** — there is no score, ranking, "winner," or
verdict field anywhere in this response. It's the same data as
`/stability`, reorganized into ready-to-render buckets:

```json
{
  "status": "mixed",
  "supporting_changes": [...],
  "counter_changes": [...],
  "trend_changes": [...],
  "gap_changes": [...],
  "relevance_changes": [...],
  "value_changes": [...],
  "new_issues": [...],
  "disappeared_issues": [...],
  "decision_level_changes": [...],
  "issue_summaries": {"<issue-id>": {"title": "...", "category": "...", "affected_feature": "...", "status": "..."}},
  "all_events": [...]
}
```

Build the "Challenge My Decision" panel directly from these buckets — e.g.
a "Trend reversed" callout when `trend_changes` contains an event with
`interpretation: "direction_reversed"`, an "Issue disappeared" card per
entry in `disappeared_issues` (resolved via `issue_summaries` for its
title), etc. `all_events` is the flat union of every bucket, useful for a
"show everything" view.

## 10. Request/response conventions

- **UUIDs**: standard string form (`"3fae1234-....-...."`) everywhere, in both paths and bodies.
- **Datetimes**: ISO 8601 with timezone offset (e.g. `"2026-06-15T12:00:00+00:00"`).
- **Enums**: lowercase snake_case strings (e.g. `"needs_review"`, `"insufficient_history"`).
- **Errors**: always `{"error": {"code": "...", "message": "..."}}`. Common
  codes: `not_found` (404), `validation_error` (422, malformed UUIDs
  included — FastAPI/Pydantic catches these automatically),
  `signals_missing` (404, issue exists but signals never calculated),
  `analysis_missing` (404, feedback exists but never analyzed),
  `embedding_missing` (404), `llm_configuration_error` (503, Gemini not
  configured), `llm_provider_error` (502, Gemini call failed),
  `llm_output_validation_error` (502, malformed LLM output),
  `llm_ungrounded_output` (502, LLM cited something it wasn't given),
  `database_error` (503). Never a raw stack trace or SQL error.
- **Pagination**: every list endpoint takes `limit`/`offset` query params
  and returns `{"items": [...], "total": N, "limit": N, "offset": N}`.

## 11. Important enums

| Enum | Values |
|---|---|
| `ProcessingStatus` (feedback) | `received`, `pending_analysis`, `analyzing`, `analyzed`, `needs_review`, `failed` |
| `SourceType` | `review`, `support_ticket`, `survey`, `social`, `email`, `interview`, `other` |
| `FeedbackCategory` | `bug`, `feature_request`, `ui_friction`, `performance`, `billing` |
| `RelationshipType` | `duplicate`, `related`, `distinct` |
| `ProductIssueStatus` | `open`, `resolved`, `dismissed` |
| `DecisionStatus` | `open`, `answered`, `archived` |
| `MatchMethod` | `affected_feature`, `title`, `affected_feature_and_title` |
| `EvidenceSufficiency` | `no_matches`, `insufficient`, `sufficient` |
| `GenerationStatus` (brief) | `completed`, `failed` |
| `IssueStabilityStatus` | `new`, `removed`, `no_change`, `strengthened`, `weakened`, `mixed` |
| `DecisionStabilityStatus` | `insufficient_history`, `stable`, `strengthened`, `weakened`, `mixed` |
| `ChangeEventType` | `supporting_fact_added`/`_removed`, `counter_fact_added`/`_removed`, `gap_added`/`_resolved`, `decision_level_gap_added`/`_resolved`, `trend_changed`, `match_relevance_changed`, `value_changed`, `issue_appeared`, `issue_disappeared` |
| `ChangeInterpretation` | `increased`, `decreased`, `changed`, `not_evaluated`, `newly_available`, `unavailable`, `newly_present`, `no_longer_present`, `appeared`, `disappeared`, `direction_reversed`, `transitioned` |

Full enum definitions (with docstrings explaining exactly when each value
applies) are always visible in `GET /docs`.

## 12. Error format

```json
{"error": {"code": "not_found", "message": "Decision 3fae... not found."}}
```

Always this shape, always this status-code-to-code mapping (see §10).
Validation errors (missing required field, wrong type) come from FastAPI
automatically as `422` with `error.code: "validation_error"`.

## 13. CORS / local frontend configuration

CORS is enabled with `CORS_ORIGINS` (comma-separated), defaulting to:

```
http://localhost:3000, http://127.0.0.1:3000, http://localhost:5173, http://127.0.0.1:5173
```

covering Create React App/Next.js (3000) and Vite (5173) out of the box —
run your frontend dev server on either and it just works. If you use a
different port, add it to `CORS_ORIGINS` in `.env` (comma-separated) and
restart the backend. This is never a wildcard `*` — only explicitly
configured origins are allowed.

## 14. Demo seed instructions

```bash
python scripts/seed_demo_data.py            # refuses if a Decision row already exists
python scripts/seed_demo_data.py --force     # only bypasses that refusal; never deletes existing rows itself
```

Deterministic and repeatable — populates multiple customers/tiers/sources,
a "Checkout" issue with a real engineered trend reversal and a resolved
gap, an issue that **disappears** between evaluations (marked resolved),
and an issue that **appears fresh** at the second evaluation. It attempts
a real Decision Brief only if `GEMINI_API_KEY`/`LLM_PROVIDER`/`LLM_MODEL`
are configured; everything else works without them. Full narrative and
design rationale are documented in the script's own module docstring —
worth reading if you want to understand exactly why each event in the demo
exists.

**Important operational note**: this project's tests run against a real
PostgreSQL database (a per-test transaction is rolled back, but that only
undoes what *that test* wrote — it does not hide already-committed rows).
Several existing tests use `"checkout"` as their own default fixture
value, so **running `pytest` while the demo data is seeded will produce
unrelated-looking test failures** (issue-matching collisions, not a real
bug). Either:
- run `pytest` first, seed the demo data after, and don't run `pytest`
  again until you truncate (`TRUNCATE feedback, decision RESTART IDENTITY
  CASCADE` cascades everything else), or
- point `DATABASE_URL` at a second, separate database for running tests.

The seed script never runs automatically (not imported by the app, no
startup hook) and refuses to run twice without `--force`.

## 15. Recommended frontend call sequence for the main demo

1. `GET /api/v1/decisions` — find (or `POST` to create) the demo Decision.
2. `GET /api/v1/decisions/{id}` — header info (question, keywords, status).
3. `GET /api/v1/decisions/{id}/evaluations/latest` — the evidence: matched
   issues, each with its supporting/counter/gap facts and match info.
4. For each matched issue: `GET /api/v1/issues/{issue_id}/signals` if you
   want the full underlying signal snapshot (frequency, trend, etc.)
   beyond what the evaluation already inlines.
5. `GET /api/v1/decisions/{id}/brief` (or `POST` to generate one if Gemini
   is configured) — the narrative, with its `citations` map for
   click-to-expand provenance.
6. `GET /api/v1/decisions/{id}/evaluations` — show there's evaluation
   history (the demo seed has exactly 2).
7. `GET /api/v1/decisions/{id}/stability` — the headline "has this
   changed?" answer, with the full `events` list.
8. `GET /api/v1/decisions/{id}/challenge` — the "Challenge My Decision"
   panel, pre-categorized for direct rendering.

Steps 3, 5, 7, and 8 are independent reads once the decision id is known —
fetch them in parallel if useful.

---

## Appendix: full endpoint list

```
GET  /                                                          liveness
GET  /api/v1/health                                             {"status": "ok"}
GET  /api/v1/health/db                                          DB connectivity check

POST /api/v1/feedback                                           ingest one item
GET  /api/v1/feedback                                            paginated/filterable list
POST /api/v1/feedback/bulk                                       CSV bulk ingest
GET  /api/v1/feedback/{id}                                       one item
GET  /api/v1/feedback/{id}/analysis                              existing analysis (read-only)
POST /api/v1/feedback/{id}/analyze                                run LLM analysis
POST /api/v1/feedback/{id}/embed                                  generate/reuse embedding
GET  /api/v1/feedback/{id}/similar                                preview semantic neighbors
POST /api/v1/feedback/{id}/semantic-analysis                      persist relationships

POST /api/v1/issues/discover                                     cluster into issues
GET  /api/v1/issues                                               paginated/filterable list
GET  /api/v1/issues/{id}                                          one issue + feedback_ids
POST /api/v1/issues/{id}/signals/recalculate                      compute signals (one issue)
GET  /api/v1/issues/{id}/signals                                  read signals
POST /api/v1/issues/signals/recalculate                           compute signals (all issues)

POST /api/v1/decisions                                           create
GET  /api/v1/decisions                                            paginated list
GET  /api/v1/decisions/{id}                                       one decision
POST /api/v1/decisions/{id}/evaluate                               new evaluation (append-only)
GET  /api/v1/decisions/{id}/evaluations                            evaluation history
GET  /api/v1/decisions/{id}/evaluations/latest                     latest evaluation, full detail
GET  /api/v1/decisions/{id}/evaluations/{evaluation_id}             one evaluation, full detail
POST /api/v1/decisions/{id}/brief                                   generate a grounded brief
GET  /api/v1/decisions/{id}/brief                                   latest completed brief
GET  /api/v1/decisions/{id}/brief/history                           all attempts
GET  /api/v1/decisions/{id}/brief/{brief_id}                        one specific brief
GET  /api/v1/decisions/{id}/stability                               latest-vs-second-latest comparison
GET  /api/v1/decisions/{id}/evaluations/{from_id}/compare/{to_id}    arbitrary-pair comparison
GET  /api/v1/decisions/{id}/challenge                                Challenge My Decision view
```
