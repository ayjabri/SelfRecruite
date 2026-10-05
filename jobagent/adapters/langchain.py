"""Expose registered tools to LangChain/LangGraph agents."""
from langchain_core.tools import StructuredTool

from jobagent.tools import TOOLS, ToolSpec


def as_langchain_tool(spec: ToolSpec) -> StructuredTool:
    def run(**kwargs) -> str:
        return spec.call(**kwargs).model_dump_json()
    return StructuredTool.from_function(
        func=run, name=spec.name, description=spec.description, args_schema=spec.input_model,
    )


def langchain_tools(names: list[str]) -> list[StructuredTool]:
    return [as_langchain_tool(TOOLS[n]) for n in names]
