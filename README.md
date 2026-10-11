# SignalForge — Feedback Intelligence Platform

> **From customer noise to decision-grade evidence.**

SignalForge (codename **Hackgenix**) is a Product Decision Intelligence platform. It is **not** a review summarizer or a generic AI chatbot. Customer feedback arrives through five ingestion channels, is structured by AI, clustered into **Product Issues**, measured with **deterministic signals**, and then used to answer questions like *"Should we prioritize fixing checkout?"* with a cited, auditable **Decision Brief**. A **Challenge My Decision** view shows exactly how the evidence has changed over time, and an **AI Analytics** page interprets the aggregate picture, with every claim citing the exact figure it rests on.

Every number in the UI traces back to real feedback. Every LLM-written sentence carries a citation. Every "what changed" claim is a structured, factual event, never an invented recommendation.

---

## Team

Built by **Team Mind Flayers**:

| Name |
|---|
| YD Nimal |
| S Hammad Rehan |
| R D Kirittin Kumar |
| Rahul Srivatsan |

---

## Table of Contents

1. [Core Principles](#core-principles)
2. [Features](#features)
3. [Architecture](#architecture)
4. [Tech Stack](#tech-stack)
5. [Repository Structure](#repository-structure)
6. [Getting Started](#getting-started)
7. [Configuration](#configuration)
8. [Demo Data](#demo-data)
9. [Ingestion Channels](#ingestion-channels)
10. [The Pipeline, Phase by Phase](#the-pipeline-phase-by-phase)
11. [Analytics](#analytics)
12. [API Reference](#api-reference)
13. [Frontend](#frontend)
14. [Testing](#testing)
15. [Database & Migrations](#database--migrations)
16. [Design Decisions & Limitations](#design-decisions--limitations)
17. [Roadmap](#roadmap)

---

## Core Principles

- **LLMs interpret; the backend calculates.** The LLM reads one feedback item in isolation (analysis), or explains an already-computed evidence bundle (decision briefs, analytics insights). It never computes frequency, trend, priority, or any aggregate.
- **No blended scores.** There is no single "priority score" or "stability score" anywhere. Signals, evidence quality, issue ranking and stability are exposed as transparent components.
- **Counter-evidence is not "positive feedback".** The schema can't represent satisfaction. Counter-evidence only means the case for an issue is weaker or less reliable than it first appears. Insufficient data becomes a **gap**, never a counter-argument.
- **Grounded generation.** Every claim in a Decision Brief cites a `citation_id`, and every claim in an Analytics Insight cites an `evidence_id`, both from the evidence the model was given. An unknown citation rejects the entire generation.
- **Append-only history where history matters.** Decision evaluations and briefs are never overwritten, so evidence drift can be compared over time.
- **One ingestion path.** REST, CSV, JSON, webhook and scheduled sync all converge on the same service, so deduplication behaves identically no matter how feedback arrives.
- **Full traceability.** Decision → evaluation → issue → cluster → original feedback.
- **Privacy-aware.** Nothing logs API keys or raw feedback text. Brief and insight prompts never contain raw feedback or embeddings.

---

## Features

| Area | What it does |
|---|---|
| **Ingestion (5 channels)** | CSV and JSON bulk upload, REST API, webhooks (Zendesk, survey, App Store, generic), scheduled/polled sync. All idempotent on `(source, external_id)` |
| **AI understanding** | Per-item structured analysis via Gemini: category, affected feature, severity (1–10), confidence, summary |
| **Embeddings** | 3072-dim Gemini embeddings stored in PostgreSQL with pgvector |
| **Semantic engine** | pgvector cosine-similarity retrieval; deterministic duplicate / related / distinct classification |
| **Issue discovery** | Connected-components clustering over the relationship graph, with a structured cohesion filter |
| **Signal engine** | Per-issue frequency, severity distribution, recency, trend, customer reach, source diversity, segment coverage, duplicate ratio, time distribution, evidence quality |
| **Decision evidence** | Keyword matching of decisions to issues, then supporting / counter / gap classification with full provenance |
| **Decision brief** | Grounded, cited LLM synthesis over stored evidence, with backend-authoritative `evidence_sufficiency` |
| **Decision stability** | Deterministic comparison of any two evaluations into a fixed vocabulary of change events |
| **Challenge My Decision** | Read-only, categorized view over stability events |
| **Analytics** | Deterministic overview, trends, category, source and issue-ranking endpoints, plus a grounded AI interpretation |
| **Frontend** | Next.js app: Overview, Raw Firehose, AI Analytics, Ingest Feedback, per-issue investigation workspace, light/dark theme |

---

## Architecture

```
INGESTION                     REST · CSV · JSON · Webhook · Scheduled sync  (one common service)
  → AI UNDERSTANDING          Phase 3A    Gemini structured analysis (per item)
  → EMBEDDINGS                Phase 3B-1  Gemini Embedding 2 + pgvector
  → SEMANTIC ENGINE           Phase 3B-2  duplicate / related detection (cosine similarity)
  → PRODUCT ISSUES            Phase 3B-3  connected-components clustering
  → SIGNAL ENGINE             Phase 3C    deterministic per-issue signals
  → ANALYTICS                             deterministic aggregates + grounded AI insight
  → EVIDENCE ANALYSIS         Phase 4A    supporting / counter / gap facts per decision
  → DECISION BRIEF            Phase 4B    grounded, cited LLM synthesis
  → DECISION STABILITY        Phase 4C    deterministic evaluation-to-evaluation comparison
  → CHALLENGE MY DECISION                 categorized view of stability events
```

**Planned (not implemented):** Priority Engine, Product Knowledge Base, Query Planner, Hybrid Retrieval, Human Review workflow, Audit Trail.

### Backend layering

```
app/api/        thin FastAPI routers (validation, status codes, response shaping)
app/schemas/    Pydantic request/response models + LLM output contracts
app/services/   all business logic
  llm/          LLMProvider Protocol + Gemini implementation + factory
  embedding/    EmbeddingProvider Protocol + Gemini implementation + factory
  connectors/   FeedbackSourceConnector abstraction + demo connectors (scheduled sync)
app/db/models/  SQLAlchemy ORM models
app/core/       config, logging, error hierarchy
```

The app depends only on the `LLMProvider` and `EmbeddingProvider` Protocols. Nothing outside the Gemini provider files imports the Gemini SDK, so adding OpenAI or Ollama is a new class plus a factory branch.

---

## Tech Stack

**Backend**
- Python, **FastAPI**, Uvicorn
- **SQLAlchemy 2.x**, **Alembic**
- **PostgreSQL** + **pgvector** (SQLite is intentionally not supported)
- **Pydantic v2** / pydantic-settings
- **Google Gemini** (`google-genai`) for analysis, embeddings, briefs and insights
- **pytest**, httpx

**Frontend**
- **Next.js 16** (App Router), **React 19**, TypeScript
- **Tailwind CSS v4**, shadcn/ui (base-ui primitives), lucide-react
- Fraunces (display serif), Inter, IBM Plex Mono
- Warm editorial theme: light by default, dark mode via toggle (persisted, no flash on reload)

---

## Repository Structure

```
hackgenix-backend/
├── alembic/versions/            8 migrations: feedback → analysis → embedding → relationship →
│                                issue → signal snapshot → decision evidence → decision brief (head)
├── app/
│   ├── api/                     feedback, ingestion, issues, decisions, analytics, health, router
│   ├── core/                    config.py, errors.py, logging.py
│   ├── db/models/               Feedback, FeedbackAnalysis, FeedbackEmbedding, FeedbackRelationship,
│   │                            ProductIssue, ProductIssueFeedback, ProductIssueSignalSnapshot,
│   │                            Decision, DecisionEvaluation, DecisionIssueEvidence, DecisionBrief
│   ├── schemas/                 request/response + LLM contracts (incl. ingestion, analytics, analytics_insight)
│   ├── services/                feedback_ingestion, feedback_sync, webhook_normalization,
│   │                            feedback_analysis, feedback_embedding, semantic_engine,
│   │                            issue_discovery, issue_signals, analytics, analytics_insight,
│   │                            decision_matching, decision_evidence, decision_brief,
│   │                            decision_stability, decision_challenge,
│   │                            connectors/, llm/, embedding/
│   └── main.py
├── docs/
│   ├── architecture.md          phase-by-phase design write-up
│   └── frontend-integration.md  API contract + demo walkthrough
├── scripts/
│   ├── seed_demo_data.py        deterministic demo dataset
│   ├── manual_gemini_check.py   optional real-Gemini smoke check
│   └── manual_embedding_check.py
├── tests/                       pytest suite (fakes for every LLM/embedding call)
├── .env.example · alembic.ini · pytest.ini · requirements.txt

hackgenix-frontend/
├── app/
│   ├── page.tsx                 Overview — product command center
│   ├── firehose/page.tsx        Raw Firehose — live signal stream + process action
│   ├── analytics/page.tsx       AI Analytics — report-style page
│   ├── ingest/page.tsx          Ingest Feedback — all five channels
│   ├── layout.tsx, globals.css  shell, theme tokens
├── components/ui/               Sidebar, MobileNav, ThemeProvider/Toggle, IssueRow, DashboardCharts,
│                                DecisionBrief (investigation workspace), ProcessFeedbackPanel,
│                                PipelineStepper, PageHeader, shadcn primitives
├── lib/                         api.ts (fully typed client), format.ts, utils.ts
├── .env.local.example · package.json
```

---

## Getting Started

### Prerequisites

- Python 3.11+
- Node.js 20+
- PostgreSQL with the **pgvector** extension available to the server
- A Gemini API key *(optional; only needed for analysis, embeddings, briefs and AI insights)*

### 1. Backend

```bash
cd hackgenix-backend
python -m venv venv
venv\Scripts\activate              # Windows; use `source venv/bin/activate` on macOS/Linux
pip install -r requirements.txt

copy .env.example .env             # then edit DATABASE_URL at minimum
alembic upgrade head               # creates the schema (also runs CREATE EXTENSION vector)
uvicorn main:app --reload          # http://localhost:8000
```

- Interactive OpenAPI docs: `http://localhost:8000/docs`
- Liveness: `GET /` · Health: `GET /api/v1/health` · DB check: `GET /api/v1/health/db`

### 2. Seed demo data (recommended)

```bash
python scripts/seed_demo_data.py
```

### 3. Frontend

```bash
cd hackgenix-frontend
npm install
copy .env.local.example .env.local   # optional; defaults to http://localhost:8000
npm run dev                          # http://localhost:3000
```

The backend's default CORS configuration already allows `localhost:3000` and `:5173`.

---

## Configuration

Set in `hackgenix-backend/.env`:

| Variable | Required | Notes |
|---|---|---|
| `DATABASE_URL` | **yes** | PostgreSQL, e.g. `postgresql+psycopg://user:pass@localhost:5432/hackgenix` |
| `APP_ENV` / `DEBUG` / `LOG_LEVEL` | no | defaults: `development` / `false` / `INFO` |
| `LLM_PROVIDER` | for AI features | only `gemini` implemented |
| `LLM_MODEL` | for AI features | Gemini model name |
| `GEMINI_API_KEY` | for AI features | shared by LLM and embedding providers; never logged |
| `LLM_CONFIDENCE_REVIEW_THRESHOLD` | no | default `0.5`; below this, analysis routes to `needs_review` |
| `LLM_TIMEOUT_SECONDS` | no | default `30` |
| `EMBEDDING_PROVIDER` / `EMBEDDING_MODEL` | for `/embed` | `gemini` / e.g. `gemini-embedding-2` |
| `CORS_ORIGINS` | no | comma-separated; defaults to common local dev ports; never `*` |
| `TEST_DATABASE_URL` | no | overrides the auto-derived test database (see [Testing](#testing)) |

Frontend (`hackgenix-frontend/.env.local`): `NEXT_PUBLIC_API_BASE_URL` (default `http://localhost:8000`).

Everything except `DATABASE_URL` is optional for running the app. Without Gemini configured, ingestion, listing, signals, evidence, stability and the deterministic analytics all work; only analysis, embeddings, briefs and AI insights need it.

---

## Demo Data

`scripts/seed_demo_data.py` builds a realistic, repeatable story using the real services wherever possible:

- Multiple customers, tiers, sources, and severities.
- **Decision:** *"Should we prioritize fixing checkout?"*
- **Evaluation 1** (partial evidence): a "Checkout" issue with real gaps, plus a strong "Checkout timeout" issue.
- New feedback arrives; evidence grows and shifts.
- **Evaluation 2** produces a genuinely mixed result:
  - a gap resolves,
  - a trend **reverses** from increasing to decreasing (new counter-evidence),
  - the "Checkout timeout" issue is resolved and **disappears**,
  - a new "Checkout international card declined" issue **appears**.
- If Gemini is configured, a grounded Decision Brief is generated for Evaluation 2.

The script refuses to run if a `decision` row already exists unless `--force` is passed (and never deletes existing rows). It is never run automatically by the app.

---

## Ingestion Channels

A company should never have to type thousands of records by hand. All channels call the same `create_or_get_feedback` service, so a record is deduplicated identically however it arrived, and each new row stores its channel in `metadata.ingestion_method`.

| Channel | Endpoint | Notes |
|---|---|---|
| **REST** | `POST /api/v1/feedback` | `201` if created, `200` if the `(source, external_id)` pair already exists |
| **CSV bulk** | `POST /api/v1/feedback/bulk` | required columns `source, raw_text`; up to 5000 rows; bad rows reported, never abort the import |
| **JSON bulk** | `POST /api/v1/feedback/bulk/json` | top-level array of objects shaped like the REST payload; same resilience and limits |
| **Webhook** | `POST /api/v1/ingestion/webhook/{source}` | sources: `zendesk`, `survey`, `app_store`, `generic`; provider payloads are normalized, then ingested. Response includes explicit `status: created \| duplicate` |
| **Scheduled sync** | `POST /api/v1/ingestion/sync/{source}` | sources: `demo` (simulated partner REST API), `csv_drop` (simulated shared CSV). Returns fetched/new/duplicates/failed counts |

Notes:
- Sync is **manually or externally triggered**. Nothing runs on a background timer inside the app; a real deployment points an external cron job at the sync endpoint. Running the demo sync twice shows "all new" then "all duplicates".
- Adding a webhook provider means one normalizer function and one registry entry. Adding a sync source means implementing `FeedbackSourceConnector` and registering it.
- Ingestion only stores records (`received`). Analysis and everything downstream is a separate, explicit step (the **Process** action in the Raw Firehose).
- Unknown webhook/sync sources return `404 ingestion_source_not_supported`; a connector failure returns `502 ingestion_connector_error`.

---

## The Pipeline, Phase by Phase

### Phase 3A: Structured Analysis
`POST /feedback/{id}/analyze` → Gemini returns strictly validated JSON: `category` (bug, feature_request, ui_friction, performance, billing), `affected_feature`, `severity` 1–10, `confidence` 0–1, `summary`. Status moves `analyzing → analyzed` (or `needs_review` for low confidence, `failed` on error).

### Phase 3B-1: Embeddings
`POST /feedback/{id}/embed` stores a 3072-dim vector in pgvector. Idempotent; the raw vector is never returned over the API.

### Phase 3B-2: Semantic Engine
Cosine-similarity nearest-neighbor search runs **inside Postgres**. A deterministic classifier labels pairs: similarity ≥ 0.93 → `duplicate`, ≥ 0.75 → `related`, else `distinct`. Structured analysis can only **downgrade** (category mismatch, different feature, severity gap ≥ 5), never upgrade. Each unordered pair is stored once; only `duplicate`/`related` are persisted.

### Phase 3B-3: Product Issue Discovery
Union-find connected components over the relationship graph, plus a category-majority cohesion filter to peel off chained false merges. Issues are **evidence containers**, not scores. Reruns are idempotent and additive; membership only grows.

### Phase 3C: Signal Engine
All aggregation happens in SQL. One overwritten snapshot per issue, versioned (`signal-v1`):

| Signal | Content |
|---|---|
| Frequency | linked feedback count |
| Severity | full histogram, buckets (low/medium/high/critical), mean, median, analyzed vs. unanalyzed |
| Recency | oldest/latest feedback, days since |
| Trend | two adjacent 7-day windows; explicit `insufficient_data` below 4 samples |
| Customer reach | distinct known customers; anonymous feedback counted separately |
| Source diversity | distinct sources + distributions |
| Segment coverage | customer-tier distribution |
| Duplicate ratio | internal duplicate vs. related pairs |
| Time distribution | sparse weekly histogram (UTC-normalized) |
| Evidence quality | transparent component flags, no blended score |

### Phase 4A: Decision Evidence Engine
Decisions carry explicit keywords (never LLM-extracted). Matching uses `affected_feature` (exact 1.0, substring 0.7), title token overlap (×0.5) and a category boost (+0.15, never standalone). `match_score` is a relevance ranking, **not** a probability. Matched issues' signals are classified into **supporting**, **counter** and **gap** facts, each as `{code, label, value, threshold, source_signal_path}`. With confidence gating (`MIN_VOLUME_FOR_JUDGEMENT = 5`), the same weak signal is counter-evidence with enough volume and a gap without it. Evaluations are **append-only**.

### Phase 4B: Grounded Decision Brief
The LLM receives only persisted Phase 4A facts, each with a deterministic `citation_id`, and returns a headline plus supporting/counter/gap claims, each citing facts. Any unknown citation fails the whole generation (`llm_ungrounded_output`). `evidence_sufficiency` (`no_matches` / `insufficient` / `sufficient`) is computed by the backend **before** the LLM runs and is authoritative. Briefs are append-only; failed attempts are recorded without narrative.

### Phase 4C: Decision Stability
A pure, deterministic comparison of two evaluations (no new table, no LLM). Output is a fixed vocabulary of change events (`supporting_fact_added`, `counter_fact_added`, `gap_resolved`, `trend_changed`, `issue_appeared`, `issue_disappeared`, `value_changed`, …) with factual interpretations (`increased`, `direction_reversed`, …). Status rollups (`stable`, `strengthened`, `weakened`, `mixed`, `insufficient_history`) are convenience only; `events` is authoritative. **`stable` never means "the decision is correct."**

### Challenge My Decision
`GET /decisions/{id}/challenge` regroups the same stability events into UI-ready buckets with issue titles resolved. No ranking, score, or verdict exists in the response.

---

## Analytics

Two layers, deliberately separate.

**Deterministic (no LLM)**, computed in PostgreSQL by `app/services/analytics.py`:

| Endpoint | Returns |
|---|---|
| `GET /analytics/overview` | totals, analysis coverage, average/high severity, unique customers, source-type and category distributions, issue volume, whole-corpus trend, per-issue trends, top issues |
| `GET /analytics/trends?days=` | one point per calendar day (feedback, analyzed, high-severity); `sufficient_history: false` plus a message instead of a misleading line when data is thin |
| `GET /analytics/categories` | every known category with a real (possibly zero) count |
| `GET /analytics/sources` | per-source feedback and analyzed counts |
| `GET /analytics/issues?limit=` | issues ranked by a transparent multi-key sort (feedback count, then average severity, then known customers), returned with every component metric |

**AI interpretation**, `POST /analytics/insights`:
- Builds a deterministic evidence bundle (facts with ids like `analytics:category:bug` or `analytics:issue:<id>:trend`) and asks Gemini to interpret it.
- Output sections: headline, key findings, emerging patterns, areas to investigate, caveats. There is no recommendation, priority or score field.
- Every claim must cite real `evidence_ids`; an unknown id rejects the whole response. Raw feedback text is never sent.
- Stateless: nothing is persisted. If the LLM fails, the deterministic analytics are unaffected.

---

## API Reference

Base path: `/api/v1`. All lists take `limit`/`offset` and return `{items, total, limit, offset}`. Errors always use `{"error": {"code", "message"}}`.

### Feedback
```
POST /feedback                         ingest one item (REST)
GET  /feedback                         list (filters: source, source_type, status)
POST /feedback/bulk                    CSV upload
POST /feedback/bulk/json               JSON array upload
GET  /feedback/{id}
GET  /feedback/{id}/analysis           404 analysis_missing if not analyzed
POST /feedback/{id}/analyze
POST /feedback/{id}/embed
GET  /feedback/{id}/similar            preview, persists nothing
POST /feedback/{id}/semantic-analysis  persist relationships
```

### Ingestion
```
POST /ingestion/webhook/{source}       zendesk | survey | app_store | generic
POST /ingestion/sync/{source}          demo | csv_drop
```

### Issues
```
POST /issues/discover
GET  /issues                           filters: status, category
GET  /issues/{id}                      includes feedback_ids
POST /issues/{id}/signals/recalculate
GET  /issues/{id}/signals              404 signals_missing if never calculated
POST /issues/signals/recalculate       all issues
```

### Decisions
```
POST /decisions
GET  /decisions
GET  /decisions/{id}
POST /decisions/{id}/evaluate                      new append-only evaluation
GET  /decisions/{id}/evaluations
GET  /decisions/{id}/evaluations/latest
GET  /decisions/{id}/evaluations/{evaluation_id}
POST /decisions/{id}/brief                         needs Gemini configured
GET  /decisions/{id}/brief                         latest completed
GET  /decisions/{id}/brief/history                 includes failed attempts
GET  /decisions/{id}/brief/{brief_id}
GET  /decisions/{id}/stability                     latest vs. second-latest
GET  /decisions/{id}/evaluations/{from}/compare/{to}
GET  /decisions/{id}/challenge
```

### Analytics
```
GET  /analytics/overview
GET  /analytics/trends?days=
GET  /analytics/categories
GET  /analytics/sources
GET  /analytics/issues?limit=
POST /analytics/insights                           needs Gemini configured
```

### Common error codes
`not_found`, `validation_error`, `signals_missing`, `analysis_missing`, `embedding_missing`, `ingestion_source_not_supported` (404), `ingestion_connector_error` (502), `llm_configuration_error` (503), `llm_provider_error` (502), `llm_output_validation_error` (502), `llm_ungrounded_output` (502), `database_error` (503).

For the full contract (enums, conventions, recommended call sequence) see `hackgenix-backend/docs/frontend-integration.md`; for design rationale see `hackgenix-backend/docs/architecture.md`.

---

## Frontend

| Route | Description |
|---|---|
| `/` **Overview** | Product command center: a dominant feedback-signal count, secondary metrics, a customer-signal trend chart, a "what needs attention" list (the backend's own ranking, filtered to open issues), the product-issue index with status/category filters, and a signal-mix panel. Selecting an issue opens its investigation workspace |
| `/firehose` **Raw Firehose** | Live signal stream with source, source-type and status filters. Each `received` or `failed` row has a **Process** / **Retry** action, plus an ingest form and pipeline tools (issue discovery, signal recalculation) |
| `/analytics` **AI Analytics** | Report-style page: executive summary, 30-day signal activity, category and source signals, ranked product-issue intelligence, and a deliberately contrasting **AI Analysis** panel whose claims each show clickable evidence chips |
| `/ingest` **Ingest Feedback** | All channels in one place: bulk CSV/JSON upload with result stats, a REST example, a webhook console (copyable endpoint, example payloads per provider, **Send Test Event**, flow diagram) and a scheduled-sync runner |

**Investigation workspace** (side panel for any issue): a pipeline stepper (Raw signals → Product issues → Evidence → Decision → Challenge/stability), headline metrics, signals with an optional full snapshot, the AI decision headline, supporting/counter/gap evidence with expandable citations, a two-point **Stability** timeline, the **Challenge** change log, and raw source records. It reuses an existing Decision whose keywords match the issue, so a decision's real evaluation history is preserved rather than restarted; it only auto-evaluates the first time a decision is opened.

**Process feedback panel**: sequences the existing endpoints for one item (analyze → embed → find relationships → discover issues → recalculate signals), shows each step's real status, is safe to retry, and links the result back to its product issue.

**UI notes**: warm editorial light theme by default with a persisted dark toggle; collapsible sidebar and a compact mobile nav; graceful degradation everywhere (a missing Gemini key shows the underlying deterministic facts instead). No metric is ever fabricated client-side.

---

## Testing

```bash
cd hackgenix-backend
pytest
```

**Isolated test database.** `tests/conftest.py` derives `<your db name>_test` on the same server (or uses `TEST_DATABASE_URL`), creates it if missing, and applies the real Alembic migrations to it, before the app is imported. Tests therefore never touch your development database or its demo data, and the old "don't run pytest while demo data is seeded" caveat no longer applies. Each test additionally runs inside a rolled-back transaction.

The suite covers startup and health, config validation, all five ingestion channels (including dedup parity across them and connector failure handling), LLM and embedding providers, the semantic engine (deterministic unit vectors against real pgvector), issue discovery, signals, analytics (empty-database behavior, real counts, ranking, insight grounding and failure isolation), decision evidence, briefs, stability, challenge, and one end-to-end HTTP workflow test.

**No default test calls Gemini.** `tests/fakes.py` provides `FakeLLMProvider` / `FakeEmbeddingProvider`. An opt-in real-API smoke test is excluded by default (`addopts = -m "not gemini_smoke"`):

```bash
pytest -m gemini_smoke tests/test_decision_brief_gemini_smoke.py
```

Manual credential checks (not collected by pytest):

```bash
python scripts/manual_gemini_check.py "The app crashes when I upload a large photo."
python scripts/manual_embedding_check.py "The app crashes when I upload a large photo."
```

---

## Database & Migrations

`alembic/env.py` reads `DATABASE_URL` from the app's own `Settings` and targets `Base.metadata`, giving a single source of truth.

```bash
alembic upgrade head
alembic revision --autogenerate -m "message"
```

Migration chain: `feedback` → `feedback_analysis` → `feedback_embedding` → `feedback_relationship` → `product_issue` → `product_issue_signal_snapshot` → decision evidence tables → `decision_brief` (head). Ingestion channels, analytics and stability add no tables.

---

## Design Decisions & Limitations

- **Connected components instead of HDBSCAN.** Reuses the already-computed relationship graph (O(E·α(n))) rather than O(n²) pairwise distances. HDBSCAN remains an option at larger scale.
- **No ANN index yet.** Exact pgvector scans are fine at demo scale; add HNSW once `feedback_embedding` nears ~10k–50k rows.
- **Keyword-only decision matching.** Exact/substring only (no stemming, fuzzy or semantic matching) because there are no issue-level embeddings yet.
- **Fixed, unvalidated thresholds.** Similarity cutoffs, signal windows, evidence thresholds and analytics constants are hackathon-scale values, version-stamped so stored rows identify the logic that produced them.
- **Citation validation is mechanical.** It proves a claim points at a real supplied fact, not that every word is entailed by it (mitigated by length caps and strict prompting). This applies to both briefs and analytics insights.
- **Demo connectors only.** The `demo` and `csv_drop` sync sources return fixed synthetic records; real connectors implement `FeedbackSourceConnector` and track their own high-water mark.
- **Webhooks are unauthenticated.** There is no signature verification or API-key check on ingestion endpoints yet.
- **Historical issue metadata isn't pinned.** `DecisionIssueEvidence` doesn't snapshot issue title/category, so issue display fields reflect current state.
- **No population denominator.** Nothing is expressed as a percentage of all customers.
- **Anonymous feedback** is never assumed to map to distinct people.
- **Items without an `external_id`** can't be deduplicated by the database (Postgres `NULL ≠ NULL`).
- **Synchronous generation.** No background-job infrastructure exists yet.
- **No retention policy** for append-only evaluation and brief history.

---

## Roadmap

- Priority Engine (explainable, deterministic scoring consuming the existing signal components)
- Human review workflow (correct AI classifications, exclude false-positive matches)
- Audit trail
- Authenticated webhooks and real third-party connectors
- Semantic / hybrid decision matching with issue-level embeddings
- Configurable thresholds and windows
- Background processing and automatic signal recalculation
- Additional LLM/embedding providers (OpenAI, Ollama)

---

*Built for a hackathon by Team Mind Flayers: YD Nimal, S Hammad Rehan, R D Kirittin Kumar, and Rahul Srivatsan.*
