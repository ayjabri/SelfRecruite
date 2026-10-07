# Plan: a chat box on the job page, and two more job agents (Q&A, fit commentary)

Date: Oct 6, 2026. Status: **awaiting approval.**

Follows `docs/KEYWORDS_PLAN.md`. The job graph (orchestrator → one specialist → respond), the
`POST /api/jobs/{id}/chat` endpoint, the `job_messages` transcript and the per-user budget already
exist; today the only way in is the "Extract keywords" button, and the only specialist is
`keywords_agent`. This plan adds the free-text box and two specialists that make it worth typing
into. Tailoring stays later (it writes resumes, so it deserves its own plan).

## 1. The chat box (UI, plus one small endpoint)

- A new **"Ask the AI"** card on the job page, under the keywords card: the transcript from
  `GET /api/jobs/{id}/messages` (user turns right, assistant turns left, the agent's name and
  model in small print, errors in red, assistant text rendered with the existing
  `renderMarkdown`), then a textarea and **Send** (Enter sends, Shift+Enter is a new line).
- **Quick actions** above the box, one chip per job agent, from a new
  `GET /api/job-agents` → `[{name, label, description, example}]` built from the registry, so a
  new agent shows up with no page change. Clicking a chip sends its example phrasing.
- While a turn is queued or running, the box is disabled and shows "waiting in the llm lane" /
  "thinking…"; the page polls every 3 s as the keywords card does now. The keywords card keeps its
  own button and refreshes when a keywords turn finishes.
- Budget errors (429) and other failures show under the box, not as a broken page.
- All in `web/static/tracker.js` (or a new `jobchat.js` if tracker.js passes ~400 lines), no inline
  script; smoke test extended to send a message against a stubbed model.

## 2. Specialists see the conversation

Today a specialist receives only `route.task`. Follow-ups ("and what about the second one?") need
context, so each specialist gets the last 6 human/assistant messages of the thread before the
task. The checkpointed thread already holds them; no schema change.

## 3. Q&A agent (`qa_agent`), answers questions about this job against your resume

- **Intents:** "what does this role", "do i qualify", "how does my experience", "question",
  "explain", "who would I report to". Anything the router judges to be a question about the job
  goes here.
- **Tools (new, no LLM):**
  - `job_posting(job_id)` → title, company, location, pay, the flattened full posting text and
    the parsed requirement bullets. (Split out of `job_keywords_context` so keywords reuses it.)
  - `resume_evidence(user_id, query, k=4)` → the user's resume sections closest to the query by
    embedding (the same `golden_chunks` retrieval `judge/core.py:retrieve` uses, generalised to a
    free-text query), each with its heading.
- **Skill prompt:** `config/prompts/qa.md`, editable and versioned in the prompt store. Rules:
  answer only from the posting and the resume sections returned; quote them; say "the posting
  doesn't say" rather than guess; short answers.
- **Storage:** none beyond the transcript.

## 4. Fit commentary agent (`fit_agent`), a written assessment, stored as a card

- **Intents:** "assess my fit", "fit", "how do i match", "strengths and gaps", "should i apply",
  "commentary".
- **Tools:** `job_posting`, `resume_evidence`, `job_keywords_get` (the stored keywords with
  have/missing, when extracted), `job_fit_facts` (the fast score, its rule components, scope
  gates and preference-model estimate; no LLM), and `save_job_fit`.
- **Output, validated by `save_job_fit`:**
  `{verdict: strong|good|stretch|weak, headline, strengths:[{point, evidence}], gaps:[{point,
  mitigation}], talking_points:[str], apply: yes|maybe|no}`. Evidence must quote the resume.
- **Storage:** new table `job_fit` (user_id, job_id, verdict, body JSONB, model, prompt_version,
  created_at), latest only, like `job_keywords`. One Alembic migration.
- **UI:** a "Fit assessment" card above the keywords card (verdict badge, headline, strengths
  and gaps lists, talking points), with an "Assess fit" / "Assess again" button that is a chat
  message, same pattern as keywords. A small badge in the job list for jobs that have one.
- This is the LLM judgement the old `judge_job` did, done on demand and readable. Once it is in
  use, `judge_jobs` and the `match_*` columns can be retired (open item in the handover).

## 5. Tests

Router fast path for the new intents; graph wiring picks up three specialists; `job_posting`,
`resume_evidence` and `job_fit_facts` against fixture rows; `save_job_fit` validation (bad
verdict, missing evidence); history passed to specialists; `/api/job-agents` shape; the UI smoke
test sends a chat message and sees the transcript update (model stubbed).

## Order of work

1. Chat box + `/api/job-agents` + history to specialists (useful immediately: free text routes to
   keywords or gets the orchestrator's direct answer).
2. `job_posting` / `resume_evidence` tools + `qa_agent`.
3. `job_fit` table, tools, `fit_agent`, fit card.

Each step ends with tests green, the smoke test passing and a restart of `jobagent-ui.service`
(Python changes). Commits only when you ask.

## Not in this plan

Streaming replies; resume tailoring; cover letters; agents calling other agents.
