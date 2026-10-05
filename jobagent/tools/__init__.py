"""Importing this package registers every tool in registry.TOOLS."""
from jobagent.tools import extraction, filtering, greenhouse, oracle, workday  # noqa: F401
from jobagent.tools.registry import TOOLS, ToolSpec, tool_spec  # noqa: F401
