"""Importing this package registers every agent in registry.AGENTS."""
from jobagent.agents import dedup, embedding, filtering, greenhouse, oracle, parsing, preference, scoring, workday  # noqa: F401
from jobagent.agents import job  # noqa: F401 - the job agents (kind="job")
from jobagent.agents.registry import AGENTS, AgentSpec, register_agent  # noqa: F401
