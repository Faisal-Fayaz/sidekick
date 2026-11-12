"""Golden files for the turns people actually see (#112).

Each golden is a committed file with the exact output of one turn. The rule
for adding one is in ``tests/golden/README.md``; the manifest in
``tests/golden/manifest.json`` says what each one locks.

These run offline and deterministically: no model, no network, no clock. A
volatile value (a date, a path, the machine's facts) is masked before it is
written, so a golden does not rot every night.
"""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path

import pytest

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
MANIFEST = json.loads((GOLDEN_DIR / "manifest.json").read_text())
GOLDENS = {entry["id"]: entry for entry in MANIFEST["goldens"]}


# --- masking ---------------------------------------------------------------
#
# A golden is only a lock if it is stable. Anything that changes on its own
# (the date, the temporary directory, the machine's CPU) is replaced with a
# placeholder before it is written, so the file records the shape and not the
# weather.

_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_WEEKDAY = re.compile(r"\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b")
_TIME = re.compile(r"\b\d{2}:\d{2}(:\d{2})?\b")


def _mask(text: str, tmp_path: Path) -> str:
    """Replace volatile values with stable placeholders."""
    out = str(text or "")
    out = out.replace(str(tmp_path), "<TMP>")
    out = out.replace(str(Path.home()), "<HOME>")
    out = _DATE.sub("<DATE>", out)
    out = _WEEKDAY.sub("<WEEKDAY>", out)
    out = _TIME.sub("<TIME>", out)
    # macOS resolves /etc, /var and /tmp through /private. The behaviour is the
    # same on both platforms; only the spelling of the path differs.
    out = re.sub(r"/private/(etc|var|tmp)\b", r"/\1", out)
    return out


# --- producers -------------------------------------------------------------
#
# One per manifest entry. Each returns the text the golden file holds.


def _cfg():
    from sk.config import Config

    return Config(
        provider="ollama",
        model="qwen3:4b",
        base_url="http://x/v1",
        api_key="x",
        max_steps=5,
        temperature=0.0,
    )


def _golden_help(tmp_path, monkeypatch) -> str:
    """The /help reply — and that every spelling of it gives the same answer."""
    from sk.slash import handle

    monkeypatch.chdir(tmp_path)  # no project .sidekick/commands leak in
    texts = []
    for prompt in ("/help", "/h", "/"):
        out = handle(prompt, session="s", cfg=_cfg(), state={})
        assert out.handled is True and not out.agent_prompt, prompt
        texts.append(out.text)
    assert len(set(texts)) == 1, "the /help spellings disagree"
    return texts[0]


def _golden_grounding(tmp_path, monkeypatch) -> str:
    """The grounding block the agent is told about the machine it is on."""
    import sk.agent as agent

    monkeypatch.chdir(tmp_path)
    # The real facts are the machine's; the golden locks their shape.
    monkeypatch.setattr(
        agent,
        "tool_sysinfo",
        lambda: "CPU: test-cpu\nMem: 1.0Gi / 2.0Gi\nGPU: none",
    )
    (tmp_path / "README.md").write_text("# project\n")
    messages = agent.build_messages("what is in this repo?", [], _cfg())
    system = str(next((m for m in messages if m.get("role") == "system"), {}).get("content", ""))
    # Only the environment block, not the whole system prompt: the rules are
    # already locked rule-by-rule in tests/test_eval.py, and a golden that
    # repeats them is noise.
    start = system.index("- Current working directory:")
    # The block header, not the rules that mention it: the MEMORY rule says
    # "SAVED MEMORIES below" and comes earlier in the prompt.
    end = system.index("SAVED MEMORIES (use these")
    block = system[start:end].strip()
    # The OS and the kernel string are the machine's, not the behaviour's.
    block = re.sub(r"OS: .*?\. Platform: .*", "OS: <OS>. Platform: <PLATFORM>", block)
    return _mask(block, tmp_path)


def _golden_refusal(tmp_path, monkeypatch) -> str:
    """The two refusals a user can see."""
    from sk.sandbox import check_tool
    from sk.tools.shell import irreversible_refusal

    import sk.config as config_mod

    config_dir = tmp_path / ".sidekick"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "sandbox.toml").write_text(
        'schema = 1\nmode = "enforced"\n[filesystem]\nread_roots = []\n'
    )
    monkeypatch.setattr(config_mod, "CONFIG_DIR", config_dir)
    monkeypatch.chdir(tmp_path)

    import sk.sandbox as sandbox

    sandbox.reset_cache()

    hard = irreversible_refusal("shell", {"cmd": "rm -rf /"}) or ""
    denied = check_tool("shell", {"cmd": "cat /etc/passwd"}) or ""
    return _mask(hard + "\n" + denied, tmp_path)


