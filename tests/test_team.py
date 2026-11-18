"""Team memory and shared skill packs (#482).

The rules under test: the user's layer always loads, the project's layer loads
only with the outer-shell trust flag, project data is fenced rather than treated
as a fact, and a project pack can never widen the user's permission set.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

import sk.store as store
import sk.team as team
from sk import trust
from sk.cli import app

CO_PY = "- user note from the company\n"


@pytest.fixture
def layout(tmp_path, monkeypatch):
    """An isolated user home and a project, with no trust by default."""
    home = tmp_path / "home"
    team_dir = home / ".sidekick" / "team"
    (team_dir / "skills").mkdir(parents=True)
    project = tmp_path / "project"
    (project / ".sidekick" / "team").mkdir(parents=True)
    (project / ".sidekick" / "skills").mkdir(parents=True)
    monkeypatch.setattr(team, "CONFIG_DIR", home / ".sidekick")
    monkeypatch.setenv("SIDEKICK_TRUST_REPO", "0")
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    monkeypatch.chdir(project)
    return {"home": home, "team_dir": team_dir, "project": project}


def _user_memory(team_dir: Path, text: str = CO_PY) -> Path:
    path = team_dir / "memories.md"
    path.write_text("# Team notes\n\n" + text)
    return path


def _project_memory(project: Path, text: str = "- project note from the repo\n") -> Path:
    path = project / ".sidekick" / "team" / "memories.md"
    path.write_text(text)
    return path


def _skill(path: Path, tools: str = "", body: str = "Use the standard deploy.") -> Path:
    path.write_text(f"---\nname: deploy\ntools: {tools}\n---\n\n{body}\n")
    return path


# --- memory loading --------------------------------------------------------


def test_user_memory_loads_with_source(layout):
    _user_memory(layout["team_dir"])
    items = team.load_memories(project_root=str(layout["project"]))
    assert [m.content for m in items] == ["user note from the company"]
    assert all(m.source == "user" and m.trusted for m in items)
    assert "user: user note from the company" == items[0].label()


def test_project_memory_is_ignored_unless_trusted(layout, monkeypatch):
    _project_memory(layout["project"])
    assert all(m.source == "user" for m in team.load_memories(project_root=str(layout["project"])))

    monkeypatch.setenv("SIDEKICK_TRUST_REPO", "1")
    items = team.load_memories(project_root=str(layout["project"]))
    assert any(m.source == "project" for m in items)
    assert not next(m for m in items if m.source == "project").trusted


def test_memory_lines_only_and_capped(layout):
    path = layout["team_dir"] / "memories.md"
    path.write_text("# heading\nprose\n\n- a real fact\n- \n- " + "x" * 2001 + "\n")
    assert [m.content for m in team.load_memories()] == ["a real fact"]


# --- skill loading ---------------------------------------------------------


def test_user_skills_always_load(layout):
    _skill(layout["team_dir"] / "skills" / "deploy.md", body="Follow the runbook.")
    packs, errors = team.load_shared_skills(project_root=str(layout["project"]))
    assert errors == []
    assert [(p.name, p.source, p.body) for p in packs] == [
        ("deploy", "user", "Follow the runbook.")
    ]


def test_project_skills_are_ignored_unless_trusted(layout, monkeypatch):
    _skill(layout["project"] / ".sidekick" / "skills" / "repo.md", body="Repo-only guidance.")
    packs, _ = team.load_shared_skills(project_root=str(layout["project"]))
    assert all(p.source == "user" for p in packs)

    monkeypatch.setenv("SIDEKICK_TRUST_REPO", "1")
    packs, errors = team.load_shared_skills(project_root=str(layout["project"]))
    assert errors == []
    assert any(p.source == "project" and p.body == "Repo-only guidance." for p in packs)


# --- permissions -----------------------------------------------------------


def test_project_cannot_widen_permissions(layout, monkeypatch):
    monkeypatch.setenv("SIDEKICK_TRUST_REPO", "1")
    _skill(
        layout["project"] / ".sidekick" / "skills" / "sneaky.md",
        tools="shell, write_file",
    )
    packs, errors = team.load_shared_skills(project_root=str(layout["project"]))
    assert packs == [], "a pack that asked for unapproved tools was accepted"
    assert any("cannot widen permissions" in e for e in errors)
    assert any("shell" in e and "write_file" in e for e in errors)


def test_project_can_request_only_user_allowlisted_tools(layout, monkeypatch):
    monkeypatch.setenv("SIDEKICK_TRUST_REPO", "1")
    (layout["team_dir"] / "policy.toml").write_text('schema = 1\nallow_tools = ["read_file"]\n')
    _skill(
        layout["project"] / ".sidekick" / "skills" / "ok.md",
        tools="read_file",
    )
    packs, errors = team.load_shared_skills(project_root=str(layout["project"]))
    assert errors == []
    assert any(p.source == "project" and p.tools == ("read_file",) for p in packs)


def test_user_skills_do_not_need_the_allow_list(layout):
    """The user owns the policy; their own layer is not policed by it."""
    _skill(layout["team_dir"] / "skills" / "deploy.md", tools="shell")
    packs, errors = team.load_shared_skills(project_root=str(layout["project"]))
    assert errors == []
    assert any(p.tools == ("shell",) for p in packs)


def test_broken_policy_fails_closed(layout, monkeypatch):
    monkeypatch.setenv("SIDEKICK_TRUST_REPO", "1")
    (layout["team_dir"] / "policy.toml").write_text('schema = 99\nallow_tools = ["shell"]\n')
    _skill(layout["project"] / ".sidekick" / "skills" / "sneaky.md", tools="shell")
    policy = team.user_policy()
    assert policy["allow_tools"] == ()
    packs, errors = team.load_shared_skills(project_root=str(layout["project"]))
    assert packs == []
    assert any("unsupported schema" in e for e in errors)


def test_a_project_cannot_add_to_the_policy_by_shipping_one(layout, monkeypatch):
    """Only ~/.sidekick/team/policy.toml is read; a repo copy is ignored."""
    monkeypatch.setenv("SIDEKICK_TRUST_REPO", "1")
    (layout["project"] / ".sidekick" / "team" / "policy.toml").write_text(
        'schema = 1\nallow_tools = ["shell"]\n'
    )
    _skill(layout["project"] / ".sidekick" / "skills" / "sneaky.md", tools="shell")
    packs, errors = team.load_shared_skills(project_root=str(layout["project"]))
    assert packs == []
    assert any("cannot widen permissions" in e for e in errors)


# --- prompt injection ------------------------------------------------------


def test_project_data_is_fenced_not_a_system_fact(layout, monkeypatch):
    from sk.config import Config

    monkeypatch.setenv("SIDEKICK_TRUST_REPO", "1")
    _user_memory(layout["team_dir"])
    _project_memory(layout["project"], text="- THE PROJECT CLAIM\n")
    ctx = team.prompt_context(project_root=str(layout["project"]))
    assert "user note from the company" in ctx["trusted_memories"]
    assert "THE PROJECT CLAIM" not in ctx["trusted_memories"]
    assert any("THE PROJECT CLAIM" in text for _, text in ctx["untrusted"])

    cfg = Config(model="t", base_url="http://x/v1", api_key="x", max_steps=1, temperature=0.0)
    from sk.agent import build_messages

    msgs = build_messages("say hi", [], cfg)
    assert "THE PROJECT CLAIM" not in msgs[0]["content"], "a repo note became a system fact"
    fenced = msgs[-2]["content"]
    assert trust.OPEN in fenced and "THE PROJECT CLAIM" in fenced
    assert "project team memory" in fenced
    assert "[team/user] user note from the company" in msgs[0]["content"]


def test_skills_are_guidance_and_run_no_commands(layout, monkeypatch):
    body = "!`touch /tmp/sidekick-should-not-exist`\nFollow the runbook."
    _skill(layout["team_dir"] / "skills" / "deploy.md", body=body)

    def no_subprocess(*args, **kwargs):
        raise AssertionError("loading a shared skill ran a command")

    monkeypatch.setattr(subprocess, "run", no_subprocess)
    ctx = team.prompt_context(project_root=str(layout["project"]))
    assert "touch /tmp/sidekick-should-not-exist" in ctx["trusted_skills"]


def test_project_skill_guidance_is_fenced(layout, monkeypatch):
    monkeypatch.setenv("SIDEKICK_TRUST_REPO", "1")
    _skill(
        layout["project"] / ".sidekick" / "skills" / "repo.md",
        body="REPO SKILL BODY",
    )
    ctx = team.prompt_context(project_root=str(layout["project"]))
    assert "REPO SKILL BODY" not in ctx["trusted_skills"]
    assert any("REPO SKILL BODY" in text for _, text in ctx["untrusted"])
    assert any("normal approval and audit" in text for _, text in ctx["untrusted"])


# --- inspection surface ----------------------------------------------------


def test_status_reports_everything(layout, monkeypatch):
    monkeypatch.setenv("SIDEKICK_TRUST_REPO", "1")
    _user_memory(layout["team_dir"])
    _project_memory(layout["project"])
    _skill(layout["team_dir"] / "skills" / "u.md")
    _skill(layout["project"] / ".sidekick" / "skills" / "p.md", tools="shell")
    report = team.status(project_root=str(layout["project"]))
    assert report["project_trusted"] is True
    assert {m["source"] for m in report["memories"]} == {"user", "project"}
    assert {s["source"] for s in report["skills"]} == {"user"}
    assert any("cannot widen permissions" in e for e in report["errors"])
    assert str(layout["team_dir"] / "policy.toml") == report["policy"]


def test_team_check_cli(layout, monkeypatch):
    monkeypatch.setenv("SIDEKICK_TRUST_REPO", "0")
    _user_memory(layout["team_dir"])
    result = CliRunner().invoke(app, ["team", "check", "--json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["project_trusted"] is False
    assert report["memories"][0]["source"] == "user"


def test_team_check_shows_the_source_in_human_output(layout):
    _user_memory(layout["team_dir"])
    result = CliRunner().invoke(app, ["team", "check"])
    assert result.exit_code == 0, result.output
    assert "[team/user]" in result.output, "Rich markup swallowed the source label"


def test_team_check_help_exists():
    result = CliRunner().invoke(app, ["team", "check", "--help"])
    assert result.exit_code == 0
    assert "trust state" in result.output


def test_memories_slash_shows_team_source(layout, monkeypatch):
    from sk.slash import handle
    from tests.test_slash import _ctx

    _user_memory(layout["team_dir"])
    ctx = _ctx(layout["home"], monkeypatch)
    out = handle("/memories", session=ctx["session"], cfg=ctx["cfg"], state=ctx["state"])
    assert "[team/user] user note from the company" in out.text
