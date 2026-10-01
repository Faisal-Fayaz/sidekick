"""Custom slash commands tests (fixes #153): parse/render, precedence,
builtin wins, slash dispatch, help, autocomplete. Fully offline."""

import pytest
import sk.custom_commands as cc
import sk.slash as slash
import sk.store as store


def _iso(tmp_path, monkeypatch, chdir=None):
    import sk.config as config_mod

    cfgdir = tmp_path / ".sidekick"
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(config_mod, "CONFIG_DIR", cfgdir)
    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfgdir / "config.toml")
    monkeypatch.chdir(chdir or tmp_path)
    return cfgdir


def _write(d, name, body):
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(body)
    return d / name


def _ctx(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    from sk.config import Config

    cfg = Config(
        provider="ollama",
        model="t",
        base_url="http://x/v1",
        api_key="x",
        max_steps=1,
        temperature=0.0,
    )
    return {"session": "test", "cfg": cfg, "state": {"yolo": False}}


def test_parse_and_render(tmp_path, monkeypatch):
    cfgdir = _iso(tmp_path, monkeypatch)
    _write(
        cfgdir / "commands",
        "ship.md",
        '---\ndescription: Ship it\nargument-hint: "[branch]"\n---\nReview {{args}} then merge.\n',
    )
    rows = cc.list_custom_commands()
    assert rows == [("ship", "Ship it", cfgdir / "commands" / "ship.md")]
    assert cc.render_custom_command("ship", "main") == "Review main then merge.\n"
    assert cc.render_custom_command("SHIP", "x") is not None  # case-insensitive
    assert cc.render_custom_command("nope", "x") is None


def test_dollar_arguments_and_empty_args(tmp_path, monkeypatch):
    cfgdir = _iso(tmp_path, monkeypatch)
    _write(cfgdir / "commands", "w.md", "Do $ARGUMENTS now.\n")
    assert cc.render_custom_command("w", "it") == "Do it now.\n"
    assert cc.render_custom_command("w", "") == "Do  now.\n"


def test_no_frontmatter_uses_first_line(tmp_path, monkeypatch):
    cfgdir = _iso(tmp_path, monkeypatch)
    _write(cfgdir / "commands", "x.md", "\nSummarize this.\nMore.\n")
    (name, desc, _) = cc.list_custom_commands()[0]
    assert (name, desc) == ("x", "Summarize this.")


def test_bad_names_and_oversize_skipped(tmp_path, monkeypatch):
    cfgdir = _iso(tmp_path, monkeypatch)
    d = cfgdir / "commands"
    _write(d, "Bad Name.md", "x")
    _write(d, ".hidden.md", "x")
    _write(d, "big.md", "y" * (cc.MAX_BODY + 1))
    _write(d, "ok.md", "fine")
    assert [n for n, _, _ in cc.list_custom_commands()] == ["ok"]
    assert cc.render_custom_command("bad name", "") is None


def test_missing_dirs_empty(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    assert cc.list_custom_commands() == []
    assert cc.custom_warnings() == []


def test_project_beats_global(tmp_path, monkeypatch):
    """Project commands only load when the user opted in (closes #287)."""
    cfgdir = _iso(tmp_path, monkeypatch)
    proj = tmp_path / "proj"
    (proj / ".sidekick" / "commands").mkdir(parents=True)
    monkeypatch.chdir(proj)
    _write(cfgdir / "commands", "d.md", "global version {{args}}")
    _write(proj / ".sidekick" / "commands", "d.md", "project version {{args}}")
    # Untrusted repo: the project template is invisible, the global one loads.
    monkeypatch.delenv(cc.TRUST_REPO_ENV, raising=False)
    assert cc.render_custom_command("d", "go") == "global version go"
    # Opted in from the outer environment: project template wins on name clash.
    monkeypatch.setenv(cc.TRUST_REPO_ENV, "1")
    assert cc.render_custom_command("d", "go") == "project version go"


def test_project_command_shell_needs_trust(tmp_path, monkeypatch):
    """A repo-supplied !`cmd` must not reach sh -c without an explicit opt-in."""
    _iso(tmp_path, monkeypatch)
    proj = tmp_path / "proj"
    (proj / ".sidekick" / "commands").mkdir(parents=True)
    monkeypatch.chdir(proj)
    marker = proj / "pwned"
    _write(proj / ".sidekick" / "commands", "x.md", f"ok !`touch {marker}`")
    monkeypatch.delenv(cc.TRUST_REPO_ENV, raising=False)
    cc.render_custom_command("x", "")
    assert not marker.exists(), "untrusted repo command executed"
    monkeypatch.setenv(cc.TRUST_REPO_ENV, "1")
    cc.render_custom_command("x", "")
    assert marker.exists()


@pytest.mark.parametrize("val", ["", "0", "true", "yes", "2"])
def test_trust_repo_flag_is_exact(tmp_path, monkeypatch, val):
    _iso(tmp_path, monkeypatch)
    proj = tmp_path / "proj"
    (proj / ".sidekick" / "commands").mkdir(parents=True)
    monkeypatch.chdir(proj)
    _write(proj / ".sidekick" / "commands", "y.md", "hi")
    monkeypatch.setenv(cc.TRUST_REPO_ENV, val)
    assert cc.list_custom_commands() == []


def test_builtin_collision_excluded_and_warned(tmp_path, monkeypatch):
    cfgdir = _iso(tmp_path, monkeypatch)
    _write(cfgdir / "commands", "yolo.md", "custom yolo")
    names = [n for n, _ in slash.all_commands()]
    assert names.count("yolo") == 1
    assert any("yolo" in w for w in cc.custom_warnings())


def test_slash_dispatch_and_help(tmp_path, monkeypatch):
    c = _ctx(tmp_path, monkeypatch)
    cfgdir = tmp_path / ".sidekick"
    out = slash.handle("/planx foo", session=c["session"], cfg=c["cfg"], state=c["state"])
    assert out.handled is True and "unknown command" in out.text
    _write(cfgdir / "commands", "myplan.md", "Plan {{args}}.\n")
    out = slash.handle("/myplan auth", session=c["session"], cfg=c["cfg"], state=c["state"])
    assert out.handled is True and out.agent_prompt == "Plan auth.\n"
    assert out.text == ""
    text = slash.handle("/help", session=c["session"], cfg=c["cfg"], state=c["state"]).text
    assert "/myplan" in text


def test_help_warns_collisions(tmp_path, monkeypatch):
    c = _ctx(tmp_path, monkeypatch)
    _write(tmp_path / ".sidekick" / "commands", "clear.md", "custom clear")
    text = slash.handle("/help", session=c["session"], cfg=c["cfg"], state=c["state"]).text
    assert "shadows builtin" in text


def test_autocomplete_includes_custom(tmp_path, monkeypatch):
    from sk.tui import SidekickTUI

    _iso(tmp_path, monkeypatch)
    cfgdir = tmp_path / ".sidekick"
    _write(cfgdir / "commands", "mydeploy.md", "Deploy {{args}}.\n")
    app = SidekickTUI()
    assert any(n == "mydeploy" for n, _ in app._slash_items("myd"))
    assert any(n == "mydeploy" for n, _ in app._slash_items("mydeploy"))


def test_unknown_still_unknown(tmp_path, monkeypatch):
    c = _ctx(tmp_path, monkeypatch)
    out = slash.handle("/definitelynotarealcommand", session="s", cfg=c["cfg"], state=c["state"])
    assert out.handled is True and "unknown command" in out.text


def test_shell_brace_and_backtick(tmp_path, monkeypatch):
    cfgdir = _iso(tmp_path, monkeypatch)
    _write(cfgdir / "commands", "d.md", "A !{echo hi} B !`echo yo` C\n")
    out = cc.render_custom_command("d", "")
    assert out is not None and "hi" in out and "yo" in out
    assert "!{" not in out and "!`" not in out  # fully expanded


def test_shell_failure_inlines_error(tmp_path, monkeypatch):
    cfgdir = _iso(tmp_path, monkeypatch)
    _write(cfgdir / "commands", "f.md", "X !{exit 3} Y !{no-such-cmd-xyz-123} Z\n")
    out = cc.render_custom_command("f", "")
    assert out is not None
    assert "exit 3" in out  # non-zero exit inlined, no raise
    assert "no-such-cmd-xyz-123" in out


def test_shell_timeout_is_bounded(tmp_path, monkeypatch):
    import time

    cfgdir = _iso(tmp_path, monkeypatch)
    monkeypatch.setattr(cc, "SHELL_TIMEOUT", 0.2)
    _write(cfgdir / "commands", "s.md", "wait !{sleep 5} done\n")
    start = time.monotonic()
    out = cc.render_custom_command("s", "")
    assert time.monotonic() - start < 4  # nowhere near the 5s sleep
    assert out is not None and "timed out" in out


def test_shell_empty_and_unclosed_stay_literal(tmp_path, monkeypatch):
    cfgdir = _iso(tmp_path, monkeypatch)
    body = "a !{} b !`` c !{unclosed d @\n"
    _write(cfgdir / "commands", "e.md", body)
    assert cc.render_custom_command("e", "") == body


def test_file_hit_miss_dir_cap(tmp_path, monkeypatch):
    cfgdir = _iso(tmp_path, monkeypatch)  # chdir == tmp_path
    (tmp_path / "notes.md").write_text("hello-file")
    (tmp_path / "big.txt").write_text("z" * (cc.MAX_EXPANSION_BYTES + 100))
    _write(
        cfgdir / "commands",
        "r.md",
        "H @notes.md M @missing-nope.txt B @{big.txt} D @./ T @~\n",
    )
    out = cc.render_custom_command("r", "")
    assert out is not None
    assert "hello-file" in out
    assert "file not found: missing-nope.txt" in out
    assert "truncated at" in out and len(out) < cc.MAX_EXPANSION_BYTES + 2000
    assert "not a file" in out  # @./ and @~ are directories


def test_mixed_template_and_args_first_order(tmp_path, monkeypatch):
    cfgdir = _iso(tmp_path, monkeypatch)
    (tmp_path / "data.txt").write_text("D")
    _write(cfgdir / "commands", "m.md", "Args={{args}} S=!{echo S} F=@data.txt\n")
    out = cc.render_custom_command("m", "A1")
    assert out is not None
    assert "Args=A1" in out and "S=S" in out.replace("\n", "") and "F=D" in out
    # args are substituted before expansion, so injected forms expand too
    _write(cfgdir / "commands", "o.md", "got: {{args}}\n")
    assert "PWNED" in cc.render_custom_command("o", "!{echo PWNED}")


def test_file_content_never_reexpanded(tmp_path, monkeypatch):
    cfgdir = _iso(tmp_path, monkeypatch)
    (tmp_path / "payload.txt").write_text("!{echo FROMFILE}")
    _write(cfgdir / "commands", "n.md", "F=@payload.txt\n")
    out = cc.render_custom_command("n", "")
    assert out is not None and "!{echo FROMFILE}" in out  # inlined literally


def test_slash_dispatch_expands(tmp_path, monkeypatch):
    c = _ctx(tmp_path, monkeypatch)
    cfgdir = tmp_path / ".sidekick"
    _write(cfgdir / "commands", "st.md", "says !{echo dispatched}\n")
    out = slash.handle("/st", session="s", cfg=c["cfg"], state=c["state"])
    assert out.handled and out.agent_prompt and "dispatched" in out.agent_prompt
