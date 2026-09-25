"""Usage stats tests: exact numbers on seeded data, clean empties. Fully offline."""

import sk.store as store


def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")


def _seed():
    store.save_message("s", "user", "x" * 400)  # 100 tokens
    store.save_message("s", "assistant", "y" * 200)  # 50 tokens
    store.log_tool_run(
        "s", "llm_call", "claude-sonnet-5", True, "anthropic", "api.anthropic.com", True
    )
    store.log_tool_run(
        "s", "list_dir", "list_dir|path=/tmp", True, "anthropic", "api.anthropic.com", True
    )
    store.log_tool_run(
        "s", "shell", "shell|cmd=zzz", False, "anthropic", "api.anthropic.com", False
    )


def test_exact_numbers(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    _seed()
    stats = store.usage_stats("s")
    assert stats["turns"] == 1
    assert stats["tools"] == {"list_dir": 1, "shell": 1}
    assert stats["denied"] == 1 and stats["failed"] == 0
    assert stats["local_runs"] == 0 and stats["egress_runs"] == 3
    assert stats["sessions"] == 1
    # tokens: 100 + 50 message + targets (3 + 4 + 3)
    assert stats["tokens"] == 100 + 50 + 3 + 4 + 3
    assert stats["per_model"]["claude-sonnet-5"]["turns"] == 1
    assert stats["per_model"]["claude-sonnet-5"]["cost_usd"] == round(160 / 1_000_000 * 2.0, 4)
    assert stats["cost_usd"] == stats["per_model"]["claude-sonnet-5"]["cost_usd"]


def test_empty_reports_cleanly(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    stats = store.usage_stats("nosuch")
    assert stats["turns"] == 0 and stats["tool_runs"] == 0 and stats["tokens"] == 0
    assert stats["cost_usd"] is None
    from typer.testing import CliRunner

    from sk.cli import app

    res = CliRunner().invoke(app, ["stats", "--session", "nosuch"])
    assert res.exit_code == 0 and "no usage" in res.output.lower()


def test_unknown_model_cost_na(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    store.save_message("s", "user", "hi")
    store.log_tool_run("s", "llm_call", "mystery-model", True, "ollama", "localhost", True)
    stats = store.usage_stats("s")
    assert stats["per_model"]["mystery-model"]["cost_usd"] is None
    assert stats["cost_usd"] is None and stats["unpriced_tokens"] > 0
    assert stats["local_runs"] == 1  # ollama provider counts local


def test_stats_cli_md_and_json(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from sk.cli import app

    _iso(tmp_path, monkeypatch)
    _seed()
    res = CliRunner().invoke(app, ["stats", "--session", "s"])
    assert res.exit_code == 0, res.output
    assert "1 turns" in res.output and "local" in res.output and "egress" in res.output
    assert "claude-sonnet-5" in res.output
    res = CliRunner().invoke(app, ["stats", "--format", "json"])
    assert res.exit_code == 0, res.output
    import json

    doc = json.loads(res.output)
    assert doc["turns"] == 1 and doc["tools"]["list_dir"] == 1
