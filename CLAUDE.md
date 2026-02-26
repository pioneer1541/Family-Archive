# Family Knowledge Vault — CLAUDE.md

AI assistant guide for the Family Knowledge Vault (FKV) codebase. Read this before making any changes.

---

## Project Overview

**Family Knowledge Vault** is a self-hosted document management and AI assistant system designed for personal family archives. It ingests documents from a NAS share and Gmail, extracts text (with OCR fallback), generates bilingual summaries (Chinese / English), classifies documents into a taxonomy, indexes them in a vector store, and exposes a chat-based agent for querying the archive.

**Tech stack at a glance:**
| Layer | Technology |
|---|---|
| Backend API | FastAPI + Uvicorn (Python 3.12) |
| Task queue | Celery + Redis |
| Relational DB | SQLite via SQLAlchemy 2.x + Alembic |
| Vector store | Qdrant |
| LLM / Embeddings | Ollama (local) — Qwen3 model family |
| Frontend | Next.js 14 (App Router) + next-intl (`zh-CN` / `en-AU`) |
| E2E tests | Playwright |
| Unit tests (FE) | Vitest + Testing Library |
| Unit tests (BE) | pytest |
| Containerisation | Docker Compose |

---

## Repository Layout

```
Family-Archive/
├── backend/                  # FastAPI application + Celery worker
│   ├── app/
│   │   ├── main.py           # FastAPI app factory, lifespan hooks, background loops
│   │   ├── config.py         # Pydantic-settings (all FAMILY_VAULT_* env vars)
│   │   ├── models.py         # SQLAlchemy ORM models
│   │   ├── schemas.py        # Pydantic request/response schemas
│   │   ├── crud.py           # Database helper functions
│   │   ├── db.py             # Engine + SessionLocal
│   │   ├── celery_app.py     # Celery instance
│   │   ├── worker.py         # Celery task definitions
│   │   ├── logging_utils.py  # Structured logging helpers
│   │   ├── api/
│   │   │   ├── routes.py     # All API route handlers (single router)
│   │   │   └── deps.py       # FastAPI dependency injection (get_db)
│   │   └── services/         # Business logic modules
│   │       ├── agent.py               # Agent orchestration (plan → execute → synthesize)
│   │       ├── agent_graph*.py        # LangGraph agent graph nodes/state/edges
│   │       ├── bill_facts.py          # Structured bill extraction
│   │       ├── document_summary.py    # Summary orchestration
│   │       ├── friendly_name.py       # Auto title generation
│   │       ├── governance.py          # Category debt detection/blocking
│   │       ├── image_hash.py          # pHash near-duplicate detection
│   │       ├── ingestion.py           # Core ingestion pipeline (Celery task)
│   │       ├── llm_summary.py         # LLM calls: summary, category, name
│   │       ├── mail_ingest.py         # Gmail polling + attachment download
│   │       ├── map_reduce.py          # Hierarchical map-reduce summarisation
│   │       ├── nas.py                 # NAS directory scanning
│   │       ├── ocr_fallback.py        # Tesseract OCR for scanned PDFs/images
│   │       ├── parsing.py             # Text extraction (PDF/DOCX/XLSX/TXT), chunking
│   │       ├── path_scan.py           # Recursive file discovery
│   │       ├── planner.py             # Agent planner (structured decision JSON)
│   │       ├── qdrant.py              # Qdrant client, upsert, search, collection mgmt
│   │       ├── search.py              # Hybrid retrieval (Qdrant vector + lexical)
│   │       ├── source_tags.py         # Category path → labels mapping
│   │       ├── sync_run.py            # Aggregate sync run (NAS + mail)
│   │       ├── tag_rules.py           # Config-driven tag inference
│   │       └── vl_fallback.py         # Vision-language model fallback for images
│   ├── alembic/              # DB migrations
│   │   └── versions/         # Migration scripts (date-prefixed, sequential)
│   ├── evaluation/           # Offline evaluation harnesses and case banks
│   ├── scripts/              # One-off maintenance scripts (backfill, cleanup, etc.)
│   ├── tests/                # pytest test suite
│   ├── requirements.txt
│   ├── pytest.ini
│   ├── alembic.ini
│   └── Dockerfile
├── frontend/                 # Next.js App Router application
│   ├── app/
│   │   └── [locale]/         # i18n route group (zh-CN / en-AU)
│   │       ├── dashboard/    # Dashboard page
│   │       ├── docs/         # Document list page
│   │       ├── cats/         # Categories browser
│   │       └── agent/        # AI agent chat page
│   ├── src/
│   │   ├── components/       # React components (agent, cats, docs, overlays, shell)
│   │   └── lib/
│   │       ├── api/          # API client (types, real adapter, mock adapter)
│   │       └── ui-state/     # Overlay, toast, sync, content-viewer context/state
│   ├── messages/             # i18n strings (en-AU.json, zh-CN.json)
│   ├── i18n/                 # next-intl routing/request config
│   ├── tests/
│   │   ├── e2e/              # Playwright end-to-end tests
│   │   └── unit/             # Vitest unit tests
│   ├── package.json
│   ├── next.config.mjs
│   └── playwright.config.js
├── services/
│   └── kb-worker/config/
│       └── tag_rules.json    # Tag family/synonym/whitelist config (edit to add rules)
├── .github/workflows/
│   ├── family-vault-gate.yml # Primary CI gate (backend pytest + frontend build/test)
│   └── ai-code-review.yml    # AI code review on PRs
├── docker-compose.yml        # Local dev full-stack (API + Worker + Redis + Qdrant + Frontend)
├── docker-compose.prod.yml   # Production variant
├── Makefile                  # All common commands
├── .env.example              # Environment variable reference (copy to .env for local dev)
└── .gitignore
```

