"""Builds the pipeline graph from the agent registry and runs it with a Postgres checkpointer."""
import uuid

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph

import jobagent.agents  # noqa: F401 - registers the agents
from jobagent.agents.base import make_agent_node
from jobagent.agents.orchestrator import after_review, dispatch, plan_node, report_node, review_node
from jobagent.agents.registry import sources, stages
from jobagent.agents.state import PipelineState
from jobagent.config import get_settings


def build_graph() -> StateGraph:
    g = StateGraph(PipelineState)
    g.add_node("plan", plan_node)
    g.add_node("review", review_node)
    g.add_node("report", report_node)

    source_names = [a.name for a in sources()]
    for spec in sources():
        g.add_node(spec.name, make_agent_node(spec))
        g.add_edge(spec.name, "review")           # all parallel branches join at review

    stage_names = [a.name for a in stages()]
    for spec in stages():
        g.add_node(spec.name, make_agent_node(spec))
    for a, b in zip(stage_names, stage_names[1:] + ["report"]):
        g.add_edge(a, b)                          # stages run one after another

    g.add_edge(START, "plan")
    g.add_conditional_edges("plan", dispatch, source_names + ["review"])
    g.add_conditional_edges("review", after_review, stage_names[:1] + ["report"])
    g.add_edge("report", END)
    return g


def run_pipeline(request: str, thread_id: str | None = None, resume: bool = False) -> tuple[str, dict]:
    """Run (or resume) one pipeline run. Returns (thread_id, final state)."""
    thread_id = thread_id or uuid.uuid4().hex[:12]
    config = {"configurable": {"thread_id": thread_id}}
    with PostgresSaver.from_conn_string(get_settings().psycopg_url) as saver:
        saver.setup()
        graph = build_graph().compile(checkpointer=saver)
        state = graph.invoke(None if resume else {"request": request}, config)
    return thread_id, state
