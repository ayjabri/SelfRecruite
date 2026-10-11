"""Importing this package registers every tool in registry.TOOLS."""
from jobagent.tools import cleanup, dedup, embeddings, enrichment, filtering, greenhouse, groups, judging, keywords, oracle, parsing, preference, scoring, skills, workday  # noqa: F401
from jobagent.tools.registry import TOOLS, ToolSpec, tool_spec  # noqa: F401
