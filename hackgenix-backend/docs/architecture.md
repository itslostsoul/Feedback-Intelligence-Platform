# Long-term architecture

Hackgenix turns unstructured feedback into evidence-backed product decisions.
The full pipeline, and what's implemented so far:

```
INGESTION                    IMPLEMENTED (Phase 1: persistence; Phase 2: ingestion API)
  -> AI UNDERSTANDING        IMPLEMENTED (Phase 3A: per-item structured analysis)
  -> EMBEDDINGS              IMPLEMENTED (Phase 3B-1: generation + pgvector storage)
  -> SEMANTIC ENGINE         IMPLEMENTED (Phase 3B-2: duplicate/related detection)
  -> PRODUCT ISSUES          IMPLEMENTED (Phase 3B-3: clustering into issues)
  -> SIGNAL ENGINE           IMPLEMENTED (Phase 3C: deterministic per-issue signals)
  -> EVIDENCE ANALYSIS       IMPLEMENTED (Phase 4A: Decision Evidence Engine — supporting / counter / gaps)
  -> PRIORITY ENGINE         PLANNED  (explainable deterministic scoring)
  -> PRODUCT KNOWLEDGE BASE  PLANNED
  -> QUERY PLANNER           PLANNED
  -> HYBRID RETRIEVAL        PLANNED
  -> DECISION BRIEF          IMPLEMENTED (Phase 4B: grounded LLM synthesis over Phase 4A's stored evidence)
  -> DECISION STABILITY      IMPLEMENTED (Phase 4C: deterministic evidence-change comparison over evaluation history)
  -> HUMAN REVIEW            PLANNED  (correcting AI classifications)
  -> AUDIT TRAIL             PLANNED
```

## Phase 1 — Backend Foundation (this phase)

Implemented:

- Modular FastAPI project structure (`app/api`, `app/core`, `app/db`, `app/schemas`,
  `app/services`).
- Environment-based configuration via Pydantic Settings (`app/core/config.py`).
- SQLAlchemy 2.x engine/session/Base + FastAPI DB dependency (`app/db/database.py`),
  targeting PostgreSQL only.
- `Feedback` domain model — the root of the future traceability chain
  (decision → insight → issue → cluster → feedback). Every later table
  (`feedback_analysis`, `feedback_embedding`, `feedback_cluster`,
  `cluster_feedback`, `product_issue`, `priority_score`, `product_insight`,
  `product_decision`, `review_history`, `audit_event`, `processing_run`) will
  reference this one by id rather than replacing it.
- `ProcessingStatus` enum describing the feedback lifecycle
  (`received -> pending_analysis -> analyzing -> analyzed / needs_review / failed`),
  designed to gain new members rather than be redesigned.
- Alembic wired to the app's own settings and metadata (single source of truth
  for the DB URL and schema); initial migration creates the `feedback` table.
- Centralized error handling (`app/core/errors.py`): a stable JSON error envelope
  (`{"error": {"code", "message"}}`) for app errors, validation errors, database
  errors, and unhandled exceptions — never leaks stack traces or secrets.
- Application logging (`app/core/logging.py`) with request/endpoint context,
  never logging secrets or raw sensitive payloads.
- API foundation: `GET /`, `GET /api/v1/health`, `GET /api/v1/health/db` (the
  latter actually queries the database), served behind a central versioned
  router (`/api/v1`).
- Pytest suite covering startup, root/health endpoints, configuration
  validation, and the DB session dependency.

Explicitly **not** implemented yet (by design — later phases):

- No `/issues`, `/search`, `/insights`, `/reviews`, `/decisions`, `/analytics`
  endpoints — the frontend contract for these is reserved but unbuilt.
- No clustering, semantic search, priority scoring, or Decision Brief
  generation.
- No background job processing.

## Phase 2 — Feedback Ingestion

Implemented:

- `POST /api/v1/feedback` — single-item ingestion. Normalizes whitespace on
  string fields, rejects blank `source`/`raw_text`, never invents a value for
  a field that wasn't provided.
- `GET /api/v1/feedback/{id}` — single-record retrieval, `404` if missing.
- `GET /api/v1/feedback` — pagination (`limit`, `offset`) and filtering
  (`source`, `source_type`, `status`), ordered newest-first.
- `POST /api/v1/feedback/bulk` — CSV upload. Structural problems (empty file,
  missing required columns, non-UTF-8 content, more than 5000 rows) reject
  the whole request; a bad *row* is recorded in `errors` and skipped, never
  aborting the rest of the import.
- Idempotency via the existing `uq_feedback_source_external_id` constraint:
  ingesting the same `(source, external_id)` again — via the single endpoint,
  within one CSV, or across separate bulk imports — returns/reuses the
  existing row instead of creating a duplicate. **Known limitation**:
  Postgres treats `NULL <> NULL` for unique constraints, so a feedback item
  ingested without an `external_id` can never be deduplicated this way and
  will always create a new row. This wasn't solved with a schema change,
  per Phase 2 scope — see `app/services/feedback_ingestion.py`.
- New records enter `ProcessingStatus.RECEIVED` — nothing else acts on them
  yet.
- `app/schemas/feedback.py` keeps API request/response shapes fully separate
  from the `Feedback` ORM model; `app/services/feedback_ingestion.py` holds
  the ingestion/dedup/CSV logic so routes stay thin.
- No new migration — Phase 2 reuses the Phase 1 `feedback` table and
  constraint as-is.
- Tests use a savepoint-based transactional fixture (`tests/conftest.py`) so
  the ingestion test suite never leaves rows in the real database.

Explicitly **not** implemented yet:

- No update/delete endpoints for feedback (append-only ingestion for now).
- CSV bulk import only supports flat/scalar columns — no `metadata` column
  (embedding JSON in a CSV cell is fragile; add it via the single-item
  endpoint instead).

## Phase 3A — LLM Provider Abstraction + Structured Feedback Analysis

Implemented:

- **Provider abstraction** (`app/services/llm/base.py`): `LLMProvider` is a
  `typing.Protocol` with one method, `analyze_feedback(raw_text) ->
  FeedbackAnalysisResult`. The rest of the app depends on this interface
  only — nothing outside `app/services/llm/gemini_provider.py` imports the
  Gemini SDK. Adding OpenAI/Ollama later means adding another class
  satisfying the same Protocol plus a branch in `app/services/llm/factory.py`.
