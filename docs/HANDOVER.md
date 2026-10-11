# Handover — Self Recruiter (AI) / jobagent

Written Oct 6, 2026 for whoever (or whichever conversation) picks this up next. Everything
below is current as of commit `9b73a99` on `main`.

## What it is

A multi-user job-match platform on this server (`/srv/jobagent`, Ubuntu, host `llm`,
LAN address 10.0.0.100). A shared crawl pulls postings from Greenhouse, Workday and Oracle
Recruiting sites, parses them with rules (no LLM), embeds them, de-duplicates them. Each
user has a profile (scope, scoring targets, skills), a library of markdown resumes, fast fit
scores from embeddings + rules + a preference model trained on their marks, and an on-demand
AI conversation per job (today: keyword extraction) on either the shared local model or their
own Claude key. The owner is Ethan / Ayman (user 1, admin, `ayjabri@gmail.com`); a second
account `ethan.jabri@gmail.com` is a member.

Plans and decisions, in order: `docs/BUILD_PLAN.md` (original), `docs/AGENT_PLAN.md`
(agentic rebuild), `docs/MULTIUSER_PLAN.md` (phases 1–5, all done), `docs/KEYWORDS_PLAN.md`
(per-job AI, done), `docs/HTTPS.md`, `docs/COMPANY_SOURCES.md`.

## Running it

- Conda env: `/srv/llm/miniforge3/envs/jobagent` (Python 3.12). Binaries: `$B=/srv/llm/miniforge3/envs/jobagent/bin`.
  Not a venv; the owner uses miniforge. Project installed editable (`pip install -e .[dev]`).
- Database: Docker container `jobagent-db` (pgvector/pg16, 127.0.0.1:5432), credentials in
  `.env` (`DATABASE_URL`). Migrations: `$B/alembic upgrade head`; `$B/alembic check` must say
  "No new upgrade operations". Nightly dump via `systemd/jobagent-backup.timer` → `backups/`.
- LLMs: Ollama on localhost:11434 (`qwen3.6:27b` chat, `nomic-embed-text` embeddings, one
  RTX 3090). `LLM_MODEL` in `.env`. Users may store their own Claude key (Fernet-encrypted
  with `SECRET_KEY` in `.env`).
- Web: `systemd/jobagent-ui.service` runs `jobagent ui --host 127.0.0.1 --port 8765
  --no-reload --proxy-headers`; Caddy (`deploy/Caddyfile`, installed at /etc/caddy/Caddyfile)
  serves it as `https://10.0.0.100` / `https://llm` with its internal CA (`tls internal`),
  HTTP redirects to HTTPS. **Python changes need `sudo systemctl restart jobagent-ui.service`**
  (static files and index.html are read from disk, so page-only changes need just a reload).
  The assistant has no sudo: give the owner the command.
- Daily crawl: `systemd/jobagent-crawl.timer` (06:00). The owner may not have installed it yet.
- CLI: `jobagent list | run <tool> [--field v | --json '{...}'] | agents | pipeline "<request>"
  | score [--user N] | users | passwd --user N [--email E] | secret-key | ui`.
- Tests: `$B/pytest -q` (86 pass). Browser smoke test (needs playwright chromium; on this
  host set `LD_LIBRARY_PATH=$ENV/lib` for libasound): start a test server on 8766
  (`$B/uvicorn jobagent.web.app:app --port 8766`), then `$B/python scripts/ui_smoke.py`.
  Kill test servers by port (`ss -ltnp | grep 8766`), never with `pkill -f` on a pattern that
  appears in your own command line (it kills the shell: exit code 144).

## Code map

