"""Parallel dispatch tests: order, isolation, serial approvals, cache. No network."""

import threading
import time

import sk.agent as agent


def _tracker(sleep=0.0, fail_on=(), approve_calls=None):
    """Fake dispatch_tool recording concurrency depth. Returns (state, fake)."""
    state = {"depth": 0, "max_depth": 0, "calls": [], "lock": threading.Lock()}

    def fake(name, args):
        with state["lock"]:
            state["depth"] += 1
            state["max_depth"] = max(state["max_depth"], state["depth"])
            state["calls"].append(name)
        try:
            if sleep:
                time.sleep(sleep)
            if name in fail_on:
                raise RuntimeError(f"{name} blew up")
            return f"ok-{name}"
        finally:
            with state["lock"]:
                state["depth"] -= 1

    return state, fake


def test_order_preserved_despite_skew(monkeypatch):
    from sk.agent import _run_tools_batch

    def fake(name, args):
        time.sleep(0.05 if name == "a" else 0.0)
        return f"ok-{name}"

    monkeypatch.setattr(agent, "dispatch_tool", fake)
    outs = _run_tools_batch([("a", {}), ("b", {}), ("c", {})], None, None, {}, session="s")
    assert [r for r, _ in outs] == ["ok-a", "ok-b", "ok-c"]
    assert all(rep is False for _, rep in outs)


def test_failure_isolated(monkeypatch):
    from sk.agent import _run_tools_batch

    _, fake = _tracker(fail_on=("bad",))
    monkeypatch.setattr(agent, "dispatch_tool", fake)
    outs = _run_tools_batch([("good", {}), ("bad", {}), ("good2", {})], None, None, {}, session="s")
    assert outs[0][0] == "ok-good" and outs[2][0] == "ok-good2"
    assert "Error" in outs[1][0] and "bad" in outs[1][0]


def test_gated_tools_run_serially(monkeypatch):
    from sk.agent import _run_tools_batch

    state, fake = _tracker(sleep=0.05)
    monkeypatch.setattr(agent, "dispatch_tool", fake)

    def approve(n, a):
        return True

    outs = _run_tools_batch(
        [("shell", {"cmd": "one"}), ("shell", {"cmd": "two"})], approve, None, {}, session="s"
    )
    assert state["max_depth"] == 1  # never overlapped
    assert [r for r, _ in outs] == ["ok-shell", "ok-shell"]


def test_free_tools_run_concurrently(monkeypatch):
    from sk.agent import _run_tools_batch

    state, fake = _tracker(sleep=0.3)
    monkeypatch.setattr(agent, "dispatch_tool", fake)
    outs = _run_tools_batch(
        [("list_dir", {"path": "x"}), ("sysinfo", {}), ("exec", {"cmd": "pwd"})],
        None,
        None,
        {},
        session="s",
    )
    assert state["max_depth"] > 1  # overlapped for real
    assert [r for r, _ in outs] == ["ok-list_dir", "ok-sysinfo", "ok-exec"]


def test_timing_beats_serial_floor(monkeypatch):
    from sk.agent import _run_tools_batch

    # Deterministic proof of true parallelism (no wall-clock margins that
    # flake on loaded CI runners): all three workers must rendezvous.
    # Serial execution would deadlock at the barrier and time out instead.
    gate = threading.Barrier(3)

    def fake(name, args):
        gate.wait(timeout=30)
        return f"ok-{name}"

    monkeypatch.setattr(agent, "dispatch_tool", fake)
    t0 = time.monotonic()
    outs = _run_tools_batch(
        [("list_dir", {}), ("sysinfo", {}), ("exec", {})], None, None, {}, session="s"
    )
    elapsed = time.monotonic() - t0
    assert [r for r, _ in outs] == ["ok-list_dir", "ok-sysinfo", "ok-exec"]
    assert elapsed < 25  # sanity only: serial would hang 30s+ at the barrier


def test_cache_hits_skip_execution(monkeypatch):
    from sk.agent import _run_tools_batch

    state, fake = _tracker()
    monkeypatch.setattr(agent, "dispatch_tool", fake)
    seen = {"list_dir|path=x": "cached-result"}
    outs = _run_tools_batch([("list_dir", {"path": "x"})], None, None, seen, session="s")
    assert len(outs) == 1 and outs[0][1] is True
    assert "cached-result" in outs[0][0] and "already ran" in outs[0][0]
    assert state["calls"] == []


def test_on_tool_replays_in_order(monkeypatch):
    from sk.agent import _run_tools_batch

    _, fake = _tracker(sleep=0.02)
    monkeypatch.setattr(agent, "dispatch_tool", fake)
    notes = []
    _run_tools_batch(
        [("b", {}), ("a", {}), ("c", {})], None, lambda n, a: notes.append(n), {}, session="s"
    )
    assert notes == ["b", "a", "c"]


def test_denied_never_executes(monkeypatch):
    from sk.agent import _run_tools_batch

    state, fake = _tracker()
    monkeypatch.setattr(agent, "dispatch_tool", fake)
    outs = _run_tools_batch([("shell", {"cmd": "x"})], lambda n, a: False, None, {}, session="s")
    assert outs[0][1] is False and "Denied" in outs[0][0]
    assert state["calls"] == []
