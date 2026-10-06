# Multi-user plan — Self Recruiter (AI) as a platform

Date: Oct 6, 2026. Status: **approved Oct 6, 2026. Phases 1 and 2 done (Oct 6); Phase 3 (auth) next.**

## Goal

Other people register, describe what they are looking for, upload a resume, log in and see the
jobs that match them. The crawl stays shared (one pull serves everyone); everything that is
about a person becomes per-user.

## Where the code is single-user today

| Area | Today | Problem for many users |
|---|---|---|
| Marks, favorites, applied, notes, fit score, match score, judgement | columns on `jobs` | one person's data on a shared row |
| Resume | one file, `docs/Ethan_*.md`, chunks in `golden_chunks` | no owner |
| Preference model | `data/preference_model.joblib` | one model for everyone |
| Targets, pre-filter keywords, allowed states, skills "have" | `config/*.yaml` | shared files; the Settings tab edits them for all |
| Pre-filter | decides which postings get **parsed** at all, by title keywords | one user's keywords would hide jobs from others |
| Companies to crawl | `config/companies.yaml` | fine as shared, needs an owner (admin) |
| Runs from the UI | one subprocess at a time, global | per-user scoring jobs would queue behind each other |
| LLM judgement | one local Ollama on one GPU, ~15 s per job | shared bottleneck; cost policy needed |
| Web UI | no login, open on the LAN | anyone sees everything |
| Hosting | uvicorn on port 8765, HTTP | no HTTPS, no secrets handling, no rate limit |

## Target shape

```
shared layer (system, admin-owned)             per-user layer
──────────────────────────────────             ─────────────────────────────────
companies, raw_postings, jobs, job_sources     users, sessions
parsed fields, embeddings, dedup pairs         profiles  (targets, keywords, states, skills)
crawl runs (scheduled)                         resumes + golden_chunks(user_id)
                                               user_jobs (score, marks, notes, judgement)
                                               preference models (one per user)
                                               user runs (score / judge, queued)
```

Rule: **parse and embed every US posting once; filter, score and judge per user.** The parser
has no LLM cost, so parsing everything is cheap (2.7k postings today). Title keywords stop
being a crawl filter and become each user's "scope".

## Phases

### Phase 1 — Data model split (foundation, no UI change yet)

New tables (Alembic):

- `users` (id, email unique, password_hash, name, role admin|member, status, created_at)
- `profiles` (user_id, targets JSONB, scope JSONB [title include/exclude, states, remote ok],
  skills JSONB [have, not_have], llm JSONB [judge budget/day, own API key encrypted or null])
- `resumes` (id, user_id, filename, text, sha, uploaded_at, active)
- `golden_chunks` + `user_id`, `resume_id`
- `user_jobs` (user_id, job_id, in_scope, fit_score, fit_details, user_label, labeled_at,
  favorite, applied_at, notes, match_score, match_explanation, match_details, scored_at,
  judged_with, unique (user_id, job_id))
- `preference_models` (user_id, trained_at, labels, cv_auc, blob bytea)
- `runs` + `user_id` nullable (null = system crawl)

Removed from `jobs`: every per-user column listed above. `jobs.status` keeps only parse/dedup
states. `raw_postings.filter_status` keeps only the location decision (US / state gate stays
global as a crawl-wide floor; per-user states are applied in scope).

Migration script: create user 1 from the current data (resume, marks, model, config files →
profile JSON). Nothing is lost.

Config files keep one job: **defaults for a new profile** (`config/*.yaml` → template).

### Phase 2 — Per-user tools and pipeline

- Every per-user tool takes `user_id`: `index_resume`, `score_jobs_fast`, `train_preference_model`,
  `judge_jobs`, `label_job`, `annotate_job`, `job_skills`, `purge` becomes per-user scope, not delete.
- Scope computation: `compute_scope(user_id)` writes `user_jobs.in_scope` from the profile's
  keywords and states. Replaces the pre-filter for users.
- Two pipelines:
  - **crawl pipeline** (system, scheduled): fetch → location filter → parse → embed → dedup.
  - **user pipeline** (per user, after each crawl and on demand): scope → fast score → judge top N.
- Job queue: replace the subprocess runner with a Postgres-backed worker
  (`procrastinate`; no Redis). One worker process; LLM tasks in a single-concurrency queue so the
  GPU is never oversubscribed; everything else parallel.
- LLM policy: per-user daily judgement budget (e.g. 20 jobs/day on the shared Ollama). A user may
  store their own Anthropic key (encrypted with a server secret) to lift the cap; then
  `LLM_MODEL` is per user.

### Phase 3 — Auth

- Email + password (argon2 via `pwdlib`), server-side sessions in Postgres, HttpOnly secure cookie,
  CSRF token on state-changing requests, login rate limit, email verification and password reset
  via SMTP (needs an SMTP account; can be a later sub-step).
- Roles: `member` sees only their own data; `admin` also sees companies, crawl runs, duplicates,
  user list, system health.
- Optional later: Google sign-in.

### Phase 4 — UI

- Public: landing, register, login, reset password.
- Onboarding wizard: (1) upload resume (markdown, PDF, DOCX → text via `pypdf` / `python-docx`;
  chunk by headings, fallback by paragraphs), (2) pick role families + seniority, (3) states and
  remote preference, (4) skills review (auto-detected from resume, tick/untick). Ends by queuing
  the user pipeline and showing a "scoring…" state.
