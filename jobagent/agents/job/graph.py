"""The job graph: one user's conversation about one job.

    user message -> orchestrator -> one specialist (kind="job" in the registry) -> respond
                                 -> respond (direct answer when no specialist fits)

Built from the registry like the pipeline graph: a new specialist is one AgentSpec file.
Threads are checkpointed in Postgres (thread_id = job:<user>:<job>) and every turn is also
written to job_messages for the page.
"""
import json
import logging
import re
from datetime import datetime, timezone

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

import jobagent.agents.job  # noqa: F401 - registers the job agents
from jobagent.adapters.langchain import langchain_tools
from jobagent.agents.registry import AgentSpec, job_agents
from jobagent.agents.state import JobChatState, Route
from jobagent.config import get_settings
from jobagent.db import session
from jobagent.llm import get_chat_model, model_spec
from jobagent.models import JobMessage
from jobagent.prompts import get_prompt

log = logging.getLogger(__name__)
MAX_STEPS = 12   # tool-calling rounds per specialist per turn

ROUTER_PROMPT = (
    "You are the orchestrator of an assistant that helps a job seeker with ONE job posting. "
    "Decide which specialist should handle the user's latest message, or answer it yourself when no "
    "specialist fits (for example a greeting, or a question about what you can do).\n"
    "Specialists:\n{agents}\n"
    "Rules: pick exactly one specialist, or none. Put the user's request, made specific to this job, in "
    "`task`. When answering directly, `task` is your reply and must mention what the specialists can do."
)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower()).strip()


def fast_route(message: str, agents: list[AgentSpec]) -> AgentSpec | None:
    """Match the message against each agent's example phrasings; no model call."""
    m = _norm(message)
    if not m:
        return None
    for spec in agents:
        for intent in spec.intents:
            i = _norm(intent)
            if i and (i in m or m in i):
                return spec
    return None


def _agent_lines(agents: list[AgentSpec]) -> str:
    return "\n".join(f"- {a.name}: {a.description} (say e.g. {', '.join(repr(i) for i in a.intents[:3])})" for a in agents)


def orchestrator_node(state: JobChatState) -> dict:
    agents = job_agents()
    last = state["messages"][-1].content if state.get("messages") else ""
    if (spec := fast_route(last, agents)) is not None:
        return {"route": Route(agent=spec.name, task=last, reasoning="matched the agent's intents").model_dump()}
    if not agents:
        return {"route": Route(agent=None, task="No specialists are configured yet.", reasoning="none registered").model_dump()}
    model = get_chat_model(user_id=state["user_id"]).with_structured_output(Route)
    history = [m for m in state["messages"][-8:] if isinstance(m, (HumanMessage, AIMessage))]
    route: Route = model.invoke([("system", ROUTER_PROMPT.format(agents=_agent_lines(agents))), *history])
    if route.agent not in {a.name for a in agents}:
        route = Route(agent=None, task=route.task or "I can: " + "; ".join(a.description for a in agents), reasoning=route.reasoning)
    return {"route": route.model_dump()}


def dispatch(state: JobChatState):
    route = state.get("route") or {}
    if route.get("agent"):
        return [Send(route["agent"], state)]
    return "respond"


def _tool_calls(messages) -> list[dict]:
    args_by_id = {c["id"]: c for m in messages if isinstance(m, AIMessage) for c in (m.tool_calls or [])}
    out = []
    for m in messages:
        if isinstance(m, ToolMessage):
            call = args_by_id.get(m.tool_call_id, {})
            try:
                result = json.loads(m.content)
            except (TypeError, ValueError):
                result = {"error": str(m.content)[:500]}
            # keep the facts small: long context payloads are summarised by size
            if isinstance(result, dict) and len(json.dumps(result)) > 4000:
                result = {k: (v if len(json.dumps(v)) < 500 else f"<{len(json.dumps(v))} chars>") for k, v in result.items()}
            out.append({"tool": m.name, "args": call.get("args", {}), "result": result})
    return out


