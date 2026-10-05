"""Turns an AgentSpec into a graph node: an LLM tool-calling loop over the agent's own tools."""
import json
import logging
from datetime import date, datetime, timezone

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, ToolMessage

from jobagent.adapters.langchain import langchain_tools
from jobagent.agents.registry import AgentSpec
from jobagent.agents.state import AgentReport
from jobagent.db import session
from jobagent.llm import get_chat_model
from jobagent.models import Run
from jobagent.tools import TOOLS

log = logging.getLogger(__name__)

MAX_STEPS = 25  # hard stop so an unattended run always ends


def _tool_calls(messages) -> list[dict]:
    """Pair each tool call with its result. These are the facts; the LLM's prose is only commentary."""
    args_by_id = {
        c["id"]: c for m in messages if isinstance(m, AIMessage) for c in (m.tool_calls or [])
    }
    out = []
    for m in messages:
        if isinstance(m, ToolMessage):
            call = args_by_id.get(m.tool_call_id, {})
            try:
                result = json.loads(m.content)
            except (TypeError, ValueError):
                result = {"error": str(m.content)[:500]}
            out.append({"tool": m.name, "args": call.get("args", {}), "result": result})
    return out


def _log_run(spec: AgentSpec, started: datetime, report: AgentReport) -> None:
    with session() as s, s.begin():
        s.add(Run(
            run_date=date.today(), stage=spec.name, started_at=started,
            finished_at=datetime.now(timezone.utc), status=report["status"],
            counts={"task": report["task"], "tool_calls": report["tool_calls"]},
            error=report["error"],
        ))


def make_agent_node(spec: AgentSpec):
    def node(state: dict) -> dict:
        # sources receive an AgentTask via Send; stages receive the pipeline state
        task = state.get("task") or spec.default_task
        if spec.kind == "stage" and state.get("request"):
            task = f"{task}\n\nRun request, for context: {state['request']}"
        started = datetime.now(timezone.utc)
        log.info("%s started: %s", spec.name, task.splitlines()[0][:150])
        report = AgentReport(agent=spec.name, task=task, status="ok", summary="", tool_calls=[], error=None)
        try:
            if spec.direct:
                tool = TOOLS[spec.tools[0]]
                result = tool.call()
                report["tool_calls"] = [{"tool": tool.name, "args": {}, "result": result.model_dump(mode="json")}]
                report["summary"] = "Ran without an LLM."
                if getattr(result, "error", None):
                    report["status"], report["error"] = "error", result.error
                _log_run(spec, started, report)
                log.info("%s finished: %s", spec.name, report["status"])
                return {"reports": [report]}
            agent = create_agent(
                model=get_chat_model(), tools=langchain_tools(list(spec.tools)), system_prompt=spec.prompt,
            )
            result = agent.invoke({"messages": [("user", task)]}, {"recursion_limit": MAX_STEPS})
            messages = result["messages"]
            report["tool_calls"] = _tool_calls(messages)
            report["summary"] = str(messages[-1].text if hasattr(messages[-1], "text") else messages[-1].content).strip()
            failures = [c for c in report["tool_calls"] if c["result"].get("error")]
            if failures and len(failures) == len(report["tool_calls"]):
                report["status"], report["error"] = "error", "every tool call failed"
            elif not report["tool_calls"]:
                report["status"], report["error"] = "error", "agent called no tools"
        except Exception as e:  # noqa: BLE001 - one agent failing must not take the run down
            report["status"], report["error"] = "error", f"{type(e).__name__}: {e}"[:500]
        _log_run(spec, started, report)
        log.info("%s finished: %s, %d tool calls%s", spec.name, report["status"], len(report["tool_calls"]),
                 f" - {report['error']}" if report["error"] else "")
        return {"reports": [report]}

    node.__name__ = spec.name
    return node