def _golden_tool_call_fallback(tmp_path, monkeypatch) -> str:
    """The text-JSON fallback: what the agent does with markup it cannot parse."""
    from sk.toolcalls import parse_text_tools, strip_tool_residue, tool_residue

    monkeypatch.chdir(tmp_path)
    # The captured residue from a real session (tests/test_tool_residue.py),
    # and the JSON block a model emits when it cannot use native tools.
    residue = '<invoke name="read_file">{"path": "src/app.py"}</invoke>'
    json_block = '```json {"name": "read_file", "arguments": {"path": "src/app.py"}} ```'
    lines = [
        f"residue detected: {tool_residue(residue)}",
        f"prose kept: {strip_tool_residue(residue)!r}",
        f"residue parsed as calls: {parse_text_tools(residue)}",
        f"json block parsed as calls: {parse_text_tools(json_block)}",
    ]
    return "\n".join(lines)


def _golden_review_prompt(tmp_path, monkeypatch) -> str:
    """The prompt the review engine sends for a diff."""
    from sk.review.diff import DiffModel, FileDiff
    from sk.review.engine import _review_prompt

    monkeypatch.chdir(tmp_path)
    diff = DiffModel(
        target="worktree",
        repo_root=str(tmp_path),
        ref="HEAD",
        changed_lines=1,
        files=(FileDiff(path="app.py", additions=1),),
    )
    chunk = "@@ -1 +1 @@\n-x = 1\n+x = 2"
    return _review_prompt(diff, chunk, 1, 2, rules_text="severity: major")


PRODUCERS = {
    "help": _golden_help,
    "grounding": _golden_grounding,
    "refusal": _golden_refusal,
    "tool_call_fallback": _golden_tool_call_fallback,
    "review_prompt": _golden_review_prompt,
}


# --- the suite -------------------------------------------------------------


def test_mask_normalizes_the_platform_spellings():
    """The goldens run on Linux and macOS in CI, so the two spellings of the
    same path have to become the same text."""
    from pathlib import Path as _Path

    out = _mask(
        "sandbox: read denied — /private/etc/passwd is outside the roots (project=/tmp/x)",
        _Path("/tmp/x"),
    )
    assert "/etc/passwd" in out
    assert "/private/" not in out
    assert "<TMP>" in out


def test_manifest_and_producers_agree():
    """A manifest entry without a producer never runs; a producer without an
    entry never runs either. Both are silent failures otherwise."""
    assert set(PRODUCERS) == set(GOLDENS), (
        f"manifest/producer mismatch: only in manifest {sorted(set(GOLDENS) - set(PRODUCERS))}, "
        f"only in producers {sorted(set(PRODUCERS) - set(GOLDENS))}"
    )


def test_every_golden_names_what_it_locks():
    """A golden that does not say what it locks is snapshot noise."""
    for entry in GOLDENS.values():
        assert entry.get("locks", "").strip(), f"{entry['id']} does not say what it locks"
        assert entry.get("file", "").strip(), f"{entry['id']} has no file"
        assert entry.get("issue", "").strip(), f"{entry['id']} does not name its issue"


@pytest.mark.parametrize("golden_id", sorted(GOLDENS))
def test_golden(golden_id, tmp_path, monkeypatch, request):
    entry = GOLDENS[golden_id]
    path = GOLDEN_DIR / entry["file"]
    produced = PRODUCERS[golden_id](tmp_path, monkeypatch)
    produced = produced if produced.endswith("\n") else produced + "\n"
    if request.config.getoption("--update-goldens"):
        path.write_text(produced)
        return
    assert path.exists(), (
        f"the golden file {path.name} is missing — create it with "
        f"`pytest tests/test_golden.py --update-goldens`"
    )
    expected = path.read_text()
    if produced != expected:
        diff = "\n".join(
            difflib.unified_diff(
                expected.splitlines(),
                produced.splitlines(),
                fromfile=f"golden/{path.name}",
                tofile="current behaviour",
                lineterm="",
            )
        )
        pytest.fail(
            f"the {golden_id} golden no longer matches ({entry['locks']}).\n"
            f"If the change is intended, update it in the same commit:\n"
            f"    pytest tests/test_golden.py --update-goldens\n\n{diff}"
        )


def test_golden_files_are_small():
    """A golden that grew huge is a snapshot of the wrong thing."""
    for entry in GOLDENS.values():
        path = GOLDEN_DIR / entry["file"]
        if not path.exists():
            continue
        lines = len(path.read_text().splitlines())
        assert lines <= 400, f"{path.name} is {lines} lines — too big to be a golden"
