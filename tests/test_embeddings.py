from jobagent.tools.embeddings import ALWAYS_INCLUDE, chunk_markdown, job_embedding_text

MD = """# Jane Doe
Intro line.

# EXPERIENCE
## Bank A
### Role Scope
Ran treasury analytics.
### Empty Heading
## Bank B
Built models.

# OPEN ITEMS / FACTS TO VERIFY
Check dates.
"""


def test_chunks_carry_their_heading_path_and_skip_empty_sections():
    chunks = chunk_markdown(MD)
    assert [h for h, _ in chunks] == [
        "Jane Doe", "EXPERIENCE > Bank A > Role Scope", "EXPERIENCE > Bank B", "OPEN ITEMS / FACTS TO VERIFY",
    ]
    assert dict(chunks)["EXPERIENCE > Bank A > Role Scope"] == "Ran treasury analytics."
    assert ALWAYS_INCLUDE.search(chunks[-1][0]) and not ALWAYS_INCLUDE.search(chunks[1][0])


def test_job_embedding_text_prefers_requirement_bullets():
    parsed = {"sections_quality": "labelled", "sections": [
        {"heading": "Requirements", "label": "required", "bullets": ["Python", "IRRBB"]},
        {"heading": "Benefits", "label": "other", "bullets": ["Dental"]}]}
    assert job_embedding_text("Analyst", parsed, "long description") == "Analyst\n- Python\n- IRRBB"
    assert job_embedding_text("Analyst", None, "long description") == "Analyst\nlong description"
