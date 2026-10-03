"""Cancelling a TUI turn must actually stop it (#311).

Before this, `run_worker(..., exclusive=True)` cancelled only the *awaiting*
task. `await asyncio.to_thread(run_agent, ...)` runs the agent on a plain
executor thread that `Task.cancel()` cannot interrupt, so the turn carried on
dispatching tools and posting approval cards — while its `finally` had already
cleared `_plan_approved`, and the next turn's card fought the orphan's for one
shared `_pending_approval` slot.

A thread cannot be killed, only asked. This covers the cooperative stop, the
turn-scoped approval state, and the waiter that was never woken.

No existing test drove a full send → agent → cancel → new-turn cycle, which is
why it survived; the last two tests here do.
"""

from __future__ import annotations

import threading
import time as _t

import sk.agent as agent_mod
from sk.tui.app import SidekickTUI


def _blob(app) -> str:
    """Rendered chat-log text. Only valid while the app is mounted."""
    from textual.widgets import RichLog

    try:
        return "\n".join(str(x) for x in app.query_one("#chat-log", RichLog).lines)
    except Exception:
        return ""


def _iso(tmp_path, monkeypatch):
    import sk.config as config_mod
    import sk.store as store

    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")


# --- the cooperative token --------------------------------------------------


def test_cancelled_turn_stops_dispatching_tools(tmp_path, monkeypatch):
    """The core acceptance criterion: cancellation takes effect within a step."""
    _iso(tmp_path, monkeypatch)
    import sk.config as config_mod

    cfg = config_mod.Config()
    cfg.provider = "openai"
    cfg.model = "gpt-4o"
    cfg.api_key = "k"

    class _Chunk:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    seen = {"calls": 0}

    def transport(_p):
        seen["calls"] += 1
        if seen["calls"] <= 3:
            yield _Chunk(
                choices=[
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": f"c{seen['calls']}",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": '{"path":"/etc/hostname"}',
                                    },
                                }
                            ]
                        }
                    }
                ]
            )
            yield _Chunk(choices=[{"delta": {}, "finish_reason": "tool_calls"}])
        else:
            yield _Chunk(choices=[{"delta": {"content": "final"}}])
            yield _Chunk(choices=[{"delta": {}, "finish_reason": "stop"}])

    real = agent_mod._stream_chat
    monkeypatch.setattr(
        agent_mod, "_stream_chat", lambda *a, **kw: real(*a, **{**kw, "transport": transport})
    )
    monkeypatch.setattr(agent_mod, "_create_with_retry", lambda *a, **kw: iter([]))

    # uncancelled: the model is consulted for every step
    seen["calls"] = 0
    out = agent_mod.run_agent("go", [], cfg, cancel=None)
    assert seen["calls"] > 1
    assert out == "final"

    # cancelled after the first tool: it stops immediately
    seen["calls"] = 0
    token = threading.Event()

    def _on_tool(_n, _a):
        token.set()

    out = agent_mod.run_agent("go", [], cfg, on_tool=_on_tool, cancel=token)
    assert out == agent_mod.CANCELLED_TEXT
    assert seen["calls"] == 1, f"consulted the model {seen['calls']} times after cancelling"


