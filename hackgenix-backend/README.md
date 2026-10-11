# Hackgenix — Feedback Intelligence API

Turning messy human feedback into product decisions.

This is **not** a review summarizer or a generic AI chatbot. It's the backend for a
Product Decision Intelligence system: feedback is ingested once into a persistent
product knowledge base, then later phases let a user ask a question and get back an
evidence-backed Decision Brief, with full traceability from decision → insight →
issue → cluster → original feedback.

## Current status: backend feature-complete through Phase 4C + frontend-ready

Feedback ingestion (Phase 2) → per-item LLM analysis (3A) → embeddings +
pgvector (3B-1) → duplicate/related detection (3B-2) → Product Issue
clustering (3B-3) → deterministic per-issue signals (3C) → the Decision
Evidence Engine (4A: deterministic supporting/counter/gap classification) →
grounded LLM Decision Brief synthesis (4B: cited, evidence-only prose) →
Decision Stability & Evidence Change Analysis (4C: deterministic
evaluation-to-evaluation comparison, no LLM) → a "Challenge My Decision"
read-only API built on 4C's own ChangeEvents. A Product Issue is **never**
a priority score, and no phase invents a blended stability score or a
recommendation — see [`docs/architecture.md`](docs/architecture.md) for the
full pipeline, phase by phase, and [`docs/frontend-integration.md`](docs/frontend-integration.md)
for the API contract, conventions, and demo walkthrough a frontend
developer needs without reading backend source.

**Building a frontend against this backend?** Start with
[`docs/frontend-integration.md`](docs/frontend-integration.md) — it has the
full endpoint list, request/response conventions, enums, error format,
CORS setup, and the recommended call sequence for the main demo, plus how
to run `scripts/seed_demo_data.py` for a realistic, repeatable dataset.

## Architecture

```
app/
    main.py            FastAPI app: wires logging, error handlers, routers
    api/
        router.py       central APIRouter, mounted at /api/v1
        health.py        GET /health, GET /health/db
        feedback.py       feedback ingestion + retrieval endpoints
    core/
        config.py        Pydantic Settings (env vars / .env)
        logging.py        application-wide logging setup
        errors.py         AppError hierarchy + exception handlers
    db/
        database.py       SQLAlchemy engine, session factory, declarative Base
        models/
            feedback.py    Feedback ORM model
            feedback_analysis.py   FeedbackAnalysis ORM model (Phase 3A)
            feedback_embedding.py  FeedbackEmbedding ORM model, pgvector column (Phase 3B-1)
            feedback_relationship.py  FeedbackRelationship ORM model (Phase 3B-2)
            product_issue.py         ProductIssue ORM model (Phase 3B-3)
            product_issue_feedback.py  membership join table (Phase 3B-3)
            enums.py       ProcessingStatus, SourceType, FeedbackCategory, RelationshipType, ProductIssueStatus
    schemas/               Pydantic request/response models (health, feedback, common, analysis, embedding, semantic, issue)
    services/
        feedback_ingestion.py   dedup lookup, CSV parsing, row-level validation
        feedback_analysis.py    analysis orchestration + status transitions
        feedback_embedding.py   embedding orchestration (generate-or-reuse)
        semantic_engine.py      pgvector candidate retrieval + deterministic classification
        issue_discovery.py      connected-components clustering + issue persistence
        llm/
            base.py         LLMProvider Protocol — the only thing the app depends on
            factory.py       builds the configured provider from Settings
            gemini_provider.py   Gemini implementation (only file importing the SDK)
        embedding/
            base.py         EmbeddingProvider Protocol
            factory.py       builds the configured provider from Settings (reuses GEMINI_API_KEY)
            gemini_provider.py   Gemini Embedding 2 implementation

alembic/                   migration environment (uses app's Settings + Base.metadata)
tests/                     pytest suite
docs/architecture.md       full long-term architecture, phase by phase
main.py                    thin entry point: `from app.main import app`
```

## Setup

Requires **PostgreSQL** — this project intentionally never falls back to SQLite.
Requires the **pgvector** extension available to that Postgres server (used
from Phase 3B-1 onward) — most managed Postgres providers (Supabase, RDS,
etc.) already ship it; on a self-managed install you may need to add it once
(see `docs/architecture.md`'s Phase 3B-1 section for how this project's
Windows/Postgres 18 setup built it from source).

