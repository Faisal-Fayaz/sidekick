"""#400: repo_map tree, languages, symbols, guards, and explicit caps."""

from __future__ import annotations

from pathlib import Path

from sk.cli.approvers import _make_approver
from sk.tokens import estimate_tokens
from sk.tools import (
    APPROVAL_TOOLS,
    PLAN_DENIED_TOOLS,
    READONLY_DENIED_TOOLS,
    TOOLS_SCHEMA,
    dispatch_tool,
)
from sk.tools import repo_map as repo_mod
from sk.tools import read as read_mod
from sk.tools.repo_map import tool_repo_map


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_repo_map_lists_tree_languages_sizes_and_symbols(tmp_path):
    repo = tmp_path / "repo"
    _write(
        repo,
        "src/app.py",
        "class App:\n    def run(self):\n        return 1\n\nasync def main():\n    return 2\n",
    )
    _write(
        repo,
        "web/app.ts",
        "export class Widget {}\nexport function greet() {}\nconst arrow = () => 1;\n",
    )
    _write(repo, "go/main.go", "package main\nfunc main() {}\ntype Config struct {}\n")
    _write(repo, "README.md", "# repo\n")

    out = tool_repo_map(str(repo), max_tokens=2000)

    assert f"{repo.resolve()}: 4 files" in out
    assert "languages:" in out
    assert "Python" in out
    assert "TypeScript" in out
    assert "Go" in out
    assert "Markdown" in out
    assert "src/app.py" in out
    assert "web/app.ts" in out
    assert "README.md" in out
    assert " B" in out
    assert "class App" in out
    assert "def run" in out
    assert "async def main" in out
    assert "class Widget" in out
    assert "function greet" in out
    assert "function arrow" in out
    assert "func main" in out
    assert "type Config" in out
    assert out == tool_repo_map(str(repo), max_tokens=2000)


def test_repo_map_extracts_common_non_python_definitions(tmp_path):
    _write(
        tmp_path,
        "src/main.rs",
        "pub struct App;\npub fn build() {}\n",
    )
    _write(
        tmp_path,
        "src/main.rb",
        "class Runner\n  def call\n  end\nend\n",
    )
    _write(tmp_path, "src/task.sh", "task_run() {\n  echo hi\n}\n")
    _write(
        tmp_path,
        "src/Main.java",
        "public class Main {\n  public static void main(String[] a) {}\n}\n",
    )

    out = tool_repo_map(str(tmp_path), max_tokens=2000)

    assert "struct App" in out
    assert "fn build" in out
    assert "class Runner" in out
    assert "def call" in out
    assert "function task_run" in out
    assert "class Main" in out
    assert "function main" in out


def test_repo_map_skips_ignored_hidden_vendor_and_symlinks(tmp_path):
    repo = tmp_path / "repo"
    _write(repo, "keep.py", "def kept():\n    pass\n")
    _write(repo, "ignored.txt", "ignored\n")
    _write(repo, "node_modules/pkg/index.js", "function vendor() {}\n")
    _write(repo, "build/generated.py", "def generated():\n    pass\n")
    _write(repo, "venv/lib/site.py", "def virtual():\n    pass\n")
    _write(repo, "dist/bundle.js", "function bundle() {}\n")
    _write(repo, ".git/config", "secret\n")
    _write(repo, ".hidden/secret.py", "def hidden():\n    pass\n")
    (repo / ".gitignore").write_text("ignored.txt\n")
    outside = tmp_path / "outside.py"
    outside.write_text("def outside():\n    pass\n")
    (repo / "link.py").symlink_to(outside)

    out = tool_repo_map(str(repo), max_tokens=2000)

    assert "keep.py" in out
    assert "def kept" in out
    for hidden in (
        "ignored.txt",
        "node_modules",
        "generated.py",
        "build/",
        "venv",
        "dist",
        ".git",
        ".hidden",
        "link.py",
        "outside.py",
    ):
        assert hidden not in out


