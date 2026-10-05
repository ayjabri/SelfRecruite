import logging

from langgraph.checkpoint.memory import InMemorySaver

from jobagent.agents import graph as graph_mod


def test_execute_streams_progress_and_returns_final_state(monkeypatch, caplog):
    """Runs the real graph wiring with the LLM-backed nodes replaced by stubs."""
    monkeypatch.setattr(graph_mod, "plan_node", lambda s: {"plan": {"reasoning": "r", "dispatches": [
        {"agent": "greenhouse_agent", "task": "pull stripe"}, {"agent": "oracle_agent", "task": "pull jpmc"}]}})
    monkeypatch.setattr(graph_mod, "review_node", lambda s: {"review": {"proceed": True, "summary": "fine"}})
    monkeypatch.setattr(graph_mod, "make_agent_node", lambda spec: lambda s: {"reports": [{
        "agent": spec.name, "task": s.get("task", ""), "status": "ok", "summary": "", "tool_calls": [], "error": None}]})
    g = graph_mod.build_graph().compile(checkpointer=InMemorySaver())
    with caplog.at_level(logging.INFO, logger="jobagent"):
        state = graph_mod.execute(g, "sample", {"configurable": {"thread_id": "t"}})
    ran = [r["agent"] for r in state["reports"]]
    assert sorted(ran) == ["dedup_agent", "embedding_agent", "fast_score_agent", "filter_agent", "greenhouse_agent", "oracle_agent", "parse_agent"]  # workday not dispatched
    assert "greenhouse_agent" in state["final_report"]
    assert "orchestrator -> oracle_agent: pull jpmc" in caplog.text
    assert "orchestrator review: fine (proceeding)" in caplog.text
