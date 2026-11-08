"""Model boundary parsing and both provider paths (offline fakes)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from sk.config import Config
from sk.review import model


def _cfg(provider="ollama"):
    return Config(provider=provider, model="fake", base_url="http://x/v1", api_key="x")


def test_parse_json_accepts_fences_and_prose():
    assert model.parse_json('```json\n{"findings": []}\n```') == {"findings": []}
    assert model.parse_json('Sure.\n{"findings": []}\nDone.') == {"findings": []}
    assert model.parse_json("not json") is None


def test_parse_findings_variants():
    assert model.parse_findings('{"findings":[{"file":"a"}]}') == [{"file": "a"}]
    assert model.parse_findings('{"results":[{"file":"a"}]}') == [{"file": "a"}]
    assert model.parse_findings('[{"file":"a"}]') == [{"file": "a"}]
    assert model.parse_findings('{"file":"a","line":1,"problem":"p"}') == [
        {"file": "a", "line": 1, "problem": "p"}
    ]
    assert model.parse_findings("nope") == []


def test_parse_assessment_variants():
    assert model.parse_assessment('{"assessment":{"verdict":"proceed"}}') == {"verdict": "proceed"}
    assert model.parse_assessment('{"verdict":"proceed"}') == {"verdict": "proceed"}
    assert model.parse_assessment("[]") is None


def test_complete_openai_success(monkeypatch):
    class Completions:
        def create(self, **kwargs):
            assert kwargs["model"] == "fake"
            assert kwargs["temperature"] == 0.0
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}'))]
            )

    class Client:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=Completions())

    import openai

    monkeypatch.setattr(openai, "OpenAI", Client)
    out = model.complete(_cfg(), system="s", user="u")
    assert out == '{"ok":true}'


def test_complete_openai_raises_model_error(monkeypatch):
    class Completions:
        def create(self, **kwargs):
            raise RuntimeError("down")

    class Client:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=Completions())

    import openai

    monkeypatch.setattr(openai, "OpenAI", Client)
    with pytest.raises(model.ModelError):
        model.complete(_cfg(), system="s", user="u")


def test_complete_anthropic_success(monkeypatch):
    import sk.anthropic_backend as backend

    monkeypatch.setattr(
        backend,
        "_post",
        lambda *a, **k: {"content": [{"type": "text", "text": "hello"}]},
    )
    out = model.complete(_cfg("anthropic"), system="s", user="u")
    assert out == "hello"


def test_complete_anthropic_empty_is_error(monkeypatch):
    import sk.anthropic_backend as backend

    monkeypatch.setattr(backend, "_post", lambda *a, **k: {"content": []})
    with pytest.raises(model.ModelError):
        model.complete(_cfg("anthropic"), system="s", user="u")
