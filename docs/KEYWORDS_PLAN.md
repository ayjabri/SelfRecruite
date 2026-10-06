# Plan: on-demand LLM keywords per job (replaces batch LLM judgement)

Date: Oct 6, 2026. Status: **approved and built Oct 6, 2026.**

## How the batch LLM step works today

The pipeline is one LangGraph graph (`jobagent/agents/graph.py`). After the source agents pull
postings and the crawl stages run (filter → parse → embed → dedup), the graph fans out one branch
per active user and runs the user-scope stages in order:

1. `fast_score_agent` → tool `score_jobs_fast` (no LLM; embeddings + rules + the preference model)
2. `scoring_agent` → tool `judge_jobs` (**the LLM step**)

`scoring_agent` is a registered `AgentSpec` with `scope="user"` and `direct=True`: it is a node
in the graph, but no model decides what to do; the node just calls `judge_jobs(user_id=...)`.
That tool picks up to 20 jobs per user (in scope, fit ≥ 50, not marked not-interested, favorites
first), and for each one calls `jobagent/judge/core.py:judge()`, which retrieves the three
closest resume sections per requirement, sends one prompt to the chat model, and stores the
per-requirement verdicts as `match_score` / `match_details` on `user_jobs`. On the local
qwen3.6:27b that is 15–60 s per job, serialised on the GPU, with a daily budget per user. The
same tool backs the "score my jobs" button and the daily crawl timer.

## What changes

### 1. Remove the LLM from the batch

- Unregister `scoring_agent` (delete `jobagent/agents/judging.py`). The graph then has one
  user-scope stage, `fast_score_agent`; "score my jobs", the daily crawl and the fan-out are
  otherwise unchanged. No LLM runs unattended any more.
- Keep the `judge_job` / `judge_jobs` tools and the match-score columns for now (an admin can
  still call them from the Run tab; nothing in the batch calls them). They can be retired in a
  later clean-up once keywords have replaced them in daily use.
- The per-user daily judgement budget stays on `judge_jobs` and will also meter the new keyword
  calls on the shared model.

### 2. The job conversation: a LangGraph with an orchestrator and specialist agents

Everything the AI does *about one job for one user* goes through a second LangGraph graph, the
**job graph** (`jobagent/agents/job/`), separate from the crawl pipeline graph. It is built the
same way the pipeline is: from a registry, so a new capability is one `AgentSpec` file.

```
user message ("extract keywords")            thread = (user_id, job_id), checkpointed in Postgres
        │
        ▼
   ┌──────────────┐    structured output: {agent, task, reply_directly?}
   │ orchestrator │──► routes to exactly one specialist (or answers a plain question itself)
   └──────────────┘
        │ Send
        ▼
   ┌──────────────┐  ┌───────────────┐  ┌───────────────┐  ┌─────────────┐
   │ keywords     │  │ commentary    │  │ tailoring     │  │ qa          │   ← registry, kind="job"
   │ (phase 1)    │  │ (later)       │  │ (later)       │  │ (later)     │
   └──────────────┘  └───────────────┘  └───────────────┘  └─────────────┘
        │ tool calls: job_keywords_context, save_job_keywords, ...
        ▼
   ┌──────────────┐
   │ respond      │──► assistant turn stored; structured results stored in their own tables
   └──────────────┘
```

- **Registry.** `AgentSpec` gains `kind="job"`. A job agent declares its name, a one-line
  description the orchestrator reads, its system prompt (the *skill*, loaded from the prompt
  store so admins can edit it), its tools, and `intents`: example phrasings ("extract keywords",
  "what will the ATS scan for") that the orchestrator's fast path matches before calling the
  model. Adding commentary or tailoring later is one file; the graph, the router prompt and the
  API pick it up with no other change, exactly like adding a source agent today.
- **Orchestrator.** A node with structured output (`Route {agent, task, reasoning}`) over the
  conversation so far and the agent descriptions. Deterministic fast path first: if the message
  matches an agent's intents, no model call is spent on routing. If nothing fits, it answers
  the user directly ("I can extract keywords for this job; say 'extract keywords'").
- **Specialists.** Each is a tool-calling agent (`create_agent`, same as the source agents)
  running with the user's chosen model, its own skill prompt, and only its own tools. The
  keywords agent's tools: `job_keywords_context` (posting text + similar-role terms, no LLM) and
  `save_job_keywords` (validated JSON → `job_keywords`). The agent's final message is the
  assistant turn; the saved row is what the UI renders.
