from jobagent.agents.registry import AgentSpec, register_agent

register_agent(AgentSpec(
    name="fast_score_agent",
    kind="stage",
    scope="user",
    order=30,
    description="Gives every job in each user's scope a fast fit score against their resume, with no LLM. The last per-user stage: the LLM is only used on demand, per job, from the page.",
    default_task="Score every in-scope job that has no fast score yet.",
    prompt=(
        "You are the fast-score agent. Call score_jobs_fast once with no arguments and reply with the "
        "counts it returned."
    ),
    tools=("score_jobs_fast",),
    direct=True,
))
