"""Agent registry. The graph and the orchestrator's prompt are generated from AGENTS,
so adding an agent is one AgentSpec; no graph code changes."""
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class AgentSpec:
    name: str
    kind: Literal["source", "stage"]  # sources fan out in parallel; stages run in `order`
    description: str                  # what the orchestrator reads when deciding who to dispatch
    prompt: str                       # the agent's own system prompt
    tools: tuple[str, ...]            # names in jobagent.tools.TOOLS
    order: int = 0                    # stages only
    # stages only: "crawl" runs once on the shared job pool; "user" runs once per active user
    # (its tools get user_id) after every crawl stage has finished
    scope: Literal["crawl", "user"] = "crawl"
    default_task: str = ""            # stages only: what to do when the orchestrator proceeds
    config_key: str | None = None     # sources only: section of companies.yaml listing its targets
    # True for a stage with nothing to decide: each of its tools is called in order with
    # default arguments, with no LLM in the loop. Set False to put an LLM agent back in charge.
    direct: bool = False


AGENTS: dict[str, AgentSpec] = {}


def register_agent(spec: AgentSpec) -> AgentSpec:
    if spec.name in AGENTS:
        raise ValueError(f"duplicate agent: {spec.name}")
    AGENTS[spec.name] = spec
    return spec


def sources() -> list[AgentSpec]:
    return [a for a in AGENTS.values() if a.kind == "source"]


def stages(scope: str | None = None) -> list[AgentSpec]:
    return sorted((a for a in AGENTS.values() if a.kind == "stage" and (scope is None or a.scope == scope)),
                  key=lambda a: a.order)
