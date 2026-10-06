import operator
from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from pydantic import BaseModel, Field


class Dispatch(BaseModel):
    agent: str = Field(description="Name of the source agent to run")
    task: str = Field(description="Plain-language instruction for that agent, naming every target to pull")


class Plan(BaseModel):
    reasoning: str = Field(description="One or two sentences on why these agents were chosen")
    dispatches: list[Dispatch]


class Review(BaseModel):
    proceed: bool = Field(description="True if the downstream stages should run now")
    summary: str = Field(description="Two or three sentences on what the source agents achieved and any problems")


class AgentReport(TypedDict):
    agent: str
    task: str
    status: str            # ok | error
    summary: str           # the agent's own closing message
    tool_calls: list[dict]  # [{"tool": name, "args": {...}, "result": {...}}] - the facts
    error: str | None


class PipelineState(TypedDict, total=False):
    request: str                                         # what the user / scheduler asked for
    plan: dict                                           # Plan.model_dump()
    review: dict                                         # Review.model_dump()
    reports: Annotated[list[AgentReport], operator.add]  # one per agent run, appended in parallel
    final_report: str


class AgentTask(TypedDict):
    """Payload sent to a source agent by the orchestrator."""
    task: str
    request: str


class UserTask(TypedDict):
    """Payload sent to the per-user stages, one per active user."""
    user_id: int
    request: str


class Route(BaseModel):
    """The job orchestrator's decision for one user message."""
    agent: str | None = Field(description="Name of the job agent to run, or null to answer directly")
    task: str = Field(description="The instruction for that agent, or the direct answer when agent is null")
    reasoning: str = Field(default="", description="One sentence on why")


class JobChatState(TypedDict, total=False):
    """One user's conversation about one job (thread per user per job)."""
    user_id: int
    job_id: int
    messages: Annotated[list[AnyMessage], add_messages]
    route: dict                                          # Route.model_dump()
    turn: dict                                           # the assistant turn produced this run
