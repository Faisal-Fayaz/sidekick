"""Event hooks tests (fixes #152): config, runner semantics, integration,
sk hooks CLI. Fully offline (spawns local echo/python/sleep only)."""

import json
import shlex
import sys

import sk.hooks as hooks
import sk.store as store


def _iso(tmp_path, monkeypatch):
    import sk.config as config_mod

    cfgdir = tmp_path / ".sidekick"
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(config_mod, "CONFIG_DIR", cfgdir)
    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfgdir / "config.toml")
    monkeypatch.chdir(tmp_path)
    hooks._started_sessions.clear()
    return cfgdir


def _script(tmp_path, name, body):
    p = tmp_path / name
    p.write_text(body)
    return shlex.join([sys.executable, str(p)])


def _toml_str(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _write_config(cfgdir, text):
    cfgdir.mkdir(parents=True, exist_ok=True)
    (cfgdir / "config.toml").write_text(text)


ALLOW_SRC = "import json,sys; json.load(sys.stdin); print(json.dumps({'decision': 'allow'}))"
DENY_SRC = "import json,sys; json.load(sys.stdin); print(json.dumps({'decision': 'deny', 'reason': 'nope'}))"
CRASH_SRC = "import sys; sys.exit(3)"
SLOW_SRC = "import time; time.sleep(30)"
GARBAGE_SRC = "print('just logging')"


def _hook_config(tmp_path, allow_cmd, deny_cmd="", extra=""):
    lines = [f"[[hooks.PreToolUse]]\ncommand = {_toml_str(allow_cmd)}\n"]
    if deny_cmd:
        lines.append(f"[[hooks.PreToolUse]]\ncommand = {_toml_str(deny_cmd)}\n")
    return "".join(lines) + extra


def test_config_parsing_and_project_blocked(tmp_path, monkeypatch):
    cfgdir = _iso(tmp_path, monkeypatch)
    allow = _script(tmp_path, "a.py", ALLOW_SRC)
    _write_config(
        cfgdir,
        _hook_config(tmp_path, allow)
        + '[[hooks.Bogus]]\ncommand = "x"\n'
        + "[[hooks.PostToolUse]]\ntimeout = 5\n",
    )
    from sk.config import Config, load_project_values

    loaded = Config.load().hooks
    assert len(loaded) == 1
    assert loaded[0]["event"] == "PreToolUse" and loaded[0]["timeout"] == 30.0
    p = tmp_path / ".sidekick.toml"
    p.write_text('[[hooks.PreToolUse]]\ncommand = "evil"\n')
    vals, warnings = load_project_values(p)
    assert "hooks" not in vals and any("hooks" in w for w in warnings)


def test_run_hook_decisions(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    allow = _script(tmp_path, "a.py", ALLOW_SRC)
    deny = _script(tmp_path, "d.py", DENY_SRC)
    crash = _script(tmp_path, "c.py", CRASH_SRC)
    slow = _script(tmp_path, "s.py", SLOW_SRC)
    garbage = _script(tmp_path, "g.py", GARBAGE_SRC)
    payload = {"session": "s", "tool": "exec", "args": {}}

    res = hooks.run_hook(allow, "PreToolUse", payload, 10)
    assert res == {"ok": True, "decision": "allow", "reason": "", "command": allow}
    res = hooks.run_hook(deny, "PreToolUse", payload, 10)
    assert res["decision"] == "deny" and res["reason"] == "nope" and res["ok"] is True
    res = hooks.run_hook(garbage, "PreToolUse", payload, 10)
    assert res["decision"] == "allow" and res["ok"] is True
    res = hooks.run_hook(crash, "PreToolUse", payload, 10)
    assert res["decision"] == "deny" and res["ok"] is False
    res = hooks.run_hook(slow, "PreToolUse", payload, 1)
    assert res["decision"] == "deny" and "timed out" in res["reason"]
    # fail-open elsewhere
    res = hooks.run_hook(crash, "PostToolUse", payload, 10)
    assert res["decision"] == "allow" and res["ok"] is False
    res = hooks.run_hook("/nonexistent-hook-binary-xyz", "PreToolUse", payload, 5)
    assert res["decision"] == "deny"
    res = hooks.run_hook("/nonexistent-hook-binary-xyz", "SessionStart", payload, 5)
    assert res["decision"] == "allow"


def test_envelope_contents(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    src = (
        "import json,sys; d=json.load(sys.stdin);"
        "print(json.dumps({'decision':'allow','reason':d['tool']}))"
    )
    cmd = _script(tmp_path, "e.py", src)
    res = hooks.run_hook(cmd, "PreToolUse", {"session": "s1", "tool": "shell", "args": {}}, 10)
    assert res["reason"] == "shell"


def test_pre_tool_use_first_deny_wins(tmp_path, monkeypatch):
    cfgdir = _iso(tmp_path, monkeypatch)
    allow = _script(tmp_path, "a.py", ALLOW_SRC)
    deny = _script(tmp_path, "d.py", DENY_SRC)
    _write_config(cfgdir, _hook_config(tmp_path, allow, deny))
    ok, reason = hooks.pre_tool_use("s", "exec", {"cmd": "ls"})
    assert ok is False and reason.startswith("Denied by hook") and "nope" in reason
    _write_config(cfgdir, _hook_config(tmp_path, allow))
    assert hooks.pre_tool_use("s", "exec", {"cmd": "ls"}) == (True, "")


def test_post_tool_use_observes_result(tmp_path, monkeypatch):
    cfgdir = _iso(tmp_path, monkeypatch)
    log = tmp_path / "post.log"
    src = (
        "import json,sys; d=json.load(sys.stdin);"
        f"open({str(log)!r},'a').write(d.get('result','')[:50]);"
        "print(json.dumps({'decision':'allow'}))"
    )
    _write_config(
        cfgdir, f"[[hooks.PostToolUse]]\ncommand = {_toml_str(_script(tmp_path, 'p.py', src))}\n"
    )
    hooks.post_tool_use("s", "exec", {"cmd": "ls"}, "hello-result")
    assert "hello-result" in log.read_text()


def test_session_start_once_per_process(tmp_path, monkeypatch):
    cfgdir = _iso(tmp_path, monkeypatch)
    log = tmp_path / "start.log"
    src = f"import sys; open({str(log)!r},'a').write('x\\n')"
    _write_config(
        cfgdir, f"[[hooks.SessionStart]]\ncommand = {_toml_str(_script(tmp_path, 's.py', src))}\n"
    )
    hooks.session_start("s1")
    hooks.session_start("s1")
    hooks.session_start("s2")
    assert log.read_text() == "x\nx\n"


def test_gated_dispatch_hook_deny_and_audit(tmp_path, monkeypatch):
    cfgdir = _iso(tmp_path, monkeypatch)
    import sk.agent as agent

    deny = _script(tmp_path, "d.py", DENY_SRC)
    _write_config(cfgdir, _hook_config(tmp_path, deny))
    result, approved = agent._gated_dispatch(
        "exec", {"cmd": "ls"}, approve=lambda n, a: True, session="s"
    )
    assert approved is False and "Denied by hook" in result and "nope" in result
    rows = store.list_tool_runs("s")
    assert any(r["tool"] == "exec" and r["approved"] == 0 for r in rows)


def test_gated_dispatch_hook_allow_runs_and_posts(tmp_path, monkeypatch):
    cfgdir = _iso(tmp_path, monkeypatch)
    import sk.agent as agent

    log = tmp_path / "post.log"
    allow = _script(tmp_path, "a.py", ALLOW_SRC)
    src = (
        "import json,sys; d=json.load(sys.stdin);"
        f"open({str(log)!r},'a').write(d.get('tool',''));"
        "print(json.dumps({'decision':'allow'}))"
    )
    post = _script(tmp_path, "p.py", src)
    _write_config(
        cfgdir,
        f"[[hooks.PreToolUse]]\ncommand = {_toml_str(allow)}\n"
        f"[[hooks.PostToolUse]]\ncommand = {_toml_str(post)}\n",
    )
    result, approved = agent._gated_dispatch(
        "exec", {"cmd": "echo hi"}, approve=lambda n, a: True, session="s"
    )
    assert approved is True and "hi" in result
    assert "exec" in log.read_text()


def test_sk_hooks_cli(tmp_path, monkeypatch):
    cfgdir = _iso(tmp_path, monkeypatch)
    from typer.testing import CliRunner

    from sk.cli import app

    res = CliRunner().invoke(app, ["hooks"])
    assert res.exit_code == 0, res.output
    assert "no hooks" in res.output
    allow = _script(tmp_path, "a.py", ALLOW_SRC)
    _write_config(cfgdir, _hook_config(tmp_path, allow))
    res = CliRunner().invoke(app, ["hooks"])
    assert "PreToolUse" in res.output and "a.py" in res.output
    res = CliRunner().invoke(app, ["hooks", "--check"])
    assert "ok" in res.output and "allow" in res.output
