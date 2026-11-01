# Daily Job-Match Agent — Build Plan

Sep 30, 2026

## Goal and design principles

Every morning the system finds US roles posted in the last 24 hours, scores each one against your golden-source resume, and produces a tailored resume for the ones worth applying to. It runs unattended on your Ubuntu server and leaves a ranked list in Postgres plus a folder of resumes.

**Principles that drive every choice below:**

- **Deterministic spine, LLMs only where judgment is needed.** Fetching, storing and de-duplicating are plain code. Ollama does bulk extraction, a local model does first-pass scoring, and Claude Code does only the expensive tailoring step.
- **Cost funnel.** Thousands of postings a day are fetched; a few hundred survive keyword and location filters; only jobs above a score threshold (start at 70) reach Claude Code.
- **Every stage is idempotent and restartable.** Each stage reads a status column and writes the next one, so a crash at step 5 reruns from step 5, not from scratch.
- **The golden source is the only fact base.** Scoring and tailoring cite it; nothing about you is ever taken from the job posting.
- **"Opened today" means first seen today.** Many ATS feeds expose no reliable post date, so the system records when it first saw each job and treats the source's own post date as secondary.

## Architecture

The pipeline is a funnel: four kinds of source agent feed six stages that share one Postgres state store, and only the last stage costs real money.

&#91;embedded content: daily pipeline · 4 source agents, 6 stages, 1 gate\]

Each stage picks up rows by `status` and hands them on, so any stage can be rerun alone; jobs below the threshold stay searchable in the database without being tailored.

## Step 1 — Ingestion agents

One source agent per ATS, all sharing one interface: `fetch(company, since) -> list[RawPosting]`. Direct ATS feeds give clean, complete postings but need a company list; JobSpy covers everything else by keyword. Each agent writes the untouched payload to `raw_postings` before anything else happens, so extraction can always be rerun without refetching.

| Source | Endpoint (public, no key) | "New today" signal | Watch out for |
| --- | --- | --- | --- |
| Workday | `POST https://{tenant}.wd{N}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs`, then `GET …/job/{externalPath}` for detail | `postedOn` says "Posted Today" | Page size is capped at 20; above that you get an empty page or a 400. Deep pagination stops returning new rows, so always pass `searchText` per keyword |
| Oracle Recruiting Cloud | `GET https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?finder=findReqs;siteNumber={site}` | Posting date on each requisition | Host and site number (often `CX_1`) are read from the careers URL |
| Greenhouse | `boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true` | First seen | Description is HTML-escaped; unescape before parsing |
| Lever | `api.lever.co/v0/postings/{slug}?mode=json` | `createdAt` | Many small companies; cheap to poll |
| Ashby | `api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true` | `publishedAt` | Returns structured compensation; trust it over the LLM |
| SmartRecruiters | `api.smartrecruiters.com/v1/companies/{slug}/postings` | Release date | Detail needs a second call |
| JobSpy (Indeed, LinkedIn, Glassdoor, Google, ZipRecruiter) | `pip install python-jobspy`, `scrape_jobs(..., hours_old=24, country_indeed="USA")` | `hours_old=24` | Catches iCIMS, Taleo and SuccessFactors employers. LinkedIn throttles hard; Indeed is the most reliable board. Scraping boards may breach their terms |

**Company registry.** A `companies.yaml` lists each target employer with its ATS and slug (`workday:nvidia:wd5:NVIDIAExternalCareerSite`, `greenhouse:stripe`). A resolver detects the ATS from any careers URL by pattern (`*.myworkdayjobs.com`, `*.oraclecloud.com/hcmUI/CandidateExperience`, `jobs.lever.co/*`, `jobs.ashbyhq.com/*`). Every employer JobSpy surfaces that isn't in the registry gets resolved and added, so direct coverage grows on its own.

