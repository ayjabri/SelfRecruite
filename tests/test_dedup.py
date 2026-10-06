from jobagent.tools.dedup import _level_words


def test_level_words_tell_openings_apart():
    assert _level_words("senior software engineer, dgx cloud") == {"senior"}
    assert _level_words("software engineer, dgx cloud") == frozenset()
    assert _level_words("infrastructure engineer iii - genai") == {"iii"}
    assert _level_words("ai engineer - sr lead software engineer") == _level_words("sr lead software engineer - ai engineer")


def test_differing_requisition_ids_mean_different_openings():
    from types import SimpleNamespace as N
    from jobagent.tools.dedup import _Node, _different_reqs
    mk = lambda req: _Node(N(id=1, title_norm="x", description_text="t", embedding=None, locations=[], parsed=None,  # noqa: E731
                              requisition_id=req, in_scope=True))
    assert _different_reqs(mk("R1"), mk("R2"))
    assert not _different_reqs(mk("R1"), mk("R1"))
    assert not _different_reqs(mk(None), mk("R2"))   # unknown on one side: the text still decides
