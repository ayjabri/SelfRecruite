<p align="center">
  <img src="jobagent/web/logo.png" alt="SelfRecruite" width="140">
</p>

<h1 align="center">SelfRecruite</h1>

<p align="center">
  A self-hosted job-search agent. It crawls the career sites you choose, scores every posting against <em>your</em> resume, learns from the jobs you mark, and keeps the whole thing on your own machine.
</p>

<p align="center">
  <a href="https://github.com/ayjabri/SelfRecruite/actions/workflows/ci.yml"><img src="https://github.com/ayjabri/SelfRecruite/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <img src="https://img.shields.io/badge/python-3.12-blue" alt="Python 3.12">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT">
  <img src="https://img.shields.io/badge/docker-compose-2496ED?logo=docker&logoColor=white" alt="Docker Compose">
</p>

---

## What it does

- **Crawls** Greenhouse, Workday and Oracle Recruiting career sites for the companies in your list. No scraping of job boards, no third-party API keys: these are the sites' own public endpoints.
- **Parses** each posting with rules (title, location, seniority, required and preferred bullets), embeds it, and removes duplicates across companies and sites.
- **Scores** every in-scope job against your resume: embedding similarity per requirement, title family, seniority and domain, combined by weights you control.
- **Learns** from you. Mark jobs interested or not and a per-user preference model retrains on your marks, with company groups and per-company rules ("at Citi, only Treasury roles").
- **Explains** itself. A score-check tab shows held-out accuracy, score bands, where the model and the rules disagree, and a forward test.
- **Talks to an LLM only when you ask.** Per-job AI (today: keyword extraction, more agents planned) runs on a free local model through Ollama, or on your own Claude key stored encrypted in your profile.
- **Multi-user.** Accounts, sessions, CSRF, admin role, per-user resumes, targets, scope and skills. One crawl, many people.

Everything is a registered tool: callable by an agent, from the CLI and over the API. Crawl stages and per-user stages are LangGraph graphs over that registry.

## Quick start with Docker

You need Docker with Compose v2. An NVIDIA GPU is optional but makes the local chat model usable.

```bash
git clone https://github.com/ayjabri/SelfRecruite.git
cd SelfRecruite
cp .env.example .env
```

Open `.env` and set `POSTGRES_PASSWORD`. Pick a chat model that fits your hardware with `LLM_MODEL` (any Ollama tag, or `anthropic:claude-sonnet-5-5` with `ANTHROPIC_API_KEY`). Then:

```bash
docker compose up -d --build
```

With an NVIDIA GPU and the NVIDIA Container Toolkit installed, use the GPU override instead:

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

The first start pulls the embedding model and, for an Ollama chat model, the chat model too. Follow progress with `docker compose logs -f`. The page is at **http://localhost:8765**.

Claim the admin account (user 1, created by the first migration), then sign in:

```bash
docker compose exec app jobagent passwd --user 1 --email you@example.com
```

On the page: upload your resume in **Settings → Resumes**, walk through the onboarding (title families, skills), and start a crawl from the **Run** tab, or from the shell:

```bash
docker compose exec app jobagent pipeline "Daily pull: at most 200 new postings per target from every configured source, posted in the last 48 hours."
```

A `Makefile` wraps the common commands: `make up`, `make crawl`, `make score`, `make logs`, `make test`.

### Already running Ollama on the host?

Clear `COMPOSE_PROFILES` in `.env` so the bundled Ollama does not start, and add the host override:

```bash
docker compose -f docker-compose.yml -f docker-compose.host-ollama.yml up -d --build
```

### Reaching the page from other devices

Set `UI_BIND=0.0.0.0` in `.env` (and, ideally, put a reverse proxy with HTTPS in front; see [docs/HTTPS.md](docs/HTTPS.md) for a Caddy setup with a local certificate authority).

## Native install

Requirements: Python 3.12, a Postgres 16 with the `pgvector` extension, and Ollama.

```bash
git clone https://github.com/ayjabri/SelfRecruite.git
cd SelfRecruite
python -m venv .venv && source .venv/bin/activate      # or: conda env create -f environment.yml
pip install -e .[dev]
cp .env.example .env                                   # set DATABASE_URL, LLM_MODEL
docker compose up -d db                                # or point DATABASE_URL at your own Postgres
ollama pull nomic-embed-text && ollama pull qwen3.6:27b   # or whatever LLM_MODEL names
alembic upgrade head
jobagent passwd --user 1 --email you@example.com
jobagent ui                                            # http://127.0.0.1:8765
```

Running it as a service, nightly backups and the daily crawl timer are covered in [docs/DEPLOY.md](docs/DEPLOY.md).

