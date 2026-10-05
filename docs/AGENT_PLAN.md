# Agent Re-Plan — LangGraph multi-agent architecture

Oct 5, 2026 · supersedes the pipeline shape in `BUILD_PLAN.md` (the schema, sources, scoring rules and guardrails there still apply)

## Goal

An orchestrator agent delegates to specialised agents: one per data source, then one per pipeline stage. Adding a source or a stage means adding one tools file and one registry entry, with no change to the graph code.

## Three layers

```
Layer 3  Exposure     CLI · LangChain tools · MCP server · FastAPI      (thin adapters, generated from the registry)
Layer 2  Agents       LangGraph: orchestrator + one subgraph per agent  (LLM decides what to call)
Layer 1  Tools        plain Python functions, Pydantic in / Pydantic out (no LLM, no LangGraph imports)
```

**Rule: all real work lives in Layer 1.** A tool can be run from the command line, called by an agent, or served over MCP/HTTP, and it behaves the same in each case.

### Layer 1 — tool contract

Every tool follows the same shape:

```python
class FetchGreenhouseInput(BaseModel):
    board: str = Field(description="Greenhouse board slug, e.g. 'stripe'")
    limit: int | None = Field(None, description="Max postings; None = all")

class FetchResult(BaseModel):          # shared by every source tool
    source: str
    target: str
    fetched: int
    inserted: int
    already_present: int
    raw_ids: list[int]                 # capped; full set is in the DB
    error: str | None

@tool_spec(name="fetch_greenhouse_jobs",
           description="Fetch postings from one Greenhouse board and store them in raw_postings.")
def fetch_greenhouse_jobs(args: FetchGreenhouseInput) -> FetchResult: ...
```

- Typed input and output models: these become the LLM tool schema, the MCP schema and the OpenAPI schema for free.
- **Tools write to Postgres and return a small summary** (counts, IDs, error). Payloads never pass through an LLM's context; a board with 700 postings would not fit and would cost time for nothing.
- Tools never raise for expected failures (404 board, timeout after retries); they return `error` so an agent can react.
- Idempotent: rerunning a tool is harmless (existing unique keys already guarantee this for `raw_postings`).
- `@tool_spec` adds the function to a `TOOLS` registry. Layer 3 adapters loop over that registry.

### Layer 2 — graph

```
START → orchestrator ──Send──▶ greenhouse_agent ─┐
                     ──Send──▶ workday_agent    ─┤
                     ──Send──▶ oracle_agent     ─┼─▶ collect → extraction_agent → dedup_agent → scoring_agent → tailoring_agent → report → END
                     ──Send──▶ jobspy_agent     ─┘
```

- **Orchestrator.** Reads the company registry and the last `runs` rows, decides which source agents to dispatch and with which targets, fans out in parallel with LangGraph's `Send`, then reads their summaries and decides whether to retry, skip or continue.
- **Source agents.** Each is its own subgraph with its own tools and prompt (e.g. `greenhouse_agent` has `fetch_greenhouse_jobs`, `resolve_careers_url`). It receives a task ("pull these boards"), calls its tools, handles failures, and returns a `SourceResult` summary.
- **Stage agents.** Extraction, dedup, scoring and tailoring become agents the same way, each owning its tools.
- **Agent registry.** `AGENTS = {"greenhouse": AgentSpec(tools=[...], prompt=..., build=...)}`. The orchestrator's prompt and the graph's nodes are generated from it, which is what makes adding an agent a one-entry change.
- **State.** One `PipelineState` (run date, targets, `source_results` with an additive reducer, per-stage counts, errors). Only summaries are held in state; rows live in Postgres.
- **Restartability.** `langgraph-checkpoint-postgres` checkpoints the graph into the existing database, so a crashed run resumes at the failed node. Each agent also writes its row to the `runs` table (currently unused).
- **LLM.** Agents use Ollama through `langchain-ollama`. Model is set per agent in config, so the orchestrator can use a stronger model than a source agent.

### Layer 3 — exposure

- `python -m jobagent.cli <tool> --args…` — run any tool by hand or from cron.
- `jobagent/adapters/langchain.py` — `StructuredTool` per registry entry (what the agents use).
- `jobagent/adapters/mcp_server.py` — MCP server exposing the same tools (official `mcp` SDK).
- `jobagent/adapters/api.py` — FastAPI route per tool. Built only when needed.

## Review of the code written so far

