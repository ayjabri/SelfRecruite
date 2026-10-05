from jobagent.agents.registry import AgentSpec, register_agent

register_agent(AgentSpec(
    name="embedding_agent",
    kind="stage",
    order=15,
    description="Keeps the golden-source index current and embeds jobs that have no vector yet.",
    default_task="Index the golden source if it changed, then embed every job without an embedding.",
    prompt=(
        "You are the embedding agent. Call index_golden_source, then embed_jobs, each once with no "
        "arguments, and reply with the counts they returned."
    ),
    tools=("index_golden_source", "embed_jobs"),
    direct=True,
))
