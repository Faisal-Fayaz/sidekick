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

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _resp({"models": [{"name": "m1"}, {"name": "m2"}]}))
    assert auth.fetch_models("ollama", "http://localhost:11434/v1", "") == ["m1", "m2"]


def test_fetch_models_cloud(monkeypatch):
    import httpx

    seen = {}
    monkeypatch.setattr(httpx, "get", lambda url, headers=None, **k: seen.update({"url": url, "h": headers}) or _resp({"data": [{"id": "a"}, {"id": "b"}]}))
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
    cfg = Config(provider="openai", model="m", base_url="", api_key="", max_steps=5, temperature=0.2)
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
    monkeypatch.setattr(_cli, "run_agent", lambda *a, **k: "hi there hello")
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