| File | Verdict | What changes |
| --- | --- | --- |
| `models.py`, `db.py`, migrations | Keep | `db.py` builds the engine at import time; make it lazy so importing a tool has no side effects. |
| `sources/greenhouse.py` | Keep the logic, reshape | Becomes `tools/greenhouse.py` with the tool contract. Add `pay_transparency=true`. Record the board slug (needed for `companies.ats_slug`). No `since` filter yet. |
| `ingest.py` | Split | `store()` is already source-agnostic; it becomes the shared `store_raw_postings` helper. The `__main__` block is replaced by the CLI. |
| `extract/schema.py`, `extract/llm.py` | Keep | Model name, URL and `num_ctx` are hard-coded; move to config. |
| `extract/run.py` | Rewrite | One 100-line loop mixes selection, company upsert, caching, the LLM call and writes, and it assumes Greenhouse's payload shape (`title`, `location.name`, `company_name`). It will break on the second source. |

**Gaps to close during the refactor**

1. **No normalisation step between sources and extraction.** Each source tool must also emit a common `NormalizedPosting` (company, title, location text, description HTML/text, requisition ID, URL, posted date). Extraction then works on that and never sees source-specific JSON.
2. **Seniority is unreliable from the 4B model.** Derive it in code from title keywords and `min_years_experience`; keep the model's answer as fallback.
3. **Failed extractions are never retried.** A failed row is linked in `job_sources`, so the "pending" query skips it forever. Add an explicit retry tool.
4. **A DB session is held open across LLM calls.** Fine for 10 rows, wrong for thousands with concurrency.
5. **Project hygiene.** No `pyproject.toml` (code only runs from the repo root), no dependency file, no tests, no git repository, no central config.

## Target layout

```
jobagent/
  config.py                 # settings from .env: DB, Ollama URL, model per agent
  db.py  models.py
  tools/
    registry.py             # @tool_spec, TOOLS
    common.py               # FetchResult, NormalizedPosting, store_raw_postings
    greenhouse.py  workday.py  oracle.py  jobspy.py
    extraction.py           # list_pending, extract_posting, retry_failed
    companies.py            # resolve_careers_url, upsert_company
  agents/
    registry.py             # AgentSpec, AGENTS
    state.py                # PipelineState, SourceResult
    base.py                 # build_tool_agent(spec) -> subgraph
    orchestrator.py  graph.py
  adapters/
    langchain.py  mcp_server.py  api.py
  cli.py
tests/
```

## Build order

| Phase | Deliverable | Status |
| --- | --- | --- |
| 0. Foundations | `pyproject.toml`, config, `environment.yml`, git, pytest | Done |
| 1. Tools layer | Registry, Greenhouse fetch, normalisation, extraction tools, CLI | Done |
| 2. Graph skeleton | State, agent registry, orchestrator, checkpointer, `runs` logging | Done |
| 3. More sources | `workday_agent`, `oracle_agent` | Done |
| 3b. Pre-filter | `config/filters.yaml`, `filter_agent` | Done |
| 4. Parse, don't extract | Revision 2 below, part A | Done |
| 5. Embeddings and golden source | Revision 2, part B | Done |
| 6. De-duplication | BUILD_PLAN step 4, on parsed jobs | Done |
| 7. Fast score | Revision 2, part C | Done, plus a preference model trained on marks and a review UI |
| 8. LLM judgement for the top slice | Revision 2, part D | Next |
| 9. Tailoring | BUILD_PLAN step 6 | |
| Any time | MCP server; concurrent LLM calls; JobSpy agent | |

## Revision 2 (Oct 5, 2026) — parse first, LLM last

Two changes, one idea: the LLM is the slowest and least repeatable part, so it moves to the end of
the funnel and only sees jobs that have already ranked well.

```
fetch → pre-filter → PARSE → de-dup → FAST SCORE ──top slice──▶ ENRICH + JUDGE → tailor
 code      code       code     code      code/ML                      LLM          LLM
```

Today the LLM runs at "parse" for every kept posting (about 10 s each). After this revision it runs
only for jobs above the fast-score cutoff, expected to be tens per day.

### A. Parse stage (replaces LLM extraction)

Every posting that passes the pre-filter becomes a `jobs` row immediately, built by code.

| Field | How |
| --- | --- |
| Title, company, URL, requisition ID, posted date | Copied from the source (already in `NormalizedPosting`) |
| Employment type, job family | Copied where the source has it (Workday `timeType`, Oracle `JobSchedule`/`JobFamily`, Greenhouse `departments`) |
| Locations | Split the location text into city / state / country using the existing `geo` module |
| Pay range | ATS field if present, else a dollar-range pattern in the text; the matched sentence is stored |
| Minimum years | Pattern (`5+ years`), smallest value found in a requirements section |
| Workplace type, clearance, visa | Keyword checks |
| Seniority | Existing `derive_seniority` |
| Sections and bullets | Walk the description HTML: each heading starts a section, each `<li>` is a bullet. Sections are labelled `responsibilities`, `required`, `preferred` or `other` by heading keywords ("what you'll do", "requirements", "nice to have") |