def make_specialist_node(spec: AgentSpec):
    def node(state: JobChatState) -> dict:
        uid, jid = state["user_id"], state["job_id"]
        task = (state.get("route") or {}).get("task") or state["messages"][-1].content
        prompt, version = get_prompt(spec.prompt_name) if spec.prompt_name else (spec.prompt, None)
        system = f"{prompt}\n\nYou are working on job #{jid}. Call your tools with no job or user identifiers; they are set for you."
        turn = {"agent": spec.name, "content": "", "tool_calls": [], "model": model_spec(uid), "error": None, "prompt_version": version}
        try:
            agent = create_agent(model=get_chat_model(user_id=uid),
                                 tools=langchain_tools(list(spec.tools), bound={"user_id": uid, "job_id": jid}),
                                 system_prompt=system)
            result = agent.invoke({"messages": [("user", task)]}, {"recursion_limit": MAX_STEPS * 2 + 1})
            messages = result["messages"]
            turn["tool_calls"] = _tool_calls(messages)
            final = messages[-1]
            turn["content"] = str(final.text if hasattr(final, "text") else final.content).strip() or "(no reply)"
            saved = [c for c in turn["tool_calls"] if c["tool"].startswith("save_") and not (isinstance(c["result"], dict) and c["result"].get("error"))]
            if saved and turn["content"].lstrip().startswith("{"):  # the result is stored; the transcript gets a sentence, not the JSON
                summary = saved[-1]["args"].get("summary") or ""
                turn["content"] = f"Stored the result ({', '.join(f'{k} {v}' for k, v in saved[-1]['result'].items() if k not in ('job_id', 'error'))}). {summary}".strip()
            failures = [c for c in turn["tool_calls"] if isinstance(c["result"], dict) and c["result"].get("error")]
            if failures:
                turn["error"] = "; ".join(str(c["result"]["error"]) for c in failures)[:500]
        except Exception as e:  # noqa: BLE001 - the turn records the failure; the page shows it
            turn["error"] = f"{type(e).__name__}: {e}"[:500]
            turn["content"] = "Sorry, that did not work: " + turn["error"]
        return {"turn": turn, "messages": [AIMessage(content=turn["content"], name=spec.name)]}
    node.__name__ = spec.name
    return node


def respond_node(state: JobChatState) -> dict:
    """Stores the assistant turn (from a specialist, or the orchestrator's direct answer)."""
    turn = state.get("turn")
    if not turn or turn.get("stored"):
        text = (state.get("route") or {}).get("task") or "I can help with this job; try 'extract keywords'."
        turn = {"agent": None, "content": text, "tool_calls": [], "model": model_spec(state["user_id"]), "error": None}
        extra = [AIMessage(content=text)]
    else:
        extra = []
    with session() as s, s.begin():
        s.add(JobMessage(user_id=state["user_id"], job_id=state["job_id"], role="assistant", content=turn["content"],
                         agent=turn.get("agent"), tool_calls=turn.get("tool_calls"), model=turn.get("model"), error=turn.get("error")))
    return {"turn": {**turn, "stored": True}, "messages": extra}


def build_job_graph() -> StateGraph:
    g = StateGraph(JobChatState)
    g.add_node("orchestrator", orchestrator_node)
    g.add_node("respond", respond_node)
    names = []
    for spec in job_agents():
        g.add_node(spec.name, make_specialist_node(spec))
        g.add_edge(spec.name, "respond")
        names.append(spec.name)
    g.add_edge(START, "orchestrator")
    g.add_conditional_edges("orchestrator", dispatch, names + ["respond"])
    g.add_edge("respond", END)
    return g


def thread_id(user_id: int, job_id: int) -> str:
    return f"job:{user_id}:{job_id}"


def chat(user_id: int, job_id: int, message: str) -> dict:
    """Run one turn and return the stored assistant turn."""
    with session() as s, s.begin():
        s.add(JobMessage(user_id=user_id, job_id=job_id, role="user", content=message))
    config = {"configurable": {"thread_id": thread_id(user_id, job_id)}, "recursion_limit": 50}
    started = datetime.now(timezone.utc)
    with PostgresSaver.from_conn_string(get_settings().psycopg_url) as saver:
        saver.setup()
        graph = build_job_graph().compile(checkpointer=saver)
        state = graph.invoke({"user_id": user_id, "job_id": job_id, "messages": [HumanMessage(content=message)], "turn": None}, config)
    turn = state.get("turn") or {}
    log.info("job chat user %d job %d -> %s in %.0fs%s", user_id, job_id, turn.get("agent") or "orchestrator",
             (datetime.now(timezone.utc) - started).total_seconds(), f" ({turn['error']})" if turn.get("error") else "")
    return turn