- **Gemini provider** (`app/services/llm/gemini_provider.py`): uses the
  `google-genai` SDK, configured entirely from `Settings`
  (`LLM_PROVIDER`, `LLM_MODEL`, `GEMINI_API_KEY`, `LLM_TIMEOUT_SECONDS`) —
  no hardcoded key or model. Requests structured JSON output via an
  explicit `google.genai.types.Schema` (not derived from the Pydantic model —
  see the comment in that file: the live API rejects the
  `additionalProperties` field pydantic's `extra="forbid"` produces, even
  though the SDK's local type accepts it). The response is still always
  parsed and validated against `FeedbackAnalysisResult` before being
  trusted — arbitrary prose is never accepted as a successful analysis.
- **Structured analysis contract** (`app/schemas/analysis.py`,
  `FeedbackAnalysisResult`, `extra="forbid"`): `category` (initial taxonomy:
  bug / feature_request / ui_friction / performance / billing — isolated in
  `FeedbackCategory`, `app/db/models/enums.py`, so it can grow without
  touching provider code), `affected_feature`, `severity` (1-10),
  `confidence` (0-1), `summary` (one actionable sentence). The LLM is only
  ever asked to interpret one feedback item in isolation — never frequency,
  trend, priority, source diversity, affected-user counts, product health,
  or decision stability; those stay deterministic backend calculations in a
  later phase.
- **Processing lifecycle**: `POST /api/v1/feedback/{id}/analyze` drives
  `ANALYZING -> ANALYZED` (or `-> NEEDS_REVIEW` if `confidence` is below
  `LLM_CONFIDENCE_REVIEW_THRESHOLD`, default `0.5`) on success, or
  `-> FAILED` if the provider call fails or its output fails schema
  validation — persisted before the error response is returned, never
  silently treated as success.
- **Persistence**: a new `feedback_analysis` table (one row per feedback
  item; re-analysis overwrites it rather than keeping history — see
  `app/db/models/feedback_analysis.py`), migration
  `ec78fda217b8_create_feedback_analysis_table`. Stores only the structured
  fields above plus `provider`/`model`/`analyzed_at` — never the raw LLM
  response.
- **Error handling** (`app/core/errors.py`): `LLMConfigurationError` (503 —
  missing/unsupported provider or API key, a setup problem caught before any
  feedback status is touched), `LLMProviderError` (502 — provider
  timeout/failure/rate-limit), `LLMOutputValidationError` (502 — malformed
  structured output). All go through the same error envelope as every other
  endpoint; none leak the API key or a raw provider stack trace.
- **Privacy**: nothing in this phase logs `GEMINI_API_KEY` or `raw_text` —
  only ids, categories, severities, and error *types* (never provider error
  bodies, which could echo request content back).
- **Testing**: `tests/fakes.py` provides a deterministic `FakeLLMProvider`
  used via a FastAPI dependency override (`get_llm_provider`,
  `app/api/dependencies.py`) — no automated test makes a real Gemini call.
  `scripts/manual_gemini_check.py` is a separate, explicitly-optional script
  for a real smoke test against your configured credentials; it is not
  collected by pytest.

Explicitly **not** implemented yet:

- No bulk/batch AI processing — one feedback item per `/analyze` call.
- No clustering, semantic search, priority scoring, Decision Brief
  generation, or Decision Audit.
- No re-analysis history (each analysis overwrites the prior one for that
  feedback item) — an audit trail is explicitly a later, planned phase.

## Phase 3B-1 — Embedding Provider + pgvector Storage

A feedback item now has two independent, optional derived representations:

```
Feedback (raw_text)
 ├── Structured Analysis   (feedback_analysis)   — Phase 3A
 │     What an LLM concludes about this item: category, affected feature,
 │     severity, confidence, a one-sentence summary. Human/LLM-readable
 │     interpretation, not something you can do math on directly.
 └── Embedding              (feedback_embedding)  — Phase 3B-1
       A 3072-dimensional numeric fingerprint of the same raw_text. Not
       interpretable on its own, but comparable: nearest-neighbor distance
       between two vectors is the raw material the semantic engine
       (duplicate/related-feedback detection, Phase 3B-2) will run on.
```

Both derive from the same `raw_text` and are otherwise unrelated — a
feedback item may have neither, either, or both, generated independently
and in any order. Neither writes to the other's table, and embedding
generation never reads or writes `Feedback.processing_status`.

Implemented:

- **Provider abstraction** (`app/services/embedding/base.py`):
  `EmbeddingProvider` is a `typing.Protocol` mirroring
  `app.services.llm.base.LLMProvider`'s shape — one method,
  `embed_feedback(raw_text) -> EmbeddingResult`. Nothing outside
  `app/services/embedding/gemini_provider.py` imports the Gemini SDK.
- **Gemini Embedding 2** (`app/services/embedding/gemini_provider.py`):
  configured from `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL`, and the
  **same** `GEMINI_API_KEY` used by the Phase 3A LLM provider —
  `app/services/embedding/factory.py` deliberately does not introduce a
  second Gemini credential. Live-verified output dimension: **3072**
  (`client.models.embed_content(model="gemini-embedding-2", ...)`).
- **Database**: new `feedback_embedding` table (migration
  `45e334cbc674_create_feedback_embedding_table`, which also runs
  `CREATE EXTENSION IF NOT EXISTS vector` so a fresh environment doesn't
  need a separate manual step) — FK to `feedback.id` (`ON DELETE CASCADE`),
  a `vector(3072)` pgvector column (`FEEDBACK_EMBEDDING_DIMENSION` constant
  in `app/db/models/feedback_embedding.py`), a unique constraint on
  `feedback_id` (one current embedding per feedback item — regenerating
  would overwrite, mirroring `feedback_analysis`), `provider`/`model`
  metadata, and `created_at`/`updated_at`. No raw feedback text is
  duplicated into this table.
  - **Environment note**: this machine's Postgres 18 install didn't ship
    pgvector; it was built from pgvector's official source using the
    already-installed VS Build Tools C++ toolchain (`nmake /f Makefile.win`)
    and installed into the Postgres install directory before this
    migration could run — an admin-rights, one-time machine setup step,
    not something `alembic upgrade` can do by itself on a machine that
    lacks the extension binary.
- **Service** (`app/services/feedback_embedding.py`): `embed_feedback()`
  looks up any existing embedding first and returns it unchanged — the
  provider is never called again for an item that already has one — only
  querying/inserting when none exists. Also checks the returned vector's
  dimension against `FEEDBACK_EMBEDDING_DIMENSION` before persisting,
  raising `EmbeddingOutputValidationError` on mismatch (protects the fixed
  pgvector column from a misconfigured or future differently-sized
  provider).
- **API**: `POST /api/v1/feedback/{id}/embed` → `{"feedback_id",
  "embedding": {...}, "was_generated"}`. The response never includes the
  raw 3072-float array — only `dimension`, `provider`, `model`, and
  timestamps — since nothing downstream yet needs the vector as JSON and
  it's an awkward payload to hand back until the semantic engine actually
  consumes it.
- **Error handling** (`app/core/errors.py`): `EmbeddingConfigurationError`
  (503), `EmbeddingProviderError` (502), `EmbeddingOutputValidationError`
  (502) — same envelope, same pattern as the Phase 3A LLM errors.
- **Privacy**: same as Phase 3A — no log line includes `GEMINI_API_KEY` or
  `raw_text`.
- **Testing**: `tests/fakes.py::FakeEmbeddingProvider` (with a `call_count`
  used to assert the provider is *not* called again on a repeat request) —
  no automated test makes a real Gemini call.
  `scripts/manual_embedding_check.py` is the separate, explicitly-optional
  real smoke test (not collected by pytest).

Explicitly **not** implemented yet:

- No semantic search, duplicate/related-feedback detection, or clustering
  using these vectors — Phase 3B-1 only generates and stores them.
- No priority scoring, Decision Brief, Decision Audit, or aggregate
  analytics.
- No embedding regeneration path (if `raw_text` could ever change, which it
  currently can't — feedback ingestion is append-only per Phase 2 — a new
  embedding would need one; not needed yet).

## Phase 3B-2 — Semantic Engine: Duplicate & Related-Feedback Detection

```
Feedback
   ↓
Embedding                         (feedback_embedding, Phase 3B-1)
   ↓
pgvector candidate retrieval      (cosine distance, in Postgres — Phase 3B-2)
   ↓
Semantic relationship detection   (feedback_relationship, Phase 3B-2)
   ├── duplicate   (near-identical content)
   ├── related     (different records, same underlying problem)
   └── distinct    (a classifier outcome — see below; not persisted)
```

**Semantic similarity is a candidate-generation signal, not proof.** A high
cosine similarity means two feedback items are *close in embedding space*,
which is strong evidence they're about the same thing — but embeddings can't
see category, severity, or "is this actually the same underlying bug."
That's why classification here is two-stage: pgvector produces similarity-
ranked *candidates*, and a deterministic rule layer (using `FeedbackAnalysis`
when it exists) can still downgrade a high-similarity pair to `related` or
`distinct`. Nothing in this phase treats "cosine similarity ≥ threshold" as
a final verdict.

Implemented:

- **Service layer** (`app/services/semantic_engine.py`): entirely separate
  from ingestion, analysis, and embedding generation (imports from
  `app.services.feedback_embedding` for the one thing it needs — looking up
  an existing embedding — but neither of those services import back). Routes
  in `app/api/feedback.py` stay thin: look up the feedback, call the service,
  shape the response.
- **Similarity metric**: **cosine distance**, via pgvector's `<=>` operator
  (`FeedbackEmbedding.vector.cosine_distance(...)` — `pgvector.sqlalchemy`'s
  comparator). Chosen because it's magnitude-invariant (L2 distance isn't —
  it would penalize two embeddings for differing in length even if they
  point the same direction) and because cosine similarity is the standard,
  provider-recommended way to compare Gemini text embeddings. Reported
  `similarity = 1 - cosine_distance`.
- **Candidate retrieval** (`find_similar_feedback`): one query —
  `SELECT ... ORDER BY vector <=> :source LIMIT :limit` with the source
  feedback excluded and a `WHERE cosine_distance <= 1 - threshold` floor —
  does the nearest-neighbor search **inside Postgres**. Nothing in this
  service loads the embedding table into Python to compare vectors itself;
  it scales with what pgvector can serve, not with an O(n) or O(n²) Python
  loop. `limit` (default 10, capped at 50) and `similarity_threshold`
  (default 0.55) are both request-configurable, not hardcoded — see
  `DEFAULT_CANDIDATE_LIMIT`, `MAX_CANDIDATE_LIMIT`, `DEFAULT_SIMILARITY_FLOOR`
  in `app/services/semantic_engine.py`.
- **Classification** (`classify_relationship`, no LLM call): a pure,
  deterministic function.
  - `similarity >= 0.93` (`DUPLICATE_SIMILARITY_THRESHOLD`) → tentative
    `duplicate`; `>= 0.75` (`RELATED_SIMILARITY_THRESHOLD`) → tentative
    `related`; below that → `distinct`. Chosen conservatively for
    gemini-embedding-2 cosine similarity on short product-feedback text —
    a false `duplicate` silently discards a distinct signal, which is worse
    for signal quality than an extra `related` row a human can dismiss —
    and confirmed against real data during this phase's smoke test (two
    differently-worded reports of the same upload crash scored **0.926**,
    just below the duplicate bar, correctly landing as `related`; an
    unrelated dark-mode feature request scored **0.593**, correctly
    `distinct`).
  - If `FeedbackAnalysis` exists for **both** items, three structured
    signals can only ever *downgrade* the tentative classification, never
    upgrade it (missing analysis must never manufacture a stronger claim
    than the embeddings support): a **category** mismatch (e.g. Bug vs.
    Feature Request) downgrades one full tier; a different
    **affected_feature** downgrades `duplicate` → `related`; a **severity**
    gap of 5+ points downgrades one tier. `summary` is deliberately *not*
    compared here — a real semantic comparison of free text is exactly what
    the embedding step already does properly; hand-rolled string matching on
    `summary` would be a worse, redundant version of the same signal. It's
    reserved for a possible future *second-stage* LLM-assisted check on
    borderline candidates (see "not implemented" below) — consistent with
    "don't call Gemini for every pair by default."