- **State.** `JobChatState {user_id, job_id, messages, route, results}`; `PostgresSaver`
  checkpointer (already in use for the pipeline) with `thread_id = f"job:{user_id}:{job_id}"`,
  so the conversation resumes across requests and later phases can refer back ("tailor my
  resume for the gaps you found"). Turns are also written to `job_messages` (user_id, job_id,
  role, content, agent, created_at) for the page to list without touching checkpoints.
- **Execution.** `chat(user_id, job_id, message)` is a tool (`job_chat`) like every other, so
  the CLI, the API and other agents can call it. The API endpoint `POST /api/jobs/{id}/chat`
  queues it: `llm` lane for the shared model (one at a time, daily budget), direct for a user
  on their own Claude key. Results arrive by polling the job (same pattern as runs today);
  streaming can be added later without changing the graph.
- **The button is a message.** "Extract keywords" on the job page calls
  `POST /api/jobs/{id}/chat {"message": "extract keywords"}`. Replacing the button with a chat
  box is a UI-only change: same endpoint, free text, and the transcript from `job_messages`
  shown above it.
- **Guard rails that make it production-grade:** one specialist per turn with a recursion
  limit; every tool call recorded on the turn (facts, not prose); failures stored as an
  assistant turn with the error, never a crash; per-user budget and lane enforced at the queue;
  prompts versioned; the model and prompt version recorded on every stored result; unit tests
  for the router fast path, the graph wiring from the registry, the context builder and the
  JSON validation, with the model stubbed.

### 3. What the LLM receives

Message to the model = the standard skill (system prompt, below) + a user message containing:

1. **The full posting text** (the sanitised HTML flattened to text; the same thing the page
   shows under "Full posting text").
2. **Terms commonly asked for in similar roles**, computed in code before the call:
   - Find the N (default 25) nearest historical jobs by **the existing job embeddings** (title +
     requirement bullets, nomic-embed-text, already stored on every job), restricted to the same
     title family when one is known; this is the "similar jobs" set.
   - From that set, extract candidate terms two ways: the rule-based skill catalogue hits
     (`jobagent/skills.py`), and the distinctive phrases of the set versus the whole pool
     (TF-IDF on 1–3-grams, top 40 by average weight, excluding generic words).
   - Rank by how many of the similar jobs mention the term; pass the top 30 with their
     frequency ("Python (21 of 25 similar postings)").

   No new model is trained for this: the embeddings already exist and the TF-IDF statistics are
   recomputed per request in milliseconds. Fine-tuning an embedding model is listed under
   "later" because it needs labelled pairs we do not have yet.

### 4. Which model does the work: the user's choice

Each user picks the model that runs their keyword extraction (and, later, their commentary and
tailoring). Two options for now:

- **Local Ollama server** (free, shared, the default). Runs in the one-at-a-time GPU lane and
  counts against the user's daily budget (default 20 calls, admin-adjustable, 0 = unlimited).
- **Claude with the user's own API key.** No lane, no budget; the call goes straight to
  Anthropic on the user's account.

Where it lives: the existing profile section Settings → "AI account" (`profiles.llm`), reworked:

- Provider: "Local model (free)" or "Claude (my own key)". The OpenAI option is removed from the
  page for now (the code path stays, hidden, for later).
- API key: pasted once, encrypted at rest with the server's `SECRET_KEY` (Fernet, already built),
  never shown again; the page only shows "a key is stored" and offers Replace / Remove. A
  "Test key" button makes one cheap call and reports success or the provider's error.
- Model: once a working key is stored, the page lists the **Claude models available to that
  key** by calling Anthropic's models endpoint (`GET /v1/models`) with it, cached per user for
  an hour, with a static fallback list if the call fails. The user picks one; it is saved as
  `profiles.llm.model`. With the local provider the model is the server's `LLM_MODEL` and is
  shown read-only.
- Every stored result records which provider and model produced it (`job_keywords.model`), and
  the Keywords card shows it.

Resolution at call time is the existing `get_chat_model(user_id=...)`: the user's provider, model
and key when set, else the shared Ollama model.

New endpoints: `GET /api/llm/models` (the list for the signed-in user's key),
`POST /api/llm/test` (one test call). The admin's "AI skills" prompt is the same whichever
model runs it.

### 5. The standard skill (system prompt)

- One prompt for every user, stored in the database (`skills_prompts` table: name, text,
  updated_by, updated_at, version history) and seeded from a file in `config/prompts/keywords.md`.
- Visible and editable **by admins only**, under Settings → Shared → "AI skills". Members never
  see it. Each save keeps the previous version; an admin can restore one.
- The prompt instructs the model to return JSON: `{"keywords": [{"term", "category"
  (skill | tool | domain | certification | soft | title), "weight" (1–3), "evidence" (short
  quote from the posting or "similar roles")}], "summary": one line}`. Parsed with the same
  structured-output path used by the judgement today.

### 6. Storing and showing the result

- Table `job_keywords` (user_id, job_id, keywords JSONB, summary, model, prompt_version,
  created_at). One row per user per job; re-running replaces it.
- A **Keywords** card on the job detail, between the skills boxes and the notes: chips grouped
  by category, weight shown by chip strength, each chip's evidence on hover; terms that also
  appear in the user's "have" skills are tinted green, missing ones red (reusing the skills
  logic), so the card doubles as a gap view. A "Copy as list" button for pasting into a resume
  tailor.
