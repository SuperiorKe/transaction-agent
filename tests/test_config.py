"""The operator's `.env`: `.env.example` must stay in step with Settings."""

from pathlib import Path

from app.config import Settings

ENV_EXAMPLE = Path(__file__).resolve().parent.parent / ".env.example"


def example_keys() -> list[str]:
    lines = ENV_EXAMPLE.read_text().splitlines()
    return [
        line.split("=", 1)[0].strip()
        for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_env_example_lists_every_setting_exactly_once():
    assert sorted(example_keys()) == sorted(name.upper() for name in Settings.model_fields)


def test_env_example_values_match_code_defaults(monkeypatch):
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)

    assert Settings(_env_file=ENV_EXAMPLE) == Settings(_env_file=None)
