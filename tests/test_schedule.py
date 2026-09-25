"""Scheduled tasks tests (#38): parser, calendar renders, persistence, CLI."""

import sk.daemon as daemon


def _iso_state(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon, "STATE_PATH", tmp_path / "daemon.json")


def test_parse_named_slots():
    assert daemon.parse_schedule("brief me every morning") == {
        "kind": "daily",
        "hour": 8,
        "minute": 0,
        "text": "brief me every morning",
    }
    assert daemon.parse_schedule("every evening")["hour"] == 18
    assert daemon.parse_schedule("at noon")["hour"] == 12
    assert daemon.parse_schedule("midnight run")["hour"] == 0


def test_parse_explicit_times():
    assert daemon.parse_schedule("daily at 9:30") == {
        "kind": "daily",
        "hour": 9,
        "minute": 30,
        "text": "daily at 9:30",
    }
    assert daemon.parse_schedule("every day at 9")["hour"] == 9
    assert daemon.parse_schedule("9am") == {"kind": "daily", "hour": 9, "minute": 0, "text": "9am"}
    assert daemon.parse_schedule("5pm")["hour"] == 17
    assert daemon.parse_schedule("at 17:45")["minute"] == 45
    assert daemon.parse_schedule("12am")["hour"] == 0
    assert daemon.parse_schedule("12pm")["hour"] == 12


def test_parse_hourly_and_intervals():
    assert daemon.parse_schedule("hourly")["kind"] == "hourly"
    assert daemon.parse_schedule("every hour")["kind"] == "hourly"
    assert daemon.parse_schedule("every 30 minutes") == {
        "kind": "interval",
        "seconds": 1800,
        "text": "every 30 minutes",
    }
    assert daemon.parse_schedule("every 2 hours")["seconds"] == 7200


def test_parse_rejects_garbage():
    assert daemon.parse_schedule("") is None
    assert daemon.parse_schedule("sometime-ish") is None
    assert daemon.parse_schedule("at 99") is None
    assert daemon.parse_schedule(None) is None


def test_describe_schedule():
    assert daemon.describe_schedule({"kind": "daily", "hour": 8, "minute": 5}) == "daily at 08:05"
    assert daemon.describe_schedule({"kind": "hourly"}) == "hourly at :00"
    assert daemon.describe_schedule({"kind": "interval", "seconds": 60}) == "every 60s"
    assert daemon.describe_schedule(None) == "(none)"
    assert daemon.describe_schedule({}) == "(none)"


def test_set_get_clear_roundtrip(tmp_path, monkeypatch):
    _iso_state(tmp_path, monkeypatch)
    assert daemon.get_schedule() is None
    spec, msg = daemon.set_schedule("every morning")
    assert spec is not None and "daily at 08:00" in msg
    assert daemon.get_schedule()["hour"] == 8
    assert "cleared" in daemon.clear_schedule().lower()
    assert daemon.get_schedule() is None


def test_set_rejects_garbage(tmp_path, monkeypatch):
    _iso_state(tmp_path, monkeypatch)
    spec, msg = daemon.set_schedule("whenever")
    assert spec is None and "every morning" in msg


def test_timer_text_renders():
    out = daemon.timer_text({"kind": "daily", "hour": 8, "minute": 30})
    assert "OnCalendar=*-*-* 08:30:00" in out
    assert "Persistent=true" in out and "WantedBy=timers.target" in out
    assert daemon.timer_text({"kind": "hourly"}) and "OnCalendar=hourly" in daemon.timer_text(
        {"kind": "hourly"}
    )


def test_unit_text_schedule_runs_once():
    out = daemon.unit_text(schedule={"kind": "daily", "hour": 8, "minute": 0})
    assert "daemon --once" in out and "--interval" not in out


def test_launchd_text_schedule_calendar():
    out = daemon.launchd_text(schedule={"kind": "daily", "hour": 8, "minute": 30})
    assert "StartCalendarInterval" in out and "StartInterval" not in out
    assert "<integer>8</integer>" in out and "--once" in out and "--interval" not in out
    hourly = daemon.launchd_text(schedule={"kind": "hourly"})
    assert "StartCalendarInterval" in hourly and "<key>Minute</key>" in hourly