```
jobagent/
  config.py, db.py, models.py, users.py, auth.py, secrets.py, llm.py, prompts.py, skills.py
  groups.py         per-user company groups + interest statements (Segments.check: qualify / cap / hide)
  tools/            one file per capability; @tool_spec registers Pydantic in/out in TOOLS
                    (sources greenhouse/workday/oracle; filtering, parsing, embeddings, dedup,
                     scoring (+compute_scope, label, annotate), preference (+refresh, segment features),
                     groups (company_rules_report), judging (legacy),
                     keywords (job_keywords_context, save_job_keywords, job_chat), cleanup, skills)
  agents/           AgentSpec registry: kind=source | stage (scope crawl|user) | job
    graph.py        pipeline graph: plan -> sources -> review -> crawl stages -> user_stages per user -> report
    job/graph.py    job graph: orchestrator (intent fast path, else Route) -> specialist -> respond
    job/keywords.py the first job agent (prompt_name="keywords", intents)
  adapters/langchain.py   TOOLS -> StructuredTool, with bound user_id/job_id
  parse/            rule-based posting parser; html.py sanitises posting HTML for display
  keywords/context.py     similar-role terms from job embeddings + TF-IDF
  judge/core.py     legacy per-requirement LLM judgement (not in the batch any more)
  web/app.py        FastAPI: middleware (hardening headers, body limit, sign-in gate + CSRF), all /api
  web/configs.py    Settings data: profile sections + shared config files (ruamel, comment-preserving)
  web/runs.py       in-process run queue (lanes: llm=1, default=2), logs in data/runs/
  web/index.html, login.html, static/{style.css, app, tracker, run, settings, groups, check, onboarding, admin, boot, login}.js
config/             companies.yaml (crawl list), filters.yaml (crawl coverage + template),
                    targets.yaml (scoring template), skills.yaml (catalogue), prompts/keywords.md
migrations/versions  Alembic; latest b8c9d0e1f2a3
```

Data model essentials: shared `companies`, `raw_postings`, `jobs` (+`job_sources`,
`possible_duplicates`); per user `users`, `sessions`, `profiles` (targets/scope/skills/llm
JSONB), `resumes` (one active), `golden_chunks` (resume sections, embedded), `user_jobs`
(scope, fit score, marks, notes, applied_resume_id, legacy match_*), `preference_models`,
`job_messages`, `job_keywords`; shared `prompts` + `prompt_versions`.

## Conventions the owner expects

- Commits only when asked ("Commit the working tree changes with a sensible message").
  No git identity configured: use `git -c user.name="ayman" -c user.email="ayjabri@gmail.com"`;
  end messages with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Plans before big changes: write them to `docs/` and wait for "Approve".
- Keep NY/NJ: the owner cannot move. Director roles, banking and fintech.
- Vanilla HTML/JS/CSS, no build step, no CDN (CSP is `script-src 'self'`): every script is a
  file under `web/static/`, never inline. Verify page changes with the smoke test; a JS
  syntax error in one file silently breaks the page.
- Every function is a registered tool callable by an agent, the CLI and the API; adding a
  source / stage / job agent is one `AgentSpec` file.

## Scoring, as of Oct 10, 2026 (docs/INDUSTRY_SCORING_PLAN.md)

- User stages: `preference_agent` (retrains the user's model when marks, groups or role families
  changed; `refresh_preference_model`) then `fast_score_agent`. `score_jobs_fast` re-scores any job
  whose `fit_details.targets_sha` / `golden_source_sha` is stale, so "score my jobs" always converges.
- Company groups (Settings → Company groups) live in `profile.targets.company_groups` /
  `company_interest`. A statement = role families + keywords + cap|hide; a job qualifies on any
  family or keyword; the company's statement replaces its group's. The model's `segment` feature is
  the company when it has its own statement, else its group.
- Tracker: group filter; label filter "Interested, against my rules" lists marks a rule now caps or hides.
- All page scripts share one global scope: `tests/test_page_scripts.py` parses them together.
- Score check tab (`tools/scorecheck.py`, `static/check.js`, `GET /api/score-check`): held-out accuracy
  vs the rule score, score bands, disagreements, per group/company, capped interested marks, forward
  test, history. Training stores held-out scores and a history in `preference_models.diagnostics`;
  `label_job` records `user_jobs.label_score` (the score when marked). Migration `b8c9d0e1f2a3`.

## Open items and known gaps

- Chat box on the job page (the endpoint `POST /api/jobs/{id}/chat` already takes free text;
  the Extract button sends "extract keywords"). More job agents: commentary, tailoring, Q&A.
- Retire `judge_jobs` / `match_*` columns once unused.
- Password reset by email (needs SMTP), Google sign-in, public domain + HTTPS block in the
  Caddyfile, `REGISTRATION_OPEN=false` for invite-only.
- Sources: Lever, Ashby, SmartRecruiters, JobSpy; Mizuho/Jefferies entries documented, not added.
- MCP server from the TOOLS registry; LLM concurrency for hosted providers.
- 72 pending duplicate pairs (Greenhouse jobs without requisition IDs) await the owner.
- Windows client: the owner was installing the Caddy root certificate
  (`https://10.0.0.100/caddy-root.crt` after exporting it, see docs/HTTPS.md).
