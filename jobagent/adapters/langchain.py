"""Expose registered tools to LangChain/LangGraph agents.

`bound` fixes some input fields (user_id, job_id) from the caller's context: they disappear
from the schema the model sees and are filled in on every call.
"""
from pydantic import BaseModel, create_model
from langchain_core.tools import StructuredTool

from jobagent.tools import TOOLS, ToolSpec


def _schema_without(model: type[BaseModel], hidden: set[str]) -> type[BaseModel]:
    fields = {name: (f.annotation, f) for name, f in model.model_fields.items() if name not in hidden}
    return create_model(model.__name__ + "Args", **fields) if hidden & set(model.model_fields) else model


def as_langchain_tool(spec: ToolSpec, bound: dict | None = None) -> StructuredTool:
    bound = {k: v for k, v in (bound or {}).items() if k in spec.input_model.model_fields}

    def run(**kwargs) -> str:
        return spec.call(**{**kwargs, **bound}).model_dump_json()
    return StructuredTool.from_function(
        func=run, name=spec.name, description=spec.description,
        args_schema=_schema_without(spec.input_model, set(bound)),
    )


def langchain_tools(names: list[str], bound: dict | None = None) -> list[StructuredTool]:
    return [as_langchain_tool(TOOLS[n], bound) for n in names]
