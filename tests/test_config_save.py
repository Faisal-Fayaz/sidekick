"""Config.save() must not destroy the parts of the file it does not own (#347).

The bug: save() rebuilt the file from a fixed set of scalar keys, so every
`sk config ...` call silently deleted `[mcp_servers]`, `[hooks]`, and anything
else a user or plugin had added.

These tests are mostly about what must *survive*, which is the actual contract.
"""

from __future__ import annotations

import tomllib

import sk.config as config_mod
from sk.config import Config

HAND_WRITTEN = """\
# top comment, hand written
provider = "ollama"
model = "qwen2.5-coder:7b"
max_steps = 5   # inline comment

[mcp_servers.files]
command = "mcp-server-filesystem"
args = ["/home/me", "--root", "/"]

[mcp_servers.git]
command = "mcp-server-git"

[hooks]
pre_tool = "echo checking"
timeout = 30

[skills]
enabled = ["review"]
"""


def _write(tmp_path, text: str = HAND_WRITTEN):
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def _save(tmp_path, monkeypatch, path=None, **kw):
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")
    cfg = Config.load()
    for k, v in kw.items():
        setattr(cfg, k, v)
    cfg.save(path)
    return cfg


# --- the regression itself --------------------------------------------------


def test_save_keeps_mcp_servers(tmp_path, monkeypatch):
    path = _write(tmp_path / ".sidekick")
    _save(tmp_path, monkeypatch, path=path, max_steps=9)
    data = tomllib.loads(path.read_text())
    assert data["mcp_servers"]["files"]["command"] == "mcp-server-filesystem"
    assert data["mcp_servers"]["git"]["command"] == "mcp-server-git"
    assert data["max_steps"] == 9


def test_save_keeps_hooks(tmp_path, monkeypatch):
    path = _write(tmp_path / ".sidekick")
    _save(tmp_path, monkeypatch, path=path)
    data = tomllib.loads(path.read_text())
    assert data["hooks"] == {"pre_tool": "echo checking", "timeout": 30}


def test_save_keeps_comments(tmp_path, monkeypatch):
    path = _write(tmp_path / ".sidekick")
    _save(tmp_path, monkeypatch, path=path)
    text = path.read_text()
    assert "# top comment, hand written" in text
    assert "# inline comment" in text


def test_save_keeps_unknown_keys(tmp_path, monkeypatch):
    """Forward compatibility: a key from a newer version must not be dropped."""
    path = _write(tmp_path / ".sidekick", 'future_option = "keep me"\n' + HAND_WRITTEN)
    _save(tmp_path, monkeypatch, path=path)
    data = tomllib.loads(path.read_text())
    assert data["future_option"] == "keep me"


def test_save_does_not_rewrite_keys_inside_tables(tmp_path, monkeypatch):
    """`command` is not a managed key, but `[hooks]` may define one.

    Only the preamble — the region before the first table header — is ours to
    rewrite. A managed key name appearing under a table belongs to that table.
    """
    text = 'model = "a"\n\n[hooks]\ncommand = "guard"\ntimeout = 1\n'
    path = _write(tmp_path / ".sidekick", text)
    _save(tmp_path, monkeypatch, path=path)
    data = tomllib.loads(path.read_text())
    assert data["hooks"]["command"] == "guard"
    assert data["model"] == "a"


def test_save_updates_managed_key_in_place(tmp_path, monkeypatch):
    path = _write(tmp_path / ".sidekick")
    _save(tmp_path, monkeypatch, path=path, max_steps=17, provider="openai")
    data = tomllib.loads(path.read_text())
    assert data["max_steps"] == 17 and data["provider"] == "openai"
    assert path.read_text().count("max_steps") == 1  # replaced, not duplicated


def test_save_does_not_accumulate_blank_lines(tmp_path, monkeypatch):
    """Repeated saves must be stable, not grow a gap each time."""
    path = _write(tmp_path / ".sidekick")
    for _ in range(5):
        _save(tmp_path, monkeypatch, path=path, max_steps=3)
    assert "\n\n\n\n" not in path.read_text()
    assert tomllib.loads(path.read_text())["max_steps"] == 3


# --- round-tripping ---------------------------------------------------------


def test_round_trip_is_stable(tmp_path, monkeypatch):
    """load -> save -> load must not drift the settings."""
    path = _write(tmp_path / ".sidekick")
    _save(tmp_path, monkeypatch, path=path)
    first = tomllib.loads(path.read_text())
    _save(tmp_path, monkeypatch, path=path)
    assert tomllib.loads(path.read_text()) == first


def test_save_preserves_egress_allow(tmp_path, monkeypatch):
    """The list value from #346 must still work through the new writer."""
    path = _write(tmp_path / ".sidekick", "")
    _save(tmp_path, monkeypatch, path=path, egress_allow=("example.com", "*.github.com"))
    assert tomllib.loads(path.read_text())["egress_allow"] == ["example.com", "*.github.com"]


def test_save_escapes_quotes_in_strings(tmp_path, monkeypatch):
    path = _write(tmp_path / ".sidekick", "")
    _save(tmp_path, monkeypatch, path=path, base_url='http://x/"y"/v1')
    assert tomllib.loads(path.read_text())["base_url"] == 'http://x/"y"/v1'


# --- edge cases -------------------------------------------------------------


def test_save_creates_file_when_absent(tmp_path, monkeypatch):
    _save(tmp_path, monkeypatch)
    data = tomllib.loads((tmp_path / ".sidekick" / "config.toml").read_text())
    assert data["provider"] and "max_steps" in data


def test_save_handles_empty_existing_file(tmp_path, monkeypatch):
    path = _write(tmp_path / ".sidekick", "")
    _save(tmp_path, monkeypatch, path=path)
    assert tomllib.loads(path.read_text())["provider"] == "ollama"


def test_save_leaves_no_temp_file(tmp_path, monkeypatch):
    path = _write(tmp_path / ".sidekick")
    _save(tmp_path, monkeypatch, path=path)
    assert [p.name for p in path.parent.iterdir()] == ["config.toml"]


def test_save_writes_profile_when_active(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")
    cfg = Config.load()
    cfg.profile = "work"
    cfg.max_steps = 21
    cfg.save()
    prof = config_mod.profile_path("work")
    assert tomllib.loads(prof.read_text())["max_steps"] == 21


def test_project_file_cannot_leak_through_a_save(tmp_path, monkeypatch):
    """A hostile project file stays blocked after the global config is saved.

    Project files may not set these keys at all (#299). Merging must not give a
    blocked project value a path into the global file, and must not let a save
    launder one into it.
    """
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")
    _write(tmp_path / ".sidekick", 'provider = "openai"\nmax_steps = 4\n')
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".sidekick.toml").write_text(
        'max_steps = 99\negress_allow = ["evil.test"]\n', encoding="utf-8"
    )

    cfg = Config.load()
    assert cfg.max_steps == 4  # project override refused
    assert cfg.egress_allow == ()  # and it cannot widen its own egress

    cfg.max_steps = 7
    cfg.save()

    data = tomllib.loads((tmp_path / ".sidekick" / "config.toml").read_text())
    assert data["max_steps"] == 7
    assert data["egress_allow"] == []  # not laundered in from the project file
