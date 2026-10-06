"""The job graph: routing, wiring from the registry, bound tools and result validation (no model)."""
import pytest
from pydantic import ValidationError

from jobagent.adapters.langchain import langchain_tools
from jobagent.agents import registry
from jobagent.agents.job.graph import build_job_graph, fast_route
from jobagent.agents.registry import AgentSpec, job_agents
from jobagent.keywords.context import context_block
from jobagent.tools.keywords import KeywordItem, SaveKeywordsInput


def test_fast_route_matches_intents_without_a_model():
    agents = job_agents()
    assert fast_route("Extract Keywords, please", agents).name == "keywords_agent"
    assert fast_route("what will the ATS scan for?", agents).name == "keywords_agent"
    assert fast_route("hello there", agents) is None
    assert fast_route("", agents) is None


def test_graph_is_built_from_the_registry(monkeypatch):
    extra = {**registry.AGENTS,
             "commentary_agent": AgentSpec("commentary_agent", "job", "Explains the fit", "p", ("job_keywords_context",),
                                           intents=("why is this a fit",))}
    monkeypatch.setattr(registry, "AGENTS", extra)
    g = build_job_graph().compile().get_graph()
    edges = {(e.source, e.target) for e in g.edges}
    assert {"orchestrator", "respond", "keywords_agent", "commentary_agent"} <= set(g.nodes)
    assert ("orchestrator", "commentary_agent") in edges and ("commentary_agent", "respond") in edges
    assert fast_route("why is this a fit", job_agents()).name == "commentary_agent"


def test_bound_tools_hide_the_identifiers_from_the_model():
    tools = {t.name: t for t in langchain_tools(["job_keywords_context", "save_job_keywords"], bound={"user_id": 1, "job_id": 7})}
    assert "job_id" not in tools["job_keywords_context"].args_schema.model_fields
    assert "user_id" not in tools["save_job_keywords"].args_schema.model_fields
    assert "keywords" in tools["save_job_keywords"].args_schema.model_fields


def test_keyword_result_validation():
    ok = SaveKeywordsInput(job_id=1, keywords=[{"term": "  Python ", "category": "tool", "weight": 3, "evidence": "Python required"}])
    assert ok.keywords[0].term == "Python"
    with pytest.raises(ValidationError):
        KeywordItem(term="x", category="hobby", weight=2)
    with pytest.raises(ValidationError):
        KeywordItem(term="x", category="skill", weight=5)
    with pytest.raises(ValidationError):
        SaveKeywordsInput(job_id=1, keywords=[])


def test_context_block_formats_terms():
    assert context_block({"terms": []}) == "SIMILAR ROLES: none found."
    block = context_block({"terms": [{"term": "Python", "kind": "skill", "count": 8, "of": 25}]})
    assert block.startswith("SIMILAR ROLES (25 postings") and "- Python (8 of 25)" in block
