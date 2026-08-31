"""Settings loaded back from disk.

The config file is plain JSON in the user's home, so it can be hand-edited,
truncated by a full disk, or left over from an older version.  None of those
should reach the widgets.
"""

import json

import pytest

from tanksmanager.backend import config


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_FILE", str(path))
    return path


def test_defaults_when_there_is_no_file(config_file):
    cfg = config.Config()

    assert cfg["update_speed"] == "normal"
    assert cfg["classic_graphs"] is True


def test_saved_values_come_back(config_file):
    cfg = config.Config()
    cfg["update_speed"] = "high"
    cfg["window"] = [1200, 800]
    cfg.save()

    assert config.Config()["update_speed"] == "high"
    assert config.Config()["window"] == [1200, 800]


def test_unknown_keys_are_ignored(config_file):
    # A key left behind by an older version, such as the tray setting that
    # never did anything.
    config_file.write_text(json.dumps({"hide_when_minimised": True}))

    assert "hide_when_minimised" not in config.Config()


def test_values_of_the_wrong_type_fall_back_to_the_default(config_file):
    config_file.write_text(json.dumps({
        "window": "wide",              # should be a list
        "update_speed": 5,             # should be a string
        "confirm_kill": "yes",         # should be a boolean
        "perf_sidebar": "narrow",      # should be a number
    }))

    cfg = config.Config()

    assert cfg["window"] == config.DEFAULTS["window"]
    assert cfg["update_speed"] == "normal"
    assert cfg["confirm_kill"] is True
    assert cfg["perf_sidebar"] == 250


def test_a_boolean_is_not_accepted_where_a_number_belongs(config_file):
    # bool is a subclass of int in Python; "tab": true must not become tab 1.
    config_file.write_text(json.dumps({"tab": True}))

    assert config.Config()["tab"] == config.DEFAULTS["tab"]


def test_null_columns_means_use_the_defaults(config_file):
    config_file.write_text(json.dumps({"columns": None}))

    assert config.Config()["columns"] is None


def test_corrupt_json_leaves_every_default_standing(config_file):
    config_file.write_text("{not json at all")

    assert dict(config.Config()) == config.DEFAULTS


def test_a_json_file_that_is_not_an_object_is_ignored(config_file):
    config_file.write_text("[1, 2, 3]")

    assert dict(config.Config()) == config.DEFAULTS


def test_saving_is_atomic(config_file):
    # The write goes to a temporary file and is renamed into place, so a
    # crash mid-save cannot leave a half-written config behind.
    cfg = config.Config()
    cfg.save()

    assert config_file.exists()
    assert not (config_file.parent / (config_file.name + ".tmp")).exists()
    assert json.loads(config_file.read_text())["update_speed"] == "normal"
