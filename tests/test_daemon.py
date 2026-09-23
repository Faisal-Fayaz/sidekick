"""Daemon tests: pure check logic with isolation."""

import sk.daemon as daemon
import sk.store as store


def _iso_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")


def test_new_failures_only_new(monkeypatch, tmp_path):
    _iso_store(tmp_path, monkeypatch)
    store.log_shell("ok one", "/tmp", 0)
    store.log_shell("bad one", "/tmp", 3)
    rows, max_id = daemon.new_failures(0)
    assert any(r[1] == "bad one" for r in rows) and all(r[1] != "ok one" for r in rows)
    rows2, _ = daemon.new_failures(max_id)
    assert rows2 == []


def test_check_once_state_advances(monkeypatch, tmp_path):
    _iso_store(tmp_path, monkeypatch)
    store.log_shell("boom cmd", "/tmp", 1)
    nudges, state = daemon.check_once({"last_shell_id": 0}, projects=[], disk_warn=99)
    assert any("boom cmd" in n for n in nudges)
    assert state["last_shell_id"] > 0
    nudges2, _ = daemon.check_once(state, projects=[], disk_warn=99)
    assert not any("boom cmd" in n for n in nudges2)


def test_disk_parse(monkeypatch):
    assert daemon.disk_use_pct() is None or 0 <= daemon.disk_use_pct() <= 100


def test_unit_text_renders():
    out = daemon.unit_text(interval=120, disk_warn=80)
    assert "sidekick" in out.lower() and "daemon --interval 120 --disk-warn 80" in out
    assert "WantedBy=default.target" in out and "Restart=on-failure" in out


def test_install_writes_and_enables(monkeypatch, tmp_path):
    dest = tmp_path / daemon.UNIT_NAME
    calls: list[list[str]] = []

    def fake_run(argv, **k):
        calls.append(list(argv))
        return type("R", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

    monkeypatch.setattr(daemon.subprocess, "run", fake_run)
    monkeypatch.setattr(daemon, "has_systemd", lambda: True)
    out = daemon.install_unit(interval=60, disk_warn=80, path=dest)
    assert "Installed + started" in out
    text = dest.read_text()
    assert "daemon --interval 60 --disk-warn 80" in text
    flat = [" ".join(c) for c in calls]
    assert any("daemon-reload" in c for c in flat)
    assert any("enable --now" in c and daemon.UNIT_NAME in c for c in flat)


def test_install_no_systemd(monkeypatch, tmp_path):
    monkeypatch.setattr(daemon, "has_systemd", lambda: False)
    out = daemon.install_unit(path=tmp_path / daemon.UNIT_NAME)
    assert "No user systemd" in out


def test_install_cli_wires_through(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from sk.cli import app

    dest = tmp_path / daemon.UNIT_NAME
    monkeypatch.setattr(daemon, "has_systemd", lambda: True)
    monkeypatch.setattr("sk.daemon.unit_path", lambda: dest)  # install_unit() resolves path lazily
    monkeypatch.setattr(
        daemon.subprocess,
        "run",
        lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
    )
    res = CliRunner().invoke(app, ["daemon-install", "--interval", "60"])
    assert res.exit_code == 0, res.output
    assert "Installed + started" in res.output
    assert dest.exists()
