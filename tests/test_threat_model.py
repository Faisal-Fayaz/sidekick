"""The threat model must stay traceable to the code and tests it cites (#500)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "threat-model.md"

# label as written in the document → module that implements it, test that pins it
CONTROLS: dict[str, tuple[str, str]] = {
    "Approval gate": ("src/sk/tools/registry.py", "tests/test_security.py"),
    "Hard refusals": ("src/sk/tools/shell.py", "tests/test_security.py"),
    "OS sandbox": ("src/sk/sandbox_os.py", "tests/test_sandbox.py"),
    "Two gates, every hop": ("src/sk/tools/web.py", "tests/test_web.py"),
    "Deny by default": ("src/sk/egress.py", "tests/test_egress.py"),
    "Fences with provenance": ("src/sk/trust.py", "tests/test_trust.py"),
    "Taint reaches the gate": ("src/sk/taint.py", "tests/test_taint.py"),
    "Repo trust gate": ("src/sk/custom_commands.py", "tests/test_team.py"),
    "Permissions live in data": ("src/sk/team.py", "tests/test_team.py"),
    "Token always": ("src/sk/serve.py", "tests/test_sdk.py"),
    "Loopback only": ("src/sk/serve.py", "tests/test_sdk.py"),
    "One ledger": ("src/sk/store.py", "tests/test_sdk.py"),
    "Read-only mode": ("src/sk/cli/approvers.py", "tests/test_security.py"),
    "Proposals never write": ("src/sk/memory_rank.py", "tests/test_memory_rank.py"),
    "Crash telemetry is off by default": ("src/sk/telemetry.py", "tests/test_telemetry.py"),
    "self-contained offline dashboard": ("src/sk/enterprise.py", "tests/test_enterprise.py"),
    "removes the temp directory on a mismatch": (
        "src/sk/registry_trust.py",
        "tests/test_registry_trust.py",
    ),
    "The IDE client": ("ide/vscode/src/client.ts", "ide/vscode/src/test/client.test.ts"),
}


def test_the_document_exists_with_its_required_sections():
    text = DOC.read_text()
    for heading in (
        "## Assets",
        "## Trust boundaries",
        "### A1",
        "### A2",
        "### A3",
        "### A4",
        "### A5",
        "### A6",
        "## Local enterprise controls",
        "## Registry supply chain",
        "## Optional data egress",
        "## Audit probes",
        "## Out of scope",
        "## Maintenance",
    ):
        assert heading in text, f"missing section {heading}"


def test_every_named_control_has_real_code_and_a_test():
    text = DOC.read_text()
    assert text, "empty threat model"
    for label, (module, test) in CONTROLS.items():
        assert label in text, f"{label!r} is no longer named in the threat model"
        assert (ROOT / module).is_file(), f"{label}: missing module {module}"
        assert (ROOT / test).is_file(), f"{label}: missing test {test}"


def test_it_names_adversaries_and_residual_risks():
    text = DOC.read_text().lower()
    for phrase in (
        "malicious cloned repository",
        "prompt injection",
        "browser page",
        "dns rebinding",
        "raw tcp",
        "residual",
    ):
        assert phrase in text, f"missing adversary or residual risk: {phrase}"


def test_no_placeholders_are_left():
    text = DOC.read_text().lower()
    for placeholder in ("todo", "tbd", "fixme", "to be written"):
        assert placeholder not in text, f"placeholder {placeholder!r} left in the threat model"
