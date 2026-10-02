"""Usage accounting must measure model usage (#313).

`usage_stats` added `_est_tokens(r["target"])` for **every** `tool_runs` row, so a
session was charged for model input it never sent:

    write_file   target = `tool|path=…|content=<the file's bytes>`
    egress:*     target = `host=… url=… decision=… reason=…`
    llm_call     target = the model *name*

Only `user` and `assistant` rows are persisted to `messages` (tool results never
reach that table), so message contents are the conversation genuinely exchanged
with the model — that is the right basis for a billable estimate.

This matters beyond cosmetics: `_spend_blocked` reads `cost_usd` from here on
*every* LLM call, so a session could trip a spend cap for work that cost
nothing.
"""

from __future__ import annotations

import sk.store as store
from sk.tokens import estimate_tokens


def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")


def _run(session, tool, target):
    store.log_tool_run(session, tool, target, True, "p", "h", True)


# --- the core fix -----------------------------------------------------------


def test_write_file_content_is_not_billed_as_model_input(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    store.save_message("s", "user", "hello")
    _run("s", "llm_call", "gpt-4o")
    _run("s", "write_file", "write_file|path=/home/me/a.py|content=" + "x" * 4000)

    stats = store.usage_stats("s")

    assert stats["tokens"] == estimate_tokens("hello")
    # Reported, not priced. Bounded per row: log_tool_run truncates targets to
    # 500 chars, so one row could contribute at most ~125 phantom tokens — but
    # 5000 rows are retained, so the total was not small.
    assert stats["audit_target_chars"] == 500
    assert stats["tool_runs"] == 1


def test_egress_denial_rows_are_not_billed(tmp_path, monkeypatch):
    """The egress ledger from #346 writes a row per allow/deny decision. Those
    were billed as model input, so every refused fetch inflated the user's own
    spend cap."""
    _iso(tmp_path, monkeypatch)
    store.save_message("s", "user", "hi")
    _run("s", "llm_call", "gpt-4o")
    for i in range(20):
        _run(
            "s",
            "egress:searching",
            f"host=html.duckduckgo.com url=https://html.duckduckgo.com/html/?q={i} "
            f"decision=deny reason=egress allowlist is empty",
        )

    stats = store.usage_stats("s")

    assert stats["tokens"] == estimate_tokens("hi"), "denied fetches were billed as tokens"
    assert stats["tool_runs"] == 20
    assert stats["audit_target_chars"] > 1000


def test_model_name_is_not_counted_as_model_input(tmp_path, monkeypatch):
    """`llm_call`'s target is the model *name*, not a payload. Summing it was
    double-counting nothing at best."""
    _iso(tmp_path, monkeypatch)
    store.save_message("s", "user", "x")
    _run("s", "llm_call", "claude-opus-5")
    stats = store.usage_stats("s")
    assert stats["tokens"] == estimate_tokens("x")


def test_cost_no_longer_scales_with_file_size(tmp_path, monkeypatch):
    """The reported symptom: a turn with no model output bills, and the figure
    scales with how much text the agent wrote."""
    _iso(tmp_path, monkeypatch)
    store.save_message("s", "user", "go")
    _run("s", "llm_call", "gpt-4o")
    _run("s", "write_file", "content=" + "x" * 100_000)

    stats = store.usage_stats("s")
    assert stats["cost_usd"] == round(estimate_tokens("go") / 1_000_000 * 2.5, 4)
    assert stats["cost_usd"] < 0.001, "cost scaled with file size"


def test_messages_are_the_basis(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    store.save_message("s", "user", "a" * 400)
    store.save_message("s", "assistant", "b" * 200)
    _run("s", "llm_call", "gpt-4o")
    stats = store.usage_stats("s")
    assert stats["tokens"] == 100 + 50


# --- $0.00 is a value, not missing data --------------------------------------


def test_local_model_reports_zero_cost_not_none(tmp_path, monkeypatch):
    """A $0.00/Mtok model produced 0.0, and `if cost_known` rendered that as
    "n/a" — the same as having no rate at all."""
    _iso(tmp_path, monkeypatch)
    store.save_message("s", "user", "hello there")
    _run("s", "llm_call", "qwen2.5-coder:7b")

    stats = store.usage_stats("s")
    assert stats["cost_usd"] == 0.0
    assert stats["cost_usd"] is not None
    assert stats["unpriced_tokens"] == 0


def test_unknown_model_still_reports_none(tmp_path, monkeypatch):
    """None must keep meaning one thing only: no rate is known."""
    _iso(tmp_path, monkeypatch)
    store.save_message("s", "user", "hello there")
    _run("s", "llm_call", "some-unreleased-model")

    stats = store.usage_stats("s")
    assert stats["cost_usd"] is None
    assert stats["unpriced_tokens"] > 0


def test_zero_cost_renders_in_the_cli(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from sk.cli import app

    _iso(tmp_path, monkeypatch)
    store.save_message("s", "user", "hello there")
    _run("s", "llm_call", "qwen2.5-coder:7b")

    out = CliRunner().invoke(app, ["stats", "--session", "s"]).output
    assert "$0.0" in out, "a real $0.00 measurement still rendered as n/a"
    assert "n/a" not in out


# --- one estimator ----------------------------------------------------------


def test_one_estimator_ceil_rounds_up():
    assert estimate_tokens("") == 0
    assert estimate_tokens("a") == 1
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2


def test_floor_is_explicit_not_a_second_estimator():
    from sk.agent import estimate_tokens as agent_estimate

    # budgeting keeps its floor-of-1; accounting does not. Same arithmetic.
    assert agent_estimate("") == 1
    assert estimate_tokens("") == 0
    assert agent_estimate("abcde") == estimate_tokens("abcde") == 2


def test_store_alias_delegates():
    assert store._est_tokens("abcde") == estimate_tokens("abcde") == 2


def test_compaction_arithmetic_unchanged():
    """#308 owns context-limit behaviour; this PR must not move it."""
    from sk.agent import estimate_tokens as agent_estimate

    for s in ("", "a", "abcd", "abcde", "x" * 4000):
        assert agent_estimate(s) == max(1, (len(s) + 3) // 4)


# --- audit volume is not lost -----------------------------------------------


def test_audit_volume_covers_every_non_llm_row(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    store.save_message("s", "user", "x")
    _run("s", "llm_call", "gpt-4o")
    _run("s", "list_dir", "list_dir|path=/tmp")
    _run("s", "shell", "shell|cmd=zzz")

    stats = store.usage_stats("s")
    assert stats["audit_target_chars"] == len("list_dir|path=/tmp") + len("shell|cmd=zzz")


def test_audit_volume_zero_when_only_llm_calls(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    store.save_message("s", "user", "x")
    _run("s", "llm_call", "gpt-4o")
    assert store.usage_stats("s")["audit_target_chars"] == 0


def test_empty_stats_still_clean(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    stats = store.usage_stats("nosuchsession")
    assert stats["tokens"] == 0
    assert stats["cost_usd"] is None
    assert stats["audit_target_chars"] == 0
    assert stats["unpriced_tokens"] == 0


def test_phantom_billing_bound_was_per_row_capped(tmp_path, monkeypatch):
    """Documents the scale of what was being billed.

    log_tool_run truncates each target to 500 chars, so a single row cost at most
    ~125 phantom tokens. `tool_runs` retains the newest 5000 rows, so a session
    doing real work could accrue hundreds of thousands of tokens that were never
    sent to a model — and `_spend_blocked` reads that total on every LLM call.
    """
    _iso(tmp_path, monkeypatch)
    store.save_message("s", "user", "go")
    _run("s", "llm_call", "gpt-4o")
    for _ in range(25):
        _run("s", "write_file", "content=" + "x" * 4000)

    stats = store.usage_stats("s")
    assert stats["audit_target_chars"] == 25 * 500
    assert stats["tokens"] == estimate_tokens("go")
    assert stats["unpriced_tokens"] == 0