- Stored in a new `jobs.parsed` JSONB column. `jobs.extracted` becomes nullable and is filled only by enrichment.
- New status `parsed` at the start of the flow: `parsed → deduped → prescored → scored → queued_tailor → tailored | skipped | failed`.
- When a posting has no recognisable sections, all bullets are treated as requirements and the job is flagged `sections: unlabelled`, so the fast score still works and the gap is measurable.
- **Check before switching:** run the parser over the jobs already extracted by the LLM and report agreement per field (pay, years, workplace, seniority). The LLM path is removed from this stage only if the parser holds up.

Tools: `parse_posting`, `parse_pending_postings`. Agent: `parse_agent` replaces `extraction_agent`.

### B. Embeddings and the golden source

- `embed_jobs`: title plus requirement bullets through `nomic-embed-text` into `jobs.embedding` (used by de-dup and the fast score).
- `index_golden_source`: split the golden-source resume by heading, embed each section, store in a new `golden_chunks` table keyed by the file's SHA. Re-indexing happens only when the file changes.

### C. Fast score (no LLM)

One number per job, computed for every parsed job in seconds.

```
fit = 100 × (0.45·M + 0.20·P + 0.15·F + 0.10·S + 0.10·D)      hard gates cap it at 30
```

Same weights and gates as BUILD_PLAN step 5, so the two scores are comparable. What changes is how M and P are measured:

- **M, P:** for each required (or preferred) bullet, the best cosine similarity against the golden-source chunks, mapped to 0–1 and averaged. If a posting has no preferred section, P takes M's value.
- **F, S, D and the gates:** rules from `config/targets.yaml` (title families, seniority range, domains, allowed metros, pay floor, clearance, PhD). No model involved.

Stored in new columns `fit_score` and `fit_details` (per-bullet similarity and the chunk it matched), leaving `match_score` for the LLM's judgement.

**Limits, stated plainly.** Similarity measures topical closeness, not qualification. It will rank "treasury sales" near "treasury analyst". That is acceptable for a first cut whose job is to decide what the LLM looks at, and not acceptable as the final score.

**Upgrades, in order, only if the labelled set shows the ranking is poor:**
1. A cross-encoder reranker to score bullet/chunk pairs (more accurate; adds a PyTorch dependency).
2. A trained classifier over these signals once there are a few hundred apply/skip decisions. Thirty labels are enough to tune weights, not to train.

### D. LLM judgement for the top slice

For jobs with `fit_score` above a cutoff (start loose, e.g. 50, capped per day):

- `enrich_job`: the existing LLM extraction, now on demand, filling `jobs.extracted`.
- `score_job`: BUILD_PLAN step 5 as written (per-requirement class, percentage, evidence), producing `match_score`, `match_explanation` and `match_details` for tailoring.

Both belong to `scoring_agent`. The tailoring threshold (70) applies to `match_score`, as before.

### Calibration

The 30 "would apply / would skip" labels from BUILD_PLAN serve twice: to set the fast-score cutoff so that no "would apply" job falls below it, and to tune the weights.

### What this needs from you

- The golden-source resume file, placed in the repo (ignored by git) or its path.
- `config/targets.yaml` values: target title families, acceptable seniority, allowed metros for onsite roles, and the pay floor.

## Risks and open decisions

- **Local models and tool calling.** `qwen3.5:4b` followed a simple seniority rule only about half the time in extraction. An orchestrator that picks agents and reacts to failures needs more reliability than that. Recommendation: `qwen3.6:27b` for the orchestrator, `qwen3.5:4b` for source agents, and measure.
- **Where the LLM actually decides.** For a routine daily pull the "decision" is small (which boards, what to do on failure). The design keeps that honest: the agent chooses and sequences tools, but volume, storage and arithmetic stay in code. This preserves BUILD_PLAN's "deterministic spine" rule and keeps runs cheap and repeatable.
- **Unattended runs.** Every run must end even if a model loops: recursion limit on each agent, per-tool timeouts, and a deterministic fallback path (CLI tools in sequence) for cron.
- **Agents around pure-code stages.** `filter_agent`, and after this revision `parse_agent` and the fast-score stage, each wrap a single tool with fixed arguments. Running an LLM to call one tool adds 10–20 seconds and a failure point with no decision to make. Proposal: an `AgentSpec` option that runs such a stage's tool directly, keeping LLM agents for the stages with real choices (orchestrator, sources, scoring, tailoring). Open decision.