**Open-source reuse.** Use JobSpy as a library as-is. For the ATS agents, the [fulltime\_newsletter](https://github.com/jdw004/fulltime_newsletter) repo already has one module per source for Greenhouse, Lever, Ashby and Workday plus a seen-jobs store; fork it or lift its adapters after checking its license. Each adapter is roughly 60–120 lines of `httpx` with `tenacity` retries, so owning them is cheap and avoids a paid scraping service.

**Pre-filter before any LLM call.** Drop non-US locations and titles outside your target families (AI product or project management, business analysis, Python and AI/ML engineering, Treasury/ALM, ALM quant analytics) with include/exclude keyword lists in `config/filters.yaml`. PhD-gated quant research titles go on the exclude list. This one step usually removes most of the daily volume.

## Step 2 — Structured extraction with Ollama

Ollama's `format` parameter takes a JSON Schema and constrains decoding to it, so a Pydantic model defines the output and `Model.model_validate_json()` checks it ([Ollama docs](https://docs.ollama.com/capabilities/structured-outputs.md)). Structured fields the ATS already returns (title, location, comp on Ashby, req ID) are copied straight across; the LLM only fills what the source leaves as free text.

```python
class JobExtract(BaseModel):
    company: str
    job_title: str
    requisition_id: str | None
    locations: list[Location]            # city, state, country
    workplace_type: Literal["onsite", "hybrid", "remote", "unknown"]
    employment_type: Literal["full_time", "contract", "part_time", "unknown"]
    seniority: Literal["entry", "mid", "senior", "lead", "director", "vp_plus", "unknown"]
    summary: str                          # 2-3 sentences, your words not the JD's
    responsibilities: list[str]
    required_qualifications: list[str]
    preferred_qualifications: list[str]
    skills: list[str]
    min_years_experience: int | None
    education: str | None
    comp_min: float | None
    comp_max: float | None
    comp_period: Literal["year", "hour", "unknown"]
    comp_text: str | None                 # the exact sentence the numbers came from
    visa_sponsorship: Literal["yes", "no", "unstated"]
    clearance_required: bool
```

**Settings that matter.** Temperature 0. Raise `num_ctx` to 16k or more, because long postings otherwise get silently truncated. Convert HTML to text first (`trafilatura` or BeautifulSoup) and strip EEO and benefits boilerplate to save tokens. Start with an 8–14B instruct model and pick the final one by accuracy on a labelled set, not by reputation.

**Guardrails against invented fields.** Every `comp_min` and `comp_max` must appear as a number inside `comp_text`, and `comp_text` must appear in the source text; otherwise both are nulled. Lists of qualifications are spot-checked the same way with fuzzy substring matching. A failed validation retries once, then marks the row `extract_failed` for review instead of blocking the run.

**Throughput.** Run 2–4 concurrent requests against the local Ollama server and cache by content hash, so a posting that reappears unchanged is never re-extracted. Build a 50-posting gold set in week one and track field-level accuracy every time the model or prompt changes.

## Step 3 — Database

PostgreSQL 16 with the `pgvector` extension, run in Docker on the Ubuntu server, bound to 127.0.0.1 only (`pgvector/pgvector:pg16` image) with a named volume and a nightly `pg_dump`. Postgres gives you JSONB for raw payloads, unique constraints for de-duplication, and vector search for fuzzy matching in one server. Access it through SQLAlchemy 2 with Alembic migrations.

```sql
CREATE TABLE companies (
  id           serial PRIMARY KEY,
  name         text NOT NULL,
  name_norm    text NOT NULL UNIQUE,      -- lowercased, suffixes like Inc/LLC removed
  ats          text, ats_slug text, careers_url text
);

CREATE TABLE raw_postings (              -- exactly what each source returned
  id           bigserial PRIMARY KEY,
  source       text NOT NULL,             -- workday | oracle | greenhouse | jobspy_indeed ...
  external_id  text NOT NULL,
  url          text,
  payload      jsonb NOT NULL,
  content_hash text NOT NULL,             -- sha256 of normalized description text
  fetched_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (source, external_id, content_hash)
);

CREATE TABLE jobs (                      -- one row per real-world opening
  id                bigserial PRIMARY KEY,
  company_id        int REFERENCES companies,
  requisition_id    text,
  title             text NOT NULL,
  title_norm        text NOT NULL,
  locations         jsonb,
  workplace_type    text,
  seniority         text,
  description_text  text,
  extracted         jsonb NOT NULL,       -- the full JobExtract
  comp_min numeric, comp_max numeric, comp_period text,
  apply_url         text,
  embedding         vector(768),          -- title + requirements, for dedup and prefilter
  first_seen_at     timestamptz NOT NULL DEFAULT now(),
  source_posted_at  timestamptz,
  status            text NOT NULL DEFAULT 'extracted',
      -- extracted -> deduped -> scored -> queued_tailor -> tailored | skipped | failed
  match_score       smallint,             -- 0-100
  match_explanation text,                 -- human-readable logic behind the score
  match_details     jsonb,                -- per-requirement evidence and gaps
  match_model       text, golden_source_sha text, scored_at timestamptz,
  resume_path       text, tailored_at timestamptz
);

CREATE TABLE job_sources (               -- every posting that points to a job
  job_id        bigint REFERENCES jobs ON DELETE CASCADE,
  raw_id        bigint REFERENCES raw_postings,
  PRIMARY KEY (job_id, raw_id)
);

CREATE TABLE runs (                      -- one row per stage per day
  id bigserial PRIMARY KEY, run_date date, stage text,
  started_at timestamptz, finished_at timestamptz,
  status text, counts jsonb, error text, cost_usd numeric
);

CREATE UNIQUE INDEX jobs_req_uq ON jobs (company_id, requisition_id)
  WHERE requisition_id IS NOT NULL;
CREATE INDEX jobs_status_idx ON jobs (status);
CREATE INDEX jobs_embed_idx  ON jobs USING hnsw (embedding vector_cosine_ops);
```

`match_score` and `match_explanation` live on `jobs` as columns, as you asked; `match_details` keeps the evidence behind them for the tailoring step. `golden_source_sha` records which version of your resume a score was computed against, so editing the golden source triggers a re-score of open jobs.

## Step 4 — De-duplication

The same opening shows up on Workday, LinkedIn, Indeed and Glassdoor, and companies repost old roles under new IDs. Four layers, cheapest first, each one merging duplicates into a single `jobs` row and linking every copy through `job_sources`:

1. **Same posting, same source.** The unique key `(source, external_id, content_hash)` on `raw_postings` makes refetching harmless.
2. **Same requisition.** Same `company_id` plus the same `requisition_id` is one job, whichever board it came from. Direct ATS postings win as the canonical record because their text is complete.
3. **Same text.** Identical `content_hash` across sources or IDs is one job. The same hash reappearing within 60 days of a closed job is a **repost**: flag it and keep the original score rather than treating it as new.
4. **Near-identical.** For what's left, compare `company_norm` exactly, then title similarity (`rapidfuzz` token-set ratio ≥ 90), overlapping location, and embedding cosine ≥ 0.95 on title plus requirements. Embeddings come from `nomic-embed-text` through Ollama (768 dimensions, matching the column).

Matches in the 0.90–0.95 band go to a `possible_duplicate` review list instead of auto-merging; one wrong merge hides a real job, which costs more than scoring a duplicate twice. De-duplication runs before scoring, so Claude Code is never paid to tailor the same role twice.

## Step 5 — Match score and explanation

The score is built bottom-up from each requirement, using the same percentage anchors your resume-tailor skill already uses, so a 70 here means the same thing it means when you tailor by hand.

**How a job is scored.**

1. **Chunk the golden source once.** Split `Golden_Source_Resume.md` by heading into role and project sections, embed each with `nomic-embed-text`, and store them in a `golden_chunks` table keyed by the file's SHA. The OPEN ITEMS and FUTURE TAILORING INSTRUCTIONS sections are always included in full, never retrieved.
2. **Retrieve evidence per requirement.** For each required qualification, preferred qualification and responsibility, pull the top 4 golden-source chunks by cosine similarity.
3. **Judge each requirement with Ollama.** Structured output per requirement: `class` (Covered, Augmentable, Reframe, Gap), `pct` 0–100, `evidence` (the golden-source section name and a quoted line), and `missing` (one line). The prompt carries the anchors verbatim: 90–100 same work at the same scope; 70–89 same work, narrower scope or different domain; 40–69 related but a real part missing; 10–39 adjacent only. Score meaning over wording.
4. **Roll up in plain Python**, not in the LLM, so the arithmetic is auditable:

```latex
\text{score} = 100\,(0.45\,M + 0.20\,P + 0.15\,F + 0.10\,S + 0.10\,D)
```

*M* is the mean `pct` over required qualifications, *P* over preferred ones, *F* is title-family fit to your target list, *S* is seniority fit, and *D* is domain fit (banking, treasury, fintech or AI). All five run 0 to 1.

**Hard gates** cap the score at 30 and state why: security clearance required, a PhD required, onsite outside your allowed metros, or `comp_max` below your floor. These live in `config/targets.yaml` with the title families and locations.

**What goes in `match_explanation`.** A short, fixed-shape paragraph a human can scan in the review list. Illustrative shape: *"78/100. Strong on 6 of 8 must-haves (IRRBB modelling, Python productionization, stakeholder leadership). Gaps: Snowflake, direct P&L ownership. Seniority fit: director-level, matches."* The per-requirement records go in `match_details` and are handed to Claude Code in step 6, so tailoring starts from the gap analysis instead of redoing it.

**Calibrate in week two.** Mark 30 scored jobs as "would apply" or "would skip", then adjust the weights and the tailoring threshold (start at 70) until the score separates them.

## Step 6 — Tailoring with Claude Code

Claude Code runs headless (`claude -p`) once per qualifying job, invoking a new **`resume-tailor-auto`** skill. Your existing resume-tailor skill is interactive by design (one JD item per turn, Approve / Amend / Add / Ignore), so it can't run unattended. The auto skill reuses its scripts, template and every content rule, and swaps each human checkpoint for a conservative default: apply only Covered, Reframe and Augmentable items with golden-source evidence, never write a Gap, and keep current wording wherever a conflict is unresolved.

**Queue.** Jobs with `status = 'scored'` and `match_score >= 70`, highest first, capped at 10 a day to bound cost. Each job gets a work folder holding `job.json` (the extract plus full description) and `match_details.json` from step 5.

**Invocation.** Run in bare mode so the host's hooks, plugins and CLAUDE.md never leak in; bare mode still loads skills from a folder passed with `--add-dir`, and requires `ANTHROPIC_API_KEY` in the environment rather than your subscription login. `--json-schema` makes the result land in a `structured_output` field, and the JSON envelope reports `total_cost_usd` for the `runs` table ([Claude Code headless docs](https://code.claude.com/docs/en/headless)).

```python
# tailor/agent.py — launches one headless tailoring run
import os, json, shutil, subprocess, sys
from pathlib import Path

os.environ['ANTHROPIC_API_KEY'] = Path('key.txt').read_text().strip()
os.environ['ANTHROPIC_MODEL'] = 'claude-haiku-4-5-20251001'
MODEL = os.environ.get('ANTHROPIC_MODEL', "claude-sonnet-5")

SKILL_HOME = Path(__file__).resolve().parent      # contains .claude/skills/resume-tailor-auto/
RESULT_SCHEMA = (SKILL_HOME / "result_schema.json").read_text()
CLAUDE = shutil.which("claude") or "claude"      # full path, so systemd's short PATH still finds it

def tailor(job_dir: Path, timeout_s: int = 900) -> dict:
    prompt = (f"/resume-tailor-auto job_dir={job_dir} "
              f"golden_source={os.environ['GOLDEN_SOURCE_MD']}")
    cmd = [CLAUDE, "--bare", "-p", prompt, "--model", MODEL,
           "--add-dir", str(SKILL_HOME),
           "--permission-mode", "acceptEdits", "--permission-prompts", "none",
           "--allowedTools", "Read,Write,Edit,Bash(python *)",
           "--output-format", "json", "--json-schema", RESULT_SCHEMA]
    proc = subprocess.run(cmd, cwd=job_dir, capture_output=True, text=True, timeout=timeout_s)
    out = json.loads(proc.stdout or "{}")
    if proc.returncode != 0 or out.get("is_error"):
        raise RuntimeError(str(out.get("result") or proc.stderr)[:2000])
    return {"result": out["structured_output"], "cost_usd": out.get("total_cost_usd")}

if __name__ == "__main__":
    print(json.dumps(tailor(Path(sys.argv[1])), indent=2))
```

**After each run, the orchestrator verifies rather than trusts.** The .docx and .md exist, `check_profile.py` passes, the docx validates, and the page count did not grow. Any failure sets `status = 'failed'` with the reason; success sets `tailored` and records `resume_path`. Every resume lands as **needs review**: nothing is ever sent to an employer automatically.

**Model.** Your standing default sets Haiku 4.5 through `ANTHROPIC_MODEL`. Tailoring is the one step where output quality directly affects you, so compare a few Haiku runs against a larger model before locking it in.

**Server notes.** Install Claude Code on the server and run your build sessions over SSH or VS Code Remote-SSH. The headless tailoring runs authenticate only with `ANTHROPIC_API_KEY` from `key.txt` (`chmod 600`, owned by the service user), so no browser login is needed for them. Install `libreoffice-writer` with `--no-install-recommends` so the skill can count pages. JobSpy works from a home connection; from a datacenter IP, LinkedIn and Indeed block quickly.

The skill file itself is attached in chat as a folder you drop into `tailor/.claude/skills/`.

## Step 7 — Output folder and naming

Each tailored job gets its own folder named `{Company}_{JobID}`, with files inside carrying the same stem, so a folder and its files always sort and search together.

```text
/srv/jobagent/resumes/
  2026-10-01/
    Stripe_4412087/
      Resume_Stripe_4412087.docx   <- the deliverable
      Resume_Stripe_4412087.md     <- canonical markdown, easy to edit
      tailoring_report.md                      <- changes, gaps, claims to back up
      job.json                                 <- the extract it was built from
      match_details.json
```

- **Company** comes from `companies.name`, reduced to letters and digits in PascalCase ("JPMorgan Chase & Co." becomes `JPMorganChase`).
- **JobID** is the employer's requisition ID when the source provides one; otherwise it is `J` plus the internal `jobs.id` (`J18342`), which is always unique.
- Characters Windows rejects (`<>:"/\|?*`) are stripped, and the full path stays under 200 characters.
- If the folder already exists, a suffix `_v2`, `_v3` is added rather than overwriting, so re-tailoring after a golden-source change never destroys an earlier version.
- The date folder is the run date, which makes each morning's batch one place to review. `jobs.resume_path` stores the full path of the .docx.

## Scheduling, reliability and observability

One Prefect 3 flow, `daily_run`, started at 06:00 by a systemd timer (`jobagent.timer` plus `jobagent.service`, running as a dedicated `jobagent` user), with one task per stage and the source agents fanned out in parallel. `Persistent=true` on the timer runs a missed day after a reboot. Prefect adds retries, timeouts and run history; run `prefect server start` as its own systemd service if you want its UI, reached through an SSH tunnel.

**Sharing Ollama with Open WebUI.** The pipeline calls the Ollama instance already serving Open WebUI, set by `OLLAMA_HOST` in `config/settings.yaml`. The 06:00 run keeps it clear of interactive use; set `OLLAMA_NUM_PARALLEL` to cap concurrent requests, and pass `num_ctx` and a short `keep_alive` on each call so the extraction model unloads after the run. Pick the extraction model from what you already have installed, and pull `nomic-embed-text` if it's missing.

**Getting resumes to your desktop.** Share `/srv/jobagent/resumes` read-only over Samba, or sync it with Syncthing, so each morning's folder opens in Word on your own machine.

**Reliability rules.**

- A failing source never fails the run: each source agent catches its own errors, logs them to `runs`, and the flow continues with what arrived.
- Per-host concurrency limits and exponential backoff (`tenacity`), a 30-second request timeout, and a circuit breaker that pauses a host for the day after 5 consecutive failures.
- Every stage selects work by `status`, so rerunning the flow after a crash resumes rather than duplicates. Claude Code runs have their own 15-minute timeout.
- Ollama and Postgres health checks run first; if either is down, the flow stops before fetching and alerts you.

**Observability.** The `runs` table records counts per stage (fetched, new, deduped, scored, tailored), errors and Claude Code spend. A morning digest email lists the run counts and the day's top 10 jobs with score, explanation, apply link and resume path. For review, a small Dash page over the `jobs` table lets you filter by score and mark each job applied, skipped or wrong-score, which feeds the week-two calibration.

**Secrets and testing.** `key.txt` and `.env` are git-ignored. Each source adapter has tests against recorded responses (`respx` fixtures), so an ATS changing its JSON shows up as a failing test rather than a silent zero. The extraction gold set and a golden-source contract test (the auto skill must never output a claim absent from the golden source) run in CI on every prompt change.

## Repo layout and configuration

A single Python package deployed to `/srv/jobagent` in its own virtual environment (Python 3.10 or later, created with `uv` or `venv`); every tunable lives in `config/`, so changing targets or thresholds never touches code.

```text
jobagent/
  config/
    companies.yaml        # employer -> ATS + slug
    filters.yaml          # title include/exclude lists, US-only rule
    targets.yaml          # title families, allowed metros, comp floor, hard gates, weights, threshold
    settings.yaml         # Ollama models + num_ctx, DB URL, daily tailoring cap, output root
  jobagent/
    sources/              # one agent per source: workday.py, oracle.py, greenhouse.py,
                          #   lever.py, ashby.py, smartrecruiters.py, jobspy_boards.py
    registry/             # ATS detection from careers URLs, company resolver
    extract/              # html_to_text.py, schema.py (JobExtract), ollama_extract.py, validators.py
    dedup/                # normalize.py, hashes.py, fuzzy.py
    match/                # golden_chunks.py, judge.py (Ollama), rollup.py, explain.py
    tailor/               # queue.py, naming.py, verify.py
    db/                   # models.py (SQLAlchemy), alembic/
    flows/daily_run.py    # Prefect flow wiring the stages
    digest/               # morning email, Dash review page
  tailor/
    agent.py              # headless Claude Code launcher (step 6)
    result_schema.json
    key.txt               # git-ignored
    .claude/skills/resume-tailor-auto/
  data/golden_source/Golden_Source_Resume.md
  tests/
  docker-compose.yml      # postgres + pgvector
```

**Core dependencies:** `python-jobspy`, `httpx`, `tenacity`, `pydantic`, `ollama`, `trafilatura`, `rapidfuzz`, `sqlalchemy`, `psycopg[binary]`, `pgvector`, `alembic`, `prefect`, `pyyaml`, `respx`, `pytest`. JobSpy requires Python 3.10 or later.

## Phased build plan

Roughly four weeks part-time, built so something useful runs from the end of week 1 and each phase only starts once the previous gate is met.

&#91;embedded content: build roadmap · 4 phases, 4 gates\]

The gates are the point: extraction accuracy before scale, clean de-duplication before scoring, a calibrated score before any money is spent on tailoring.

## Risks and open decisions

| Risk | Why it matters | Mitigation |
| --- | --- | --- |
| Job-board terms of service | LinkedIn and Indeed prohibit scraping and throttle it | Keep JobSpy volume low, use it mainly for discovery, and rely on public ATS feeds for the full text |
| Undocumented ATS endpoints change | Workday and Oracle feeds are what their own career pages call, not published APIs | Recorded-response tests per adapter; a zero-result day for a large employer raises an alert |
| An unsupported claim reaches a resume | A fabricated metric can end a candidacy | Golden source is the only fact base, a contract test on every output, and every resume held for your review |
| Local-model scoring drifts | Wrong scores waste tailoring budget or hide good jobs | Week-two calibration, the gold set, and re-scoring when the model changes |
| Coverage gaps | ATS feeds only cover employers in the registry | JobSpy discovery feeds new employers into the registry each day |
| Server down or rebooted | A missed morning means stale results | Persistent=true on the systemd timer runs a missed day at boot, and each run looks back 48 hours |

**Decisions for you before the build:**

- [ ] Seed list of target employers for `companies.yaml` (start with 50–100)
- [ ] Tailoring threshold and daily cap (proposed: score 70, 10 a day)
- [ ] Internal project names on auto-tailored resumes: keep them or genericize by default
- [ ] Allowed metros, remote preference and comp floor for the hard gates
- [ ] Tailoring model after the Haiku comparison

## Sources

- [Claude Code: run programmatically (headless, bare mode, JSON schema output)](https://code.claude.com/docs/en/headless)
- [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs.md)
- [JobSpy (python-jobspy)](https://github.com/speedyapply/JobSpy)
- [fulltime\_newsletter: Greenhouse, Lever, Ashby and Workday source modules](https://github.com/jdw004/fulltime_newsletter)
- [Workday CxS endpoint and its page-size cap](https://dev.to/udaninn/workday-job-boards-have-a-json-api-too-its-just-better-hidden-23fl)
- [Workday pagination ceilings](https://dev.to/juancarlosguti/ask-workdays-public-api-for-100-jobs-and-you-get-zero-with-no-error-54b2)
- [Oracle Fusion HCM recruitingCEJobRequisitions reference](https://docs.oracle.com/en/cloud/saas/human-resources/farws/op-recruitingcejobrequisitions-get.html)
- [ATS detection from careers URLs](https://dev.to/udaninn/you-can-tell-which-ats-a-company-uses-by-looking-at-its-careers-url-3i5g)