## Configuration

All settings come from `.env` ([.env.example](.env.example) lists every variable the code reads). The ones that matter most:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Postgres connection string (`postgresql+psycopg://user:pass@host:5432/db`) |
| `LLM_MODEL` | Shared chat model as `<provider>:<model>`: `ollama:<tag>`, `anthropic:<model>` or `openai:<model>` |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` | Only when `LLM_MODEL` names that provider |
| `OLLAMA_BASE_URL`, `EMBED_MODEL` | Ollama endpoint and the embedding model (embeddings are always local) |
| `SECRET_KEY` | Fernet key that encrypts the API keys users store. Generated into `data/secret.key` when unset |
| `ADMIN_EMAIL`, `ADMIN_NAME` | Read once, when the admin account is created |
| `COOKIE_SECURE`, `REGISTRATION_OPEN`, `SESSION_DAYS` | Sign-in behaviour |

The search itself lives in YAML under `config/`, editable from the Settings page (saves keep comments and back up the previous version to `data/config_backups/`):

| File | What it holds |
|---|---|
| `config/companies.yaml` | Which companies to crawl, per source. [docs/COMPANY_SOURCES.md](docs/COMPANY_SOURCES.md) lists verified site keys for banks, asset managers, payments, fintech and tech companies |
| `config/filters.yaml` | Crawl coverage: title phrases to keep, locations, and the template for each new user's scope |
| `config/targets.yaml` | Scoring template: weights, title families, seniority and domain keywords, company groups |
| `config/skills.yaml` | Skill catalogue (canonical name → phrases) used for skill-gap detection |
| `config/prompts/*.md` | Prompts for the per-job agents, versioned in the database once edited on the page |

The shipped templates describe one concrete search (Director roles, New York / New Jersey, banking and fintech) as a worked example. Change them to yours; each user's copy is theirs after onboarding.

## How it works

```
                 ┌──────────────── pipeline graph (admin, daily) ────────────────┐
companies.yaml → │ plan → greenhouse / workday / oracle → filter → parse → embed │
                 │        → dedup → per user: preference → fast score → report   │
                 └───────────────────────────────────────────────────────────────┘
                                                                     │
   Postgres + pgvector  ◄──────────────────────────────────────────── ┘
   companies · raw_postings · jobs · user_jobs · resumes · golden_chunks · preference_models
                                                                     ▲
                 ┌────────────── job graph (on demand, per job) ─────┴───────────┐
   "extract      │ orchestrator → specialist (keywords today) → respond           │
    keywords" →  │ shared Ollama model, or the user's own Claude key               │
                 └────────────────────────────────────────────────────────────────┘
```

| Layer | Where |
|---|---|
| Tools (one file per capability, Pydantic in/out, registered with `@tool_spec`) | `jobagent/tools/` |
| Agents (`AgentSpec`: source, stage or job) and the two LangGraph graphs | `jobagent/agents/` |
| Rule-based posting parser and HTML sanitiser | `jobagent/parse/` |
| Scoring, preference model and score check | `jobagent/tools/scoring.py`, `preference.py`, `scorecheck.py` |
| Web app (FastAPI, vanilla HTML/JS/CSS, no build step, strict CSP) | `jobagent/web/` |
| Database schema and migrations (SQLAlchemy, Alembic) | `jobagent/models.py`, `migrations/` |

Adding a source, a crawl stage or a job agent is one `AgentSpec` file plus the tools it needs. The design notes in `docs/` record how each part came to be.

## Command line

```
jobagent list                          registered tools
jobagent run <tool> [--field value]    run one tool directly, no LLM
jobagent agents                        registered agents
jobagent pipeline "<request>"          the crawl + score graph
jobagent score [--user N]              per-user stages only (learn from marks, scope, fast score)
jobagent users | passwd --user N       accounts
jobagent secret-key                    print a new SECRET_KEY
jobagent ui [--host H --port P]        the web page
```

Under Docker, prefix with `docker compose exec app`.

## Privacy

Your resume, marks and notes never leave your machine unless you choose a hosted model. With `LLM_MODEL=ollama:...` and no stored Claude key, nothing is sent to any LLM provider. Career-site requests go directly from your server to the companies' own endpoints. Backups (`scripts/backup_db.sh`) contain resumes: keep the destination private.

## Development

```bash
pip install -e .[dev]
pytest -q                       # offline suite, no database or Ollama needed
pytest -q -m live               # tests that need the database, network or Ollama
alembic check                   # models and migrations agree
```

Browser smoke test against a running instance: `python scripts/ui_smoke.py` (needs `playwright install chromium`). See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE).