---

## Development Commands

All commands should be run from the repository root unless noted.

### Docker Compose (full stack)

```bash
make up          # Start all containers (detached)
make down        # Stop all containers
make restart     # Recreate api + worker + frontend containers only
make ps          # Show container status
make logs        # Tail last 120 lines from all containers
```

### Backend (in-container)

```bash
make test-backend       # Run pytest -q inside fkv-api container
make db-bootstrap       # Create all SQLAlchemy tables (idempotent)
make openapi            # Export openapi.json snapshot
make openapi-check      # Verify openapi.json matches current API (contract freeze)
```

**Without Docker (local venv):**
```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 18180
# Worker:
.venv/bin/celery -A app.worker worker --loglevel=info
# Tests:
.venv/bin/pytest -q
```

### Frontend

```bash
cd frontend
npm install
npm run dev          # Dev server on :18081
npm test             # Vitest unit tests
npm run build        # Production build
npm run check:manifest  # Validate Next.js app manifest prefix
```

### Evaluation & Governance

```bash
make eval-all            # Run all eval suites (M1, planner, map-reduce, search, cross-lang, agent)
make eval-agent          # Agent eval (random 20-case sample)
make eval-agent-dual     # Agent eval + boundary suite
make eval-agent-trend    # Aggregate trend from historical eval reports
make governance-snapshot # Capture category debt snapshot
make governance-trend    # Compute trend from snapshots
make governance-gate     # CI category debt gate (completed docs must have zero legacy paths)
make check-all           # Full gate: openapi-check + test-backend + governance + eval-all + e2e-ui
make e2e-ui              # Playwright E2E tests (uses mcr.microsoft.com/playwright Docker image)
```

---

## CI/CD

**Primary gate** (`.github/workflows/family-vault-gate.yml`):
- Triggers on `push` to `main` and `pull_request` (`opened`, `reopened`, `synchronize`, `ready_for_review`).
- Two parallel jobs:
  - `backend-tests`: Python 3.12, install `requirements.txt`, run `pytest -q`.
  - `frontend-tests-build`: Node 20, `npm ci`, `check:manifest`, `npm test`, `npm run build`.
- Full Docker Compose integration and Playwright E2E are run locally via `make check-all`.

**AI code review** (`.github/workflows/ai-code-review.yml`): Runs on PR events.

---

## Environment Configuration

All settings live in `backend/app/config.py` as a `pydantic-settings` `Settings` class with prefix `FAMILY_VAULT_`. Copy `.env.example` to `.env` and adjust.

Key variables:

