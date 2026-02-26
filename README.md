# Family Knowledge Vault (Independent Implementation)

This project implements the revised Family Knowledge Vault plan with:
- Backend: FastAPI + SQLAlchemy + Alembic + Celery + Redis
- Metadata DB: SQLite
- Vector target: Qdrant (payload keys aligned; enabled by default)
- Frontend: Next.js + next-intl (`zh-CN` / `en-AU`)

## Layout
- `backend/`: API, services, database models, migrations, tests
- `frontend/`: Next.js App Router UI (`/dashboard`, `/docs`, `/cats`, `/cats/[catId]`, `/agent`)
- `docker-compose.yml`: local single-host stack for API/Worker/Redis/Qdrant/Frontend

## Quick Start
1. Backend
```bash
cd family-vault/backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 18180
```

2. Worker
```bash
cd family-vault/backend
.venv/bin/celery -A app.worker worker --loglevel=info
```

3. Frontend
```bash
cd family-vault/frontend
npm install
npm run dev
```
Default URLs:
- API: `http://127.0.0.1:18180`
- Frontend: `http://127.0.0.1:18081`

4. Tests
```bash
cd family-vault/backend
.venv/bin/pytest -q
```

## API Endpoints
- `POST /v1/ingestion/jobs`
- `GET /v1/ingestion/jobs/{job_id}`
- `DELETE /v1/ingestion/jobs/{job_id}`
- `POST /v1/ingestion/jobs/{job_id}/retry`
- `POST /v1/ingestion/nas/scan`
- `POST /v1/search`
- `GET /v1/documents`
- `GET /v1/documents/{doc_id}`
- `GET /v1/documents/{doc_id}/content/availability`
- `GET /v1/documents/{doc_id}/content`
- `PATCH /v1/documents/{doc_id}/friendly-name`
- `GET /v1/documents/{doc_id}/tags`
- `PATCH /v1/documents/{doc_id}/tags`
- `GET /v1/tags`
- `GET /v1/categories`
- `GET /v1/queue`
- `POST /v1/documents/{doc_id}/reprocess`
- `POST /v1/tasks`
- `GET /v1/tasks`
- `GET /v1/tasks/{task_id}`
- `POST /v1/agent/plan`
- `POST /v1/agent/execute`
- `POST /v1/summaries/map-reduce`
- `GET /v1/system/prompts`
- `GET /v1/governance/category-debt`
- `GET /v1/governance/category-debt/trend`
- `POST /v1/mail/poll`
- `GET /v1/mail/events`
- `POST /v1/sync/runs`
- `GET /v1/sync/runs/{run_id}`
- `GET /v1/sync/last`

## One-Command Workflow
```bash
cd family-vault
make up
make test-backend
make eval-all
make e2e-ui
```

Full gate in one shot:
```bash
cd family-vault
make check-all
```

Contract freeze check only:
```bash
cd family-vault
make openapi-check
```

## CI Gate
- Workflow: `.github/workflows/family-vault-gate.yml`
- Trigger: `push` to `main`, plus `pull_request` (`opened` / `reopened` / `synchronize` / `ready_for_review`)
- Action (v1): layered base gate running backend `pytest -q` and frontend `check:manifest` + `test` + `build` (no Docker Compose full-stack gate)
- Roadmap: add separate Docker Compose integration and Playwright E2E workflows after the base gate is stable

