"""Importing this package registers every agent in registry.AGENTS."""
from jobagent.agents import dedup, embedding, filtering, greenhouse, oracle, parsing, scoring, workday  # noqa: F401
from jobagent.agents.registry import AGENTS, AgentSpec, register_agent  # noqa: F401
