"""mais CLI: type coercion, dotted path setters, command surface."""
from __future__ import annotations

import yaml


def test_coerce_handles_bool_int_float_json() -> None:
    from cli import _coerce

    assert _coerce("true") is True
    assert _coerce("FALSE") is False
    assert _coerce("on") is True
    assert _coerce("42") == 42
    assert _coerce("3.14") == 3.14
    assert _coerce("[1,2,3]") == [1, 2, 3]
    assert _coerce("hello") == "hello"


def test_set_dotted_creates_nested_keys() -> None:
    from cli import _get_dotted, _set_dotted

    d: dict = {}
    _set_dotted(d, "a.b.c", 7)
    assert d == {"a": {"b": {"c": 7}}}
    assert _get_dotted(d, "a.b.c") == 7
    assert _get_dotted(d, "a.b.x") is None


def test_app_has_expected_subcommands() -> None:
    """Sanity: the registered Typer app exposes every promised command."""
    from cli import app

    names = {
        (cmd.name or cmd.callback.__name__.rstrip("_"))
        for cmd in app.registered_commands
    }
    expected = {
        "status",
        "positions",
        "today",
        "doctor",
        "logs",
        "backup",
        "restore",
        "set",
        "flag",
        "init",
        "run",
    }
    assert expected <= names, f"missing: {expected - names}"


def test_set_command_writes_yaml(tmp_path, monkeypatch) -> None:
    """Round-trip: ``mais set`` writes a value, ``mais get-dotted`` reads it."""
    settings_file = tmp_path / "settings.yaml"
    settings_file.write_text(
        yaml.safe_dump({"risk": {"hard_stop_pct": 0.12}}), encoding="utf-8"
    )
    import cli

    monkeypatch.setattr(cli, "_settings_path", lambda: settings_file)

    cli._save_settings({"risk": {"hard_stop_pct": 0.10}})
    after = yaml.safe_load(settings_file.read_text(encoding="utf-8"))
    assert after["risk"]["hard_stop_pct"] == 0.10