- **Persistence** (`feedback_relationship` table, migration
  `a91cd4fc37b0_create_feedback_relationship_table`): FK to `feedback.id`
  from both `feedback_id_a`/`feedback_id_b` (`ON DELETE CASCADE` on both),
  `relationship_type`, `similarity_score`, `algorithm_version`, timestamps.
  - **No reverse-duplicate rows**: duplicate/related/distinct is symmetric
    (if A relates to B, B relates to A — there's no direction), so each
    unordered pair is stored exactly once. `feedback_id_a` is always the
    numerically smaller UUID (`_normalize_pair` in the service, and a
    `CHECK (feedback_id_a < feedback_id_b)` at the DB level as a second line
    of defense), with a unique constraint on `(feedback_id_a,
    feedback_id_b)`. Running semantic analysis from either A's or B's side
    finds and updates the same row — verified by a test that runs it from
    both directions and asserts exactly one row exists.
  - **Only `duplicate`/`related` are persisted.** `distinct` is a real,
    reachable enum value returned by `classify_relationship` and included in
    API responses, but the service never writes a row for it — storing "we
    checked and these are unrelated" for every candidate pair would grow
    this table combinatorially (toward one row per *pair ever considered*,
    not per finding) for no current benefit. The enum member stays, ready
    for a future phase where persisting an explicit "not related" (e.g. a
    human override, to suppress re-surfacing) has a concrete purpose.
  - **Reproducibility** (requirement, not just nice-to-have):
    `algorithm_version` (currently `"semantic-v1"`, the
    `SEMANTIC_ANALYSIS_ALGORITHM_VERSION` constant) is stored on every row.
    Whenever `DUPLICATE_SIMILARITY_THRESHOLD`, `RELATED_SIMILARITY_THRESHOLD`,
    or the structured-signal rules change, that constant must be bumped —
    so a historical row always identifies exactly which thresholds/logic
    produced it, even after the defaults move on.
- **API**:
  - `GET /api/v1/feedback/{id}/similar?limit=&threshold=` — read-only
    preview: retrieves and classifies candidates, **persists nothing**.
  - `POST /api/v1/feedback/{id}/semantic-analysis?limit=&threshold=` — same
    retrieval + classification, and additionally upserts a
    `FeedbackRelationship` row for every `duplicate`/`related` candidate.
  - Both return `404` (`not_found`) if the feedback doesn't exist, and a
    distinct `404` (`embedding_missing`) if it exists but has no embedding
    yet (call `/embed` first) — deliberately not conflated with "doesn't
    exist." Neither endpoint's response includes a raw vector.
- **Indexing decision**: **no ANN index (HNSW/IVFFlat) was added.** At
  current and near-term hackathon-demo data volumes (low hundreds to low
  thousands of rows), pgvector's exact/brute-force distance scan on
  `feedback_embedding` is fast enough, and building one now would add write
  overhead and (for IVFFlat) require enough data to form meaningful clusters
  in the first place for no measurable benefit yet. This is a deliberate,
  documented decision, not an oversight: **revisit once `feedback_embedding`
  is expected to exceed roughly 10,000-50,000 rows**, at which point add
  `CREATE INDEX ... USING hnsw (vector vector_cosine_ops)` via a new
  migration (HNSW over IVFFlat, since it doesn't need a representative
  sample of data upfront to build well and tolerates ongoing inserts better).
- **Testing**: `tests/test_semantic_engine.py` — pure unit tests for
  `classify_relationship` (no DB), plus real-pgvector tests using
  deterministically-constructed unit vectors (`_vector_with_similarity`
  places a vector at an exact angle from a reference vector, so cosine
  similarity is a known value, not whatever a real embedding happens to
  produce) for nearest-neighbor ordering, self-exclusion, threshold
  behavior, candidate limits, similarity-score correctness, both
  persistence-prevention properties above, and missing-feedback/
  missing-embedding/no-raw-vector-in-response cases. No test calls Gemini.

Explicitly **not** implemented yet:

- No LLM-assisted second-stage classification for borderline candidates
  (mentioned above as a possible future refinement) — every classification
  in this phase is the deterministic rule layer only.
- No priority scoring, Decision Brief, Decision Audit, or aggregate
  analytics.
- No ANN index — see the indexing decision above for when to add one.

## Phase 3B-3 — Product Issue Discovery (Clustering)

```
Raw Feedback
    ↓
Structured Analysis + Embedding        (Phase 3A, Phase 3B-1)
    ↓
Semantic Relationships                 (feedback_relationship, Phase 3B-2)
    ↓
Product Issue Discovery                (connected components + cohesion filter — Phase 3B-3)
    ↓
Product Issues                         (product_issue / product_issue_feedback)
    ↓
[future] Signal Engine                 (frequency, severity distribution, source diversity, ...)
    ↓
[future] Decision Intelligence         (priority, Decision Brief, Decision Audit)
```

**A Product Issue is an evidence container, not a priority score.** It
groups feedback that appears to describe the same underlying problem so a
later phase can compute measurable signals from that membership — nothing in
`product_issue` is a computed aggregate, a severity number, or a ranking.
`product_issue_feedback` (plus `feedback`/`feedback_analysis`) is
deliberately "enough" for a future Signal Engine to deterministically derive
frequency, severity distribution, categories, affected features, source
distribution, customer segments, and time distribution — none of which are
calculated here.

Implemented:

- **Feedback ≠ Product Issue**: `ProductIssue` (`app/db/models/product_issue.py`)
  is a distinct entity with its own id, title, category, affected_feature,
  `status` (`open`/`resolved`/`dismissed` — only `open` is ever set by this
  phase; the other two are reserved for a future human-review phase),
  `clustering_version`, and timestamps. Membership is a separate join table,
  `ProductIssueFeedback` (`app/db/models/product_issue_feedback.py`):
  modeled as a proper many-to-many table even though this phase enforces "at
  most one issue per feedback item" via a unique constraint on `feedback_id`
  — dropping just that one constraint later supports true many-to-many with
  no redesign, as the phase spec asked for.
- **Clustering approach: connected components over the existing relationship
  graph**, not HDBSCAN. Rationale (documented in
  `app/services/issue_discovery.py`): true HDBSCAN over raw embeddings needs
  pairwise distances across the whole dataset — O(n²), or an ANN index,
  which Phase 3B-2 already documented as premature at current scale.
  `feedback_relationship` (Phase 3B-2) already encodes exactly the "same
  underlying problem" signal HDBSCAN would otherwise have to rediscover from
  scratch, computed via pgvector. Building connected components over its
  `duplicate`/`related` edges (union-find, `_build_components`) is a real,
  standard graph-clustering technique — equivalent to single-linkage
  clustering cut at "an edge exists" — costs O(E · α(n)) in the number of
  *existing* relationship edges, not a Python-side O(n²) scan, and keeps
  clustering behavior consistent with what `/semantic-analysis` already
  decided. A feedback item with zero edges never appears in the graph at
  all, so it's automatically excluded — outliers are never forced into a
  cluster. **Discovery reads `feedback_relationship` as it currently
  stands; it does not itself trigger new `/semantic-analysis` calls** — for
  discovery to find anything, semantic analysis must already have been run
  for the relevant feedback. This keeps 3B-2 and 3B-3 each doing one thing.
  True HDBSCAN (variable-density clusters, soft membership) remains a
  documented option for a future phase once data volume and an ANN index
  justify the cost.
- **Cluster quality — structured-signal cohesion filter**
  (`_apply_structured_cohesion_filter`): raw similarity chained transitively
  across a component (A~B, B~C) doesn't guarantee A and C are the same
  issue — the classic weakness of single-linkage clustering. Before a
  component is promoted, if a clear majority (>50%) of its *analyzed*
  members share one category, any member whose analyzed category disagrees
  is peeled off as an outlier for this run rather than folded into a false
  merge (members with no analysis yet are given the benefit of the doubt).
  This directly reuses `FeedbackAnalysis.category` — no re-derivation of a
  signal embeddings already influenced. A peeled-off member, or a component
  that shrinks below `MIN_ISSUE_SIZE = 2` after filtering, is simply not
  linked to an issue this run — it remains fully visible via the existing
  feedback endpoints, never deleted or hidden.
- **Issue naming: deterministic**, explicitly acceptable per this phase's
  scope. `_summarize_cluster` reuses an existing member's
  `FeedbackAnalysis.summary` (already "one actionable sentence") as the
  title, and majority-votes `category`/`affected_feature` across analyzed
  members; with no analysis available for any member, it falls back to
  `"Cluster of N related feedback items"` (verified live: three real,
  differently-worded reports of the same upload-crash clustered into one
  issue during this phase's smoke test — analysis was unavailable due to an
  ongoing transient Gemini overload noted in Phase 3A, so the generic
  fallback title was exercised for real, not just in tests). A future phase
  could instead pass a compact representative set (e.g. one summary per
  distinct category+feature combination present) through the existing
  `LLMProvider` abstraction (`app.services.llm.base.LLMProvider`) for a more
  natural title — never one call per individual feedback item, and never
  asked to compute an aggregate metric.
- **Reproducibility across reruns**: `clustering_version` (currently
  `"cluster-v1"`, `CLUSTERING_ALGORITHM_VERSION`) is stamped on every issue
  created or touched, bumped whenever `MIN_ISSUE_SIZE`, the cohesion filter,
  or the summarization logic changes. Rerunning discovery is **idempotent
  and additive, never destructive**: "is this the same issue as before" is
  determined by *existing membership overlap*
  (`_find_existing_issue_for_members` — a component matches an existing
  issue if any of its members already link to one), not by
  `canonical_feedback_id`, since the canonical member could itself be
  filtered out by the cohesion filter in a later rerun — matching on it
  would silently break idempotency across reruns. Membership only ever
  **grows**: a feedback item already linked to an issue is never unlinked or
  moved, even if a rerun's graph no longer clusters it with that issue's
  other members, so historical traceability is never destroyed. Verified by
  tests that rerun discovery with no new data (no-op) and with a newly
  grown component (adds the new member to the *existing* issue, not a new
  one).
- **API**:
  - `POST /api/v1/issues/discover` — explicitly triggered; never runs
    implicitly on a GET.
  - `GET /api/v1/issues?limit=&offset=&status=&category=` — paginated list;
    `feedback_count` is computed from `product_issue_feedback` at query
    time, never stored.
  - `GET /api/v1/issues/{id}` — full detail including `feedback_ids` (the
    traceability list back to underlying feedback), `404` if missing.
- **Testing**: `tests/test_issue_discovery.py` — pure unit tests for
  connected-components building, the cohesion filter, and summarization (no
  DB), plus integration tests seeding `feedback_relationship`/
  `feedback_analysis` rows directly (mirroring Phase 3B-2's approach) for
  issue creation, transitive merging, cluster separation, outlier exclusion,
  cohesion-filter application, idempotent reruns, membership growth on
  rerun, never-deletes-feedback-or-relationships, pagination, and
  missing-issue 404. No test calls Gemini.

Explicitly **not** implemented yet (as of this phase):

- No Signal Engine calculations (frequency, severity distribution, source
  diversity, affected-user counts, time distribution) — `product_issue` and
  `product_issue_feedback` store the membership this phase needed for that,
  but computed nothing themselves. **Implemented in Phase 3C, below.**
- No priority scoring, Decision Brief, or Decision Audit.
- No true HDBSCAN, no LLM-assisted naming, no ANN index — see the
  rationale above for each.
- No issue merge/split tooling for the rare case where a later rerun's
  graph would imply two previously-separate issues should combine, or one
  issue should divide — a known, documented, not-yet-reachable edge case
  given relationships are currently append-only.

## Phase 3C — Deterministic Product Issue Signal Engine

```
Product Issues                         (product_issue / product_issue_feedback, Phase 3B-3)
    ↓
Signal Calculation                     (pure SQL aggregation over membership — Phase 3C)
    ↓
ProductIssueSignalSnapshot             (one row per issue, overwritten on recalculation)
    ↓
[future] Priority Engine               (explainable deterministic scoring)
[future] Decision Intelligence         (Decision Brief, Decision Audit)
```

**Goal and constraints.** Compute deterministic, evidence-based aggregate
signals for every `ProductIssue` from its linked feedback and existing
`FeedbackAnalysis`/`FeedbackRelationship` data — **no LLM call**, no single
opaque priority score, and no claim the data can't support (e.g. feedback
volume is never treated as if it represents the whole customer population;
anonymous feedback is never assumed to map to distinct people). Every signal
is traceable back through `product_issue_feedback` membership to the
underlying `Feedback`/`FeedbackAnalysis` rows that produced it.

**Storage: one row per issue, overwritten on recalculation** —
`ProductIssueSignalSnapshot` (`app/db/models/product_issue_signal_snapshot.py`,
table `product_issue_signal_snapshot`), with a `UniqueConstraint` on
`product_issue_id` (`ondelete="CASCADE"` from `product_issue`). This
deliberately mirrors the existing `FeedbackAnalysis` convention ("one row per
item, re-analysis overwrites it") rather than a growing point-in-time
history table, because recalculation must be idempotent and must never
accumulate uncontrolled duplicate rows for the same issue. Columns:
`signal_version` (bumped whenever a formula/threshold/window below changes —
same convention as `clustering_version`/`algorithm_version`), `reference_at`
(the timestamp recency/trend were computed relative to, persisted so a
consumer always knows exactly what point in time was measured against),
`feedback_count` (a real column — the simplest, most commonly
filtered/sorted-on signal), and one JSONB column per signal group below (B
through J) so each group's full structure survives, never collapsed to a
single scalar. `calculated_at` records when the row was last written.

**Effective timestamp.** Every time-based signal (recency, trend, time
distribution) uses `COALESCE(feedback.created_at, feedback.ingested_at)` —
preferring when the feedback happened at its source, falling back to when
Hackgenix received it — applied consistently so the three signals never
disagree about "when" a given item counts.

**Signal definitions:**

- **A. Frequency** — `feedback_count`: total rows in `product_issue_feedback`
  for the issue. No claim beyond a raw count.
- **B. Severity distribution** (`severity`) — the exact per-value histogram
  of `FeedbackAnalysis.severity` (1-10) across linked feedback that has an
  analysis (`distribution`), plus a fixed bucketing (`buckets`: low 1-3,
  medium 4-6, high 7-8, critical 9-10), `mean`, and `median` (PostgreSQL
  `percentile_cont(0.5) WITHIN GROUP`, computed in the DB). `analyzed_count`
  / `unanalyzed_count` make missing analysis explicit rather than silently
  dropping those items from the picture. The distribution is always present
  even when mean/median are — this is never reduced to a single number.
- **C. Recency** (`recency`) — `latest_feedback_at`/`oldest_feedback_at`
  (effective timestamp, min/max via SQL), `reference_at` (what "now" meant
  for this calculation), and `days_since_latest`/`days_since_oldest` computed
  against it. All `null` when the issue has no members.
- **D. Trend** (`trend`) — two adjacent, non-overlapping windows of
  `TREND_WINDOW_DAYS = 7` ending at `reference_at`: current = `(reference -
  7d, reference]`, previous = `[reference - 14d, reference - 7d]`.
  `current_window_count`/`previous_window_count` are the effective-timestamp
  counts in each. Below `MIN_TREND_SAMPLE = 4` combined items across both
  windows, `sufficient_data` is `false` and `direction` is explicitly
  `"insufficient_data"` — a trend is never guessed from too little data.
  Otherwise `direction` is `"increasing"`, `"decreasing"`, or `"flat"` by
  simple comparison of the two window counts.
- **E. Customer reach** (`customer_reach`) — `distinct_known_customers`
  (`COUNT(DISTINCT customer_id)` where not null), `known_feedback_count`, and
  `anonymous_feedback_count` (items with no `customer_id`) kept as a
  **separate, explicit count** — anonymous items are never assumed to
  correspond to one person each, or folded into the "distinct customers"
  number.
- **F. Source diversity** (`source_diversity`) — `distinct_sources`, the full
  `source_distribution` (free-text `Feedback.source`, e.g. `"app_store"`),
  and `source_type_distribution` (the `SourceType` enum) — concentration is
  preserved, not just a count.
- **G. Segment coverage** (`segment_coverage`) — `customer_tier_distribution`
  grouped directly from `Feedback.customer_tier` (only tiers already present
  on ingested feedback — nothing invented) and `unknown_tier_count` for items
  with no tier set.
- **H. Duplicate ratio** (`duplicate_ratio`) — counts only
  `feedback_relationship` rows where **both** endpoints are members of this
  issue (a relationship to a feedback item outside the issue is excluded).
  Since `feedback_relationship` already enforces `feedback_id_a <
  feedback_id_b` and a per-pair `UniqueConstraint` (Phase 3B-2), each
  unordered pair is stored, and therefore counted, exactly once — no
  symmetric double-counting to guard against.
  `duplicate_ratio = duplicate_pair_count / (duplicate_pair_count +
  related_pair_count)`, `null` when that denominator is 0
  (`insufficient_data`, not a false zero). `possible_pairs` (`C(n, 2)` over
  `feedback_count`) and `relationship_coverage` (`total_relationship_pairs /
  possible_pairs`) are included for context on how much of the issue's
  internal structure is actually backed by a computed relationship.
- **I. Time distribution** (`time_distribution`) — a sparse weekly histogram
  (`{"period_start": "<ISO Monday date>", "count": n}`, only weeks with at
  least one item) of the effective timestamp. `date_trunc` buckets according
  to the database session's timezone, which this deployment does **not**
  default to UTC (`Asia/Calcutta`) — the query explicitly normalizes via
  `timezone('UTC', ...)` before truncating so week boundaries are fixed
  regardless of session/server configuration. Deterministic, and reusable as
  a finer-grained input to a future trend analysis.
- **J. Evidence quality** (`evidence_quality`) — **transparent component
  flags, never a blended score**: `source_diversity_sufficient` (`>=
  MIN_SUFFICIENT_SOURCES = 2` distinct sources), `customer_reach_known`
  (`>0` distinct known customers), `severity_data_available` +
  `severity_analyzed_fraction`, `duplicate_concentration`
  (`"low"` `<= 1/3`, `"moderate"` `<= 2/3`, `"high"` above, or
  `"insufficient_data"` when the ratio itself is `null`),
  `recency` (`"recent"` if `days_since_latest <= RECENCY_STALE_DAYS = 30`,
  else `"stale"`, or `"unknown"` with no members), and
  `segment_coverage_known`. A consumer (or the future Priority/Decision
  Engine) decides how, or whether, to combine these — this phase exposes the
  components only.

**Versioning and reproducibility.** `SIGNAL_ALGORITHM_VERSION` (currently
`"signal-v1"`, in `app.services.issue_signals`) is stamped on every snapshot.
Recalculating with the same underlying data and `reference_time` is
byte-for-byte deterministic (verified by a test asserting two consecutive
calculations produce identical JSON and never create a second row for the
same issue). `reference_time` defaults to the current UTC time at request
time; the service function also accepts an explicit value (used by tests) so
recency/trend results don't depend on wall-clock time during a test run.

**Efficiency.** Every signal is computed via PostgreSQL `COUNT`/`GROUP
BY`/`MIN`/`MAX`/`AVG`/`percentile_cont`/`date_trunc` over
`product_issue_feedback` membership joined to `Feedback`/`FeedbackAnalysis`/
`FeedbackRelationship` — no signal loads the full member feedback corpus
into Python to aggregate it there.

**API:**

- `POST /api/v1/issues/{issue_id}/signals/recalculate` — recalculates and
  upserts one issue's snapshot; `404 not_found` if the issue doesn't exist.
- `GET /api/v1/issues/{issue_id}/signals` — returns the last-calculated
  snapshot as-is (no live recompute); `404 not_found` if the issue doesn't
  exist, or `404 signals_missing` (a distinct error code, mirroring
  `EmbeddingMissingError`'s "not found" vs. "not yet computed" split) if the
  issue exists but has never had signals calculated.
- `POST /api/v1/issues/signals/recalculate` — recalculates every issue's
  signals in one call; returns `{signal_version, issues_processed}`.

No endpoint here exposes embedding vectors, secrets, or any field beyond the
signal structures above; errors follow the existing
`{"error": {"code", "message"}}` envelope via `AppError` subclasses.

**Testing**: `tests/test_issue_signals.py` — direct-DB-seeding integration
tests (mirroring Phase 3B-3's approach) covering zero/one/many-member issues,
full severity distribution and missing-analysis handling, known vs.
anonymous customer reach, customer tiers, multi-source distribution,
duplicate-only/related-only/no-relationship ratio cases (including that a
relationship touching a non-member is excluded), recency (recent and stale),
trend (sufficient and insufficient data), weekly time-distribution
bucketing, deterministic reruns (identical output, single row), one-issue
and all-issues calculation, the recalculate/get API pair, the
`signals_missing` vs. issue-`not_found` distinction, the bulk endpoint, and
the `product_issue_id` unique constraint. No test calls Gemini.

Explicitly **not** implemented yet:

- No blended/opaque priority score — deferred to a future Priority Engine
  that explicitly consumes these components.
- No Decision Brief or Decision Audit.
- No configurable trend window or evidence-quality thresholds via API/config
  — `TREND_WINDOW_DAYS`, `MIN_TREND_SAMPLE`, `RECENCY_STALE_DAYS`,
  `MIN_SUFFICIENT_SOURCES`, and the duplicate-concentration thresholds are
  currently fixed constants in `app.services.issue_signals`, bumped via
  `signal_version` if changed.
- No point-in-time signal history — recalculating overwrites the previous
  snapshot; a past snapshot is not retrievable once superseded (by design,
  per the "no uncontrolled duplicate snapshots" requirement).
- No automatic recalculation trigger — signals are only ever computed when
  `POST .../signals/recalculate` is explicitly called (consistent with
  `POST /issues/discover` never running implicitly).

## Phase 4A — Decision Evidence Engine

```
Decision                               (persisted question + deterministic matching inputs)
    ↓ POST .../evaluate (explicit trigger only)
Deterministic Issue Matching           (affected_feature / title keywords, category boost-only)
    ↓
DecisionEvaluation                     (APPEND-ONLY — one new row per evaluation run)
    ↓
DecisionIssueEvidence                  (per matched issue: supporting / counter / gap facts)
    ↓
[future] Phase 4B — LLM synthesis      (writes prose FROM these stored facts, invents none)
[future] Phase 4C — Decision stability (diffs DecisionEvaluation history over time)
```

**Goal and constraints.** Answer questions like "Should we prioritize fixing
checkout?" with a deterministic, evidence-based, fully-provenanced structure
— **no LLM call**, no opaque priority score, and no invented positive
sentiment ("customers are satisfied with X") that this feedback schema
cannot represent. Every fact is traceable back through
`product_issue_feedback` membership to the `Feedback`/`FeedbackAnalysis`
rows that produced it. This phase reuses Phase 3C's `ProductIssueSignalSnapshot`
values directly — **it recalculates no aggregate signal itself.**

### Data model

Three new tables — deliberately not the full five-entity list a naive reading
of the brief might suggest (`Decision`, `DecisionIssueEvidence`,
`DecisionEvidence`, `DecisionGap`, `DecisionSnapshot`); gaps are just facts
with no direction (same JSONB shape as supporting/counter, distinguished by
which list they're in) and don't need their own table, and "snapshot" is
what `DecisionEvaluation` already is:

- **`Decision`** (`app/db/models/decision.py`) — the enduring question:
  `query_text` (free text, for humans/a future LLM), `keywords` (a Postgres
  `ARRAY(String)` — explicit, caller-provided deterministic matching
  inputs), `category` (optional `FeedbackCategory`), `affected_feature_hint`
  (optional, currently informational only — not read by matching),
  `status` (`DecisionStatus`: only `OPEN` is ever set by this phase;
  `ANSWERED`/`ARCHIVED` are reserved for Phase 4B/human use, same
  reserved-member convention as `ProductIssueStatus`).
- **`DecisionEvaluation`** (`app/db/models/decision_evaluation.py`) — one row
  per evaluation run. **Deliberately append-only** — the one place this
  phase diverges from every other "recalculate" table in this codebase
  (`FeedbackAnalysis`, `FeedbackEmbedding`, `ProductIssueSignalSnapshot`, all
  of which overwrite a single row). This is required, not accidental: Phase
  4C (decision stability, not implemented here) needs to compare how a
  decision's evidence changed across multiple points in time, which is only
  possible if history is preserved. Holds `evaluation_version`,
  `reference_at` (the Python-computed timestamp matching/evidence were
  measured against), and `decision_level_gaps` (JSONB — gaps that apply to
  the run as a whole, e.g. no issues matched at all, so they have no single
  issue to attach to).
- **`DecisionIssueEvidence`** (`app/db/models/decision_issue_evidence.py`) —
  one row per `(evaluation, matched issue)` pair:
  `product_issue_signal_snapshot_id` (nullable, `ON DELETE SET NULL`),
  `match_method`, `match_score`, `matched_terms` (JSONB), `volume_summary`
  (JSONB), and `supporting`/`counter`/`gaps` (JSONB arrays of facts).
  `UniqueConstraint(decision_evaluation_id, product_issue_id)`.

**The one genuine data-modeling tension, resolved:**
`ProductIssueSignalSnapshot` is overwritten in place (Phase 3C, by design).
If `DecisionIssueEvidence` only held a live FK to it, a later signal
recalculation would silently rewrite what an old evaluation "saw." Resolved
by recording every fact's `value` **inline**, at evaluation time, in
`DecisionIssueEvidence`'s own JSONB columns — self-contained and immutable.
`product_issue_signal_snapshot_id` is kept only as a "jump to the issue's
*current* signals" convenience pointer (hence `ON DELETE SET NULL`, not
`CASCADE`) — it is never read back to reconstruct historical facts. Verified
by tests that recalculate signals (or delete the snapshot entirely) after an
evaluation and assert the evaluation's recorded facts are byte-identical to
before.

**Indexes:** `decision_evaluation.decision_id`,
`decision_issue_evidence.decision_evaluation_id`,
`decision_issue_evidence.product_issue_id` — all FK-backed, matching the
indexing pattern already used on `product_issue_feedback`.

### Matching (`app/services/decision_matching.py`)

Deterministic only — **no LLM, no NLP keyword extraction, no
embeddings/semantic similarity.** `Decision.keywords` are explicit,
caller-supplied tokens; `query_text` is for humans/a future LLM only and is
never parsed for matching.

- **`affected_feature` match** (highest confidence — a clustering-normalized
  label from Phase 3B-3): exact match (after case-insensitive
  normalization) scores `AFFECTED_FEATURE_EXACT_WEIGHT = 1.0`; a substring
  match either direction scores `AFFECTED_FEATURE_SUBSTRING_WEIGHT = 0.7`.
- **`title` keyword match**: token-overlap fraction (matched keywords /
  total keywords) scaled by `TITLE_TOKEN_OVERLAP_WEIGHT = 0.5`.
- **`category`**: **never a standalone match signal** — only 5
  `FeedbackCategory` values exist, far too coarse alone (every `BUG` issue
  would "match" any bug-related decision). `CATEGORY_BOOST = 0.15` is added
  strictly *after* confirming `base_score > 0` from the two rules above —
  enforced directly in `match_issue()`'s control flow, and covered by a test
  asserting category-only input returns `None`.

`match_score` is a **deterministic relevance/match-strength number** — it is
explicitly **not** a probability, statistical confidence, likelihood, or
model confidence. It is never returned alone: every `DecisionIssueEvidence`
row also carries `match_method` and `matched_terms` (which specific
keyword(s) matched which field(s), the match kind — exact/substring/token
overlap — and a human-readable `reason` string), so the score is always
explainable.

`MIN_MATCH_SCORE = 0.34` — issues scoring below this are excluded entirely,
never stored as a weak/noisy match. Zero matches above threshold produces a
`no_matching_issues_found` decision-level gap. Only `ProductIssueStatus.OPEN`
issues are considered — a question about whether to prioritize something is
inherently about currently-open problems. All constants above are
unvalidated hackathon-scale values (see Limitations).

**Why semantic/embedding matching is deferred:** `FeedbackEmbedding` exists
per-feedback-item, not per-issue — there is no issue-level embedding today.
Doing this properly needs either a new issue-centroid embedding (a new
recompute lifecycle keyed to membership changes that doesn't exist) or an
expensive live comparison against every member's embedding per decision
query. Neither exists, and keyword/`affected_feature` matching is sufficient
to bootstrap this phase. `match_method` is an open enum specifically so a
`SEMANTIC` method can be added later and blended with keyword score without
a schema change.

### Evidence classification (`app/services/decision_evidence.py`)

Reuses `ProductIssueSignalSnapshot` fields directly — classification only,
**no signal recalculation**.

**Core principle — confidence gating:** the same weak signal is
**counter-evidence** when there's enough volume to trust the measurement,
and a **gap** when there isn't enough volume to know.
`MIN_VOLUME_FOR_JUDGEMENT = 5` is the gate for concentration/reach rules —
chosen so it lands directly between the spec's own two examples (2 items
from one source = insufficient, i.e. a gap; 10+ items from one source =
enough to observe concentration, i.e. counter-evidence), both covered by
dedicated tests. **Counter-evidence never means "positive feedback exists"**
(unrepresentable in this schema) — it only ever means the case for this
issue is weaker or less reliable than it first appears.

**Supporting** (`_supporting_facts`): `high_frequency`
(`feedback_count >= HIGH_FREQUENCY_THRESHOLD = 5`),
`high_severity_concentration` (high+critical severity buckets are
`>= HIGH_SEVERITY_FRACTION_THRESHOLD = 0.5` of *analyzed* members —
requires `analyzed_count > 0`), `increasing_trend` (reuses
`trend.direction == "increasing"` with `sufficient_data`),
`multiple_customer_segments` (`>= 2` distinct `customer_tier` values),
`multiple_independent_sources` (reuses
`evidence_quality.source_diversity_sufficient`), `broad_known_customer_reach`
(`distinct_known_customers >= MIN_CUSTOMER_REACH_THRESHOLD = 2`),
`sustained_recency` (reuses `evidence_quality.recency == "recent"`).

**Counter** (`_counter_facts`), each concentration/reach rule gated by
`feedback_count >= MIN_VOLUME_FOR_JUDGEMENT`: `single_source_concentration`,
`high_duplicate_concentration` (reuses
`evidence_quality.duplicate_concentration == "high"` — not gated by volume,
since Phase 3C's own duplicate-ratio denominator already encodes "enough
relationship pairs exist"), `stale_evidence` (reuses
`evidence_quality.recency == "stale"`), `low_known_customer_reach`,
`declining_trend` (`trend.direction == "decreasing"` **with**
`sufficient_data` — **flat trend is deliberately never counter-evidence**,
steady isn't declining; covered by an explicit test).

**Gaps** (`_gap_facts`): `no_signal_snapshot` (handled at the orchestration
level, before any per-snapshot rule runs — a matched issue with no snapshot
at all), `insufficient_recent_feedback` (`trend.sufficient_data == False`),
`insufficient_source_diversity_data` + `low_volume_overall`
(`feedback_count < MIN_VOLUME_FOR_JUDGEMENT`), `missing_customer_segmentation`
(zero known tiers at all among linked feedback), `severity_not_analyzed`
(`analyzed_count == 0`).

**Volume vs. quality, made structural:** `DecisionIssueEvidence.volume_summary`
reports `feedback_count` alongside `distinct_signal_estimate`
(`feedback_count - duplicate_pair_count`, floored at 0, explicitly
documented as a rough approximation) — so a reader can't mistake raw volume
for distinct signal count when duplicate concentration is high. **No
population denominator anywhere** — nothing in this schema claims a
percentage of total customers/users; there is nothing to compute one from.

### Provenance

Every fact — supporting, counter, or gap — has the exact shape:
```json
{
  "code": "increasing_trend",
  "label": "Increasing recent feedback",
  "value": {"direction": "increasing", "current_window_count": 8, "previous_window_count": 3},
  "threshold": null,
  "source_signal_path": "trend.direction"
}
```
`value` is always the literal observed data at evaluation time — never a
reference requiring the current snapshot to interpret. `source_signal_path`
points into the snapshot's JSONB shape for traceability;
`threshold` is the fixed rule threshold compared against, or `null` when the
rule has none (e.g. a trend direction). From `DecisionIssueEvidence.product_issue_id`,
the existing Phase 3B-3/3C traversal (`get_feedback_ids_for_issue`, then the
`feedback`/`feedback_analysis` tables) reaches the raw evidence behind any
fact — no new raw-feedback endpoint was needed for this phase.

`DECISION_EVALUATION_VERSION` (currently `"decision-evidence-v1"`, in
`app.services.decision_evidence`) is stamped on every `DecisionEvaluation`
and deliberately covers both matching and evidence-classification logic as
one version — they always ship and change together in this phase; bump it
whenever a matching rule, evidence rule, or threshold changes.

### Append-only evaluation history

Unlike Phase 3C's `ProductIssueSignalSnapshot`, calling
`POST .../evaluate` **always creates a new `DecisionEvaluation` row** —
previous evaluations are never modified. Verified by a test asserting two
back-to-back calls with identical underlying data produce two distinct rows
with byte-identical evidence content (the deliberate opposite of Phase 3C's
"single row" determinism test — a comment in the test file flags this so a
future maintainer doesn't "fix" it to match the overwrite pattern by
mistake). `GET .../evaluations` and `GET .../evaluations/latest` are ordered
by `reference_at` (computed fresh in Python per call), not `created_at` (a
Postgres `now()` server default reflecting transaction-start time, which can
tie within a single transaction, e.g. a backfill script).

### API

- `POST /api/v1/decisions` — create.
- `GET /api/v1/decisions` — paginated list.
- `GET /api/v1/decisions/{decision_id}` — get one; `404 not_found` if
  missing.
- `POST /api/v1/decisions/{decision_id}/evaluate` — runs matching +
  evidence classification, appends a new evaluation; `404 not_found` if the
  decision doesn't exist. No LLM call, no signal recalculation.
- `GET /api/v1/decisions/{decision_id}/evaluations` — paginated evaluation
  history (lightweight summaries — full per-issue evidence is only on the
  detail endpoints below).
- `GET /api/v1/decisions/{decision_id}/evaluations/{evaluation_id}` — full
  detail (all matched issues + their supporting/counter/gap facts).
- `GET /api/v1/decisions/{decision_id}/evaluations/latest` — convenience
  shortcut; `404 not_found` if the decision has no evaluations yet.

No endpoint exposes embedding vectors, secrets, or any field beyond the
structures above; errors follow the existing
`{"error": {"code", "message"}}` envelope.

**Testing**: `tests/test_decision_evidence.py` — pure unit tests for every
supporting/counter/gap rule (including the confidence-gating boundary
exactly at `MIN_VOLUME_FOR_JUDGEMENT`, and that flat trend fires neither
supporting nor counter) and every matching case (exact/case-insensitive/
substring `affected_feature`, title overlap, multi-keyword partial overlap,
category-alone-never-matches, category boost, weak-match exclusion), plus
integration tests covering the spec's own worked examples end-to-end (a
5-item multi-source/multi-segment/recent issue firing all seven supporting
rules at once; a 2-item single-source issue producing gaps, never counters;
a 10-item single-source issue producing `single_source_concentration` as
counter-evidence), provenance (facts survive signal recalculation and
snapshot deletion), append-only behavior, and the full API surface
(validation, 404s, pagination, latest-evaluation ordering,
OpenAPI route registration). No test calls Gemini or any LLM.

Explicitly **not** implemented yet (as of this phase):

- No LLM synthesis, no Decision Brief generation, no Gemini calls anywhere
  in this phase. **Implemented in Phase 4B, below.**
- No semantic/embedding-based matching (see "Why semantic matching is
  deferred" above).
- No Decision Stability algorithm — `DecisionEvaluation`'s append-only
  design exists specifically to make this possible later, but no diffing
  logic is implemented here.
- No Priority Engine or final priority score.
- No human review workflow (e.g. manually excluding a false-positive
  matched issue without deleting the audit row).
- No configurable matching/evidence thresholds via API/config —
  `MIN_MATCH_SCORE`, the matching weights, `MIN_VOLUME_FOR_JUDGEMENT`,
  `HIGH_FREQUENCY_THRESHOLD`, etc. are fixed, unvalidated hackathon-scale
  constants, bumped via `DECISION_EVALUATION_VERSION`/`MATCH_ALGORITHM_VERSION`
  if changed.
- No retention/archival policy for the now-growing append-only evaluation
  history.
- Keyword matching is exact/substring only — no stemming or fuzzy matching;
  "checkout" won't match "payment flow" without the caller supplying both as
  keywords.

## Phase 4B — Grounded LLM Decision Brief Synthesis

```
Decision → DecisionEvaluation → DecisionIssueEvidence   (Phase 4A, immutable once written)
    ↓
DecisionSynthesisInput builder   (pure, deterministic — reads only Phase 4A rows + ProductIssue title/category)
    ↓
LLMProvider.synthesize_decision_brief()   (GeminiProvider; never raw feedback, never an embedding)
    ↓
Grounding validation              (citation_ids must exist in the input's own registry — mechanical, not LLM-based)
    ↓
DecisionBrief                     (APPEND-ONLY, mirrors DecisionEvaluation's precedent)
```

**The 4A → 4B boundary.** Phase 4A computes evidence; Phase 4B explains it.
The LLM never analyzes raw feedback, never calculates a frequency/severity/
trend/aggregate, never creates new evidence, never invents a number,
percentage, segment, or source, never creates a priority score, and never
makes a recommendation. Its entire job is to write a short headline and
grouped narrative claims over an already-computed, already-persisted fact
set — every claim citing which fact(s) it restates.

### Input contract

`DecisionSynthesisInput` (`app/schemas/decision_brief.py`), built by
`app.services.decision_brief.build_synthesis_input` — a pure function
reading only persisted Phase 4A rows (`DecisionIssueEvidence`,
`DecisionEvaluation.decision_level_gaps`) plus a read-only join to
`ProductIssue.title`/`category`/`affected_feature`. No LLM call, no new
signal computation, no raw feedback access — the same evaluation always
produces a byte-identical input (verified by a test).

Every fact — `DecisionIssueEvidence`'s existing
`{code, label, value, threshold, source_signal_path}` shape — gets a
deterministically-assigned `citation_id`: `"{product_issue_id}:{code}"` for
issue-scoped facts, `"decision:{code}"` for decision-level gaps. Unique
within one evaluation, since each rule fires at most once per issue per
evaluation (Phase 4A guarantee) — verified by a test.

**Issue metadata is contextual, never evidence.** `ProductIssue.title`,
`category`, and `affected_feature` are included for readability only (e.g.
"Checkout fails during payment" reads better than a bare UUID) — the
prompt explicitly states these are not evidence, and a title mentioning a
problem does not by itself license a quantitative or evidentiary claim;
only cited `DecisionIssueEvidence` facts may back one.

### Output contract

Deliberately narrow — `DecisionBriefLLMOutput`
(`app/schemas/decision_brief.py`, `extra="forbid"`), mirroring
`FeedbackAnalysisResult`'s role in Phase 3A:

```python
class NarrativeClaim(BaseModel):
    text: str              # 1-280 chars
    citation_ids: list[str]  # at least 1, each must resolve in the registry

class DecisionBriefLLMOutput(BaseModel):
    headline: str
    supporting_narrative: list[NarrativeClaim]
    counter_narrative: list[NarrativeClaim]
    gap_narrative: list[NarrativeClaim]
```

No recommendation/priority/confidence/score field exists anywhere in this
contract — the LLM has no field to put one in even if it tried. Everything
derivable without synthesis (which issues were matched, `evidence_sufficiency`)
is computed by the backend instead, never asked of the LLM.

### Grounding / anti-hallucination

Three layers, the first two reusing the exact pattern already established
by `analyze_feedback`, the third new to this phase:
1. JSON parse failure / empty response → `LLMOutputValidationError` (reused).
2. Pydantic validation failure (missing field, wrong type, `extra="forbid"`
   violation, empty `citation_ids`) → `LLMOutputValidationError` (reused).
3. **Grounding validation** — every `citation_id` across every claim must
   exist in the input's own citation registry
   (`DecisionSynthesisInput.citation_registry()`). Any unknown citation
   rejects the **entire** generation (no partial acceptance) and raises the
   new `LLMUngroundedOutputError` (502) — distinct from
   `LLMOutputValidationError` for observability: this specifically means
   the model fabricated a reference to evidence it was never given.

**Known limitation, stated plainly**: citation validation proves a claim
points at a *real, supplied* fact — it does not, and cannot, guarantee
every word within that claim's text is strictly entailed by the cited
value (e.g. a cited-but-editorialized sentence). Mitigated, not solved, by
a 280-character claim length cap (less room to smuggle unsupported
content) and explicit prompt instructions; full prose-fact-checking against
cited values is out of scope for this phase.

**No raw feedback, no embeddings sent to Gemini.** The prompt (built in
`app/services/llm/gemini_provider.py`'s `_render_synthesis_prompt`) is
constructed entirely from `DecisionSynthesisInput` — never
`Feedback.raw_text`, never a `FeedbackEmbedding` vector. This is a
meaningfully more private posture than Phase 3A's `analyze_feedback` (which
necessarily *does* send raw text) and is itself a grounding safeguard: with
no raw text in the prompt, there is no rich free-text surface for the model
to draw outside knowledge into.

The prompt's strict rules (verbatim, in `_BRIEF_SYNTHESIS_INSTRUCTIONS`):
use only the supplied facts; no outside knowledge; no invented numbers,
percentages, segments, or sources; no claim of statistical
representativeness (no population denominator exists anywhere in this
system); `match_score` is a relevance ranking, never a probability or
confidence; no priority score; no recommendation; state insufficient
evidence plainly rather than guessing; every claim must cite supplied
evidence and only supplied evidence; issue titles/categories/affected
features are contextual, not evidentiary.

### `evidence_sufficiency`

Computed **deterministically in the backend**, before the LLM is ever
called, from persisted `DecisionIssueEvidence` rows only
(`app.services.decision_brief.compute_evidence_sufficiency`) — never
influenced by, and never waits on, what the LLM writes. Verified by a test
that persists a misleading, falsely-confident fake LLM headline and asserts
the stored `evidence_sufficiency` still reflects only the deterministic
computation.

```
NO_MATCHES    No ProductIssues matched the decision at all.
INSUFFICIENT  At least one issue matched, but none of the matched issues
              has any supporting or counter fact (gaps only, or nothing).
SUFFICIENT    At least one matched issue has at least one supporting or
              counter fact.
```

Note on reaching genuine `INSUFFICIENT` under Phase 4A's *existing,
unmodified* rules: with one or more linked feedback items, an issue's
recency is always either `"recent"` (fires `sustained_recency`, supporting)
or `"stale"` (fires `stale_evidence`, counter — ungated by volume) — so any
non-empty matched issue always carries at least one supporting or counter
fact. Genuine `INSUFFICIENT` in practice means a matched issue with zero
linked feedback (recency `"unknown"`, neither state) — an edge case worth
knowing about, not a bug; Phase 4A's rules were intentionally left
unmodified per this phase's scope boundary.

### Persistence — `DecisionBrief`

**Append-only, and deliberately NOT unique per `decision_evaluation_id`** —
mirrors `DecisionEvaluation`'s own append-only design, and is necessary
here for an additional reason: unlike every deterministic table in this
codebase, LLM output is not guaranteed byte-identical across calls even
with the same input, so regenerating must never silently overwrite what a
past reader actually saw. Every `POST .../brief` call creates a new row.

Columns: `decision_evaluation_id` (FK, `CASCADE` — tied to one *specific*
evaluation, never loosely to the decision, since evaluations are themselves
immutable once written), `status` (`GenerationStatus`: `COMPLETED` |
`FAILED` — no `NEEDS_REVIEW`; considered and rejected, since grounding
validation is binary with no continuous confidence value to threshold
against, unlike `FeedbackAnalysis`), `evidence_sufficiency` (always
populated, both outcomes), `headline`/`supporting_narrative`/
`counter_narrative`/`gap_narrative` (nullable — `NULL` on `FAILED`, never a
malformed/ungrounded value), `provider`/`model`/`generation_version`
(always populated), `error_code` (only on `FAILED`), `generated_at`
(Python-computed — see Versioning/API below), `created_at`.

**The citation registry is deliberately NOT duplicated into this table.**
`DecisionIssueEvidence` facts are already immutable and self-contained
(Phase 4A's guarantee), so `resolve_citations` reconstructs the full
citation → provenance mapping live, on read, from
`decision_evaluation_id`. Storing a second copy would only risk divergence,
never add safety.

Index: `decision_evaluation_id`.

### Versioning

`DECISION_BRIEF_SYNTHESIS_VERSION` (currently `"brief-synthesis-v1"`, in
`app.services.decision_brief`) deliberately bundles the prompt text, the
input-building logic, and the output-validation/grounding rules as **one**
version — same simplification Phase 4A already made for
`DECISION_EVALUATION_VERSION` (they always ship and change together in this
phase). Stamped on every `DecisionBrief`, `COMPLETED` or `FAILED`.

### Failure handling

```
LLM call / validation / grounding failure
        -> persist FAILED DecisionBrief (status, evidence_sufficiency,
           provider/model/version, error_code — never the narrative)
        -> re-raise the same AppError
        -> existing global exception handler produces the standard
           {"error": {"code", "message"}} response
```

No new response/error architecture — reuses `LLMConfigurationError`
(missing/misconfigured provider), `LLMProviderError` (the call itself
failed), `LLMOutputValidationError` (malformed/schema-invalid output), and
adds exactly one new class, `LLMUngroundedOutputError` (a citation didn't
resolve). The failed attempt is always persisted *before* the exception
propagates — mirroring `analyze_feedback`'s exact
persist-then-re-raise shape from Phase 3A.

### Provider integration

New `LLMProvider` Protocol method
(`synthesize_decision_brief(synthesis_input) -> DecisionBriefLLMOutput`),
implemented in `GeminiProvider` the same way `analyze_feedback` is
implemented: a hand-built `genai_types.Schema` (re-applying the
already-discovered workaround — `DecisionBriefLLMOutput`'s
`extra="forbid"` would otherwise emit an `additionalProperties` field the
live Gemini API rejects), `temperature=0`,
`response_mime_type="application/json"`. `app/services/decision_brief.py`
never imports `google.genai` — it depends on the `LLMProvider` interface
only, so a future non-Gemini provider is a matter of implementing the same
two Protocol methods. `factory.py` needed no changes. `FakeLLMProvider`
(`tests/fakes.py`) was extended symmetrically with
`brief_raise_provider_error`/`brief_raise_output_error`/
`brief_unknown_citation_id`/`brief_result` — the default test suite never
calls Gemini.

### Citation / provenance resolution

`GET` responses embed citations **fully resolved inline** — a
`citations: {citation_id: ResolvedCitation}` map alongside every claim's
bare `citation_ids`, so a frontend never needs a second round-trip to
explain a claim. `ResolvedCitation` carries `product_issue_id`,
`issue_title`, `code`, `label`, `value`, `threshold`, `source_signal_path`,
and `category` (`supporting`/`counter`/`gap`) — everything needed to render
provenance, and enough to link onward to the existing
`GET /issues/{product_issue_id}` (which already returns `feedback_ids`) for
raw-feedback drill-down. No new backend endpoint was needed for this chain
— it composes entirely from endpoints Phase 3B-3/3C/4A already built.

**Citations reference signal facts only — raw-feedback-level citation is
explicitly deferred**, unchanged from the Phase 4A proposal's reasoning: a
citation pointing at a specific feedback item would misrepresent
provenance (the LLM never saw that item's text), and nothing is lost —
`ProductIssue.feedback_ids` already lets a frontend drill from issue to raw
feedback independently of the brief.

### API

- `POST /api/v1/decisions/{decision_id}/brief` — generates against the
  decision's **latest** `DecisionEvaluation`; `404 not_found` if the
  decision doesn't exist, `404 not_found` if it has no evaluations yet.
  Synchronous, matching `analyze_feedback`'s existing request/response
  style — no background-job infrastructure exists in this codebase.
- `GET /api/v1/decisions/{decision_id}/brief` — latest **COMPLETED** brief
  across all evaluations; `404 not_found` if none exists yet. `FAILED`
  attempts are never returned here.
- `GET /api/v1/decisions/{decision_id}/brief/history` — paginated, **all**
  attempts (`COMPLETED` and `FAILED`), newest first.
- `GET /api/v1/decisions/{decision_id}/brief/{brief_id}` — one specific
  historical brief, full detail with resolved citations.

**Ordering ("latest") uses `generated_at` (Python-computed per generation
call via `datetime.now(timezone.utc)`), not `created_at`** (a Postgres
`now()` server default that reflects transaction-start time and can tie
across rows committed in the same transaction) — the exact same fix already
applied to `DecisionEvaluation.reference_at` ordering in Phase 4A, reapplied
here proactively rather than rediscovered.

**Testing**: `tests/test_decision_brief.py` — input DTO/registry
construction and determinism, citation-ID uniqueness, valid grounded
synthesis and citation resolution, malformed output, Pydantic
`extra="forbid"`/empty-`citation_ids`/blank-text/over-length rejection, an
unknown citation triggering `LLMUngroundedOutputError`, provider failure,
"no untrusted narrative persisted on any failure path" (asserted across all
three failure modes in one test), `NO_MATCHES`/`INSUFFICIENT`/`SUFFICIENT`
evidence sufficiency (including that it stays authoritative against a
deliberately misleading fake LLM headline), regeneration producing multiple
rows, provider/model/version metadata on both outcomes, a full Phase 4A
fixture round-trip proving no drift between persisted facts and resolved
citations, and the complete API surface (generate/get-latest/history/detail,
404s, pagination). No test in the default suite calls Gemini.
`tests/test_decision_brief_gemini_smoke.py` holds one opt-in real-API test,
excluded from the default run via `pytest.ini`'s
`addopts = -m "not gemini_smoke"` (a marker-based exclusion, not merely an
API-key check — this environment's `.env` happens to have `GEMINI_API_KEY`
configured, so a bare `skipif` on the key alone would not have been a
reliable guarantee).

Explicitly **not** implemented yet (as of this phase):

- No Phase 4C Decision Stability algorithm — `DecisionBrief`'s append-only
  design (like `DecisionEvaluation`'s) exists to make this possible later.
  **Implemented in Phase 4C, below.**
- No semantic/embedding-based matching, no RAG, no raw-feedback-level
  citations (all deferred per the Phase 4A/4B proposals' reasoning).
- No Priority Engine, no final priority score, no recommendation engine —
  the brief characterizes evidence, never issues a verdict.
- No human review/editing workflow for generated briefs.
- No async/background generation — synchronous only, matching existing
  precedent; no job-queue infrastructure exists yet.
- No retention/archival policy for the append-only brief history.
- Full prose-fact-checking against cited values — citation validation is
  mechanical (does the ID exist?), not semantic (does every word in the
  claim match the cited value?); see the grounding limitation above.

## Phase 4C — Decision Stability & Evidence Change Analysis

```
Decision → DecisionEvaluation #N-1 ↘
                                     Deterministic comparison → ChangeEvent[] → convenience rollup
Decision → DecisionEvaluation #N   ↗
```

**Goal.** Answer "has the evidence behind this decision remained stable?"
and "what changed between evaluations?" by deterministically comparing two
already-persisted `DecisionEvaluation`s. **No LLM anywhere in this phase**
— every dimension is a set/typed-value comparison over already-immutable
JSONB. **No new database table** — the comparison is a pure, reproducible
function of immutable Phase 4A inputs, so persisting its output would only
duplicate data without adding reproducibility, auditability, or performance
benefit (unlike `DecisionBrief`, whose LLM output is genuinely
non-deterministic and therefore worth persisting). Alembic head is
unchanged by this phase: `1009778db62e`.

**The structured `events` list is authoritative.** `IssueStabilityStatus`
and `DecisionStabilityStatus` are convenience rollups only — they
summarize evidence-pattern movement, never hide or override the underlying
events, and the API always returns the complete `events` list alongside
the status.

### Historical provenance guarantees (invariants A–D)

- Compares **only** persisted `DecisionEvaluation` + `DecisionIssueEvidence`
  rows — never `ProductIssueSignalSnapshot` (live or via its convenience
  FK). Verified by a test that recalculates the live snapshot with a
  wildly different `reference_time`, then deletes it outright, and asserts
  the comparison result is **byte-identical** before and after both.
- Never compares `ProductIssue.title`/`category`/`affected_feature`
  against historical evidence — Phase 4A's `DecisionIssueEvidence` never
  snapshotted these fields (Phase 4B reads them live, at synthesis time,
  not at evaluation time), so a historical comparison against them would
  actually be comparing old evidence against the issue's *current*,
  possibly-since-changed metadata — a category error, not a real
  evaluation-to-evaluation comparison. This is a genuine schema gap, not
  an oversight; closing it would require Phase 4A to denormalize issue
  metadata onto `DecisionIssueEvidence` at write time, which is out of
  scope here.
- Never reads `DecisionBrief` prose — `app/services/decision_stability.py`
  never imports anything from `app.services.decision_brief` or
  `app.services.llm`, a structural guarantee stronger than a test.

### Definition of stability — several dimensions, never one score

Rejecting a single blended score, consistent with Phase 3C/4A's own
"transparent components, never a blended score" precedent:

1. **Issue-set membership** — did the matched-issue set change?
2. **Supporting fact-set** (per issue) — codes added/removed.
3. **Counter fact-set** (per issue) — codes added/removed.
4. **Gap fact-set** (per issue) — codes added ("newly insufficient") /
   resolved ("no longer insufficient").
5. **Trend state** (per issue) — *derived*, not directly stored (below).
6. **Match relevance** (per issue) — `match_method`/`match_score` changes,
   reported strictly as relevance, never confidence.
7. **Numeric magnitude** (per issue) — `volume_summary` and fact `value`
   fields, compared even when no fact category changed.

"Source/segment stability" is deliberately **not** a separate dimension —
it's a filtered *view* over (2)-(4) (facts whose code is
`multiple_independent_sources`/`single_source_concentration`/etc.), not
independently computable.

### Evaluation comparison model

`compare_evaluations(db, previous, current)` — reads exactly two
evaluations' `DecisionIssueEvidence` rows via the existing
`get_evidence_for_evaluation` helper (Phase 4A), plus each evaluation's
`decision_level_gaps`. Fields that participate: `product_issue_id`
(identity), `match_method`/`match_score`, `volume_summary`,
`supporting`/`counter`/`gaps` fact-code-sets and their `value`s.
`matched_terms` is deliberately **not** deep-diffed — it's match provenance
for one point in time, not itself an evidence signal; both evaluations'
`matched_terms` remain visible in the raw evidence, just not diffed field
by field.

**`evaluation_version_mismatch`** — set when the two evaluations were
produced under different Phase 4A rule versions: an apparent change may be
an artifact of a rule change, not real-world evidence drift. Always
surfaced, never hidden. Verified by a test that directly constructs a
second `DecisionEvaluation`/`DecisionIssueEvidence` pair with a hand-set
differing `evaluation_version` (the same minimal-mocking technique already
used in `tests/test_decision_brief.py`).

### Issue lifecycle

Computed by a set diff over `product_issue_id` present in each
evaluation's evidence — never touching `ProductIssue`. Present in both →
evidence/match diffed. Present only in current → `ISSUE_APPEARED` /
`IssueStabilityStatus.NEW`. Present only in previous → `ISSUE_DISAPPEARED`
/ `REMOVED`. Category/feature context changes are explicitly **not**
attempted (see the provenance guarantees above).

### Evidence fact comparison

Canonical identity: `(product_issue_id, bucket, code)` — directly reusing
Phase 4B's own citation_id format (`f"{product_issue_id}:{code}"` /
`f"decision:{code}"`) for consistency. A three-way set diff over this
identity (`_diff_fact_index`, shared code path for both per-issue facts
and `decision_level_gaps`) produces `SUPPORTING_FACT_ADDED/REMOVED`,
`COUNTER_FACT_ADDED/REMOVED`, `GAP_ADDED/RESOLVED` (issue-scoped) or
`DECISION_LEVEL_GAP_ADDED/RESOLVED` (decision-scoped) events. "Resolved,"
not "removed," for gaps specifically — a gap disappearing is neutral, never
framed as a negative event disappearing.

### Numeric/value comparison

Every fact's `value` is, in practice, always a dict of named sub-fields
(confirmed against every `_fact(...)` call site in
`app.services.decision_evidence`). Each key is compared by **JSON scalar
type**: `bool` (checked before `int`/`float`, since `bool` is an `int`
subclass in Python) and `str` → `changed`/no-event; numbers → exact-equality
`increased`/`decreased`/no-event (**no invented epsilon/significance
threshold** — every genuine delta is reported, nothing is elevated into an
"event" beyond what Phase 4A's own existing thresholds already decide);
anything structured (`dict`/`list`) or a value that changed shape entirely
→ `not_evaluated`, with both raw values still included verbatim rather than
guessed at. A key newly present → `newly_available`; a key that disappears
→ `unavailable`.

This is a **type-based** allowlist (JSON scalar types are safe to compare;
structured values are not) rather than a hand-maintained per-fact-code
field list — a deliberate refinement of the original proposal's "named
allowlist per fact code" framing, chosen because it self-generalizes
correctly to any current or future fact shape without needing maintenance,
while providing the identical safety guarantee (never invent semantics for
a structured value).

### Trend comparison

Phase 3C never persists a `"flat"` fact directly — trend state is
**reconstructed** (`infer_trend_state`) purely from which of exactly three
fact codes is present on an evidence row: `increasing_trend` (supporting) →
`increasing`; `declining_trend` (counter) → `decreasing`;
`insufficient_recent_feedback` (gap) → `insufficient`; none present →
`flat`. A state change produces `TREND_CHANGED` with a factual
`previous_value`/`current_value` pair. **Direction reversal**
(`increasing↔decreasing` specifically) is tagged
`interpretation: "direction_reversed"` — any other transition (e.g.
`flat→increasing`, `insufficient→increasing`) is tagged `"transitioned"`.
**Neither interpretation carries a positive/negative judgment** — a
decreasing trend is never described as universally negative, nor increasing
as universally positive; Phase 4A's own rules already decide which bucket
(`supporting`/`counter`) a trend fact falls into, via the ordinary fact-set
diff above, and Phase 4C never re-derives or overrides that assignment.
Because trend state is defined purely by these three codes' presence,
every `TREND_CHANGED` event is provably accompanied by at least one
corresponding fact-set event — `TREND_CHANGED` is additional and
clarifying, never a hidden extra signal.

### Evidence-strength changes

Canonical event types, matching the approved vocabulary directly:
`supporting_fact_added`, `supporting_fact_removed`, `counter_fact_added`,
`counter_fact_removed`, `gap_added`, `gap_resolved`. **No
`supporting − counter` arithmetic anywhere.**

### Matching changes

`match_method`/`match_score` compared for issues present in both
evaluations, reported as `MATCH_RELEVANCE_CHANGED` with interpretation
`increased`/`decreased`/`changed` — **never** "confidence." `match_score`
can legitimately shift between two evaluations of the same `Decision`
without any decision edit, since `ProductIssue.title`/`affected_feature`
can change between evaluations (clustering reruns) and matching is
recomputed fresh against current issue metadata on every `/evaluate` call.

### Convenience rollup (never authoritative)

**Issue-level** (`IssueStabilityStatus`): `NEW`/`REMOVED` for lifecycle;
otherwise `STRENGTHENED` (supporting added or counter removed, with no
opposing movement), `WEAKENED` (the mirror), `MIXED` (opposing movement,
**or any gap movement at all** — gaps carry no directional weight under
Phase 4A's own design, so a gap appearing or resolving never counts as
"strengthened"/"weakened" on its own, forcing `MIXED` instead), else
`NO_CHANGE`. A `TREND_CHANGED` event never independently affects this
rollup — its underlying fact-set movement (an `increasing_trend`/
`declining_trend`/`insufficient_recent_feedback` code appearing or
disappearing) is already counted through the ordinary fact-set diff.

**Decision-level** (`DecisionStabilityStatus`): `INSUFFICIENT_HISTORY`
(fewer than 2 evaluations — see below); any decision-level gap change, or
any issue being `NEW`/`REMOVED`, forces `MIXED` (a new issue appearing
isn't inherently strengthening the case, and one disappearing isn't
inherently weakening it — Phase 4C invents no direction for either);
otherwise `STABLE` (all `NO_CHANGE`), `STRENGTHENED`/`WEAKENED` (uniform
movement), else `MIXED`.

**`STABLE` means the evidence pattern has not changed under these
deterministic rules — it is not a judgment that the underlying decision is
correct, good, or should be acted on.**

### First-evaluation behavior

`DecisionStabilityStatus.INSUFFICIENT_HISTORY` is structurally distinct
from "no changes detected" — `GET .../stability` returns `200` (never an
error) with `evaluations_available` (0 or 1),
`previous_evaluation_id`/`current_evaluation_id` both `null`, and an empty
`events` list, versus a genuinely-stable comparison's `status: "stable"`
with `events: []` and both evaluation ids populated — a consumer can never
conflate "nothing to compare yet" with "nothing changed."

### Time/order model

`get_last_two_evaluations` orders by `reference_at` (never `created_at` —
the same ordering lesson already discovered and fixed twice, for
`DecisionEvaluation.reference_at` in Phase 4A and `DecisionBrief.generated_at`
in Phase 4B), `id` as a deterministic tiebreak. **Identical `reference_at`**
→ comparison still proceeds, flagged via `reference_at_tie: true`, never
silently hidden. **Out-of-order `reference_at`** (a caller backfills an
earlier evaluation after a later one already exists) → the default
`.../stability` endpoint's "current"/"previous" roles are still assigned by
`reference_at`, not insertion order — verified by a test that calls
`/evaluate` with a *later* `reference_time` first and an *earlier* one
second, and confirms the chronologically-later one is still treated as
"current." The explicit pairwise-compare endpoint instead respects the
caller's literal `from`/`to` URL order (see API, below) — a deliberate,
documented difference in behavior between the two endpoints.

### Change-event model

One canonical shape reused for every dimension:

```json
{
  "type": "trend_changed",
  "product_issue_id": "3fae...-...",
  "fact_code": null,
  "bucket": null,
  "previous_value": "increasing",
  "current_value": "decreasing",
  "source_signal_path": "trend.direction",
  "interpretation": "direction_reversed"
}
```

`type` (`ChangeEventType`) and `interpretation` (`ChangeInterpretation`)
are both closed Python enums — a fixed, machine-readable vocabulary,
**never** LLM-generated or free-form text.

### Challenge-My-Decision foundation (building blocks only — not implemented)

Every example challenge question maps directly to a filter over the
`events` list already produced here, with no new computation needed: "trend
reversed" → `TREND_CHANGED` with `interpretation == "direction_reversed"`;
"counter-evidence increased" → count of `COUNTER_FACT_ADDED`; "a new issue
appeared" → `ISSUE_APPEARED`; "evidence became stale" →
`COUNTER_FACT_ADDED` where `fact_code == "stale_evidence"`; "gaps
resolved/appeared" → `GAP_RESOLVED`/`GAP_ADDED` directly. Phase 4C returns
**no** ranking, winner, or recommendation field — a future Challenge feature
is a pure query/filter over this output, not built here.

### API

- `GET /api/v1/decisions/{decision_id}/stability` — default comparison
  (latest vs. second-latest by `reference_at`); `404 not_found` only if the
  decision itself doesn't exist. `200` with `status: "insufficient_history"`
  if fewer than 2 evaluations exist — not an error, since a second
  evaluation simply not having happened yet is a normal state.
- `GET /api/v1/decisions/{decision_id}/evaluations/{from_evaluation_id}/compare/{to_evaluation_id}`
  — explicit comparison between any two evaluations (not limited to
  adjacent ones); `404` if the decision or either evaluation is missing or
  belongs to a different decision. `from_evaluation_id` is treated as
  "previous" and `to_evaluation_id` as "current" exactly as ordered in the
  URL — unlike the default endpoint, this one does **not** reorder by
  `reference_at`, so a caller can deliberately compare "backwards in time";
  both evaluations' `reference_at` are included in the response so this is
  always visible, never silently confusing.

No `/stability/history` endpoint — a derived convenience (an adjacent-pair
timeline) a consumer can already assemble from `GET .../evaluations` plus
the pairwise endpoint; adding it now would be a redundant endpoint.

### Versioning

`DECISION_STABILITY_VERSION` (currently `"stability-v1"`, in
`app.services.decision_stability`) bundles the comparison algorithm and the
`ChangeEventType`/`ChangeInterpretation` vocabulary as one version — the
same simplification already made twice. Since nothing is persisted, this
version is surfaced only in the API response's `stability_version` field.

### Performance

Exactly 2 calls to the existing `get_evidence_for_evaluation` per
comparison (regardless of decision size), plus 1-2 lightweight
`DecisionEvaluation` lookups — no SQL-side diffing, no vector search, no
new infrastructure. All comparison logic is Python over the small,
already-fetched JSONB, bounded by matched-issue-count × facts-per-issue.

**Testing**: `tests/test_decision_stability.py` — pure-unit tests (no DB)
for `_compare_value`'s full type matrix (including the `bool`-is-not-`int`
guard), `_diff_fact_index`, trend-state inference and transition
classification (including the explicit "never labels decreasing as
negative" regression guard), match-relevance and volume-summary
comparison, and both rollup functions' complete decision tables (including
the gap-forces-MIXED and NEW/REMOVED-forces-MIXED rules); integration
tests (real `evaluate_decision`/`calculate_issue_signals` fixtures) for
zero/one-evaluation `INSUFFICIENT_HISTORY`, identical-evaluations `STABLE`,
issue appeared/disappeared, supporting/counter fact added (via real growth
and via newly-introduced duplicate relationships), supporting-removed +
counter-added via elapsed-time staleness, decision-level gap resolution,
numeric-only value change with no fact-set change, a structured
(`customer_tier_distribution`) value change correctly marked
`not_evaluated`, trend direction reversal in both directions, insufficient
→ valid trend, **signal-snapshot recalculation/deletion producing a
byte-identical result**, repeated-comparison determinism,
`evaluation_version_mismatch`, `reference_at_tie`, out-of-order
`reference_at` ordering, and the full API surface (both endpoints, 404s,
OpenAPI registration). No test calls Gemini or any LLM — this phase has no
LLM dependency to fake.

Explicitly **not** implemented yet:

- No Challenge-My-Decision engine itself — only its deterministic building
  blocks (the filterable `events` vocabulary).
- No priority/importance ranking of changes.
- No time-weighted/decayed stability scoring.
- No cross-decision comparison.
- No automatic/scheduled comparison — on-demand `GET` only.
- No `/stability/history` (all-pairs/adjacent-pairs timeline) endpoint.
- No issue category/feature historical comparison (schema doesn't support
  it — see the provenance guarantees above).
- No modification to Phase 3C signal calculation, Phase 4A matching/evidence
  rules, or Phase 4B synthesis — Phase 4C is a strictly read-only consumer.

## Frontend contract

The frontend is built separately by another teammate. This backend's job is to
expose clean, predictable JSON — every success response is a typed Pydantic
schema, every error response follows the same `{"error": {"code", "message"}}`
envelope regardless of which layer raised it.