- Job tracker, duplicates (admin), Run (per-user actions only: rescore, retrain, judge more),
  Settings (per-user, DB-backed; the YAML editor stays for admin crawl config).
- Admin tab: companies, crawl schedule, run log, users.
- Page split: the single `index.html` becomes a handful of static files (no build step still):
  `app.js`, `tracker.js`, `settings.js`, `admin.js`, `style.css`.

### Phase 5 — Deployment hardening

- Reverse proxy with automatic HTTPS (Caddy), uvicorn with 2–4 workers under systemd, the worker
  as a second service, health endpoint, structured logs.
- Secrets in `.env` only (session secret, encryption key, SMTP), Postgres role per service,
  daily DB backup already in place → add off-box copy.
- Limits: upload size, request rate, per-user judge budget, max resumes per user.
- Privacy: resumes are personal data → delete-account flow removes resume, chunks, marks, model;
  a short privacy note on the register page. If exposed to the internet: domain, firewall,
  fail2ban or proxy rate limiting.

### Phase 6 — After launch

- Tailoring (BUILD_PLAN step 6) per user, MCP server, more sources (Lever, Ashby, SmartRecruiters).

## Phase 1 as built (Oct 6, 2026)

- Migration `d4e5f6a7b8c9`: `users`, `profiles`, `resumes`, `user_jobs`, `preference_models`;
  `golden_chunks.user_id/resume_id`; `runs.user_id`; the per-user columns left `jobs`. User 1
  (admin, email from `ADMIN_EMAIL`) received the marks, scores, judgements, resume (as a stored
  `resumes` row), chunks, preference model and a profile seeded from the config files.
- `jobagent/users.py`: `DEFAULT_USER_ID`, `get_profile` / `save_profile` (sections fall back to
  the `config/*.yaml` templates), `active_resume`, `user_job`.
- Per-user tools (all take `user_id`, default the current user): `upload_resume` (new),
  `index_golden_source`, `compute_scope` (new: profile title keywords + states → `user_jobs.in_scope`),
  `score_jobs_fast` (runs `compute_scope` first), `train_preference_model` (model stored in the DB),
  `judge_job` / `judge_jobs`, `label_job`, `annotate_job`, `job_skills`, `my_skills`.
- Crawl pre-filter keeps the union of every profile's title keywords; dedup carries every user's
  marks across a merge; `purge_jobs` keeps jobs any user marked.
- Web: every endpoint acts as `current_user()` (DEFAULT_USER_ID for now); Settings tab edits the
  profile sections (Scope, Scoring, Skills) and the resume per user, and the shared files
  (companies, skill catalogue, templates) for the admin.
- Still global in Phase 1: which postings get parsed (crawl pre-filter), companies, dedup decisions.

## Phase 2 as built (Oct 6, 2026)

- **Crawl parses every US posting.** The crawl pre-filter keeps every title and drops only by
  location: outside the US, or outside the union of every user's allowed states (no state gate if
  any user allows anywhere). Title keywords live in each user's scope (`compute_scope`).
- **Two pipelines in one graph.** Stages carry a `scope`: crawl stages (filter, parse, embed,
  dedup) run once in a chain; then `user_stages` fans out one branch per active user and runs the
  user-scope stages (fast score, judgement) with `user_id`. `jobagent score [--user N]` runs the
  per-user stages alone; the page's Run tab offers it as "score my jobs".
- **Run queue with lanes** (`jobagent/web/runs.py`): `llm` lane (one at a time: pipeline, judge,
  enrich) and `default` lane (two in parallel). Queued runs survive a server restart; runs that
  were in progress are marked failed. Chosen over a separate worker service for now: no extra
  process or tables; swap for procrastinate when more than one server is needed.
- **Scheduled crawl:** `systemd/jobagent-crawl.{service,timer}`, 06:00 daily (install with sudo).
- **Per-user LLM.** Profile `llm` section: provider shared | anthropic | openai, model, API key
  (Fernet-encrypted with `SECRET_KEY` from .env, write-only in the UI), daily judgement budget on
  the shared model (default 20; own account lifts it). `get_chat_model(user_id=...)` picks the
  user's account; `user_jobs.match_model` records which model judged.

## Order and effort

| Phase | Depends on | Rough size |
|---|---|---|
| 1 Data model split + migration | — | done |
| 2 Per-user tools, two pipelines, worker | 1 | done |
| 3 Auth | 1 | 1 session |
| 4 UI (onboarding, login, split files, admin) | 2, 3 | 2–3 sessions |
| 5 Hardening | 3, 4 | 1 session |

Phases 1–3 can be built and tested from the CLI with user 1 before any UI changes, so the
current UI keeps working throughout (it will read `user_jobs` for user 1 after Phase 1).

## Decisions (Oct 6, 2026)

1. **Audience.** LAN first; publish to the internet later. Users will be able to connect their own
   Claude (Anthropic) or OpenAI account for commentary and resume tailoring, so **per-user LLM
   provider keys are a first-class feature** (encrypted at rest, provider + model per user), not an
   afterthought. The shared local Ollama stays the default for scoring and judgement.
2. **Sign-in.** Email + password (default). Google later if wanted.
3. **LLM cost.** Shared Ollama with a daily per-user judgement budget (20/day); a user's own key
   lifts the cap and powers commentary / tailoring.
4. **Companies.** One shared crawl list managed by the admin; users can request additions.
5. **Resume formats.** Markdown only for now.
6. **No feature freeze** on the current UI; it keeps working as user 1 throughout.
