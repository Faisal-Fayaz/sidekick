"""The context budget must bound the *assembled prompt* (#308).

It used to bound history alone. Everything else was assembled on top and never
measured — system prompt, auto-context blocks, and ~1700 tokens of tool schema —
so `sk` reported a healthy session while the request overran the window. Ollama
then silently truncates the oldest tokens, which is where the system prompt
lives, or a cloud provider returns 400.

Measured on a real build before this change, with no auto-context and two tiny
history messages: system ~1970 tok + tool schemas ~1699 tok = ~3670 tok, leaving
~324 of a 4096 window for the entire conversation.

The fix measures the fixed parts from what was actually assembled, then gives
history what is genuinely left.
"""

from __future__ import annotations

import sk.config as config_mod
import sk.store as store
from sk.agent import (
    _tool_schema_tokens,
    build_messages,
    context_budget,
    context_report,
    fit_history,
    resolved_window,
)
from sk.tokens import estimate_tokens as tok


def _cfg(tmp_path, monkeypatch, **kw):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")
    cfg = config_mod.Config(
        model="qwen2.5-coder:7b",
        base_url="http://x/v1",
        api_key="x",
        max_steps=1,
        temperature=0.0,
    )
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg


# --- the budget arithmetic --------------------------------------------------


def test_tool_schemas_are_measured_not_guessed():
    """They ship on every request and are pure overhead to the conversation."""
    n = _tool_schema_tokens()
    assert n > 500, f"tool schemas look mis-measured: {n}"
    assert n < 5000, f"implausibly large: {n}"


def test_room_is_window_minus_everything_else():
    """history_budget_tokens=0 so the user cap is out of the way."""
    from sk.config import Config

    c = Config(model="gpt-4o", provider="openai", api_key="x", history_budget_tokens=0)
    b = context_budget(c, fixed_tokens=1000, max_output=512)
    assert b["room"] == b["window"] - 1000 - b["reserved_output"] - b["safety_margin"]
    assert b["over_budget"] is False


def test_the_default_history_budget_caps_the_room():
    """With the default 3000, room is the cap even on a 200k window."""
    from sk.config import Config

    c = Config(model="gpt-4o", provider="openai", api_key="x")
    assert context_budget(c, fixed_tokens=0, max_output=512)["room"] == 3000


def test_output_is_reserved_not_spent_on_history():
    """A window with no room for the reply is a window that cannot answer."""
    from sk.config import Config

    c = Config(model="gpt-4o", provider="openai", api_key="x")
    b = context_budget(c, fixed_tokens=0, max_output=8000)
    assert b["reserved_output"] >= 8000
    assert b["room"] < b["window"]


def test_over_budget_is_reported_not_silently_negative():
    from sk.config import Config

    c = Config(model="gpt-4o", provider="openai", api_key="x")
    b = context_budget(c, fixed_tokens=10_000_000, max_output=512)
    assert b["over_budget"] is True
    assert b["room"] == 0


def test_history_budget_caps_but_never_exceeds_the_window():
    """The knob keeps its meaning; the window imposes the hard ceiling."""
    from sk.config import Config

    c = Config(model="gpt-4o", provider="openai", api_key="x", history_budget_tokens=500)
    assert context_budget(c, fixed_tokens=0, max_output=512)["room"] == 500

    c.history_budget_tokens = 10**9
    big = context_budget(c, fixed_tokens=0, max_output=512)
    assert big["room"] < big["window"], "a huge budget overrode the physical window"


# --- fit_history ------------------------------------------------------------


def test_fit_history_drops_oldest_until_it_fits():
    hist = [{"role": "user", "content": "x" * 1000} for _ in range(50)]
    kept, dropped = fit_history(hist, room=6000)
    assert dropped > 0
    assert tok(str(len(kept))) >= 0
    assert len(kept) == 50 - dropped
    assert kept == hist[-len(kept) :], "must drop from the oldest end"


def test_fit_history_keeps_the_newest_floor():
    """A starved window degrades to very short memory, never to none."""
    hist = [{"role": "user", "content": "x" * 10_000} for _ in range(10)]
    kept, _ = fit_history(hist, room=1, floor=2)
    assert len(kept) == 2


def test_fit_history_leaves_short_history_alone():
    hist = [{"role": "user", "content": "hi"} for _ in range(5)]
    kept, dropped = fit_history(hist, room=100_000)
    assert dropped == 0 and len(kept) == 5


def test_fit_history_tolerates_junk():
    assert fit_history([], 100)[0] == []
    assert fit_history([{"role": "user"}], 100)[0] == [{"role": "user"}]


# --- the assembled prompt ---------------------------------------------------