| Variable | Default | Purpose |
|---|---|---|
| `FAMILY_VAULT_DATABASE_URL` | `sqlite:///./family_vault.db` | SQLite DB path |
| `FAMILY_VAULT_REDIS_URL` | `redis://redis:6379/0` | Celery broker |
| `FAMILY_VAULT_QDRANT_URL` | `http://qdrant:6333` | Qdrant endpoint |
| `FAMILY_VAULT_QDRANT_ENABLE` | `0` (tests) / `1` (Docker) | Enable vector indexing |
| `FAMILY_VAULT_OLLAMA_BASE_URL` | `http://ollama:11434` | Local LLM server |
| `FAMILY_VAULT_SUMMARY_MODEL` | `qwen3:4b-instruct` | Summary/category/name LLM |
| `FAMILY_VAULT_EMBED_MODEL` | `qwen3-embedding:0.6b` | Embedding model |
| `FAMILY_VAULT_NAS_DEFAULT_SOURCE_DIR` | `/volume1/Family_Archives` | NAS root (strictly enforced) |
| `FAMILY_VAULT_NAS_AUTO_SCAN_ENABLED` | `0` | Background NAS polling |
| `FAMILY_VAULT_MAIL_POLL_ENABLED` | `0` | Background Gmail polling |
| `FAMILY_VAULT_CELERY_TASK_ALWAYS_EAGER` | `1` (tests) | Synchronous Celery for tests |
| `FAMILY_VAULT_AUTO_CREATE_SCHEMA` | `1` | Auto-create SQLite tables on startup |

List-type variables (`INGESTION_ALLOWED_EXTENSIONS`, etc.) can be passed as comma-separated strings or JSON arrays.

---

## Database Schema & Migrations

- ORM models are in `backend/app/models.py`.
- Migrations are managed by **Alembic** (`backend/alembic/versions/`).
- Files are date-prefixed and sequential: `YYYYMMDD_NNNN_description.py`.
- `FAMILY_VAULT_AUTO_CREATE_SCHEMA=1` auto-creates tables on startup (used in dev/Docker); run Alembic for production migrations.

**Core tables:**

| Table | Purpose |
|---|---|
| `documents` | One row per ingested file; holds status, titles, summaries, category |
| `chunks` | Text chunks for each document (for embedding/search) |
| `bill_facts` | Structured bill data extracted from `finance/bills/*` docs |
| `document_tags` | Structured tags (`family:value`), auto or manual |
| `ingestion_jobs` | Queue/retry state for ingestion requests |
| `sync_runs` / `sync_run_items` | Aggregate NAS + mail sync run records |
| `tasks` | User-facing task tracker |
| `source_file_states` | mtime/size cache for incremental NAS scanning |
| `mail_processed_messages` | Dedup guard for Gmail polling |
| `mail_ingestion_events` | Audit log of mail attachments processed |
| `ignored_ingestion_paths` | Paths that were deleted from queue and should not re-enqueue |

**Adding a new migration:**
```bash
cd backend
alembic revision --autogenerate -m "describe_change"
# Review the generated file in alembic/versions/, then:
alembic upgrade head
```

---

## Ingestion Pipeline

The ingestion pipeline is a Celery task defined in `backend/app/services/ingestion.py` and `backend/app/worker.py`.

**Flow per document:**
1. **Dedup** — SHA-256 hash check (exact duplicate) + pHash Hamming distance (image near-duplicate).
2. **Text extraction** — `parsing.py`: PDF (pypdf), DOCX (python-docx), XLSX (openpyxl), TXT/MD.
3. **OCR fallback** — if extracted text is empty, `ocr_fallback.py` renders PDF pages via pypdfium2 + Tesseract.
4. **VL fallback** — `vl_fallback.py` (vision-language model) for image files.
5. **Metadata fallback** — if still empty, index a stub chunk so the file remains visible.
6. **Chunking** — `parsing.py:chunk_text()` with target 320 tokens, 48-token overlap.
7. **Summarise** — `document_summary.py` → `llm_summary.py` → Ollama.
8. **Category** — LLM-based classification into the taxonomy path (e.g. `finance/bills/electricity`).
9. **Friendly name** — Bilingual title generation (Chinese-first).
10. **Tag inference** — `tag_rules.py` applies `tag_rules.json` rules.
11. **Governance guard** — `governance.py` blocks legacy category paths and rewrites to `archive/misc`.
12. **Qdrant upsert** — `qdrant.py` embeds chunks and upserts to the vector collection `fkv_docs_v1`.
13. **Bill facts** — if category is `finance/bills/*`, `bill_facts.py` extracts structured payment data.

**Retry semantics:** Failed jobs retry up to `INGESTION_RETRY_MAX_RETRIES` times with exponential backoff. `error_code` tracks `retrying:N/M:reason`. After exhaustion the job transitions to `failed`.

---

## Category Taxonomy

Documents are classified into a hierarchical path. The canonical taxonomy is enforced by `governance.py`:

- **Legacy / blocked paths** (auto-rewritten to `archive/misc`): `general`, `finance/utilities`, `finance/telecom`, `property/strata`, `property`.
- Preferred paths follow the pattern: `finance/bills/electricity`, `property/insurance`, `archive/misc`, etc.
- Use `GET /v1/governance/category-debt` to audit remaining legacy paths.
- The CI gate fails if any `completed` document has a legacy path.

