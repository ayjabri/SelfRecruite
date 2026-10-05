from jobagent.agents.registry import AgentSpec, register_agent

register_agent(AgentSpec(
    name="fast_score_agent",
    kind="stage",
    order=30,
    description="Gives every in-scope job a fast fit score against the golden-source resume, with no LLM.",
    default_task="Score every in-scope job that has no fast score yet.",
    prompt=(
        "You are the fast-score agent. Call score_jobs_fast once with no arguments and reply with the "
        "counts it returned."
    ),
    tools=("score_jobs_fast",),
    direct=True,
))
