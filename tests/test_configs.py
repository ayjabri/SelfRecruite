import shutil

import pytest

from jobagent.web import configs


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    paths = {n: configs._path(n) for n in configs.FILES}
    for src in paths.values():
        shutil.copy(src, tmp_path / src.name)
    monkeypatch.setattr(configs, "_path", lambda name: tmp_path / paths[name].name)
    monkeypatch.setattr(configs, "BACKUPS", tmp_path / "backups")
    return tmp_path


def test_form_save_keeps_comments_and_values(scratch):
    before = configs.read("filters_template")
    data = before["data"]
    data["location"]["allowed_states"] = ["NY", "NJ", "CT"]
    data["title"]["include"].append("zzz test")
    data["title"]["include"].remove("product owner")
    after = configs.save_data("filters_template", data)
    assert after["data"] == data
    assert after["text"].count("#") == before["text"].count("#")      # comments survived
    assert "Variant A" in after["text"]
    assert configs.backups("filters_template")                                 # a backup was written


def test_text_save_rejects_bad_yaml(scratch):
    with pytest.raises(Exception):
        configs.save_text("targets_template", "weights: [unclosed")
    assert configs.read("targets_template")["data"]["weights"]["required"] == 0.45


def test_new_rows_and_unknown_file(scratch):
    data = configs.read("companies")["data"]
    data["workday"].append({"company": "Mizuho", "site": "mizuho:wd1:mizuhoamericas"})
    assert "Mizuho" in configs.save_data("companies", data)["text"]
    with pytest.raises(KeyError):
        configs.read("nope")