```bash
python -m venv venv
venv\Scripts\activate            # Windows
pip install -r requirements.txt

copy .env.example .env           # then edit DATABASE_URL etc.
```

Create the database (name must match `DATABASE_URL`), then apply migrations —
the first migration that needs pgvector runs `CREATE EXTENSION IF NOT EXISTS
vector` itself, so no separate manual step is needed once the extension
binary is available to Postgres:

```bash
alembic upgrade head
```

## Environment variables

| Variable            | Required | Notes                                                        |
|---------------------|----------|---------------------------------------------------------------|
| `DATABASE_URL`      | yes      | PostgreSQL connection string, e.g. `postgresql+psycopg://...` |
| `APP_NAME`          | no       | defaults to "Hackgenix Feedback Intelligence API"              |
| `APP_ENV`           | no       | defaults to "development"                                     |
| `DEBUG`             | no       | defaults to `false`                                            |
| `LOG_LEVEL`         | no       | defaults to "INFO"                                             |
| `LLM_PROVIDER`      | for `/analyze` | only `gemini` is implemented                             |
| `LLM_MODEL`         | for `/analyze` | e.g. `gemini-3.8-flash`                                  |
| `GEMINI_API_KEY`    | for `/analyze` | never logged; get one at https://aistudio.google.com    |
| `LLM_CONFIDENCE_REVIEW_THRESHOLD` | no | default `0.5` — below this, a successful analysis is routed to `NEEDS_REVIEW` instead of `ANALYZED` |
| `LLM_TIMEOUT_SECONDS` | no     | default `30`                                                   |
| `EMBEDDING_PROVIDER`| for `/embed` | only `gemini` is implemented                               |
| `EMBEDDING_MODEL`   | for `/embed` | e.g. `gemini-embedding-2`                                  |
| `CORS_ORIGINS`      | no       | comma-separated allowed origins; defaults to the common local Vite/CRA/Next.js dev ports — see `docs/frontend-integration.md` §13 |

`GEMINI_API_KEY` is shared between analysis and embeddings — there is no
separate embedding-specific key. Everything except `DATABASE_URL` is optional
for running the app and Phase 1/2 endpoints; the LLM/embedding variables are
only needed to call `POST /api/v1/feedback/{id}/analyze` or `.../embed`.
**No test requires them** — pytest uses fake providers by default (see
"Running tests" below).

## Running the API

```bash
uvicorn main:app --reload
```

- `GET /` — liveness
- `GET /api/v1/health` — `{"status": "ok"}`
- `GET /api/v1/health/db` — actually queries the database; returns `200` or a
  `503` with the standard error envelope if the database is unreachable
- `POST /api/v1/feedback` — ingest one feedback item. `201` if created, `200`
  if it matched an existing `(source, external_id)` (idempotent)
- `GET /api/v1/feedback/{id}` — fetch one feedback item, `404` if missing
- `GET /api/v1/feedback?limit=&offset=&source=&source_type=&status=` — paginated,
  filterable list
- `POST /api/v1/feedback/bulk` — multipart CSV upload (`file` field); required
  columns `source,raw_text`, optional `external_id,source_type,customer_id,
  customer_tier,language,created_at`. Returns per-row accepted/duplicate/rejected
  counts plus row-level error messages — a bad row never aborts the rest of the
  import
- `POST /api/v1/feedback/{id}/analyze` — run structured LLM analysis on one
  feedback item. Returns the analysis + resulting `processing_status`
  (`analyzed` / `needs_review`); `502`/`503` with the standard error envelope
  on provider/configuration failure (and the feedback's status is durably set
  to `failed` in that case — never silently treated as success)
- `POST /api/v1/feedback/{id}/embed` — generate (or reuse, if one already
  exists) a 3072-dim embedding for one feedback item. Returns
  `{"feedback_id", "embedding": {...}, "was_generated"}` — never the raw
  vector array. Independent of `/analyze`: an item can be embedded without
  being analyzed and vice versa
- `GET /api/v1/feedback/{id}/similar?limit=&threshold=` — read-only: finds
  and classifies (`duplicate`/`related`/`distinct`) semantic neighbors via
  pgvector cosine similarity; persists nothing. `404` (`embedding_missing`)
  if the item has no embedding yet
