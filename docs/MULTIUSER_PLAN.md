# Multi-user plan — Self Recruiter (AI) as a platform

Date: Oct 6, 2026. Status: **proposal, nothing built yet.**

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

## Order and effort

| Phase | Depends on | Rough size |
|---|---|---|
| 1 Data model split + migration | — | 1 session |
| 2 Per-user tools, two pipelines, worker | 1 | 2 sessions |
| 3 Auth | 1 | 1 session |
| 4 UI (onboarding, login, split files, admin) | 2, 3 | 2–3 sessions |
| 5 Hardening | 3, 4 | 1 session |

Phases 1–3 can be built and tested from the CLI with user 1 before any UI changes, so the
current UI keeps working throughout (it will read `user_jobs` for user 1 after Phase 1).

## Decisions needed before starting

1. **Audience and exposure.** Friends on the LAN, or the internet with a domain? Changes Phase 5
   and whether email verification is required. Default assumed: internet, small group (tens of users).
2. **Sign-in.** Email + password now, Google later? Default: email + password.
3. **LLM cost.** Shared Ollama with a daily per-user judgement budget, plus optional own API key?
   Default: yes, 20 judgements/user/day.
4. **Companies.** One shared crawl list managed by the admin (default), or can users request
   companies? Default: shared list, users can submit a request the admin approves.
5. **Resume formats.** Markdown only (as now), or PDF/DOCX too? Default: all three.
6. **The current UI** stays usable for you (user 1) during the rebuild. Any feature freeze
   needed on it meanwhile?

Approve the defaults, or change any, and Phase 1 starts.
