from jobagent.agents import registry
from jobagent.agents.graph import build_graph
from jobagent.agents.registry import AgentSpec
from jobagent.tools import TOOLS


def test_agents_only_reference_registered_tools():
    for a in registry.AGENTS.values():
        assert set(a.tools) <= set(TOOLS), a.name


def test_graph_is_generated_from_the_registry(monkeypatch):
    """Adding a source or a stage is one AgentSpec; the graph picks it up with no other change."""
    extra = {
        **registry.AGENTS,
        "lever_agent": AgentSpec("lever_agent", "source", "d", "p", ("fetch_greenhouse_jobs",)),
        "dedup_agent": AgentSpec("dedup_agent", "stage", "d", "p", ("parse_posting",), order=20),
    }
    monkeypatch.setattr(registry, "AGENTS", extra)
    g = build_graph().compile().get_graph()
    edges = {(e.source, e.target) for e in g.edges}
    assert {"greenhouse_agent", "lever_agent", "parse_agent", "dedup_agent"} <= set(g.nodes)
    assert ("plan", "lever_agent") in edges and ("lever_agent", "review") in edges
    assert ("filter_agent", "parse_agent") in edges  # the pre-filter runs before any LLM extraction
    assert ("parse_agent", "dedup_agent") in edges and ("dedup_agent", "report") in edges