---

## Tag System

Tags use a `family:value` key structure. Allowed families (defined in `services/kb-worker/config/tag_rules.json`):
`vendor`, `account`, `person`, `pet`, `location`, `device`, `topic`, `project`, `status`

- `topic` tags are gated by a whitelist; `status` tags by a separate whitelist.
- Max 12 tags per document (max 3 `topic` tags).
- Synonym normalisation is configured in `tag_rules.json` (e.g. `vendor:agl-energy` → `vendor:agl`).
- Manual tag edits via `PATCH /v1/documents/{doc_id}/tags`; auto-tags refresh after summary regeneration.

---

## Search & Agent

### Search (`/v1/search`)
- **Hybrid retrieval**: Qdrant vector search first; lexical fill if vector hits are insufficient.
- Tag filters: `tags_all` (AND), `tags_any` (OR).
- Source-missing documents excluded by default (`include_missing=false`).

### Agent (`/v1/agent/execute`)
Planner → Executor → Synthesizer pipeline:
1. **Planner** (`planner.py`): Emits structured `PlannerDecision` JSON (intent, route, slots).
2. **Executor** (`agent_graph_nodes.py`): Routes to:
   - Structured actions (`queue_view`, `reprocess_doc`, `tag_update`, `bill_attention`, `bill_monthly_total`) — no vector retrieval.
   - Semantic/open Q&A — hybrid retrieval with context budget.
3. **Synthesizer**: Produces a `Result Card`; falls back to deterministic template on LLM failure.

Request supports `conversation` (multi-turn history), `client_context` (locale/timezone), and `doc_scope` (constrain to selected docs).

**Agent graph** (`FAMILY_VAULT_AGENT_GRAPH_ENABLED`): Optional LangGraph-based execution path. Shadow mode available for comparison without affecting live responses.

---

## Frontend Architecture

- **Framework**: Next.js 14 App Router with `[locale]` route group for i18n.
- **Localisation**: `next-intl`; strings in `messages/zh-CN.json` and `messages/en-AU.json`. Chinese is the primary locale.
- **API proxy**: All requests go to `/api/*` which Next.js rewrites to `FKV_INTERNAL_API_BASE` (defaults to `http://fkv-api:18080`). Never hardcode `127.0.0.1` API URLs in client code.
- **Fixed navigation** (4 entries only): Dashboard / Docs / Categories / Agent.
- **Overlays**: Document detail, document content viewer, and sync run detail are overlays (not routes).
- **API client**: `src/lib/api/kb-client.ts` wraps fetch calls. `adapters/real.ts` for production, `adapters/mock.ts` for unit tests.
- **State**: UI state (overlays, toast, sync view, topbar) is managed via React context in `src/lib/ui-state/`.

### Adding i18n strings
Add keys to **both** `messages/en-AU.json` and `messages/zh-CN.json`. Never omit either locale.

---

## API Conventions

- All endpoints are under `/v1/` prefix.
- Use `GET /v1/documents` with `include_missing=false` (default) to hide source-missing records.
- `PATCH` endpoints use partial update semantics; only supplied fields are changed.
- Governance write-time guard: writing a document with a legacy category path will silently rewrite to `archive/misc`.
- `GET /v1/documents/{doc_id}/content/availability` must be called before attempting content preview (`ok` / `source_file_missing` / `document_not_ready` / `unsupported_media_type`).

---

## Testing

### Backend
```bash
# Inside container:
make test-backend
# Local venv:
cd backend && .venv/bin/pytest -q
```

- Tests are in `backend/tests/`.
- `pytest.ini` defines a `no_db_reset` marker to skip the autouse DB reset fixture for pure unit tests.
- `FAMILY_VAULT_CELERY_TASK_ALWAYS_EAGER=1` is the default for tests (synchronous Celery execution).
- `FAMILY_VAULT_QDRANT_ENABLE=0` is the default for tests (Qdrant is mocked/skipped).
- Tests use an in-memory SQLite DB reset between each test unless `@pytest.mark.no_db_reset`.

### Frontend
```bash
cd frontend
npm test          # Vitest (unit)
npm run e2e       # Playwright (requires running stack)
```

- Unit tests in `tests/unit/` use Vitest + Testing Library.
- E2E tests in `tests/e2e/` use Playwright and require a running frontend (`FKV_WEB_BASE` env var).
- `npm run check:manifest` validates the Next.js app manifest prefix (required by CI).

