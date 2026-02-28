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
- **NAS sync** — Auto-scan a local directory (e.g. Synology `/volume1/Family_Archives`)
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
ollama pull qwen3:4b-instruct       # Summarisation / synthesis
ollama pull qwen3-embedding:0.6b    # Vector embeddings
ollama pull lfm2:latest             # Friendly document titles
```

Minimum viable (low-RAM) option:

```bash
ollama pull qwen3:1.7b
ollama pull qwen3-embedding:0.6b
```

### 2 — Clone and start

```bash
git clone https://github.com/your-org/family-vault.git
cd family-vault
docker compose up -d
```

### 3 — Open the app

Visit **http://localhost:18181**

On first visit you'll be prompted to set a password and (optionally) configure the Ollama URL.

### 4 — Add your documents

- **Drag & drop** files directly from the Documents page
- Or point the NAS scanner at a local folder via **Settings → Storage & Scan**

---

## Recommended Model Configuration

| Role | Recommended | Minimum |
|------|-------------|---------|
| Planner / routing | `qwen3:1.7b` | same |
| Summarisation | `qwen3:4b-instruct` | `qwen3:1.7b` |
| Synthesiser (Q&A) | `qwen3:4b-instruct` | `qwen3:1.7b` |
| Embeddings | `qwen3-embedding:0.6b` | same |
| Category model | `qwen3:4b-instruct` | `qwen3:1.7b` |
| Friendly titles | `lfm2:latest` | `qwen3:1.7b` |
| Vision (images) | `qwen3-vl:2b` | same |

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
| `FAMILY_VAULT_NAS_DEFAULT_SOURCE_DIR` | `/volume1/Family_Archives` | Directory to scan |
| `FAMILY_VAULT_MAIL_POLL_ENABLED` | `0` | Enable Gmail polling |
| `FAMILY_VAULT_JWT_SECRET` | *(auto-generated)* | JWT signing secret (set manually for persistence) |

Runtime-configurable settings (model names, timeouts, keywords, etc.) are stored in the database and editable through the Settings UI.

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
