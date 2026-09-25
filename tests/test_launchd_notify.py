"""Launchd + notifier tests (#34): plist parity, DND, logged fallback. No live launchd."""

import sk.daemon as daemon


def test_launchd_text_renders():
    out = daemon.launchd_text(interval=120, disk_warn=80)
    assert daemon.LAUNCHD_LABEL in out
    assert "--interval" in out and "120" in out and "80" in out
    assert "<key>StartInterval</key>" in out and "<key>RunAtLoad</key>" in out
    assert "daemon.out.log" in out


def test_install_launchd_writes_and_bootstraps(monkeypatch, tmp_path):
    dest = tmp_path / f"{daemon.LAUNCHD_LABEL}.plist"
    calls: list[list[str]] = []

    def fake_run(argv, **k):
        calls.append(list(argv))
        return type("R", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

    monkeypatch.setattr(daemon.subprocess, "run", fake_run)
    monkeypatch.setattr(daemon, "has_launchd", lambda: True)
    out = daemon.install_launchd(interval=60, disk_warn=80, path=dest)
    assert "Installed + started" in out
    text = dest.read_text()
    assert daemon.LAUNCHD_LABEL in text and "60" in text
    flat = [" ".join(c) for c in calls]
    assert any("bootstrap" in c and daemon.LAUNCHD_LABEL in out or "bootstrap" in c for c in flat)


def test_install_launchd_no_backend(monkeypatch, tmp_path):
    monkeypatch.setattr(daemon, "has_launchd", lambda: False)
    out = daemon.install_launchd(path=tmp_path / "x.plist")
    assert "No launchd" in out


def test_in_dnd_boundaries():
    assert daemon.in_dnd(23) is True
    assert daemon.in_dnd(3) is True
    assert daemon.in_dnd(12) is False
    assert daemon.in_dnd(22) is True
    assert daemon.in_dnd(7) is False
    assert daemon.in_dnd(12, start=9, end=17) is True
    assert daemon.in_dnd(8, start=9, end=17) is False
    assert daemon.in_dnd(12, start=12, end=12) is False  # degenerate = never
    assert daemon.in_dnd("garbage") is False


def test_notify_dnd_logs(monkeypatch, tmp_path):
    logged: list[str] = []
    monkeypatch.setattr(daemon, "in_dnd", lambda *a, **k: True)
    monkeypatch.setattr(daemon, "append_log", lambda ns: logged.extend(ns))
    assert daemon.notify("t", "b") == "dnd"
    assert logged == ["t: b"]


def test_notify_no_backend_logs(monkeypatch):
    logged: list[str] = []
    monkeypatch.setattr(daemon, "in_dnd", lambda *a, **k: False)
    monkeypatch.setattr(daemon, "notify_backend", lambda: "")
    monkeypatch.setattr(daemon, "append_log", lambda ns: logged.extend(ns))
    assert daemon.notify("t", "b") == "logged"
    assert logged == ["t: b"]


def test_notify_backend_never_raises(monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda *a, **k: None)
    assert daemon.notify_backend() == ""
    assert daemon.notify("t", "b", force=True) in ("logged", "dnd")


def test_notify_sends_and_fallback(monkeypatch):
    monkeypatch.setattr(daemon, "in_dnd", lambda *a, **k: False)
    monkeypatch.setattr(daemon, "notify_backend", lambda: "notify-send")
    logged: list[str] = []
    monkeypatch.setattr(daemon, "append_log", lambda ns: logged.extend(ns))

    def ok_run(argv, **k):
        assert argv[0] == "notify-send"
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(daemon.subprocess, "run", ok_run)
    assert daemon.notify("t", "b", force=True) == "sent:notify-send"

    def fail_run(argv, **k):
        return type("R", (), {"returncode": 1, "stdout": "", "stderr": "x"})()

    monkeypatch.setattr(daemon.subprocess, "run", fail_run)
    assert daemon.notify("t", "b", force=True) == "logged"
    assert logged == ["t: b"]