---

## Key Conventions

### Python (backend)
- Python 3.12; use `str | None` union syntax (not `Optional[str]`).
- Pydantic v2 for schemas; `pydantic-settings` for config.
- SQLAlchemy 2.x `Mapped[]` + `mapped_column()` style for all new models.
- Structured logging via `app.logging_utils.get_logger`. Use `sanitize_log_context()` before passing dicts to `extra=`.
- Error codes: compact snake_case strings, max 120 chars, normalised via `compact_error_code()`.
- Celery tasks defined in `worker.py`; import services lazily to avoid circular imports.
- All DB sessions via `SessionLocal()` from `app.db`; always close in `finally`.

### TypeScript (frontend)
- Strict TypeScript (`"typescript": "5.9.3"`).
- No inline `127.0.0.1` or port numbers in frontend code — always use the `/api` proxy path.
- Components are named exports in `src/components/`.
- API types are centralised in `src/lib/api/types.ts`.

### OpenAPI contract
- `backend/openapi.json` is a frozen snapshot. Running `make openapi-check` in CI verifies it matches the live API.
- **When adding or changing API routes**, regenerate the snapshot: `make openapi`.

### Schema changes
- Always create an Alembic migration for any model change.
- Never use `Base.metadata.create_all()` as a substitute for migrations in non-dev environments.

### Category paths
- Never introduce new top-level category paths without updating the governance guard in `governance.py`.
- Always check `make governance-gate` passes after bulk data operations.

---

## Maintenance Scripts

All scripts live in `backend/scripts/` and must be run with `PYTHONPATH=$(pwd)` from `backend/`.

| Script | Purpose |
|---|---|
| `backfill_friendly_names.py` | Re-generate friendly names for all completed docs |
| `backfill_quality_unknown.py` | Reprocess docs with `unknown/llm_failed/needs_regen` quality state |
| `backfill_bill_facts.py` | Extract structured bill facts for `finance/bills/*` docs |
| `backfill_insurance_categories.py` | Fix insurance category paths |
| `backfill_source_availability_cache.py` | Rebuild `source_available_cached` field |
| `reconcile_qdrant_points.py` | Reconcile Qdrant points with SQLite chunks |
| `cleanup_inline_mail_images.py` | Remove inline mail image artifacts |
| `cleanup_legacy_nonprod_docs.py` | Remove legacy-category docs in non-`completed` status |
| `category_debt_snapshot.py` | Capture category debt snapshot to `data/` |
| `category_debt_trend.py` | Compute trend from snapshots |
| `check_category_debt_gate.py` | CI gate: fail if any `completed` doc has legacy path |
| `export_openapi.py` | Export OpenAPI JSON snapshot |
| `check_openapi_freeze.py` | Verify OpenAPI snapshot matches live API |
| `full_quality_rebuild.py` | Full historical rebuild (summary + category + name) |

**Always do a `--dry-run` first**, then `--apply`:
```bash
PYTHONPATH=$(pwd) .venv/bin/python scripts/<script>.py --dry-run --output ../data/report.json
PYTHONPATH=$(pwd) .venv/bin/python scripts/<script>.py --apply  --output ../data/report.json
```

---

## Docker Service Ports

| Service | Container port | Host port |
|---|---|---|
| API (`fkv-api`) | 18080 | 18180 |
| Frontend (`fkv-frontend`) | 18081 | 18181 |
| Redis (`fkv-redis`) | 6379 | 16379 |
| Qdrant (`fkv-qdrant`) | 6333 | 16333 |

---

## External Integrations

- **Ollama**: Local LLM server at `FAMILY_VAULT_OLLAMA_BASE_URL`. Required for ingestion, summarisation, and agent. Models: `qwen3:4b-instruct` (summary/category/name/synthesizer), `qwen3:1.7b` (planner), `qwen3-embedding:0.6b` (embeddings), `qwen3-vl:2b` (vision fallback).
- **Qdrant**: Vector database. Collection `fkv_docs_v1` auto-bootstrapped on startup when `FAMILY_VAULT_QDRANT_ENABLE=1`.
- **Gmail API**: OAuth2 credentials at `FAMILY_VAULT_MAIL_CREDENTIALS_PATH`. Token at `FAMILY_VAULT_MAIL_TOKEN_PATH`. Required only when `FAMILY_VAULT_MAIL_POLL_ENABLED=1`.
- **NAS**: Mounted at `/volume1/Family_Archives` inside containers. All scan paths are strictly constrained to this root.
