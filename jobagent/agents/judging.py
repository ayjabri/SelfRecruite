from jobagent.agents.registry import AgentSpec, register_agent

register_agent(AgentSpec(
    name="scoring_agent",
    kind="stage",
    order=40,
    description="Has the LLM judge the best-ranked jobs against the resume, requirement by requirement.",
    default_task="Judge the jobs that earned it.",
    prompt="You are the scoring agent. Call judge_jobs once with no arguments and reply with the counts it returned.",
    tools=("judge_jobs",),
    direct=True,
))
