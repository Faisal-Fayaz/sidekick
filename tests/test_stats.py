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
    # Tokens are conversation only: 100 + 50 from the two messages (#313).
    #
    # This assertion used to be `100 + 50 + 3 + 4 + 3`, with the comment
    # "targets (3 + 4 + 3)" — it counted `claude-sonnet-5` (the model *name*),
    # `list_dir|path=/tmp` and `shell|cmd=zzz` as model input. None of those were
    # ever sent to a model, so the test was locking in the accounting bug rather
    # than the intent. Tool targets are now reported as audit_target_chars.
    assert stats["tokens"] == 100 + 50
    assert stats["audit_target_chars"] == len("list_dir|path=/tmp") + len("shell|cmd=zzz")
    assert stats["per_model"]["claude-sonnet-5"]["turns"] == 1
    assert stats["per_model"]["claude-sonnet-5"]["cost_usd"] == round(150 / 1_000_000 * 2.0, 4)
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


def test_cost_table_covers_all_tiers():
    """Refs #278: every routable model prices (0.0 counts as priced)."""
    from sk.config import OPENCODE_FREE_MODELS, PRESETS, TIERS

    ids: set[str] = set()
    for tier in TIERS.values():
        ids.update(tier.values())
    for preset in PRESETS.values():
        if (preset.get("model") or "").strip():
            ids.add(preset["model"])
    ids.update(OPENCODE_FREE_MODELS)
    unpriced = sorted(i for i in ids if store.cost_per_mtok(i) is None)
    assert unpriced == [], f"TIERS/preset models without a rate: {unpriced}"
    assert store.cost_per_mtok("") is None
    assert store.cost_per_mtok("mystery-model") is None


def test_free_and_paid_rates(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    assert store.cost_per_mtok("qwen2.5-coder:7b") == 0.0
    assert store.cost_per_mtok("muse-spark-1.3-contributor-free") == 0.0
    assert store.cost_per_mtok("gpt-4o-mini") == 0.15
    assert store.cost_per_mtok("openai/gpt-oss-120b") == 0.15
    assert store.cost_per_mtok("models/gemini-3.6-flash") == 0.75
    # longest-key substring fallback for versioned tags
    assert store.cost_per_mtok("qwen2.5-coder:7b-instruct-q4_K_M") == 0.0
    assert store.cost_per_mtok("my-gpt-4o-mini-clone") == 0.15


def test_free_model_cost_zero_not_na(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    store.save_message("s", "user", "hi")
    store.log_tool_run("s", "llm_call", "qwen2.5-coder:7b", True, "ollama", "localhost", True)
    stats = store.usage_stats("s")
    assert stats["per_model"]["qwen2.5-coder:7b"]["cost_usd"] == 0.0
    assert stats["unpriced_tokens"] == 0


def test_stats_cli_shows_priced_cost(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from sk.cli import app

    _iso(tmp_path, monkeypatch)
    store.save_message("s", "user", "x" * 400)
    store.log_tool_run("s", "llm_call", "gpt-4o-mini", True, "openai", "api.openai.com", True)
    res = CliRunner().invoke(app, ["stats", "--session", "s"])
    assert res.exit_code == 0, res.output
    assert "gpt-4o-mini" in res.output and "≈$" in res.output
