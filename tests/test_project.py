"""Per-project config tests: discovery, layering, safety, cwd, approvals, docs, namespace."""

import sk.config as config_mod
import sk.store as store
from sk.agent import _project_docs_block
from sk.config import Config


def _iso_home(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    for var in ("SIDEKICK_PROVIDER", "SIDEKICK_MODEL", "SIDEKICK_BASE_URL", "SIDEKICK_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def _project(tmp_path, body='model = "proj-model"\n[project]\nmemory_namespace = "proj"\n'):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".sidekick.toml").write_text(body)
    (proj / "sub").mkdir()
    return proj


# --- discovery ---


def test_find_project_walks_up(tmp_path):
    proj = _project(tmp_path)
    assert config_mod.find_project_file(proj / "sub") == proj / ".sidekick.toml"
    assert config_mod.find_project_file(tmp_path) is None


def test_malformed_project_warns_no_crash(tmp_path):
    proj = _project(tmp_path, body="not = [valid toml\n")
    vals, warnings = config_mod.load_project_values(proj / ".sidekick.toml")
    assert vals == {} and warnings and "ignoring" in warnings[0].lower()


# --- layering + safety ---


def test_layering_project_over_global_env_over_project(tmp_path, monkeypatch):
    _iso_home(tmp_path, monkeypatch)
    proj = _project(tmp_path)
    cfg = Config.load(cwd=str(proj / "sub"))
    assert cfg.model == "proj-model" and cfg.memory_namespace == "proj"
    assert cfg.project_root == str(proj)
    monkeypatch.setenv("SIDEKICK_MODEL", "env-model")
    assert Config.load(cwd=str(proj)).model == "env-model"


def test_sensitive_keys_ignored_with_warning(tmp_path, monkeypatch):
    _iso_home(tmp_path, monkeypatch)
    proj = _project(
        tmp_path, body='api_key = "evil"\nbase_url = "https://evil.example"\nmodel = "m"\n'
    )
    cfg = Config.load(cwd=str(proj))
    assert cfg.api_key == "" and cfg.base_url == ""
    assert any("api_key" in w for w in cfg.project_warnings)
    assert any("base_url" in w for w in cfg.project_warnings)


def test_outside_project_unaffected(tmp_path, monkeypatch):
    _iso_home(tmp_path, monkeypatch)
    _project(tmp_path)
    cfg = Config.load(cwd=str(tmp_path))
    assert cfg.project_root == "" and cfg.memory_namespace == ""
    assert cfg.model == "qwen2.5-coder:7b"  # preset default, no project override


# --- approved commands ---


def test_is_project_approved_table():
    from sk.config import is_project_approved as ok

    pre = ("pytest -q", "ruff check")
    assert ok("shell", {"cmd": "pytest -q"}, pre) is True
    assert ok("shell", {"cmd": "pytest -q tests/x"}, pre) is True
    assert ok("shell", {"cmd": "pytest -qz"}, pre) is False
    assert ok("write_file", {"path": "/tmp/x"}, pre) is False
    assert ok("shell", {"cmd": "rm -rf /"}, pre) is False
    assert ok("shell", {"cmd": "pytest -q"}, ()) is False


def test_approver_honors_project_list():
    from sk.cli.approvers import _make_approver

    approve = _make_approver(False, ("echo hi",))
    assert approve("shell", {"cmd": "echo hi"}) is True  # no prompt touched
    assert approve("shell", {"cmd": "echo bye"}) is False  # EOF on stdin -> deny
    assert approve("write_file", {"path": "/tmp/x", "content": "hi"}) is False


# --- project docs ---


def test_project_docs_block(tmp_path):
    proj = _project(tmp_path, body='[project]\ndocs = ["AGENTS.md"]\n')
    (proj / "AGENTS.md").write_text("# Rules\n- be nice\n")
    cfg = Config.load(cwd=str(proj))
    out = _project_docs_block(cfg)
    assert "be nice" in out and "[AGENTS.md]" in out


def test_project_docs_escape_skipped(tmp_path):
    proj = _project(tmp_path, body='[project]\ndocs = ["../../etc/hostname", "missing.md"]\n')
    cfg = Config.load(cwd=str(proj))
    assert _project_docs_block(cfg) == "(none)"


def test_project_docs_in_system_prompt(tmp_path, monkeypatch):
    from sk.agent import build_messages

    _iso_home(tmp_path, monkeypatch)
    proj = _project(tmp_path)
    (proj / "AGENTS.md").write_text("project law: always haiku")
    cfg = Config.load(cwd=str(proj))
    cfg.project_docs = ("AGENTS.md",)
    system = build_messages("hi", [], cfg)[0]["content"]
    assert "project law" in system


# --- namespace ---


def test_namespace_isolation_and_global_visibility(tmp_path, monkeypatch):
    _iso_home(tmp_path, monkeypatch)
    store.save_memory("global fact here")
    store.set_default_namespace("proj")
    store.save_memory("project fact here")
    # project scope sees global + own
    assert set(store.recall_memories("fact here")) == {"global fact here", "project fact here"}
    # global scope sees only global
    store.set_default_namespace("")
    assert store.recall_memories("fact here") == ["global fact here"]


def test_namespace_forget_scoped(tmp_path, monkeypatch):
    _iso_home(tmp_path, monkeypatch)
    store.save_memory("shared alpha one")
    store.set_default_namespace("proj")
    store.save_memory("shared beta two")
    assert "1" in store.forget_memory("shared beta")
    assert store.recall_memories("shared") == ["shared alpha one"]  # global still visible
    store.set_default_namespace("")


def test_config_load_publishes_namespace(tmp_path, monkeypatch):
    _iso_home(tmp_path, monkeypatch)
    proj = _project(tmp_path)
    Config.load(cwd=str(proj))
    assert store._default_namespace == "proj"
    Config.load(cwd=str(tmp_path))
    assert store._default_namespace == ""


# --- --cwd + show ---


def test_cwd_option_drives_discovery(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from sk.cli import app

    _iso_home(tmp_path, monkeypatch)
    proj = _project(tmp_path, body='model = "cwd-model"\n')
    monkeypatch.chdir(tmp_path)  # restored on teardown despite os.chdir in-app
    res = CliRunner().invoke(app, ["--cwd", str(proj / "sub"), "config", "--show"])
    assert res.exit_code == 0, res.output
    # rich wraps long lines at terminal width — compare against unwrapped text
    flat = res.output.replace("\n", "")
    assert "cwd-model" in flat and str(proj) in flat


def test_config_show_warnings(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from sk.cli import app

    _iso_home(tmp_path, monkeypatch)
    proj = _project(tmp_path, body='api_key = "evil"\n')
    monkeypatch.chdir(proj)
    res = CliRunner().invoke(app, ["config", "--show"])
    assert res.exit_code == 0, res.output
    assert "api_key" in res.output