def test_repo_map_handles_binary_and_invalid_python(tmp_path):
    _write(tmp_path, "broken.py", "def broken(:\n")
    (tmp_path / "blob.py").write_bytes(b"class Hidden:\x00\n")

    out = tool_repo_map(str(tmp_path), max_tokens=2000)

    assert "broken.py" in out
    assert "blob.py" in out
    assert "class Hidden" not in out
    assert not out.startswith("Error:")


def test_repo_map_empty_repo(tmp_path):
    out = tool_repo_map(str(tmp_path))
    assert "0 files" in out
    assert "languages: (none)" in out
    assert "(empty)" in out


def test_repo_map_token_cap_is_explicit_and_fits(tmp_path):
    for index in range(20):
        _write(tmp_path, f"f{index:02}.py", f"class C{index}:\n    pass\n")

    out = tool_repo_map(str(tmp_path), max_tokens=200)

    assert "truncated" in out
    assert "token cap 200" in out
    assert "showing" in out
    assert estimate_tokens(out) <= 200


def test_repo_map_node_and_scan_caps_are_explicit(tmp_path, monkeypatch):
    for index in range(5):
        _write(tmp_path, f"f{index}.py", f"class C{index}:\n    pass\n")

    monkeypatch.setattr(repo_mod, "MAX_REPO_MAP_NODES", 2)
    out = tool_repo_map(str(tmp_path), max_tokens=8000)
    assert "node cap 2" in out
    assert "showing 2 of 5 files" in out
    assert "f3.py" not in out

    monkeypatch.setattr(repo_mod, "MAX_REPO_MAP_NODES", 500)
    monkeypatch.setattr(repo_mod, "MAX_REPO_MAP_FILES", 3)
    out = tool_repo_map(str(tmp_path), max_tokens=8000)
    assert "scan cap 3" in out
    assert "showing 3 of 3 scanned files" in out


def test_repo_map_uses_read_guards(tmp_path, monkeypatch):
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    _write(blocked, "secret.py", "class Secret:\n    pass\n")
    monkeypatch.setattr(read_mod, "_read_block_targets", lambda: [blocked.resolve()])

    out = tool_repo_map(str(blocked))
    assert "blocked" in out.lower()
    assert "secret" not in out

    repo = tmp_path / "repo"
    _write(repo, "keep.py", "class Keep:\n    pass\n")
    _write(repo, "blocked/secret.py", "class Secret:\n    pass\n")
    monkeypatch.setattr(read_mod, "_read_block_targets", lambda: [(repo / "blocked").resolve()])
    out = tool_repo_map(str(repo))
    assert "Keep" in out
    assert "secret" not in out


def test_repo_map_single_file_and_errors(tmp_path):
    source = _write(tmp_path, "single.py", "class Single:\n    pass\n")
    out = tool_repo_map(str(source))
    assert "1 file" in out
    assert "single.py" in out
    assert "class Single" in out

    missing = tmp_path / "missing"
    assert "does not exist" in tool_repo_map(str(missing))
    assert "max_tokens" in tool_repo_map(str(tmp_path), max_tokens="nope")
    assert "max_tokens" in tool_repo_map(str(tmp_path), max_tokens=99)


def test_repo_map_self_fits_a_small_budget():
    root = Path(__file__).resolve().parents[1]
    out = tool_repo_map(str(root), max_tokens=1500)
    assert estimate_tokens(out) <= 1500
    assert "src/sk/" in out
    assert "class " in out or "def " in out


def test_repo_map_schema_dispatch_and_approval_free(tmp_path):
    names = {entry["function"]["name"] for entry in TOOLS_SCHEMA}
    assert "repo_map" in names
    assert len(names) == 27
    assert "repo_map" not in APPROVAL_TOOLS
    assert "repo_map" not in PLAN_DENIED_TOOLS
    assert "repo_map" not in READONLY_DENIED_TOOLS

    _write(tmp_path, "app.py", "class App:\n    pass\n")
    out = dispatch_tool("repo_map", {"path": str(tmp_path), "max_tokens": 500})
    assert "class App" in out
    approve = _make_approver(False)
    assert approve("repo_map", {"path": str(tmp_path)}) is True
