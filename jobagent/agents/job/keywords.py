from jobagent.agents.registry import AgentSpec, register_agent

register_agent(AgentSpec(
    name="keywords_agent",
    kind="job",
    description="Extracts the keywords an applicant-tracking system and a recruiter would scan for in this job, and stores them.",
    prompt="Extract the keywords a resume for this job should contain.",   # fallback; the real skill is prompt_name
    prompt_name="keywords",
    intents=("extract keywords", "keywords", "key words", "what will the ats scan for", "ats keywords",
             "what should my resume contain", "scan keywords"),
    tools=("job_keywords_context", "save_job_keywords"),
))
