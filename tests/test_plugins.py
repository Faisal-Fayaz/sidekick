"""User-defined tools tests (#49): manifests, loader, dispatch, gates."""

import pytest

import sk.plugins as plugins
import sk.skills as skills


@pytest.fixture(autouse=True)
def _iso_skills(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "SKILLS_DIR", tmp_path / "skills")
    plugins.clear_plugin_cache()
    yield
    plugins.clear_plugin_cache()


URL_MANIFEST = """---
tool: weather
description: Current weather for a city.
approval: auto
kind: url-template
url: https://wttr.in/{city}?format=3
params: city: string required
---
Docs body.
"""

SHELL_MANIFEST = """---
tool: diskcheck
description: Disk usage snapshot.
approval: auto
kind: shell-template
cmd: df -h
---
"""


def _write(pack: str, text: str = URL_MANIFEST) -> None:
    d = skills.SKILLS_DIR / pack
    d.mkdir(parents=True, exist_ok=True)
    (d / "TOOLS.md").write_text(text)


def test_parse_valid_manifests():
    spec, warning = plugins.parse_manifest(URL_MANIFEST, "t")
    assert warning is None and spec["name"] == "weather"
    assert spec["approval"] == "auto" and spec["params"]["city"]["required"] is True
    spec, _ = plugins.parse_manifest(SHELL_MANIFEST, "t")
    assert spec["approval"] == "ask"  # shell-template always gates


def test_parse_rejects():
    bad_name, w = plugins.parse_manifest(
        "---\ntool: Bad Name\nkind: url-template\nurl: https://x\n---\n"
    )
    assert bad_name is None and "bad tool name" in w
    builtin, w = plugins.parse_manifest(
        "---\ntool: shell\nkind: url-template\nurl: https://x\n---\n"
    )
    assert builtin is None and "builtin" in w
    kind, w = plugins.parse_manifest("---\ntool: w1\nkind: exec\nurl: https://x\n---\n")
    assert kind is None and "unknown kind" in w
    missing, w = plugins.parse_manifest("---\ntool: w2\nkind: url-template\n---\n")
    assert missing is None and "missing url" in w
    appr, w = plugins.parse_manifest(
        "---\ntool: w3\nkind: url-template\nurl: https://x\napproval: maybe\n---\n"
    )
    assert appr is None and "bad approval" in w
    assert plugins.parse_manifest("garbage with no frontmatter")[0] is None


def test_load_from_skills_dir_only():
    _write("weatherpack")
    (skills.SKILLS_DIR / "elsewhere").mkdir(parents=True, exist_ok=True)
    specs, warnings = plugins.load_specs()
    assert [s["name"] for s in specs] == ["weather"]
    assert warnings == []


def test_project_files_never_read(tmp_path):
    proj = tmp_path / "hostile-repo"
    proj.mkdir()
    (proj / "TOOLS.md").write_text(URL_MANIFEST)
    specs, _ = plugins.load_specs()
    assert specs == []  # only SKILLS_DIR is ever globbed


def test_per_pack_cap_and_duplicates():
    blocks = "\n---\n".join(
        f"---\ntool: tool{i:02d}\ndescription: d\nkind: url-template\nurl: https://x/{i}\n---\n"
        for i in range(12)
    )
    _write("bigpack", blocks)
    dupes = "\n---\n".join(
        f"---\ntool: tool{i:02d}\ndescription: d\nkind: url-template\nurl: https://x/{i}\n---\n"
        for i in range(4)
    )
    _write("otherpack", dupes)  # tool00-03 already taken by bigpack
    specs, warnings = plugins.load_specs()
    assert len(specs) == 10  # per-pack cap: tool00-09, rest skipped
    assert any("rest skipped" in w for w in warnings)
    assert any("duplicate" in w for w in warnings)


def test_schema_and_approval_sets():
    _write("weatherpack")
    from sk.tools import approval_tools, tools_schema

    names = [e["function"]["name"] for e in tools_schema()]
    assert "weather" in names and "shell" in names  # builtins intact
    assert "weather" not in approval_tools()  # auto: no gate
    _write("shellpack", SHELL_MANIFEST)
    plugins.clear_plugin_cache()
    assert "diskcheck" in approval_tools()  # ask: gated


def test_dispatch_url_template(monkeypatch):
    _write("weatherpack")
    seen: dict = {}

    def fake_read(url: str, max_chars: int = 6000) -> str:
        seen["url"] = url
        return "sunny"

    monkeypatch.setattr("sk.tools.web.tool_read_url", fake_read)
    from sk.tools import dispatch_tool

    assert dispatch_tool("weather", {"city": "Paris"}) == "sunny"
    assert seen["url"] == "https://wttr.in/Paris?format=3"
    assert "missing/invalid" in (dispatch_tool("weather", {}) or "")


def test_dispatch_shell_template_uses_allowlist():
    _write(
        "evilpack",
        "---\ntool: evil\nkind: shell-template\ncmd: rm {target}\nparams: target: string required\n---\n",
    )
    from sk.tools import dispatch_tool

    out = dispatch_tool("evil", {"target": "/tmp/x"})
    assert "Blocked" in out or "allowlist" in out  # real exec guard, no mock
    assert dispatch_tool("nosuchtool", {}).startswith("Error: unknown tool")


def test_ask_plugin_gated_without_approval():
    _write("shellpack", SHELL_MANIFEST)
    plugins.clear_plugin_cache()
    from sk.agent import _gated_dispatch

    out, ok = _gated_dispatch("diskcheck", {}, approve=lambda n, a: False, session="t")
    assert ok is False and "Denied" in out


def test_cache_and_clear(tmp_path):
    _write("weatherpack")
    assert plugins.plugin_names() == {"weather"}
    (skills.SKILLS_DIR / "weatherpack" / "TOOLS.md").write_text(SHELL_MANIFEST)
    assert plugins.plugin_names() == {"weather"}  # stale cache
    plugins.clear_plugin_cache()
    assert plugins.plugin_names() == {"diskcheck"}


def test_plugins_cli(tmp_path):
    from typer.testing import CliRunner

    from sk.cli import app

    _write("weatherpack")
    res = CliRunner().invoke(app, ["plugins"])
    assert res.exit_code == 0, res.output
    assert "weather" in res.output
    res = CliRunner().invoke(app, ["plugins"])
    assert "TOOLS.md" in res.output
