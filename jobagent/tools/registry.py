"""Tool registry. A tool is a plain function `fn(args: InputModel) -> OutputModel`.

Adapters (CLI, LangChain, MCP, HTTP) are generated from TOOLS, so a function
registered here is callable by an agent or on its own with no extra code.
"""
from collections.abc import Callable
from dataclasses import dataclass
from typing import get_type_hints

from pydantic import BaseModel


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    fn: Callable[[BaseModel], BaseModel]

    def call(self, **kwargs) -> BaseModel:
        return self.fn(self.input_model(**kwargs))


TOOLS: dict[str, ToolSpec] = {}


def tool_spec(name: str, description: str):
    def register(fn):
        hints = get_type_hints(fn)
        params = [k for k in hints if k != "return"]
        if len(params) != 1 or "return" not in hints:
            raise TypeError(f"{fn.__name__} must be fn(args: InputModel) -> OutputModel")
        if name in TOOLS:
            raise ValueError(f"duplicate tool name: {name}")
        TOOLS[name] = ToolSpec(name, description, hints[params[0]], hints["return"], fn)
        return fn
    return register
