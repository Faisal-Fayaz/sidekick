"""Secret redaction at every persistence and display boundary (#300).

The audit ledger is meant to be handable to an auditor, pasted into a bug
report, and exported to Markdown — so it cannot hold live secrets. This is also
the acceptance criterion of #156 ("keys never appear in either artifact").

`log_shell` carried a comment claiming it skipped secrets; no filtering existed.
These tests pin the behaviour that comment described.
"""

import pytest

from sk.store import redact

# (input, must-not-appear)
SECRETS = [
    ("export TOKEN=ghp_realsecret1234567890abcd", "ghp_realsecret"),
    ("export GITHUB_TOKEN=ghp_16C7e42F292c6912E7710c838347Ae178B4a", "ghp_16C7e42F"),
    ("export OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwx", "sk-proj-abcdefghij"),
    ("export ANTHROPIC_API_KEY=sk-ant-api03-xxxxxxxxxxxxxxxxxxxx", "sk-ant-api03-xxxx"),
    ('curl -H "Authorization: Bearer sk-abcdefghijklmnopqrst" http://x', "sk-abcdefghijklmnopqrst"),
    ("mysql -uroot -pSup3rS3cret db", "Sup3rS3cret"),
    ("psql --password hunter2 host", "hunter2"),
    ("AWS_SECRET_ACCESS_KEY=abcdef123456", "abcdef123456"),
    ("GITHUB_TOKEN=xoxb-123456789012-abcdefghij", "xoxb-123456789012"),
    ("AWS_KEY=AKIAIOSFODNN7EXAMPLE", "AKIAIOSFODNN7EXAMPLE"),
    (
        "export JWT=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N",
        "dozjgNryP4J3",
    ),
    (
        "cat id_rsa\n-----BEGIN RSA PRIVATE KEY-----\nMIIEabc\n-----END RSA PRIVATE KEY-----",
        "MIIEabc",
    ),
]


@pytest.mark.parametrize("raw,leak", SECRETS)
def test_secret_never_survives(raw, leak):
    assert leak not in redact(raw)


BENIGN = [
    "ls -la",
    'git commit -m "fix login"',
    "pytest -q",
    "npm run build",
    "export PATH=/usr/bin",
    "echo hello world",
    "grep -r token src/ --include='*.py'",
    "kubectl get pods",
    "python -m pytest tests -q",
    "cargo build --release",
]


@pytest.mark.parametrize("cmd", BENIGN)
def test_benign_commands_untouched(cmd):
    """Over-redaction is its own bug: it makes the ledger unreadable."""
    assert redact(cmd) == cmd


def test_redact_never_raises():
    for bad in (None, "", 0, [], {}, "\x00\xff"):
        assert isinstance(redact(bad), str)


def test_log_shell_redacts_persisted_command(tmp_path, monkeypatch):
    import sk.store as store

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "h.db")
    assert store.log_shell("export TOKEN=ghp_realsecret1234567890abcd") is True
    conn = store._connect()
    try:
        row = conn.execute("SELECT cmd FROM shell_history ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    assert row is not None
    assert "ghp_realsecret" not in row[0], row[0]
    assert "[redacted]" in row[0]


def test_log_tool_run_redacts_target(tmp_path, monkeypatch):
    import sk.store as store

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "h.db")
    store.log_tool_run("s1", "shell", "shell|cmd=export AWS_SECRET_ACCESS_KEY=abcdef123456")
    conn = store._connect()
    try:
        row = conn.execute("SELECT target FROM tool_runs ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    assert row is not None
    assert "abcdef123456" not in row[0], row[0]


def test_daemon_notification_redacts(monkeypatch):
    """A failing command is where credentials leak; it reaches a lock screen."""
    import sk.daemon as daemon

    monkeypatch.setattr(
        daemon,
        "new_failures",
        lambda _last: (
            [(1, "curl -H 'Authorization: Bearer sk-abcdefghijklmnopqrst'", "~/p", 1)],
            1,
        ),
    )
    nudges, _ = daemon.check_once(state={"last_shell_id": 0}, projects=[])
    assert nudges
    for n in nudges:
        assert "sk-abcdefghijklmnopqrst" not in n, n


def test_digest_redacts(monkeypatch):
    import sk.daemon as daemon

    monkeypatch.setattr(
        daemon,
        "new_failures",
        lambda _last: ([(1, "export TOKEN=ghp_realsecret1234567890abcd", "~/p", 1)], 1),
    )
    # digest_text imports gather_brief locally from sk.brief
    import sk.brief as brief

    monkeypatch.setattr(brief, "gather_brief", lambda *a, **k: {})
    text, _ = daemon.digest_text(state={"last_shell_id": 0}, projects=[])
    assert "ghp_realsecret" not in text, text
