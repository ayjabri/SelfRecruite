from jobagent.agents.registry import AgentSpec, register_agent

register_agent(AgentSpec(
    name="dedup_agent",
    kind="stage",
    order=20,
    description="Merges duplicate job records (the same opening posted several times) and flags doubtful pairs.",
    default_task="Merge duplicate jobs and flag possible duplicates for review.",
    prompt="You are the de-duplication agent. Call dedup_jobs once and reply with the counts it returned.",
    tools=("dedup_jobs",),
    direct=True,
))