## Notes
- M1 baseline was text-first ingestion (`PDF/DOCX/TXT/XLSX`).
- OCR fallback is now available for scanned PDFs and image files when text extraction is empty.
- If OCR still returns empty content, ingestion can index a metadata-only fallback chunk (`FAMILY_VAULT_INGESTION_METADATA_FALLBACK_ENABLED=1`) so files remain visible/reprocessable.
- Directory scanning still follows `FAMILY_VAULT_INGESTION_ALLOWED_EXTENSIONS`; add image extensions there if you want bulk image OCR ingestion.
- NAS auto-scan is now strictly constrained to `FAMILY_VAULT_NAS_DEFAULT_SOURCE_DIR` (default `/volume1/Family_Archives`); paths outside this root are ignored.
- NAS scan extension filter is separated via `FAMILY_VAULT_NAS_ALLOWED_EXTENSIONS`.
- Mail polling now filters attachments to document/photo types (`pdf/doc/docx/xls/xlsx/jpg/jpeg/png/webp/tif/tiff/heic`).
- Mail polling now enforces real-attachment disposition (`Content-Disposition=attachment`) and skips inline/CID signature assets (`detail=inline_asset`).
- Image-format files (`jpg/jpeg/png/webp/tif/tiff/heic`) are additionally gated by size (`FAMILY_VAULT_PHOTO_MAX_SIZE_MB`, default 20MB) for NAS scan, mail attachments, and direct ingestion.
- Image near-duplicate detection is enabled via pHash (configurable Hamming threshold).
- Ingestion now accepts both file paths and directory paths (recursive scan with extension filtering).
- Friendly titles are auto-generated from parsed content (Chinese-first naming) and can be edited via `PATCH /v1/documents/{doc_id}/friendly-name` without changing original file names.
- Summary output is Chinese-first by default in map-reduce and document detail displays.
- Existing records can be backfilled once via `docker exec -i fkv-api python /app/scripts/backfill_friendly_names.py`.
- Qdrant indexing is enabled by default (`FAMILY_VAULT_QDRANT_ENABLE=1`) and collection bootstrap is automatic.
- `/v1/search` now uses hybrid retrieval: Qdrant vector search first, then lexical fallback/fill.
- `/v1/search` supports tag filters: `tags_all` (AND) and `tags_any` (OR).
- Tag rules are config-driven via `backend/services/kb-worker/config/tag_rules.json` (families, synonyms, topic whitelist, limits).
- Documents now support structured tags (`family:value`) with normalization, synonym mapping, manual edit API, and auto-tag refresh after summary regeneration.
- `/v1/summaries/map-reduce` now uses hierarchical semantic windows (200-500 token windows -> section reduce) for long-document stability.
- `/v1/summaries/map-reduce` now includes long-doc budget metadata (`longdoc_mode/pages_total/pages_used`) and sampled-page execution when oversized.
- `/v1/summaries/map-reduce` now returns `quality_state/fallback_used/quality_flags`; only `quality_state=ok` can overwrite stored summaries.
- `GET /v1/system/prompts` exposes active summary/category/name prompt text and hash for prompt audit.
- Document detail now exposes quality metadata fields: `summary_quality_state`, `summary_last_error`, `summary_model`, `summary_version`, `category_version`, `name_version`.
- `GET /v1/documents` now hides source-missing files by default; set `include_missing=true` to inspect missing-source records.
- `GET /v1/categories` and `POST /v1/search` follow the same default source-available filtering (`include_missing=false`).
- `GET /v1/documents/{doc_id}` now returns `source_available` and `source_missing_reason`.
- `GET /v1/documents/{doc_id}/content/availability` provides previewability probe (`ok/source_file_missing/document_not_ready/unsupported_media_type`) for UI preflight.
- `/v1/summaries/map-reduce` now returns `applied` and `apply_reason`; when quality is not `ok`, old summary/title/category remain unchanged.
- `/v1/agent/execute` now runs planner -> executor -> synthesizer: planner emits structured decision JSON, executor retrieves/deduplicates with context budget, synthesizer emits structured Result Card (model failure falls back to deterministic template).
- `/v1/agent/execute` response now includes `related_docs`, `trace_id`, and `executor_stats` for UI-side traceability and direct related-doc rendering.
- Agent request now accepts `conversation` and `client_context` to support short-window multi-turn context and selected-doc scoping.
- Agent context defaults to smart-followup: fresh turn for new questions, short history only for followup-style prompts.
- Agent bill-attention flow now uses structured `bill_facts` data (`amount_due/currency/due_date/payment_status`) for stable "recent bills to watch" answers.
- Month-total bill queries (e.g. `2月账单一共多少钱`) now route to structured aggregation (`bill_monthly_total`) and return month-scoped bill docs only.
- Agent executor now enforces structured-first routing: `queue_view/reprocess_doc/tag_update/bill_attention` do not call vector retrieval; semantic/open Q&A routes use hybrid retrieval.
- `/v1/agent/execute` `executor_stats` now includes `qdrant_used`, `retrieval_mode`, `vector_hit_count`, `lexical_hit_count`, and `fallback_reason` for auditability.
- Agent request supports `doc_scope`; frontend Agent page now auto-binds selected docs from Context Panel so user-selected docs constrain executor context.
- Agent UI shows low-confidence (`confidence < 0.55`) fallback semantic-search action.
- Ingestion queue uses retry semantics with capped retries and exponential backoff (`retrying -> failed` on retry exhaustion).
- `make eval-all` now includes cross-language retrieval validation (`crosslang_eval_report.json`).
- Agent eval suite now includes mixed scoring (rule + LLM judge) with 40-case bank (`backend/evaluation/agent_eval_cases_v1.json`) and random 20-case runs (`make eval-agent`), plus trend aggregation (`make eval-agent-trend`).
- Frontend IA is now fixed to 4 entries only: Dashboard / Docs / Categories / Agent.
- Agent is a standalone page view; document detail is an overlay panel opened from document cards.
- Document detail now includes "View Document Content": opens a full-screen content overlay, uses `/v1/documents/{doc_id}/content` for inline preview (PDF/images), and falls back to extracted text + download for non-previewable formats.
- NAS source scan supports incremental detection via file-state table (`source_file_states`) to avoid full re-ingest on unchanged files.
- Gmail polling (`/v1/mail/poll`) downloads new mail attachments to local storage and enqueues ingestion; event notifications are queryable from `/v1/mail/events`.
- Mail attachments are auto-tagged as `source_type=mail` and rule-classified into categories (e.g. `finance/bills`, `finance/utilities`, `property/*`) during ingestion.
- Queue page supports deleting ingestion jobs; deleted job input paths are persisted in `ignored_ingestion_paths` and will not be enqueued again automatically.
- Task IDs are now human-readable and anchored to friendly-name/file-name/title context (instead of random UUID-only IDs).
- Compose mounts follow the same integration paths as `mcp-tools`: `/mnt/nas` and `../mcp-tools/secrets/gmail`, and also expose NAS at `/volume1/Family_Archives` for scanner default path compatibility.
- Compose defaults enable background NAS auto-scan and mail polling in API container (`FAMILY_VAULT_NAS_AUTO_SCAN_ENABLED=1`, `FAMILY_VAULT_MAIL_POLL_ENABLED=1`).
- Frontend now defaults to same-origin `/api` proxy, so remote-browser access no longer depends on client-side `127.0.0.1` API reachability.
- Browser tab title is synchronized as `{current page} | Family Knowledge Vault` to avoid URL/IP-only tabs.
- Dashboard topbar now shows locale switch (`中文 | EN`) instead of doc-count meta on dashboard page.
- Dashboard recent card now includes `立即同步/Sync Now` + last-sync timestamp; syncing state opens a run-detail overlay with file/size/stage updates.
- Sync API aggregates NAS scan + mail poll in one run (`/v1/sync/runs`), with pollable run details (`/v1/sync/runs/{run_id}`).
- When backend API code changes (non-reload mode), restart `fkv-api` and verify `GET /v1/documents/{doc_id}/content/availability` before UI preview checks.
- Reprocess flow now cleans stale Qdrant point IDs for old chunks; failures are marked with document error code `qdrant_cleanup_pending` for later reconciliation.
- Governance layer now blocks legacy category paths (`general`, `finance/utilities`, `finance/telecom`, `property/strata`, `property`) at write-time by rewriting to `archive/misc`.
- Governance snapshot/trend reports are available via `/v1/governance/category-debt` and `/v1/governance/category-debt/trend`.
- CI gate now includes production-scope category debt check (`completed` must contain zero legacy paths) via `scripts/check_category_debt_gate.py`.

