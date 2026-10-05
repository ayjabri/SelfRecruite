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

| Phase | Deliverable | Proof it works |
| --- | --- | --- |
| 0. Foundations | `pyproject.toml`, `config.py`, `environment.yml`, `git init`, pytest | `pip install -e .`; tests run |
| 1. Tools layer | Registry, `fetch_greenhouse_jobs`, normalisation, extraction split into tools, CLI | Same 10 Stripe rows via CLI, no LLM orchestration involved |
| 2. Graph skeleton | State, agent registry, orchestrator, `greenhouse_agent`, `extraction_agent`, checkpointer, `runs` logging | One command runs orchestrator → Greenhouse → extraction end to end |
| 3. Second and third source | `workday_agent`, then `oracle_agent` | Each added with one tools file + one registry entry; graph code untouched |
| 4. Exposure | MCP server, optional FastAPI | Tools callable from an MCP client |
| 5. Remaining stages | Dedup, scoring, tailoring agents (BUILD_PLAN steps 4–6), JobSpy agent | Per BUILD_PLAN |

## Risks and open decisions

- **Local models and tool calling.** `qwen3.5:4b` followed a simple seniority rule only about half the time in extraction. An orchestrator that picks agents and reacts to failures needs more reliability than that. Recommendation: `qwen3.6:27b` for the orchestrator, `qwen3.5:4b` for source agents, and measure.
- **Where the LLM actually decides.** For a routine daily pull the "decision" is small (which boards, what to do on failure). The design keeps that honest: the agent chooses and sequences tools, but volume, storage and arithmetic stay in code. This preserves BUILD_PLAN's "deterministic spine" rule and keeps runs cheap and repeatable.
- **Unattended runs.** Every run must end even if a model loops: recursion limit on each agent, per-tool timeouts, and a deterministic fallback path (CLI tools in sequence) for cron.
- **Workday and Oracle** need tenant/site identifiers per company, so the company registry (`companies.yaml`) is a prerequisite for Phase 3.
