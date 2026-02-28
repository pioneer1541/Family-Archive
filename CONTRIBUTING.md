# Contributing to Family Vault

Thank you for your interest in contributing! Family Vault is a self-hosted, privacy-first AI assistant for family documents. All contributions are welcome — bug fixes, new features, documentation improvements, translations, and test cases.

---

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Environment Setup](#development-environment-setup)
- [Running Tests](#running-tests)
- [Submitting a Pull Request](#submitting-a-pull-request)
- [Commit Message Conventions](#commit-message-conventions)
- [Code Style](#code-style)
- [Reporting Security Issues](#reporting-security-issues)

---

## Code of Conduct

Please read and follow our [Code of Conduct](CODE_OF_CONDUCT.md). We are committed to providing a welcoming environment for everyone.

---

## Getting Started

1. **Fork** the repository on GitHub
2. **Clone** your fork locally:
   ```bash
   git clone https://github.com/<your-username>/Family-Archive.git
   cd Family-Archive
   ```
3. **Add the upstream remote**:
   ```bash
   git remote add upstream https://github.com/pioneer1541/Family-Archive.git
   ```
4. **Create a branch** for your change:
   ```bash
   git checkout -b feat/my-feature
   ```

---

## Development Environment Setup

### Prerequisites

- **Docker & Docker Compose** — for the full stack
- **Python 3.12+** — for backend development
- **Node.js 20+** — for frontend development
- **Ollama** — for local LLM inference ([install guide](https://ollama.ai))

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Copy and configure the environment file:
```bash
cp .env.example .env
# Set FAMILY_VAULT_JWT_SECRET to a random value:
echo "FAMILY_VAULT_JWT_SECRET=$(openssl rand -hex 32)" >> .env
```

Start the API server (development mode):
```bash
FAMILY_VAULT_AUTO_CREATE_SCHEMA=1 uvicorn app.main:app --reload --port 18080
```

Start the Celery worker (separate terminal):
```bash
celery -A app.worker worker --loglevel=info
```

### Frontend

```bash
cd frontend
npm install
npm run dev         # Starts on http://localhost:18181
```

### Full Stack (Docker)

```bash
cp .env.example .env
# Edit .env and set FAMILY_VAULT_JWT_SECRET
docker compose up -d
```

---

## Running Tests

### Backend Tests

```bash
cd backend
pytest -q                       # Run all tests
pytest tests/test_auth.py -v    # Run a specific file
```

### Frontend Tests

```bash
cd frontend
npm test                        # Unit tests (Vitest)
npx playwright test             # E2E tests
```

### Full Quality Gate

```bash
make check-all                  # Runs backend tests, frontend build, governance checks
```

All tests must pass before a PR will be merged.

---

## Submitting a Pull Request

1. **Keep PRs focused** — one feature or fix per PR
2. **Write or update tests** for any new behavior
3. **Update documentation** if you change configuration options, API endpoints, or user-facing behavior
4. **Ensure `make check-all` passes** locally before submitting
5. **Fill out the PR template** — describe what the change does and why
6. **Link related issues** using `Closes #<issue-number>` in the PR description

PRs are reviewed by maintainers. Automated AI code review runs on every PR (see `.github/workflows/ai-code-review.yml`). Small, well-tested PRs are merged faster.

---

## Commit Message Conventions

We follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <short summary>

[optional body]
[optional footer]
```

Common types:
| Type | When to use |
|------|-------------|
| `feat` | New feature |
| `fix` | Bug fix |
| `docs` | Documentation only |
| `test` | Adding or fixing tests |
| `refactor` | Code change that neither fixes a bug nor adds a feature |
| `perf` | Performance improvement |
| `ci` | CI/CD pipeline changes |
| `chore` | Build process, dependency updates |

Examples:
```
feat(ingestion): add HEIC image support via PIL
fix(auth): raise error on missing JWT secret instead of using default
docs: add Gmail OAuth setup walkthrough
test(agent): add boundary cases for multi-turn conversation
```

---

## Code Style

### Python (Backend)

- **Formatter**: `black` (line length 100)
- **Type hints**: required for all public functions and API handlers
- **Docstrings**: for non-obvious logic; code should be self-documenting where possible
- **No TODO/FIXME in PRs**: resolve or create a GitHub issue instead

### TypeScript (Frontend)

- **Strict mode**: enabled (`tsconfig.json`)
- **Component style**: functional components with hooks
- **Naming**: PascalCase for components, camelCase for functions/variables
- **i18n**: all user-facing strings must use `next-intl` translation keys (add to both `zh-CN.json` and `en-AU.json`)

---

## Reporting Security Issues

**Please do not open a public GitHub issue for security vulnerabilities.**

See [SECURITY.md](SECURITY.md) for our responsible disclosure process.

---

## Questions?

Open a [GitHub Discussion](https://github.com/pioneer1541/Family-Archive/discussions) for questions, ideas, or general feedback.
