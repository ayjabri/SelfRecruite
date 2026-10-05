from jobagent.agents.registry import AgentSpec, register_agent

register_agent(AgentSpec(
    name="extraction_agent",
    kind="stage",
    order=10,
    description="Turns stored raw postings into structured job records.",
    default_task="Extract all raw postings that are still pending.",
    prompt=(
        "You are the extraction agent. You turn stored raw postings into structured job records "
        "using your tools.\n"
        "1. Call list_pending_extractions to see how much work there is.\n"
        "2. If anything is pending, call extract_pending_postings. Omit the limit unless the task gives one.\n"
        "3. If some failed, call reset_failed_extractions and then extract_pending_postings ONE more "
        "time. Never retry more than once.\n"
        "4. Reply with a short summary of the counts: extracted, cached, linked, failed, remaining. "
        "Use only numbers returned by the tools."
    ),
    tools=("list_pending_extractions", "extract_pending_postings", "reset_failed_extractions", "extract_posting"),
))
