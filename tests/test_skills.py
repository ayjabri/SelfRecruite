from jobagent.skills import extract_skills, skill_gap


def test_extract_whole_word_and_symbols():
    text = "Strong Python and SQL; experience with LangGraph, C++ and .NET. JavaScript is a plus."
    found = extract_skills(text)
    assert {"Python", "SQL", "LangGraph", "C++", "C#", "JavaScript"} <= set(found)
    assert "Java" not in found          # "JavaScript" must not count as Java
    assert "R" not in found             # a bare letter inside words never matches


def test_gap_splits_by_what_the_user_has(monkeypatch):
    import jobagent.skills as mod
    monkeypatch.setattr(mod, "my_skills", lambda user_id=None: frozenset({"Python", "Tableau"}))
    gap = skill_gap("Must know Python, Tableau and Scala.")
    assert gap["matching"] == ["Python", "Tableau"]
    assert gap["missing"] == ["Scala"]