- The tracker list shows a small "K" badge on jobs that have keywords.

### 7. Tools and API (all per user, registered like every other tool)

- `job_keywords_context(job_id)` → the similar-role terms (no LLM; usable on its own).
- `extract_job_keywords(job_id, user_id, force)` → runs the LLM call and stores the result.
- `GET /api/jobs/{id}/keywords`, `POST /api/jobs/{id}/keywords` (queues the run),
  `GET/PUT /api/prompts/keywords` (admin).

## Order of work

1. Remove `scoring_agent` from the pipeline graph; update tests and docs.        (small)
2. AI account rework: Ollama / Claude choice, key test, model list from Anthropic.  (small–medium)
3. Prompt store + admin editor ("AI skills").                                   (small)
4. Similar-role term extraction (embeddings + TF-IDF) with tests.               (medium)
5. Job graph: registry kind="job", orchestrator with fast path, state, checkpointer,
   `job_messages` + `job_keywords` tables, `job_chat` tool, keywords agent, queue hook, API. (large)
6. Keywords card, "Extract keywords" button (a message), list badge; transcript list ready
   for the chat box.                                                            (medium)

About two sessions in total.

## As built (Oct 6, 2026)

- `scoring_agent` removed; the batch ends at the fast score.
- `jobagent/llm.py`: providers `local` | `anthropic` (OpenAI kept hidden), `available_models`
  (Anthropic models list, 1 h cache, fallback list), `test_key`. Settings → "AI model" page.
- `jobagent/prompts.py` + tables `prompts`, `prompt_versions`, seeded from `config/prompts/*.md`;
  Settings → Shared → "AI skills" (admins): edit, version history, restore.
- `jobagent/keywords/context.py`: `similar_jobs` (pgvector nearest, same title family preferred),
  `similar_terms` (catalogue hits + distinctive multi-word TF-IDF phrases vs the pool), `context_block`.
- Job graph `jobagent/agents/job/graph.py`: orchestrator (intent fast path, else structured
  `Route` from the user's model), one node per `kind="job"` agent, `respond` stores the turn;
  Postgres checkpointer thread `job:<user>:<job>`; `job_messages` transcript. Registry:
  `AgentSpec(kind="job", prompt_name, intents)`; `jobagent/agents/job/keywords.py` is the first.
- Tools: `job_keywords_context`, `save_job_keywords` (validated `KeywordItem`s), `job_chat`;
  LangChain adapter binds `user_id` / `job_id` so the model never supplies them.
- API: `GET /api/llm/models`, `POST /api/llm/test`, `GET/PUT /api/prompts/{name}` (admin),
  `POST /api/jobs/{id}/chat` (queued: llm lane + daily budget on the shared model, default lane
  on the user's own key), `GET /api/jobs/{id}/messages`, `GET /api/jobs/{id}/keywords`.
- Page: "Keywords the AI found" card with the Extract button (sends the message
  "extract keywords"), chips by category tinted by the user's skills, copy as list, "K" badge
  in the list. Tests: `tests/test_job_graph.py`.

## Later (not in this phase)

- Replace the button with a chat box on the job page (UI only; the graph already takes free text).
- More job agents, one file each: commentary ("why is this a fit"), tailoring (resume rewrite
  from the keywords and the judgement), Q&A about the posting.
- Retire `judge_jobs` and the match-score columns once unused.
- Fine-tune the embedding model on marked pairs, once there are enough.

## Decisions to confirm

1. Remove the batch judgement entirely (as above), or keep it available as an opt-in per user?
   Proposed: remove from the batch, keep the tool for admins.
2. Keywords are per user per job (the user's own account and budget are used), not shared.
   Proposed: per user. Sharing would save LLM calls but leaks nothing; it can be added later.
3. Similar-jobs set: same title family only, or whole pool? Proposed: same family when known,
   else whole pool; N = 25.
4. Model choice is per user in their profile (one choice for all their AI work), not per job.
   Proposed: per user; a per-job override can come later if wanted.