def test_assembled_prompt_fits_the_window(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    hist = [{"role": "user", "content": "x" * 4000} for _ in range(300)]
    msgs = build_messages("hi", hist, cfg)
    sent = sum(tok(str(m.get("content"))) for m in msgs) + _tool_schema_tokens()
    assert sent <= resolved_window(cfg), f"sent {sent} > window {resolved_window(cfg)}"


def test_window_and_num_ctx_agree(tmp_path, monkeypatch):
    """One resolver feeds both, so they cannot drift the way 4096 did."""
    from sk.agent import _extra_body

    cfg = _cfg(tmp_path, monkeypatch)
    assert _extra_body(cfg, plan_mode=False)["options"]["num_ctx"] == resolved_window(cfg)

    cfg.context_window = 32768
    assert _extra_body(cfg, plan_mode=False)["options"]["num_ctx"] == 32768
    assert resolved_window(cfg) == 32768


def test_local_window_can_actually_hold_the_overhead(tmp_path, monkeypatch):
    """The old 4096 could not: fixed cost alone was ~3670."""
    cfg = _cfg(tmp_path, monkeypatch)
    msgs = build_messages("hi", [], cfg)
    fixed = tok(str(msgs[0].get("content"))) + _tool_schema_tokens()
    assert resolved_window(cfg) > fixed * 2, (
        f"window {resolved_window(cfg)} leaves no usable history over {fixed} fixed tokens"
    )


def test_raising_the_budget_admits_more_history(tmp_path, monkeypatch):
    hist = [{"role": "user", "content": "z" * 2000} for _ in range(80)]
    tight = _cfg(tmp_path, monkeypatch, history_budget_tokens=800)
    loose = _cfg(tmp_path, monkeypatch, history_budget_tokens=120_000)

    def _kept(cfg):
        return sum(1 for m in build_messages("hi", hist, cfg) if m["role"] != "system") - 1

    assert _kept(loose) > _kept(tight), (
        f"budget knob inert: tight={_kept(tight)} loose={_kept(loose)}"
    )


def test_history_is_no_longer_capped_at_20(tmp_path, monkeypatch):
    """The old `hist[-20:]` capped by count while nothing else was measured."""
    cfg = _cfg(tmp_path, monkeypatch, history_budget_tokens=100_000)
    hist = [{"role": "user", "content": f"m{i}"} for i in range(60)]
    assert len(build_messages("hi", hist, cfg)) == 62  # system + 60 + user


def test_the_live_user_turn_always_survives(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    hist = [{"role": "user", "content": "y" * 9000} for _ in range(400)]
    assert build_messages("the actual question", hist, cfg)[-1]["content"] == "the actual question"


def test_summary_placeholder_is_never_trimmed_away(tmp_path, monkeypatch):
    """A compacted session is one summary message; dropping it loses the thread."""
    cfg = _cfg(tmp_path, monkeypatch, history_budget_tokens=100_000)
    hist = [{"role": "user", "content": "[Session summary so far]:\nimportant"}]
    msgs = build_messages("hi", hist, cfg)
    assert any("Session summary so far" in str(m.get("content")) for m in msgs)


# --- the breakdown ----------------------------------------------------------


def test_context_report_lists_the_real_parts(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    text = context_report(cfg, [{"role": "user", "content": "hello"}])
    for part in ("system prompt", "auto-context", "tool schemas", "history", "window"):
        assert part in text, f"missing {part}"
    assert str(resolved_window(cfg)) in text.replace(",", "")


def test_context_report_warns_when_over_budget(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, context_window=2048)
    assert "over budget" in context_report(cfg, []).lower()


def test_context_report_never_raises(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    assert context_report(cfg, None)


def test_context_slash_command_is_handled(tmp_path, monkeypatch):
    from sk.slash import handle

    cfg = _cfg(tmp_path, monkeypatch)
    out = handle("/context", session="s", cfg=cfg, state={})
    assert out.handled is True
    assert not out.agent_prompt
    assert "context budget" in out.text


def test_context_appears_in_help(tmp_path, monkeypatch):
    from sk.slash import COMMANDS, handle

    cfg = _cfg(tmp_path, monkeypatch)
    assert any(n.split()[0] == "context" for n, _ in COMMANDS)
    assert "`/context`" in handle("/help", session="s", cfg=cfg, state={}).text


def test_build_messages_actually_charges_for_tool_schemas(tmp_path, monkeypatch):
    """Decisive test that the schema cost reaches the trim decision.

    Written after a sabotage showed the earlier tests did not notice when the
    schema term was deleted from `build_messages`: with a 16k window there was
    room either way, so the trim produced the same answer. This pins a window
    where the two rooms differ by more than a message.
    """
    cfg = _cfg(tmp_path, monkeypatch, context_window=6144, history_budget_tokens=0)
    probe = build_messages("hi", [], cfg)
    sys_tok = tok(str(probe[0].get("content")))
    schemas = _tool_schema_tokens()

    real = context_budget(cfg, sys_tok + schemas, 350)["room"]
    free = context_budget(cfg, sys_tok, 350)["room"]
    assert 0 < real < free, f"window too loose to discriminate: real={real} free={free}"

    per = 200
    hist = [{"role": "user", "content": "h" * (per * 4)} for _ in range(free // per + 5)]
    kept = [m for m in build_messages("hi", hist, cfg) if m["role"] == "user"]

    # the live turn is always present; the rest must match the *charged* room
    assert len(kept) - 1 <= real // per, f"kept {len(kept) - 1}, charged room fits {real // per}"
    assert len(kept) - 1 < free // per, "schemas were not charged"
