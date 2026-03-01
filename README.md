# Family Vault

> A private, self-hosted AI assistant for your family's documents — your data never leaves your home.

Family Vault indexes your documents (PDFs, scans, bills, insurance policies, contracts, photos) and lets you ask questions in natural language. All AI inference runs locally via [Ollama](https://ollama.com).

---

## Features

- **Bilingual UI** — Simplified Chinese + Australian English (switchable at runtime)
- **Document ingestion** — PDF, DOCX, XLSX, TXT, images; OCR fallback for scanned docs
- **Local AI** — Summarisation, categorisation, and Q&A powered by Ollama (no cloud API calls)
- **Smart search** — Hybrid vector + lexical retrieval via Qdrant + SQLite
- **AI Agent** — Ask natural-language questions and get structured answers with source references
- **Graph Agent** — Optional LangGraph-based multi-step reasoning: extracts typed slots from retrieved chunks, runs a recovery loop when evidence is sparse, and streams progress via SSE. Improves answer accuracy on complex documents (insurance policies, contracts, bill aggregates)
- **Async map-reduce summarisation** — Large PDFs are summarised in background Celery tasks with per-page checkpointing; partial results are saved every 10 pages so a timeout never loses all progress
- **NAS sync** — Auto-scan a user-configurable mounted directory (set in Settings UI)
- **Gmail integration** — Auto-ingest email attachments (PDF statements, invoices)
- **Settings UI** — Configure models, timeouts, keywords, and connectivity without editing env vars
- **Password protection** — First-visit setup wizard; bcrypt-hashed password stored locally

---

## Quick Start

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (or Docker Engine + Compose v2)
- [Ollama](https://ollama.com) running on your host machine

### 1 — Pull recommended models

```bash
ollama pull qwen3:1.7b              # Planner / lightweight Q&A
ollama pull qwen3:4b-instruct       # synthesis / Q&A
ollama pull qwen3-embedding:0.6b    # Vector embeddings
ollama pull lfm2             # Friendly document titles / Summarisation
```

Minimum viable (low-RAM) option:

```bash
ollama pull qwen3:1.7b
ollama pull qwen3-embedding:0.6b
```

### 2 — Clone, configure and start

```bash
git clone https://github.com/pioneer1541/Family-Archive.git
cd Family-Archive

# Create your local env file and generate a secure JWT secret
cp .env.example .env
sed -i "s|<replace-with-your-secret>|$(openssl rand -hex 32)|" .env

docker compose up -d
```

### 3 — Open the app

Visit **http://localhost:18181**

On first visit you'll be prompted to set a password and (optionally) configure the Ollama URL.

### 4 — Add your documents

- **Drag & drop** files directly from the Documents page
- Or point the NAS scanner at a local folder via **Settings → Storage & Scan**

### 5 — Mount your NAS directory (required for NAS sync)

Add a volume mount in `docker-compose.yml` and then set the mounted path in **Settings → Storage & Scan**:

```yaml
services:
  fkv-api:
    volumes:
      - ./backend:/app
      - ./data:/app/data
      - /mnt/nas:/mnt/nas
  fkv-worker:
    volumes:
      - ./backend:/app
      - ./data:/app/data
      - /mnt/nas:/mnt/nas
```

---

## Recommended Model Configuration

| Role | Recommended | Minimum |
|------|-------------|---------|
| Planner / routing | `qwen3:1.7b` | same |
| Summarisation | `lfm2 or GLM4.7-Flash` | `qwen3:4b-instruct` |
| Synthesiser (Q&A) | `qwen3:4b-instruct` | same |
| Embeddings | `qwen3-embedding:0.6b` | same |
| Category model | `qwen3:4b-instruct` | same |
| Friendly titles | `lfm2 or GLM4.7-Flash` | `qwen3:4b-instruct` |
| Vision (images) | `any ORC Model` | same |

All models are selectable in **Settings → LLM Models** after startup.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  Browser                                            │
│  Next.js frontend  (port 18181)                     │
│  zh-CN / en-AU · dashboard / docs / cats / agent    │
└────────────────────┬────────────────────────────────┘
                     │ /api/* proxy
┌────────────────────▼────────────────────────────────┐
│  fkv-api  FastAPI + Uvicorn  (port 18080)           │
│  ┌──────────────┐  ┌────────────────────────────┐  │
│  │ REST API     │  │ AI Agent (planner + synth) │  │
│  │ Ingestion    │  │ map-reduce summarisation   │  │
│  └──────┬───────┘  └───────────────┬────────────┘  │
│         │                          │                │
│  ┌──────▼───────┐       ┌──────────▼────────────┐  │
│  │ SQLite DB    │       │ Ollama  (host:11434)  │  │
│  │ (metadata)   │       └───────────────────────┘  │
│  └──────┬───────┘                                  │
│         │  Celery tasks                            │
└─────────┼────────────────────────────────────────-─┘
          │
┌─────────▼────────────┐   ┌────────────────────────┐
│  fkv-worker  Celery  │   │  Qdrant (vector store) │
│  (document pipeline) │   │  port 6333             │
└──────────────────────┘   └────────────────────────┘
          │
      ┌───▼───┐
      │ Redis │  (task queue)
      └───────┘
```

---

## Agent Accuracy Improvements

The graph agent went through several rounds of targeted fixes. The table below summarises the problems that existed, what was changed, and what improved.

### Problem 1 — Slot extraction false negatives

| | Detail |
|---|---|
| **Problem** | If a document used non-standard field names the structured slot extractor returned "missing", the sufficiency judge declared "insufficient", and the agent returned a blank answer even when the relevant text was present in the retrieved chunks. |
| **Fix** | `agent_slots.py`: context-window fallback scans the top-6 retrieved chunks for any label or hint term matching the requested slot and returns a 240-character evidence window (confidence 0.30). `judge_sufficiency` now accepts "partial" when ≥ 6 chunks are hit and subject coverage is met. `agent_graph_nodes.py`: after one recovery attempt the graph accepts a partial result (`partial_after_recovery`) instead of looping to budget exhaustion. |
| **Result** | EXTRACT_OR_JUDGE_FALSE_NONE errors eliminated. Queries that previously returned blank now return a partial answer with source evidence. |

### Problem 2 — Multi-intent routing confusion

| | Detail |
|---|---|
| **Problem** | The intent router returned on the first matching rule, so queries matching multiple intents (e.g. "list all February bills and total amount") were assigned a single narrow intent, causing the wrong retrieval strategy and missing data. |
| **Fix** | `planner.py`: collect all matching `_INTENT_RULES` and if more than one fires return `search_bundle` at a capped confidence of 0.68 to signal ambiguity. A heuristic post-validator in `route_and_rewrite` ensures deterministic bill-aggregate queries always route to `calculate/bill_monthly_total` regardless of what the LLM decides. |
| **Result** | Monthly bill total queries and multi-part questions now route to the correct handler consistently. |

### Problem 3 — Category routing errors for domain-specific queries

| | Detail |
|---|---|
| **Problem** | The LLM planner sometimes mapped "开发商" (property developer) queries to `finance/bills`, returning zero relevant results. Bill aggregate queries with `task_kind` of `search_bundle` or `list` bypassed the structured SQL aggregate path entirely. |
| **Fix** | `agent_graph_nodes.py`: rule-based overrides detect developer/vendor tokens (`开发商`, `developer`, `vendor statement`) and force `preferred_categories = ["legal/contracts"]` + `strict_domain_filter = True`. Bill aggregate monthly queries (containing a month pattern, no single-bill type) are promoted to `aggregate_lookup` unconditionally. A safety guard prevents `strict_domain_filter` from being applied to the broad `finance/bills` parent path, which has zero exact-match Qdrant points. |
| **Result** | Developer contact queries resolve to Vendor's Statement chunks. Monthly bill total queries hit the SQL aggregate path. |

### Problem 4 — Large PDF timeouts losing all progress

| | Detail |
|---|---|
| **Problem** | Map-reduce summarisation processed all pages sequentially in a single synchronous request. A 60-second LLM timeout on any page aborted the entire job with no output saved. |
| **Fix** | Map-reduce now runs as an async Celery task (`fkv.map_reduce.process`). Page summaries are checkpointed to the database every 10 pages; section summaries are checkpointed after each section. New REST endpoints: `POST /v1/summaries/map-reduce/async` to start a job, `GET /v1/summaries/map-reduce/status/{doc_id}` to poll partial results. Alembic migration adds `mapreduce_page_summaries_json`, `mapreduce_section_summaries_json`, and `mapreduce_job_status` columns. |
| **Result** | A 200-page PDF that times out on page 150 retains the first 140 pages of summarised output. Jobs can be resumed or inspected without re-running from scratch. |

### Accuracy summary

| Agent | Custom-10 evaluation set |
|-------|-------------------------|
| Legacy v2 | 2 / 10 |
| Graph agent (after fixes) | 6 / 10 |

Remaining failures are synthesis quality issues (LLM returns wrong date or ignores table data) rather than routing or slot extraction problems.

---

## Configuration

Most settings are available in the **Settings UI** (no env vars needed after initial deploy).

The following env vars in `docker-compose.yml` control deployment-time behaviour:

| Variable | Default | Description |
|----------|---------|-------------|
| `FAMILY_VAULT_OLLAMA_BASE_URL` | `http://host.docker.internal:11434` | Ollama endpoint |
| `FAMILY_VAULT_DATABASE_URL` | `sqlite:////app/data/family_vault.db` | SQLite path |
| `FAMILY_VAULT_QDRANT_URL` | `http://qdrant:6333` | Qdrant endpoint |
| `FAMILY_VAULT_QDRANT_ENABLE` | `1` | Enable vector search |
| `FAMILY_VAULT_ALLOWED_ORIGINS` | `http://localhost:18181` | CORS allowed origins |
| `FAMILY_VAULT_NAS_AUTO_SCAN_ENABLED` | `1` | Enable background NAS scan |
| `FAMILY_VAULT_MAIL_POLL_ENABLED` | `0` | Enable Gmail polling |
| `FAMILY_VAULT_JWT_SECRET` | *(auto-generated)* | JWT signing secret (set manually for persistence) |
| `FAMILY_VAULT_AGENT_GRAPH_ENABLED` | `0` | Set to `1` to use the LangGraph graph agent instead of the legacy v2 agent |
| `FAMILY_VAULT_AGENT_SYNTH_TIMEOUT_SEC` | `60` | LLM synthesis timeout in seconds — increase for large multi-chunk answers |

Runtime-configurable settings (model names, timeouts, keywords, etc.) are stored in the database and editable through the Settings UI.
`FAMILY_VAULT_NAS_DEFAULT_SOURCE_DIR` is intentionally not pinned in `docker-compose.yml`; set it from the UI so users can change it at runtime.

---

## Custom Keywords

Go to **Settings → Keywords** to add names that the system should recognise for auto-tagging and AI routing:

- **Family member names** — used to route queries like "Alice's health check"
- **Pet names** — routes pet birthday / vaccine queries
- **Location keywords** — used for address-aware document tagging

---

## Gmail Integration

1. Create a Google Cloud project and enable the Gmail API
2. Create an **OAuth 2.0 Desktop app** credential and download `credentials.json`
3. Place it at `secrets/gmail/credentials.json` (relative to the repo root)
4. Authorise once — run the following command while the containers are up:

```bash
docker compose exec fkv-api python -c "
from google_auth_oauthlib.flow import InstalledAppFlow
import pathlib
flow = InstalledAppFlow.from_client_secrets_file(
    '/app/secrets/gmail/credentials.json',
    ['https://www.googleapis.com/auth/gmail.readonly'])
creds = flow.run_local_server(port=0)
pathlib.Path('/app/secrets/gmail/token.json').write_text(creds.to_json())
print('token.json written — Gmail authorisation complete')
"
```

5. Enable polling in **Settings → Mail Pull**

> `token.json` contains a long-lived refresh token and is automatically renewed. You only need to repeat step 4 if you revoke access in your Google account.
>
> `secrets/` is in `.gitignore` — your credentials will never be committed.

---

## Data Location

All data is stored locally inside Docker volumes:

| Data | Volume |
|------|--------|
| SQLite database | `fkv-data` → `/app/data/` |
| Qdrant index | `qdrant-data` |
| Redis state | `redis-data` |

To back up: stop containers, copy the volume directories.

---

## FAQ

**Ollama won't connect**
- Ensure Ollama is running: `ollama serve`
- On macOS/Windows Docker Desktop, use `http://host.docker.internal:11434`
- On Linux, use your host's LAN IP or bridge IP (e.g. `http://172.17.0.1:11434`)
- Check in **Settings → Advanced → Test Connection**

**Documents uploaded but no summary appears**
- Summary generation is asynchronous (Celery worker). Check `docker compose logs fkv-worker`
- Ensure the required Ollama models are pulled
- Default summary timeout is 90s/page — large PDFs may take several minutes

**Forgot the access password**
```bash
docker compose exec fkv-api python -c "
from app.db import SessionLocal
from app.auth import set_admin_password
db = SessionLocal()
set_admin_password('your-new-password', db)
db.close()
print('Password reset.')
"
```

**Mail attachments not being ingested**
- Verify `credentials.json` and `token.json` are in place
- Check **Settings → Mail Pull → Test Connection**
- Review logs: `docker compose logs fkv-api | grep mail`

---

## Development

```bash
# Backend
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 18080 --reload

# Worker
.venv/bin/celery -A app.worker worker --loglevel=info

# Frontend
cd frontend
npm install
npm run dev   # port 18081

# Tests
cd backend
.venv/bin/pytest -q
```

---

## Project Layout

```
family-vault/
├── backend/
│   ├── app/
│   │   ├── api/          # FastAPI routes + deps
│   │   ├── services/     # agent, planner, ingestion, summarisation, …
│   │   ├── models.py     # SQLAlchemy ORM models
│   │   ├── auth.py       # bcrypt + JWT auth
│   │   └── runtime_config.py  # DB-backed runtime settings
│   ├── alembic/          # DB migrations
│   ├── evaluation/       # Evaluation scripts and test cases
│   └── tests/            # pytest test suite
├── frontend/
│   ├── app/[locale]/     # Next.js pages (dashboard/docs/cats/agent/settings/setup/login)
│   ├── src/components/   # React components
│   ├── src/lib/api/      # API client (real + mock adapters)
│   └── messages/         # i18n strings (zh-CN / en-AU)
├── docker-compose.yml
└── docker-compose.prod.yml
```

---

## License

MIT
