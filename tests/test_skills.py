from jobagent.skills import extract_skills, my_skills, skill_gap


def test_extract_whole_word_and_symbols():
    text = "Strong Python and SQL; experience with LangGraph, C++ and .NET. JavaScript is a plus."
    found = extract_skills(text)
    assert {"Python", "SQL", "LangGraph", "C++", "C#", "JavaScript"} <= set(found)
    assert "Java" not in found          # "JavaScript" must not count as Java
    assert "R" not in found             # a bare letter inside words never matches


def test_gap_splits_by_resume():
    gap = skill_gap("Must know Python, Tableau and Scala.")
    assert "Python" in gap["matching"] and "Tableau" in gap["matching"]
    assert gap["missing"] == ["Scala"]
    assert "Python" in my_skills()