def test_cancel_before_the_first_step_never_calls_the_model(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    import sk.config as config_mod

    cfg = config_mod.Config()
    cfg.provider = "openai"
    cfg.model = "gpt-4o"
    cfg.api_key = "k"
    token = threading.Event()
    token.set()
    called = {"n": 0}

    def _boom(*a, **kw):
        called["n"] += 1
        raise AssertionError("must not reach the model after cancelling")

    monkeypatch.setattr(agent_mod, "_create_with_retry", _boom)
    assert agent_mod.run_agent("go", [], cfg, cancel=token) == agent_mod.CANCELLED_TEXT
    assert called["n"] == 0


def test_cancel_tolerates_a_broken_token(tmp_path, monkeypatch):
    """A malformed token must not raise out of run_agent."""

    class _Broken:
        def is_set(self):
            raise RuntimeError("boom")

    _iso(tmp_path, monkeypatch)
    import sk.config as config_mod

    cfg = config_mod.Config()
    cfg.provider = "openai"
    cfg.model = "gpt-4o"
    cfg.api_key = "k"
    monkeypatch.setattr(agent_mod, "_create_with_retry", lambda *a, **kw: iter([]))
    # must not raise
    agent_mod.run_agent("hi", [], cfg, cancel=_Broken())


def test_tool_batch_does_not_start_after_cancel(tmp_path, monkeypatch):
    """Queued calls are dropped rather than run and then reported."""
    ran = []
    token = threading.Event()
    token.set()
    out = agent_mod._run_tools_batch(
        [("read_file", {"path": "/a"}), ("read_file", {"path": "/b"})],
        approve=None,
        on_tool=lambda n, a: ran.append(n),
        seen={},
        session="s",
        cfg=None,
        cancel=token,
    )
    assert ran == [], "dispatched tools after cancellation"
    assert len(out) == 2
    assert all(agent_mod.CANCELLED_TEXT in r[0] for r in out)


# --- turn-scoped approval state --------------------------------------------


def test_plan_approval_from_a_previous_turn_does_not_auto_pass(tmp_path, monkeypatch):
    """The security edge: turn N's plan approval must not approve turn N+1."""
    _iso(tmp_path, monkeypatch)
    from sk.agent import _tool_target

    app = SidekickTUI()
    app._plan_approved = {
        "turn": 1,
        "targets": frozenset({_tool_target("write_file", {"path": "/tmp/x", "content": "hi"})}),
    }

    app._turn_id = 1
    assert app._approve("write_file", {"path": "/tmp/x", "content": "hi"}) is True

    app._turn_id = 2  # the user moved on; the orphan's approval must not count
    app._approve_timeout = 0.05
    called = []
    monkeypatch.setattr(app, "_wait_slot", lambda *a, **k: called.append(1) or False)
    assert app._approve("write_file", {"path": "/tmp/x", "content": "hi"}) is False
    assert called, "should have fallen through to a real prompt"


def test_review_plan_refuses_to_record_when_superseded(tmp_path, monkeypatch):
    """An orphan returning from _wait_slot must write nothing at all."""
    _iso(tmp_path, monkeypatch)
    app = SidekickTUI()
    app._turn_id = 7

    def _supersede(*a, **k):
        app._turn_id = 8  # a new turn opened while the card was on screen
        return True

    monkeypatch.setattr(app, "_wait_slot", _supersede)
    assert app._review_plan("1. shell -> x", [("shell", {"cmd": "x"})]) is False
    assert app._plan_approved is None, "an orphan wrote an approval for the next turn"


def test_wait_slot_fails_closed_when_its_turn_is_superseded(tmp_path, monkeypatch):
    """An orphan's card must not be satisfied by an answer meant for a later turn.

    Fails closed: the slot is stolen by the new turn, so even a `yes` recorded
    against it must not authorise the orphan's write.
    """
    _iso(tmp_path, monkeypatch)
    app = SidekickTUI()
    app._turn_id = 3
    app._approve_timeout = 1

    def _steal(*a, **k):
        app._turn_id = 4  # a new turn opened while the card was on screen
        slot = app._pending_approval
        if slot is None:
            return "too-late"
        slot["answer"] = True
        slot["event"].set()
        return "accepted"

    monkeypatch.setattr(app, "_answer_pending", _steal)
    app._show_live = getattr(app, "_show_live", None)

    import asyncio

    async def _go():
        async with app.run_test():
            return app._wait_slot("shell", "rm -rf /", "preview")

    assert asyncio.run(_go()) is False


# --- the waiter that was never woken ---------------------------------------


def test_late_answer_wakes_the_blocked_worker(tmp_path, monkeypatch):
    """The "too late" path used to clear the slot without waking the thread, so
    a cancelled turn blocked for the full timeout and accumulated."""
    _iso(tmp_path, monkeypatch)
    app = SidekickTUI()
    app._turn_id = 1
    app._approve_timeout = 300
    event = threading.Event()
    app._pending_approval = {
        "question": "shell -> rm",
        "event": event,
        "answer": False,
        "asked_at": _t.monotonic() - 999,
        "timeout": 300,
        "reply": "",
        "token": object(),
        "owner": threading.get_ident(),
        "deadline": _t.monotonic() + 300,
        "turn": 1,
    }
    assert app._answer_pending("y") == "too-late"
    assert event.is_set(), "the blocked worker was never released"
    assert app._pending_approval is None


def test_turn_ids_are_monotonic(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    app = SidekickTUI()
    assert app._turn_seq == 0 and app._turn_id == 0
    app._turn_seq += 1
    app._turn_id = app._turn_seq
    assert app._turn_id == 1


# --- worker groups ----------------------------------------------------------


def test_turn_and_voice_workers_use_separate_groups():
    """They shared Textual's "default" group, so transcribing cancelled a running
    agent turn (and vice versa) even though they are unrelated."""
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[1] / "src/sk/tui/app.py"
    text = src.read_text()
    assert 'group="turn", exclusive=True' in text
    assert 'group="voice", exclusive=True' in text
    assert "run_worker(self._answer(text), exclusive=True)" not in text
    assert "run_worker(self._do_transcribe(self._rec_wav), exclusive=True)" not in text


# --- the full cycle, which no test drove before ---------------------------


def test_pilot_new_turn_stops_the_previous_one(tmp_path, monkeypatch):
    """send → agent running → send again → first turn stands down.

    This is the acceptance path end to end. It is the scenario that produced the
    orphaned approval cards: two turns sharing one `_pending_approval` slot and
    one `_plan_approved`, with the older thread still running underneath.
    """
    import asyncio

    _iso(tmp_path, monkeypatch)
    from sk.tui import ChatArea

    tokens_seen: list = []
    entered = threading.Event()
    stopped = threading.Event()
    got_token = threading.Event()

    def fake_agent(
        text,
        hist,
        cfg,
        on_tool=None,
        on_token=None,
        approve=None,
        on_reasoning=None,
        auto_approve=False,
        review_plan=None,
        read_only=False,
        plan_mode=False,
        cancel=None,
    ):
        # Turn 1 blocks until the app sets its cancel token; turn 2 returns at once.
        if text == "first":
            entered.set()
            if cancel is None:
                got_token.set()
                return "no cancel token was passed"
            cancel.wait(timeout=10)
            stopped.set()
            return agent_mod.CANCELLED_TEXT
        tokens_seen.append(text)
        return "second answer"

    monkeypatch.setattr(agent_mod, "run_agent", fake_agent)

    app = SidekickTUI()

    async def _go():
        async with app.run_test() as pilot:
            area = app.query_one("#chat-input", ChatArea)
            area.focus()
            area.text = "first"
            await pilot.press("enter")
            for _ in range(40):
                await pilot.pause()
                if entered.is_set():
                    break
            assert entered.is_set(), "first turn never started"

            # second message: opens a new turn and must ask the first to stop
            area.focus()
            area.text = "second"
            await pilot.press("enter")
            for _ in range(60):
                await pilot.pause()
                if stopped.is_set():
                    break
            assert not got_token.is_set(), "run_agent was called without a cancel token"
            assert stopped.is_set(), "the first turn's cancel token was never set"
            for _ in range(40):
                await pilot.pause()
                if "second answer" in _blob(app):
                    break
            # read inside the pilot: the widget tree is torn down afterwards
            return _blob(app)

    blob = asyncio.run(_go())

    assert tokens_seen == ["second"], f"unexpected turns ran: {tokens_seen}"
    assert "second answer" in blob
    assert "(cancelled)" not in blob, "a cancelled turn posted its placeholder as an answer"


def test_pilot_turn_plan_does_not_survive_into_the_next(tmp_path, monkeypatch):
    """The acceptance criterion with teeth: turn N's plan approval must not
    auto-approve a write in turn N+1."""
    import asyncio

    _iso(tmp_path, monkeypatch)
    from sk.tui import ChatArea

    from sk.agent import _tool_target

    target = _tool_target("write_file", {"path": "/tmp/leak", "content": "x"})
    seen: list = []

    def fake_agent(
        text,
        hist,
        cfg,
        on_tool=None,
        on_token=None,
        approve=None,
        on_reasoning=None,
        auto_approve=False,
        review_plan=None,
        read_only=False,
        plan_mode=False,
        cancel=None,
    ):
        ok = approve("write_file", {"path": "/tmp/leak", "content": "x"})
        seen.append((text, ok))
        return "done"

    monkeypatch.setattr(agent_mod, "run_agent", fake_agent)

    app = SidekickTUI()
    # Nobody answers this card, so the default 300s approval timeout would be
    # waited out in full — a 5-minute test. A timeout denial still proves the
    # point: the stale plan approval did not auto-pass.
    app._approve_timeout = 0.1

    async def _go():
        async with app.run_test() as pilot:
            app._plan_approved = {"turn": 0, "targets": frozenset({target})}
            area = app.query_one("#chat-input", ChatArea)
            area.focus()
            area.text = "go"
            await pilot.press("enter")
            for _ in range(60):
                await pilot.pause()
                if seen:
                    break

    asyncio.run(_go())

    assert seen, "the fake agent never ran"
    assert seen[0][1] is False, "a stale plan approval auto-passed a write"
