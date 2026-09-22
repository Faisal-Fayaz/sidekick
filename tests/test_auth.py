"""Auth UX tests: mocked HTTP, isolated config. No network, no real keys."""

import sk.auth as auth
from sk.config import Config


def _resp(payload, status=200):
    class R:
        def raise_for_status(self):
            if status >= 400:
                raise Exception(f"HTTP {status}")

        def json(self):
            return payload

    return R()


def test_fetch_models_ollama(monkeypatch):
    import httpx

    monkeypatch.setattr(
        httpx, "get", lambda *a, **k: _resp({"models": [{"name": "m1"}, {"name": "m2"}]})
    )
    assert auth.fetch_models("ollama", "http://localhost:11434/v1", "") == ["m1", "m2"]


def test_fetch_models_cloud(monkeypatch):
    import httpx

    seen = {}
    monkeypatch.setattr(
        httpx,
        "get",
        lambda url, headers=None, **k: (
            seen.update({"url": url, "h": headers}) or _resp({"data": [{"id": "a"}, {"id": "b"}]})
        ),
    )
    assert auth.fetch_models("groq", "https://api.groq.com/openai/v1", "k") == ["a", "b"]
    assert seen["h"] == {"Authorization": "Bearer k"}


def test_validate_key_rejects_401(monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _resp({}, 401))
    ok, msg = auth.validate_key("groq", "https://x/v1", "bad")
    assert ok is False and "401" in msg


def test_validate_key_empty():
    assert auth.validate_key("openai", "https://x/v1", "")[0] is False


def test_provider_status_no_key():
    cfg = Config(
        provider="openai", model="m", base_url="", api_key="", max_steps=5, temperature=0.2
    )
    ok, msg = auth.provider_status(cfg)
    assert ok is False and "sk auth add" in msg


def _runner():
    from typer.testing import CliRunner

    from sk.cli import app

    return CliRunner(), app


def test_auth_add_saves_validated(monkeypatch):
    monkeypatch.setattr("sk.auth.validate_key", lambda *a, **k: (True, "valid (3 models)"))
    runner, app = _runner()
    res = runner.invoke(app, ["auth", "add", "groq", "--key", "k-test"])
    assert res.exit_code == 0, res.output
    from sk.config import Config as _C

    assert _C.load().provider == "groq"


def test_auth_add_refuses_bad_key(monkeypatch):
    monkeypatch.setattr("sk.auth.validate_key", lambda *a, **k: (False, "key rejected (401/403)"))
    runner, app = _runner()
    res = runner.invoke(app, ["auth", "add", "groq", "--key", "bad"])
    assert res.exit_code != 0
    from sk.config import Config as _C

    assert _C.load().provider == "ollama"  # unchanged default


def test_auth_list_masks(monkeypatch):
    import sk.store as _s

    runner, app = _runner()
    res = runner.invoke(app, ["auth", "list"])
    assert res.exit_code == 0 and "ollama" in res.output


def test_model_picker_saves(monkeypatch):
    monkeypatch.setattr("sk.auth.fetch_models", lambda *a, **k: ["m-a", "m-b"])
    runner, app = _runner()
    res = runner.invoke(app, ["model"], input="1\n")
    assert res.exit_code == 0, res.output
    from sk.config import Config as _C

    assert _C.load().model == "m-a"


def test_setup_local_flow(monkeypatch):
    import sk.cli as _cli

    monkeypatch.setattr("sk.auth.fetch_models", lambda *a, **k: ["m-a"])
    monkeypatch.setattr("sk.auth.ping", lambda *a, **k: (True, "hi"))
    runner, app = _runner()
    res = runner.invoke(app, ["setup"], input="1\nn\n")
    assert res.exit_code == 0, res.output
    assert "setup complete" in res.output


def test_version_command():
    from typer.testing import CliRunner

    from sk.cli import _code_version, app

    assert len(_code_version()) >= 4
    res = CliRunner().invoke(app, ["version"])
    assert res.exit_code == 0 and "sk " in res.output


def test_chat_models_filters_junk():
    names = [
        "models/gemini-3.6-flash",
        "models/gemini-2.5-flash-preview-tts",
        "models/gemini-3-pro-image-preview",
        "models/gemini-3.5-transcribe",
        "models/gemini-3.8-live",
        "qwen3:4b",
        "models/gemini-3.6-flash",
    ]
    assert auth.chat_models(names) == ["models/gemini-3.6-flash", "qwen3:4b"]


def test_ping_uses_tiny_completion(monkeypatch):
    seen = {}

    class FakeCompletions:
        def create(self, **kw):
            seen.update(kw)
            return type(
                "R",
                (),
                {
                    "choices": [
                        type("C", (), {"message": type("M", (), {"content": " hi there"})()})()
                    ]
                },
            )()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        def __init__(self, **kw):
            seen["init"] = kw

        chat = FakeChat()

    import openai

    monkeypatch.setattr(openai, "OpenAI", FakeClient)
    ok, msg = auth.ping("groq", "https://api.groq.com/openai/v1", "k", "m")
    assert ok is True and "hi there" in msg
    assert seen["max_tokens"] == 5 and "tools" not in seen
    assert seen["init"]["base_url"] == "https://api.groq.com/openai/v1"


def test_ping_failure():
    ok, msg = auth.ping("groq", "http://127.0.0.1:9/v1", "k", "m", timeout=2)
    assert ok is False and msg


def test_connect_flow(monkeypatch):
    from typer.testing import CliRunner

    from sk.cli import app

    monkeypatch.setattr("sk.auth.validate_key", lambda *a, **k: (True, "valid (2 models)"))
    monkeypatch.setattr(
        "sk.auth.fetch_models", lambda *a, **k: ["m-junk-tts", "m-good", "m-pic-image"]
    )
    monkeypatch.setattr("sk.auth.ping", lambda *a, **k: (True, "hello"))
    monkeypatch.setattr("sk.cli._pick_provider", lambda default="": "groq")
    runner = CliRunner()
    # key, then model pick 1 (only m-good survives the chat filter)
    res = runner.invoke(app, ["connect"], input="gsk-test\n1\n")
    assert res.exit_code == 0, res.output
    assert "connected" in res.output and "m-good" in res.output
    from sk.config import Config as _C

    saved = _C.load()
    assert saved.provider == "groq" and saved.model == "m-good"


def test_connect_local_skips_key(monkeypatch):
    from typer.testing import CliRunner

    from sk.cli import app

    monkeypatch.setattr("sk.auth.fetch_models", lambda *a, **k: ["qwen3:4b"])
    monkeypatch.setattr("sk.auth.ping", lambda *a, **k: (True, "hi"))
    runner = CliRunner()
    res = runner.invoke(app, ["connect"], input="1\n1\n")
    assert res.exit_code == 0, res.output
    assert "no key needed" in res.output
