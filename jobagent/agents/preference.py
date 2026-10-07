from jobagent.agents.registry import AgentSpec, register_agent

register_agent(AgentSpec(
    name="preference_agent",
    kind="stage",
    scope="user",
    order=25,
    description="Retrains each user's preference model when their interested / not-interested marks changed since the last training, and re-scores their jobs with it. No LLM; skipped when nothing changed or there are too few marks.",
    default_task="Retrain the preference model if the user's marks changed.",
    prompt=(
        "You are the preference agent. Call refresh_preference_model once with no arguments and reply with "
        "what it returned."
    ),
    tools=("refresh_preference_model",),
    direct=True,
))