- `POST /api/v1/feedback/{id}/semantic-analysis?limit=&threshold=` — same
  retrieval + classification, and persists a relationship row for every
  `duplicate`/`related` candidate found (never for `distinct`)
- `POST /api/v1/issues/discover` — clusters existing `feedback_relationship`
  rows (connected components) into Product Issues. Explicitly triggered,
  never automatic. Idempotent: rerunning with no new relationships is a
  no-op; a grown cluster updates the existing issue rather than duplicating it
- `GET /api/v1/issues?limit=&offset=&status=&category=` — paginated list of
  discovered issues
- `GET /api/v1/issues/{id}` — full detail including `feedback_ids` (full
  traceability back to the underlying feedback), `404` if missing
- `GET /docs` — interactive OpenAPI docs

## Running tests

```bash
pytest
```

Tests cover the root/health endpoints, app startup, configuration validation, the
database session dependency, the full feedback ingestion layer (single + bulk
CSV ingestion, retrieval, pagination/filtering, idempotency, validation and error
handling), LLM analysis (provider configuration, structured output validation,
successful analysis, low-confidence routing to `NEEDS_REVIEW`, provider/output
failure routing to `FAILED`, and the API endpoint), embeddings (provider
configuration, successful generation + persistence, correct vector dimension,
idempotent reuse on repeat requests, missing feedback, provider/output
failure, and that analysis/embedding are independent), and the semantic engine
(deterministic classification thresholds and structured-signal downgrades as
pure unit tests, plus real-pgvector tests using deterministically-constructed
vectors for nearest-neighbor ordering, self-exclusion, threshold behavior,
candidate limits, similarity-score correctness, no-duplicate-row and
no-reverse-row persistence, and missing-feedback/missing-embedding cases), and
issue discovery (connected-components clustering, the structured cohesion
filter, deterministic title/category summarization, transitive merging,
cluster separation, outlier exclusion, idempotent/additive reruns,
never-deletes-feedback-or-relationships, pagination, and missing-issue 404).
The `/health/db` test accepts either a healthy or an "unavailable" response, since
session creation itself is lazy. DB-touching tests run against a real database
connection but each test is wrapped in a transaction that's rolled back
afterward (`tests/conftest.py`), so they never leave rows behind.

**No test calls the real Gemini API** — `tests/fakes.py` provides deterministic
`FakeLLMProvider`/`FakeEmbeddingProvider` classes, wired in by default via
FastAPI dependency overrides, so `pytest` runs the same whether or not
`GEMINI_API_KEY` is set. To manually sanity-check real credentials (not run by
pytest):

```bash
python scripts/manual_gemini_check.py "The app crashes when I upload a large photo."
python scripts/manual_embedding_check.py "The app crashes when I upload a large photo."
```

## Database migrations (Alembic)

`alembic/env.py` reads `DATABASE_URL` from the application's own `Settings` (not
from `alembic.ini`), and targets `Base.metadata` directly — there is a single
source of truth for both the connection string and the schema.

```bash
alembic upgrade head                              # apply migrations
alembic revision --autogenerate -m "message"       # generate a new migration
```

## Phase roadmap

See [`docs/architecture.md`](docs/architecture.md) for the full, phase-by-phase
write-up. In short: Phase 1 was the backend foundation, Phase 2 added
feedback ingestion, Phase 3A added per-item LLM analysis, Phase 3B-1 added
embedding generation + pgvector storage, Phase 3B-2 added duplicate/related
detection, Phase 3B-3 clusters that graph into persistent Product Issues,
Phase 3C added deterministic per-issue signals, Phase 4A added the
deterministic Decision Evidence Engine, Phase 4B added grounded LLM
Decision Brief synthesis, and Phase 4C added deterministic Decision
Stability & Evidence Change Analysis (the foundation for "Challenge My
Decision," exposed via `GET /decisions/{id}/challenge`). Explicitly **not**
implemented, by design (see `docs/architecture.md` for why): a priority
engine, a blended priority/stability score, a recommendation/"best
decision" engine, and a human-review workflow.

For building a frontend against this backend, see
[`docs/frontend-integration.md`](docs/frontend-integration.md).
