"""The orchestrator: decides which source agents run and whether the stages follow."""
import json

import yaml
from langgraph.types import Send
from sqlalchemy import select

from jobagent.agents.registry import AGENTS, sources, stages
from jobagent.agents.state import Plan, PipelineState, Review
from jobagent.config import get_settings
from jobagent.db import session
from jobagent.llm import get_chat_model
from jobagent.models import Run

PLAN_PROMPT = (
    "You are the orchestrator of a job-collection pipeline. You do not fetch anything yourself; "
    "you delegate to source agents, each specialised in one data source.\n"
    "Decide which source agents to dispatch for this run and write each one a clear task.\n"
    "Rules:\n"
    "- Use only the agents listed. Dispatch each agent at most once, with all of its targets in one task.\n"
    "- Use only targets from the configured list, unless the request names others explicitly.\n"
    "- If the request narrows the run (specific companies, a per-target limit), reflect that in the task text.\n"
    "- If an agent has no targets, do not dispatch it."
)

REVIEW_PROMPT = (
    "You are the orchestrator of a job-collection pipeline. The source agents have reported back. "
    "Decide whether the downstream stages should run now.\n"
    "Proceed if at least one source stored postings or postings may still be waiting from earlier runs. "
    "Do not proceed only if every source failed. Summarise using only the numbers in the reports."
)


def _targets() -> dict[str, list[str]]:
    path = get_settings().companies_file
    data = yaml.safe_load(path.read_text()) if path.exists() else {}
    return {a.name: list((data or {}).get(a.config_key) or []) for a in sources()}


def _recent_runs(n: int = 6) -> list[dict]:
    with session() as s:
        rows = s.scalars(select(Run).order_by(Run.id.desc()).limit(n)).all()
        return [{"date": str(r.run_date), "agent": r.stage, "status": r.status, "error": r.error} for r in rows]


def plan_node(state: PipelineState) -> dict:
    targets = _targets()
    context = {
        "request": state["request"],
        "source_agents": [
            {"name": a.name, "does": a.description, "configured_targets": targets[a.name]} for a in sources()
        ],
        "recent_runs": _recent_runs(),
    }
    model = get_chat_model().with_structured_output(Plan)
    plan: Plan = model.invoke([("system", PLAN_PROMPT), ("user", json.dumps(context, indent=1))])
    known = {a.name for a in sources()}
    seen, kept = set(), []
    for d in plan.dispatches:  # the model proposes; code enforces the rules
        if d.agent in known and d.agent not in seen:
            seen.add(d.agent)
            kept.append(d)
    plan.dispatches = kept
    return {"plan": plan.model_dump()}


def dispatch(state: PipelineState):
    """Fan out: one parallel branch per dispatched source agent."""
    sends = [
        Send(d["agent"], {"task": d["task"], "request": state["request"]})
        for d in state["plan"]["dispatches"]
    ]
    return sends or "review"


def review_node(state: PipelineState) -> dict:
    reports = [
        {k: r[k] for k in ("agent", "status", "error", "tool_calls")} for r in state.get("reports", [])
    ]
    model = get_chat_model().with_structured_output(Review)
    review: Review = model.invoke([
        ("system", REVIEW_PROMPT),
        ("user", json.dumps({"request": state["request"], "source_reports": reports}, indent=1)),
    ])
    return {"review": review.model_dump()}


def after_review(state: PipelineState) -> str:
    first = stages()[0].name if stages() else "report"
    return first if state["review"]["proceed"] else "report"


def report_node(state: PipelineState) -> dict:
    """Plain code, no LLM: the final report is assembled from tool results."""
    lines = [f"Request: {state['request']}", f"Plan: {state['plan']['reasoning']}"]
    for r in state.get("reports", []):
        lines.append(f"\n[{r['agent']}] {r['status']}" + (f" - {r['error']}" if r["error"] else ""))
        for c in r["tool_calls"]:
            res = {k: v for k, v in c["result"].items() if k not in ("raw_ids", "failed_raw_ids")}
            lines.append(f"  {c['tool']}({json.dumps(c['args'])}) -> {json.dumps(res)}")
    if state.get("review"):
        lines.append(f"\nOrchestrator review: {state['review']['summary']}")
        if not state["review"]["proceed"]:
            lines.append("Downstream stages were skipped.")
    return {"final_report": "\n".join(lines)}


__all__ = ["AGENTS", "after_review", "dispatch", "plan_node", "report_node", "review_node"]
