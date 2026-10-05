from jobagent.tools.dedup import _level_words


def test_level_words_tell_openings_apart():
    assert _level_words("senior software engineer, dgx cloud") == {"senior"}
    assert _level_words("software engineer, dgx cloud") == frozenset()
    assert _level_words("infrastructure engineer iii - genai") == {"iii"}
    assert _level_words("ai engineer - sr lead software engineer") == _level_words("sr lead software engineer - ai engineer")