## Quality Rebuild
Run full historical cleanup with map-reduce summary + category + friendly-name regeneration:
```bash
cd family-vault/backend
.venv/bin/python scripts/full_quality_rebuild.py --workers 2 --output ../data/before_after_quality_report.json
```

Backfill only problematic completed docs (`unknown/llm_failed/needs_regen`) and emit a report:
```bash
cd family-vault/backend
PYTHONPATH=$(pwd) .venv/bin/python scripts/backfill_quality_unknown.py --include-missing --output ../data/quality_backfill_report.json
```

Backfill structured `bill_facts` for completed `finance/bills/*` docs (dry-run then apply):
```bash
cd family-vault/backend
PYTHONPATH=$(pwd) .venv/bin/python scripts/backfill_bill_facts.py --dry-run --output ../data/bill_facts_backfill_report.json
PYTHONPATH=$(pwd) .venv/bin/python scripts/backfill_bill_facts.py --apply --output ../data/bill_facts_backfill_report.json
```

Reconcile Qdrant points with SQLite chunks (dry-run then apply):
```bash
cd family-vault/backend
PYTHONPATH=$(pwd) .venv/bin/python scripts/reconcile_qdrant_points.py --output ../data/qdrant_reconcile_report.json
PYTHONPATH=$(pwd) .venv/bin/python scripts/reconcile_qdrant_points.py --apply --output ../data/qdrant_reconcile_report.json
```

Cleanup inline mail image artifacts (dry-run then apply):
```bash
cd family-vault/backend
PYTHONPATH=$(pwd) .venv/bin/python scripts/cleanup_inline_mail_images.py --dry-run --output ../data/inline_mail_cleanup_report.json
PYTHONPATH=$(pwd) .venv/bin/python scripts/cleanup_inline_mail_images.py --apply --output ../data/inline_mail_cleanup_report.json
```

Generate category debt governance reports:
```bash
cd family-vault/backend
PYTHONPATH=$(pwd) .venv/bin/python scripts/category_debt_snapshot.py --output-dir ../data
PYTHONPATH=$(pwd) .venv/bin/python scripts/category_debt_trend.py --data-dir ../data --output ../data/category_debt_trend_latest.json
```

Cleanup legacy debt records in non-production statuses (`failed/duplicate`) with dry-run first:
```bash
cd family-vault/backend
PYTHONPATH=$(pwd) .venv/bin/python scripts/cleanup_legacy_nonprod_docs.py --dry-run --days 30 --output ../data/legacy_nonprod_cleanup_report.json
PYTHONPATH=$(pwd) .venv/bin/python scripts/cleanup_legacy_nonprod_docs.py --apply --days 30 --output ../data/legacy_nonprod_cleanup_report.json
```