def test_install_unit_schedule_writes_timer(tmp_path, monkeypatch):
    _iso_state(tmp_path, monkeypatch)
    dest = tmp_path / daemon.UNIT_NAME
    tdest = tmp_path / daemon.TIMER_NAME
    calls: list[list[str]] = []

    def fake_run(argv, **k):
        calls.append(list(argv))
        return type("R", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

    monkeypatch.setattr(daemon.subprocess, "run", fake_run)
    monkeypatch.setattr(daemon, "has_systemd", lambda: True)
    out = daemon.install_unit(path=dest, timer=tdest, schedule="every morning")
    assert "Installed + started" in out and "daily at 08:00" in out
    assert "daemon --once" in dest.read_text()
    assert "OnCalendar=*-*-* 08:00:00" in tdest.read_text()
    assert daemon.get_schedule()["hour"] == 8
    flat = [" ".join(c) for c in calls]
    assert any(daemon.TIMER_NAME in c and "enable" in c for c in flat)


def test_install_unit_bad_schedule_writes_nothing(tmp_path, monkeypatch):
    _iso_state(tmp_path, monkeypatch)
    dest = tmp_path / daemon.UNIT_NAME
    monkeypatch.setattr(daemon, "has_systemd", lambda: True)
    out = daemon.install_unit(path=dest, schedule="whenever")
    assert "Could not parse" in out
    assert not dest.exists()


def test_install_launchd_schedule_calendar(tmp_path, monkeypatch):
    _iso_state(tmp_path, monkeypatch)
    dest = tmp_path / f"{daemon.LAUNCHD_LABEL}.plist"
    monkeypatch.setattr(
        daemon.subprocess,
        "run",
        lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
    )
    monkeypatch.setattr(daemon, "has_launchd", lambda: True)
    out = daemon.install_launchd(path=dest, schedule="daily at 9:30")
    assert "Installed + started" in out and "daily at 09:30" in out
    assert "StartCalendarInterval" in dest.read_text()


def test_remove_unit_removes_timer(tmp_path, monkeypatch):
    dest = tmp_path / daemon.UNIT_NAME
    tdest = tmp_path / daemon.TIMER_NAME
    dest.write_text("unit")
    tdest.write_text("timer")
    monkeypatch.setattr(daemon, "has_systemd", lambda: True)
    monkeypatch.setattr(
        daemon.subprocess,
        "run",
        lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
    )
    out = daemon.remove_unit(path=dest, timer=tdest)
    assert "Removed" in out
    assert not dest.exists() and not tdest.exists()


def test_schedule_cli(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from sk.cli import app

    _iso_state(tmp_path, monkeypatch)
    res = CliRunner().invoke(app, ["daemon-schedule"])
    assert res.exit_code == 0 and "(none)" in res.output
    res = CliRunner().invoke(app, ["daemon-schedule", "--set", "every morning"])
    assert res.exit_code == 0 and "daily at 08:00" in res.output
    res = CliRunner().invoke(app, ["daemon-schedule"])
    assert "daily at 08:00" in res.output
    res = CliRunner().invoke(app, ["daemon-schedule", "--set", "whenever"])
    assert res.exit_code == 0 and "Could not parse" in res.output
    res = CliRunner().invoke(app, ["daemon-schedule", "--clear"])
    assert res.exit_code == 0 and "cleared" in res.output.lower()


def test_install_cli_schedule_flag(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from sk.cli import app

    _iso_state(tmp_path, monkeypatch)
    dest = tmp_path / daemon.UNIT_NAME
    tdest = tmp_path / daemon.TIMER_NAME
    monkeypatch.setattr(daemon, "has_systemd", lambda: True)
    monkeypatch.setattr("sk.daemon.unit_path", lambda: dest)
    monkeypatch.setattr("sk.daemon.timer_path", lambda: tdest)
    monkeypatch.setattr(
        daemon.subprocess,
        "run",
        lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
    )
    res = CliRunner().invoke(app, ["daemon-install", "--schedule", "every morning"])
    assert res.exit_code == 0, res.output
    assert "daily at 08:00" in res.output and tdest.exists()
