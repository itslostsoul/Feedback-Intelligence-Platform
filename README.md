# Hackgenix — Feedback Intelligence Platform

> **Turning messy human feedback into evidence-backed product decisions.**

Hackgenix is a Product Decision Intelligence system. It is **not** a review summarizer or a generic AI chatbot. Feedback is ingested once into a persistent knowledge base, structured and clustered into **Product Issues**, measured with **deterministic signals**, and then used to answer questions like *"Should we prioritize fixing checkout?"* with a cited, auditable **Decision Brief**, plus a **Challenge My Decision** view that shows exactly how the evidence has changed over time.

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
3. [Architecture Overview](#architecture-overview)
4. [Tech Stack](#tech-stack)
5. [Repository Structure](#repository-structure)
6. [Getting Started](#getting-started)
7. [Configuration](#configuration)
8. [Demo Data](#demo-data)
9. [The Pipeline, Phase by Phase](#the-pipeline-phase-by-phase)
10. [API Reference](#api-reference)
11. [Frontend](#frontend)
12. [Testing](#testing)
13. [Database & Migrations](#database--migrations)
14. [Design Decisions & Limitations](#design-decisions--limitations)
15. [Roadmap](#roadmap)

---

## Core Principles

- **LLMs interpret; the backend calculates.** The LLM only ever reads one feedback item in isolation (Phase 3A) or explains already-computed evidence (Phase 4B). It never computes frequency, trend, priority, or any aggregate.
- **No blended scores.** There is no single "priority score" or "stability score" anywhere. Signals, evidence quality, and stability are exposed as transparent components.
- **Counter-evidence is not "positive feedback".** The schema can't represent satisfaction. Counter-evidence only means the case for an issue is weaker or less reliable than it first appears. Insufficient data becomes a **gap**, never a counter-argument.
- **Grounded generation.** Every claim in a Decision Brief must cite a `citation_id` from the evidence it was given. Any unknown citation rejects the entire generation.
- **Append-only history where history matters.** Decision evaluations and briefs are never overwritten, so evidence drift can be compared over time.
- **Full traceability.** Decision → evaluation → issue → cluster → original feedback.
- **Privacy-aware.** Nothing logs API keys or raw feedback text. The brief prompt never contains raw feedback or embeddings.

---

## Features

| Area | What it does |
|---|---|
| **Ingestion** | Single-item and bulk CSV ingestion, idempotent on `(source, external_id)`, row-level validation |
| **AI understanding** | Per-item structured analysis via Gemini: category, affected feature, severity (1–10), confidence, summary |
| **Embeddings** | 3072-dim Gemini embeddings stored in PostgreSQL with pgvector |
| **Semantic engine** | pgvector cosine-similarity candidate retrieval; deterministic duplicate / related / distinct classification |
| **Issue discovery** | Connected-components clustering over the relationship graph, with a structured cohesion filter |
| **Signal engine** | Deterministic per-issue signals: frequency, severity distribution, recency, trend, customer reach, source diversity, segment coverage, duplicate ratio, time distribution, evidence quality |
| **Decision evidence** | Keyword matching of decisions to issues, then classification into **supporting / counter / gap** facts with full provenance |
| **Decision brief** | Grounded, cited LLM synthesis over stored evidence, with backend-authoritative `evidence_sufficiency` |
| **Decision stability** | Deterministic comparison of any two evaluations into a fixed vocabulary of change events |
| **Challenge My Decision** | Read-only, categorized view over stability events (new/disappeared issues, trend reversals, gaps resolved, etc.) |
| **Frontend** | Next.js dashboard: Impact Grid, Raw Firehose, per-issue Decision Brief panel |

---

## Architecture Overview

```
INGESTION                     Phase 2    POST /feedback, /feedback/bulk
  → AI UNDERSTANDING          Phase 3A   Gemini structured analysis (per item)
  → EMBEDDINGS                Phase 3B-1 Gemini Embedding 2 + pgvector
  → SEMANTIC ENGINE           Phase 3B-2 duplicate / related detection (cosine similarity)
  → PRODUCT ISSUES            Phase 3B-3 connected-components clustering
  → SIGNAL ENGINE             Phase 3C   deterministic per-issue signals
  → EVIDENCE ANALYSIS         Phase 4A   supporting / counter / gap facts per decision
  → DECISION BRIEF            Phase 4B   grounded, cited LLM synthesis
  → DECISION STABILITY        Phase 4C   deterministic evaluation-to-evaluation comparison
  → CHALLENGE MY DECISION                categorized view of stability events
```

**Planned (not implemented):** Priority Engine, Product Knowledge Base, Query Planner, Hybrid Retrieval, Human Review workflow, Audit Trail.

### Backend layering

```
app/api/        thin FastAPI routers (validation, status codes, response shaping)
app/schemas/    Pydantic request/response + LLM output contracts
app/services/   all business logic (ingestion, analysis, semantic engine, signals, decisions...)
app/services/llm/        LLMProvider Protocol + Gemini implementation + factory
app/services/embedding/  EmbeddingProvider Protocol + Gemini implementation + factory
app/db/models/  SQLAlchemy ORM models
app/core/       config, logging, error hierarchy
```

The app depends only on the `LLMProvider` / `EmbeddingProvider` Protocols. Nothing outside the Gemini provider files imports the Gemini SDK, so adding OpenAI or Ollama is a new class plus a factory branch.

---

## Tech Stack

**Backend**
- Python, **FastAPI**, Uvicorn
- **SQLAlchemy 2.x**, **Alembic**
- **PostgreSQL** + **pgvector** (SQLite is intentionally not supported)
- **Pydantic v2** / pydantic-settings
- **Google Gemini** (`google-genai`) for analysis, embeddings, and brief synthesis
- **pytest**, httpx

**Frontend**
- **Next.js 16** (App Router), **React 19**, TypeScript
- **Tailwind CSS v4**, shadcn/ui (base-ui primitives), lucide-react
- IBM Plex Mono + Inter, dark "signal console" theme

---

## Repository Structure

```
hackgenix-backend/
├── alembic/                    migrations (env.py uses the app's own Settings + metadata)
│   └── versions/               feedback → analysis → embedding → relationship → issue →
│                               signal snapshot → decision evidence → decision brief
├── app/
│   ├── api/                    feedback.py, issues.py, decisions.py, health.py, router.py
│   ├── core/                   config.py, errors.py, logging.py
│   ├── db/models/              Feedback, FeedbackAnalysis, FeedbackEmbedding, FeedbackRelationship,
│   │                           ProductIssue, ProductIssueFeedback, ProductIssueSignalSnapshot,
│   │                           Decision, DecisionEvaluation, DecisionIssueEvidence, DecisionBrief
│   ├── schemas/                request/response + LLM contracts
│   ├── services/               feedback_ingestion, feedback_analysis, feedback_embedding,
│   │                           semantic_engine, issue_discovery, issue_signals,
│   │                           decision_matching, decision_evidence, decision_brief,
│   │                           decision_stability, decision_challenge, llm/, embedding/
│   └── main.py
├── docs/
│   ├── architecture.md         full phase-by-phase design write-up
│   └── frontend-integration.md API contract + demo walkthrough
├── scripts/
│   ├── seed_demo_data.py       deterministic demo dataset
│   ├── manual_gemini_check.py  optional real-Gemini smoke check
│   └── manual_embedding_check.py
├── tests/                      pytest suite (fakes for all LLM/embedding calls)
├── .env.example
├── alembic.ini
├── pytest.ini
└── requirements.txt

hackgenix-frontend/
├── app/
│   ├── page.tsx                Impact Grid (issues table + decision brief sheet)
│   ├── firehose/page.tsx       Raw feedback list, ingestion form, pipeline tools
│   ├── layout.tsx, globals.css
├── components/ui/              shadcn components, Sidebar, DecisionBrief
├── lib/
│   ├── api.ts                  fully typed API client
│   ├── format.ts               label/format helpers
│   └── utils.ts
└── package.json
```

---

## Getting Started

### Prerequisites

- Python 3.11+
- Node.js 20+
- PostgreSQL with the **pgvector** extension available to the server (most managed providers ship it; self-managed installs may need to add it once)
- A Gemini API key (optional, only needed for `/analyze`, `/embed`, and `POST /decisions/{id}/brief`)

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

See [Demo Data](#demo-data) for what this creates.

### 3. Frontend

```bash
cd hackgenix-frontend
npm install
npm run dev                        # http://localhost:3000
```

The backend's default CORS configuration already allows `http://localhost:3000` and `:5173`. To point the UI at a different backend, set `NEXT_PUBLIC_API_BASE_URL` in `.env.local` (defaults to `http://localhost:8000`).

---

## Configuration

Set in `hackgenix-backend/.env`:

| Variable | Required | Notes |
|---|---|---|
| `DATABASE_URL` | **yes** | PostgreSQL, e.g. `postgresql+psycopg://user:pass@localhost:5432/hackgenix` |
| `APP_ENV` | no | default `development` |
| `DEBUG` | no | default `false` |
| `LOG_LEVEL` | no | default `INFO` |
| `LLM_PROVIDER` | for analysis/brief | only `gemini` implemented |
| `LLM_MODEL` | for analysis/brief | Gemini model name |
| `GEMINI_API_KEY` | for analysis/brief/embedding | shared by LLM and embedding providers; never logged |
| `LLM_CONFIDENCE_REVIEW_THRESHOLD` | no | default `0.5`; below this, analysis routes to `needs_review` |
| `LLM_TIMEOUT_SECONDS` | no | default `30` |
| `EMBEDDING_PROVIDER` | for `/embed` | only `gemini` implemented |
| `EMBEDDING_MODEL` | for `/embed` | e.g. `gemini-embedding-2` |
| `CORS_ORIGINS` | no | comma-separated; defaults to common local dev ports; never `*` |

Frontend: `NEXT_PUBLIC_API_BASE_URL` (optional).

Everything except `DATABASE_URL` is optional for running the app. **No test requires Gemini credentials.**

---

## Demo Data

`scripts/seed_demo_data.py` builds a realistic, repeatable story across every phase, using the real services (analysis, discovery, signals, evaluation) wherever possible:

- Multiple customers, tiers, sources, and severities.
- **Decision:** *"Should we prioritize fixing checkout?"*
- **Evaluation 1** (partial evidence): a "Checkout" issue with real gaps, plus a strong "Checkout timeout" issue.
- New feedback arrives; evidence grows and shifts.
- **Evaluation 2** produces a genuinely mixed result:
  - a previously-insufficient gap resolves,
  - a trend **reverses** from increasing to decreasing (new counter-evidence),
  - the "Checkout timeout" issue is resolved and **disappears**,
  - a new "Checkout international card declined" issue **appears**.
- If Gemini is configured, a grounded Decision Brief is generated for Evaluation 2.

The script refuses to run if a `decision` row already exists unless `--force` is passed (and never deletes existing rows). It is never run automatically by the app.

> **Heads-up:** tests run against the real database, and several tests use `"checkout"` as a fixture value. Running `pytest` while demo data is seeded can cause unrelated-looking failures. Either run tests first and seed after, or point `DATABASE_URL` at a separate database when testing.

---

## The Pipeline, Phase by Phase

### Phase 2: Feedback Ingestion
`POST /feedback` (201 created / 200 duplicate), `POST /feedback/bulk` (CSV, up to 5000 rows, bad rows skipped and reported), paginated/filterable listing. Idempotent on `(source, external_id)`. Items enter `received`.

### Phase 3A: Structured Analysis
`POST /feedback/{id}/analyze` → Gemini returns strictly validated JSON: `category` (bug, feature_request, ui_friction, performance, billing), `affected_feature`, `severity` 1–10, `confidence` 0–1, `summary`. Status moves `analyzing → analyzed` (or `needs_review` for low confidence, `failed` on error).

### Phase 3B-1: Embeddings
`POST /feedback/{id}/embed` stores a 3072-dim vector in pgvector. Idempotent; the raw vector is never returned over the API.

### Phase 3B-2: Semantic Engine
Cosine-similarity nearest-neighbor search runs **inside Postgres**. A deterministic classifier labels pairs:
- similarity ≥ 0.93 → `duplicate`, ≥ 0.75 → `related`, else `distinct`
- structured analysis can only **downgrade** (category mismatch, different feature, severity gap ≥ 5), never upgrade

Each unordered pair is stored once; only `duplicate`/`related` are persisted.

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
Decisions carry explicit keywords (never LLM-extracted). Matching uses `affected_feature` (exact 1.0, substring 0.7), title token overlap (×0.5), and a category boost (+0.15, never standalone). `match_score` is a relevance ranking, **not** a probability. Matched issues' signals are classified into **supporting**, **counter**, and **gap** facts, each with `{code, label, value, threshold, source_signal_path}`. Confidence gating (`MIN_VOLUME_FOR_JUDGEMENT = 5`): the same weak signal is counter-evidence with enough volume, and a gap without it. Evaluations are **append-only**.

### Phase 4B: Grounded Decision Brief
The LLM receives only persisted Phase 4A facts, each with a deterministic `citation_id`. It returns a headline plus supporting/counter/gap narrative claims, each citing facts. Any unknown citation fails the whole generation (`llm_ungrounded_output`). `evidence_sufficiency` (`no_matches` / `insufficient` / `sufficient`) is computed by the backend **before** the LLM runs and is authoritative. Briefs are append-only; failed attempts are recorded without narrative.

### Phase 4C: Decision Stability
A pure, deterministic comparison of two evaluations (no new table, no LLM). Output is a fixed vocabulary of `ChangeEvent`s (`supporting_fact_added`, `counter_fact_added`, `gap_resolved`, `trend_changed`, `issue_appeared`, `issue_disappeared`, `value_changed`, ...) with factual interpretations (`increased`, `direction_reversed`, ...). Status rollups (`stable`, `strengthened`, `weakened`, `mixed`, `insufficient_history`) are convenience only; `events` is authoritative. **`stable` never means "the decision is correct."**

### Challenge My Decision
`GET /decisions/{id}/challenge` regroups the same stability events into UI-ready buckets with issue titles resolved. No ranking, score, or verdict exists in the response.

---

## API Reference

Base path: `/api/v1`. All lists take `limit`/`offset` and return `{items, total, limit, offset}`. Errors always use `{"error": {"code", "message"}}`.

### Feedback
```
POST /feedback                         ingest one item
GET  /feedback                         list (filters: source, source_type, status)
POST /feedback/bulk                    CSV upload
GET  /feedback/{id}
GET  /feedback/{id}/analysis           404 analysis_missing if not analyzed
POST /feedback/{id}/analyze
POST /feedback/{id}/embed
GET  /feedback/{id}/similar            preview, persists nothing
POST /feedback/{id}/semantic-analysis  persist relationships
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

### Common error codes
`not_found`, `validation_error`, `signals_missing`, `analysis_missing`, `embedding_missing`, `llm_configuration_error` (503), `llm_provider_error` (502), `llm_output_validation_error` (502), `llm_ungrounded_output` (502), `database_error` (503).

For the full contract (enums, conventions, recommended call sequence), see `hackgenix-backend/docs/frontend-integration.md`. For design rationale, see `hackgenix-backend/docs/architecture.md`.

---

## Frontend

| Route | Description |
|---|---|
| `/` **Impact Grid** | Paginated Product Issues with real counts and a category breakdown. Selecting an issue opens a side sheet that creates/reuses a Decision, evaluates it, loads or generates a grounded brief with expandable citations, and shows live signal metrics. **Challenge this decision** re-evaluates and shows what changed. **View raw sources** pulls underlying feedback. |
| `/firehose` **Raw Firehose** | Unfiltered feedback with source-type/status filters, an ingestion form, and pipeline buttons (run issue discovery, recalculate all signals). |
| AI Analytics | Placeholder ("Soon"); no backend aggregate endpoint exists yet. |

Graceful degradation: if Gemini isn't configured, the brief panel falls back to the underlying deterministic evidence facts. No metric is ever fabricated client-side.

---

## Testing

```bash
cd hackgenix-backend
pytest
```

The suite covers startup and health, config validation, ingestion (single, bulk, idempotency), LLM and embedding providers, the semantic engine (with deterministically constructed unit vectors against real pgvector), issue discovery, signals, decision evidence, briefs and grounding, stability, challenge, and one end-to-end HTTP workflow test.

- All DB tests run inside a rolled-back transaction (`tests/conftest.py`), so they leave no rows behind.
- **No default test calls Gemini.** `tests/fakes.py` provides `FakeLLMProvider` / `FakeEmbeddingProvider`.
- An opt-in real-API smoke test is excluded by default (`addopts = -m "not gemini_smoke"`); run it explicitly with:

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

Migration chain: `feedback` → `feedback_analysis` → `feedback_embedding` → `feedback_relationship` → `product_issue` → `product_issue_signal_snapshot` → decision evidence tables → `decision_brief` (head).

---

## Design Decisions & Limitations

- **Connected components instead of HDBSCAN.** Reuses the already-computed relationship graph (O(E·α(n))) rather than O(n²) pairwise distances. HDBSCAN remains an option at larger scale.
- **No ANN index yet.** Exact pgvector scans are fine at demo scale; add HNSW once `feedback_embedding` approaches ~10k–50k rows.
- **Keyword-only decision matching.** Exact/substring only (no stemming, fuzzy, or semantic matching), because there are no issue-level embeddings yet.
- **Fixed, unvalidated thresholds.** Similarity cutoffs, signal windows, and evidence thresholds are hackathon-scale constants, version-stamped so stored rows identify the logic that produced them.
- **Citation validation is mechanical.** It proves a claim points at a real supplied fact, not that every word is entailed by the cited value (mitigated by a 280-character claim cap and strict prompting).
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
- Semantic / hybrid decision matching with issue-level embeddings
- AI Analytics page backed by aggregate endpoints
- Configurable thresholds and windows
- Background processing and automated signal recalculation
- Additional LLM/embedding providers (OpenAI, Ollama)

---

*Built for a hackathon by Team Mind Flayers: YD Nimal, S Hammad Rehan, R D Kirittin Kumar, and Rahul Srivatsan.*
