"""Layered review packs: precedence, trust, validation, filtering, and wiring."""

from __future__ import annotations

import textwrap

import pytest
from typer.testing import CliRunner

import sk.config as config_mod
from sk.cli import app
from sk.config import Config
from sk.review.diff import DiffModel, FileDiff, Hunk, SkipReason
from sk.review.engine import ReviewError, ReviewOptions, review
from sk.review.pack import (
    BUILTIN_RULES,
    filter_diff,
    glob_match,
    load_review_pack,
    rule_applies,
)


def _write(path, body: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).lstrip())
    return path


def _user_pack(body: str):
    return _write(config_mod.CONFIG_DIR / "review.toml", body)


def _cfg():
    return Config(provider="ollama", model="fake", base_url="http://x/v1", api_key="x")


def _hunk(line: str = '+y = eval("1")') -> Hunk:
    return Hunk(1, 1, 1, 2, "@@ -1 +1,2 @@", (" x = 1", line))


def _diff(repo, *paths: str) -> DiffModel:
    files = tuple(
        FileDiff(path=path, additions=1, hunks=(_hunk(),)) for path in (paths or ("app.py",))
    )
    return DiffModel(
        target="worktree",
        repo_root=str(repo),
        ref="HEAD",
        files=files,
        changed_lines=len(files),
    )


def _completer(payload: str = '{"findings":[]}'):
    def call(cfg, *, system, user, max_tokens=4096):
        return payload

    return call


def test_default_pack_is_stable_and_uses_builtin_rules(tmp_path):
    missing = tmp_path / "missing.toml"
    pack = load_review_pack(
        cwd=tmp_path,
        user_path=missing,
        project_path=tmp_path / "also-missing.toml",
    )
    assert pack.errors == ()
    assert pack.max_findings == 25
    assert pack.context_lines == 3
    assert pack.max_chars == 12_000
    assert pack.max_files == 200
    assert pack.max_diff_bytes == 400_000
    assert pack.fail_on == "never"
    assert pack.assess == "auto"
    assert {r.id for r in pack.rules} == {r.id for r in BUILTIN_RULES}
    assert [layer.name for layer in pack.layers] == ["built-in", "user", "project"]
    assert pack.hash
    again = load_review_pack(
        cwd=tmp_path,
        user_path=missing,
        project_path=tmp_path / "also-missing.toml",
    )
    assert again.hash == pack.hash


def test_layers_merge_scalars_lists_and_rules(tmp_path):
    user = _write(
        tmp_path / "user.toml",
        """
        schema = 1
        [review]
        max_findings = 10
        fail_on = "minor"
        include = ["src/**"]
        assess = "never"
        model = "user-model"
        [[rules]]
        id = "project/style"
        severity = "minor"
        category = "style"
        guidance = "user guidance"
        """,
    )
    project = _write(
        tmp_path / "project.toml",
        """
        schema = 1
        [review]
        max_findings = 5
        fail_on = "major"
        include = ["tests/**"]
        assess = "always"
        model = "project-model"
        [[rules]]
        id = "project/style"
        severity = "major"
        guidance = "project guidance"
        """,
    )
    pack = load_review_pack(
        cwd=tmp_path,
        trust_project=True,
        user_path=user,
        project_path=project,
        overrides={
            "max_findings": 2,
            "include": ["docs/**"],
            "fail_on": "blocker",
            "model": "cli-model",
            "severity_overrides": ["project/style=nit"],
        },
    )
    assert pack.errors == ()
    assert pack.max_findings == 2
    assert pack.fail_on == "blocker"
    assert pack.assess == "always"
    assert pack.model == "cli-model"
    assert pack.include == ("src/**", "tests/**", "docs/**")
    rule = next(r for r in pack.rules if r.id == "project/style")
    assert rule.severity == "nit"
    assert rule.guidance == "project guidance"
    assert rule.source_layer == "cli"


def test_project_layer_is_untrusted_by_default(tmp_path, monkeypatch):
    project = _write(
        tmp_path / ".sidekick" / "review.toml",
        """
        schema = 1
        [review]
        fail_on = "major"
        model = "repo-model"
        max_findings = 1
        """,
    )
    (tmp_path / ".git").mkdir()
    user = tmp_path / "missing-user.toml"

    untrusted = load_review_pack(cwd=tmp_path, user_path=user)
    assert untrusted.fail_on == "never"
    assert untrusted.model == ""
    assert untrusted.max_findings == 25
    assert untrusted.layers[2].loaded is False
    assert any("untrusted" in w for w in untrusted.warnings)

    trusted = load_review_pack(cwd=tmp_path, trust_project=True, user_path=user)
    assert trusted.fail_on == "major"
    assert trusted.model == "repo-model"
    assert trusted.max_findings == 1
    assert trusted.layers[2].loaded is True
    assert not trusted.warnings

    monkeypatch.setenv("SIDEKICK_TRUST_REPO", "1")
    env_trusted = load_review_pack(cwd=tmp_path, user_path=user)
    assert env_trusted.fail_on == "major"
    assert env_trusted.max_findings == 1
    assert project.is_file()


def test_project_pack_is_scoped_to_the_git_root(tmp_path):
    project = _write(
        tmp_path / "sub" / ".sidekick" / "review.toml",
        """
        schema = 1
        [review]
        fail_on = "major"
        """,
    )
    pack = load_review_pack(
        cwd=tmp_path / "sub",
        trust_project=True,
        user_path=tmp_path / "missing.toml",
    )
    assert pack.fail_on == "never"
    assert project.is_file()
    assert pack.layers[2].reason == "not found"


def test_protected_rules_need_user_layer_and_explicit_opt_in(tmp_path):
    user = _write(
        tmp_path / "user.toml",
        """
        schema = 1
        [review]
        allow_protected_override = true
        [[rules]]
        id = "security/dynamic-eval"
        severity = "blocker"
        """,
    )
    pack = load_review_pack(cwd=tmp_path, user_path=user, project_path=tmp_path / "none")
    assert pack.errors == ()
    rule = next(r for r in pack.rules if r.id == "security/dynamic-eval")
    assert rule.severity == "blocker"
    assert rule.protected is True

    disable = _write(
        tmp_path / "disable.toml",
        """
        schema = 1
        [review]
        allow_protected_override = true
        [[rules]]
        id = "security/dynamic-eval"
        disable = true
        """,
    )
    disabled = load_review_pack(cwd=tmp_path, user_path=disable, project_path=tmp_path / "none")
    assert disabled.errors == ()
    assert all(r.id != "security/dynamic-eval" for r in disabled.rules)

    revive = _write(
        tmp_path / "revive.toml",
        """
        schema = 1
        [[rules]]
        id = "security/dynamic-eval"
        severity = "nit"
        """,
    )
    revived = load_review_pack(
        cwd=tmp_path,
        trust_project=True,
        user_path=disable,
        project_path=revive,
    )
    assert any("protected" in e for e in revived.errors)

    no_flag = _write(
        tmp_path / "no-flag.toml",
        """
        schema = 1
        [[rules]]
        id = "security/dynamic-eval"
        severity = "nit"
        """,
    )
    blocked = load_review_pack(cwd=tmp_path, user_path=no_flag, project_path=tmp_path / "none")
    assert any("protected" in e for e in blocked.errors)


def test_project_and_cli_cannot_weaken_protected_rules(tmp_path):
    project = _write(
        tmp_path / "project.toml",
        """
        schema = 1
        [[rules]]
        id = "security/dynamic-exec"
        disable = true
        """,
    )
    pack = load_review_pack(
        cwd=tmp_path,
        trust_project=True,
        user_path=tmp_path / "missing.toml",
        project_path=project,
    )
    assert any("protected" in e for e in pack.errors)

    cli = load_review_pack(
        cwd=tmp_path,
        user_path=tmp_path / "missing.toml",
        project_path=tmp_path / "none",
        overrides={"disable_rules": ["security/os-system"]},
    )
    assert any("protected" in e for e in cli.errors)


def test_check_config_reports_schema_keys_rules_and_globs(tmp_path):
    cases = {
        "schema = 2\n": "unsupported schema",
        "schema = '1'\n": "schema must be an integer",
        "schema = 1\n[wat]\nx = 1\n": "unknown key",
        "schema = 1\n[review]\nnope = 1\n": "unknown [review] key",
        "schema = 1\n[review]\nmax_findings = 0\n": "max_findings",
        "schema = 1\n[[rules]]\nid = 'x'\nseverity = 'loud'\n": "severity must be",
        "schema = 1\n[[rules]]\nid = 'x'\ncategory = 'vibes'\n": "category must be",
        "schema = 1\n[review]\ninclude = ['../secret/**']\n": "may not traverse",
        "schema = 1\n[review]\nignore = ['/etc/**']\n": "absolute glob",
        "schema = 1\n[review]\ninclude = ['[oops']\n": "invalid glob",
        "schema = 1\n[review]\ninclude = ['[z-a]']\n": "invalid glob",
        ("schema = 1\n[[rules]]\nid = 'x'\n[[rules]]\nid = 'x'\n"): "duplicate rule id",
    }
    for index, (body, expected) in enumerate(cases.items()):
        path = _write(tmp_path / f"bad-{index}.toml", body)
        pack = load_review_pack(cwd=tmp_path, user_path=path, project_path=tmp_path / "none")
        joined = "\n".join(pack.errors)
        assert expected in joined, (body, joined)
        assert any(level == "error" for level, _ in pack.check())


def test_malformed_overrides_report_errors_instead_of_raising(tmp_path):
    pack = load_review_pack(
        cwd=tmp_path,
        user_path=tmp_path / "none",
        project_path=tmp_path / "none2",
        overrides={
            "max_findings": "lots",
            "include": 7,
            "fail_on": "sometimes",
            "severity_overrides": ["no-equals", "x=loud"],
        },
    )
    assert pack.errors
    joined = "\n".join(pack.errors)
    assert "max_findings" in joined
    assert "fail_on" in joined
    assert "RULE=LEVEL" in joined


def test_disable_unknown_rule_is_a_warning_not_an_error(tmp_path):
    path = _write(
        tmp_path / "user.toml",
        """
        schema = 1
        [[rules]]
        id = "no/such-rule"
        disable = true
        """,
    )
    pack = load_review_pack(cwd=tmp_path, user_path=path, project_path=tmp_path / "none")
    assert pack.errors == ()
    assert any("not found" in w for w in pack.warnings)
    assert any(level == "warning" for level, _ in pack.check())


def test_explain_lists_layers_options_rules_warnings_and_hash(tmp_path):
    user = _write(
        tmp_path / "user.toml",
        """
        schema = 1
        [review]
        max_findings = 7
        [[rules]]
        id = "project/custom"
        guidance = "do the thing"
        """,
    )
    project = _write(
        tmp_path / "project.toml",
        """
        schema = 1
        [review]
        fail_on = "major"
        """,
    )
    pack = load_review_pack(cwd=tmp_path, user_path=user, project_path=project)
    text = pack.explain()
    assert "Layers:" in text
    assert "user:" in text
    assert "project:" in text and "untrusted" in text
    assert "max_findings: 7" in text
    assert "security/dynamic-eval" in text
    assert "project/custom" in text
    assert "Warnings:" in text
    assert pack.hash in text


def test_glob_matching_preserves_dotfiles_and_matches_zero_directories():
    assert glob_match("./src/app.py", "src/**")
    assert glob_match(".github/workflows/ci.yml", "**/*.yml")
    assert glob_match("generated/a.py", "**/generated/**")
    assert glob_match("src/deep/generated/a.py", "**/generated/**")
    assert not glob_match("src/deep/a.py", "**/generated/**")
    assert glob_match(".github/x.yml", "*.yml")
    assert not glob_match(".github/x.yml", "github/**")
    assert glob_match("README.md", "**/*")


def test_rule_applies_uses_the_effective_globs(tmp_path):
    path = _write(
        tmp_path / "user.toml",
        """
        schema = 1
        [[rules]]
        id = "project/docs-only"
        applies_to = ["docs/**"]
        """,
    )
    pack = load_review_pack(cwd=tmp_path, user_path=path, project_path=tmp_path / "none")
    rule = next(r for r in pack.rules if r.id == "project/docs-only")
    assert rule_applies(rule, "docs/guide.md")
    assert not rule_applies(rule, "src/app.py")


def test_filter_diff_reports_excluded_files_as_skipped():
    diff = DiffModel(
        target="worktree",
        repo_root="/tmp",
        ref="HEAD",
        files=(
            FileDiff(path="src/app.py", additions=1, hunks=(_hunk(),)),
            FileDiff(path="docs/guide.md", additions=1, hunks=(_hunk(),)),
            FileDiff(path="vendor/lib.py", additions=1, hunks=(_hunk(),)),
        ),
        skipped=(SkipReason("gone.bin", "binary file"),),
        changed_lines=3,
    )
    filtered = filter_diff(diff, ["**/*.py"], ["vendor/**"])
    assert [f.path for f in filtered.files] == ["src/app.py"]
    reasons = {s.path: s.reason for s in filtered.skipped}
    assert reasons["docs/guide.md"] == "excluded by review pack include"
    assert reasons["vendor/lib.py"] == "excluded by review pack ignore"
    assert reasons["gone.bin"] == "binary file"
    assert filtered.changed_lines == 1


def test_engine_applies_pack_hash_fail_on_and_disabled_rule(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "app.py").write_text('x = 1\ny = eval("1")\n')
    _user_pack(
        """
        schema = 1
        [review]
        fail_on = "major"
        allow_protected_override = true
        [[rules]]
        id = "security/dynamic-eval"
        disable = true
        """
    )
    report = review(
        cwd=str(repo),
        diff=_diff(repo),
        cfg=_cfg(),
        completer=_completer(),
        options=ReviewOptions(assess="never"),
    )
    assert report.findings == []
    assert report.fail_on == "major"
    assert (
        report.review_pack_hash
        == load_review_pack(cwd=str(repo), overrides={"assess": "never"}).hash
    )
    assert report.pack_warnings == []


def test_engine_applies_pack_severity_override(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "app.py").write_text('x = 1\ny = eval("1")\n')
    _user_pack(
        """
        schema = 1
        [review]
        allow_protected_override = true
        [[rules]]
        id = "security/dynamic-eval"
        severity = "nit"
        """
    )
    report = review(
        cwd=str(repo),
        diff=_diff(repo),
        cfg=_cfg(),
        completer=_completer(),
        options=ReviewOptions(assess="never"),
    )
    finding = next(f for f in report.findings if f.rule_id == "security/dynamic-eval")
    assert finding.severity == "nit"


def test_engine_filters_ignored_paths_and_keeps_usage_honest(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "app.py").write_text("x = 1\n")
    (repo / "docs").mkdir()
    (repo / "docs" / "guide.md").write_text("# guide\n")
    _user_pack(
        """
        schema = 1
        [review]
        ignore = ["docs/**"]
        """
    )
    report = review(
        cwd=str(repo),
        diff=_diff(repo, "app.py", "docs/guide.md"),
        cfg=_cfg(),
        completer=_completer(),
        options=ReviewOptions(assess="never"),
    )
    assert report.files_reviewed == 1
    assert report.changed_lines == 1
    assert any("review pack ignore" in s.reason for s in report.skipped)


def test_engine_project_pack_requires_trust(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "app.py").write_text("x = 1\n")
    _write(
        repo / ".sidekick" / "review.toml",
        """
        schema = 1
        [review]
        fail_on = "major"
        """,
    )
    untrusted = review(
        cwd=str(repo),
        diff=_diff(repo),
        cfg=_cfg(),
        completer=_completer(),
        options=ReviewOptions(assess="never"),
    )
    assert untrusted.fail_on == "never"
    assert untrusted.pack_warnings

    trusted = review(
        cwd=str(repo),
        diff=_diff(repo),
        cfg=_cfg(),
        completer=_completer(),
        options=ReviewOptions(assess="never", trust_project=True),
    )
    assert trusted.fail_on == "major"
    assert trusted.pack_warnings == []


def test_engine_rejects_invalid_pack(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "app.py").write_text("x = 1\n")
    _user_pack("schema = 99\n")
    with pytest.raises(ReviewError, match="invalid review pack"):
        review(
            cwd=str(repo),
            diff=_diff(repo),
            cfg=_cfg(),
            completer=_completer(),
            options=ReviewOptions(assess="never"),
        )


def test_engine_prompt_includes_effective_pack_guidance(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "app.py").write_text("x = 1\n")
    _user_pack(
        """
        schema = 1
        [[rules]]
        id = "project/custom"
        guidance = "prefer the platform parser"
        """
    )
    systems: list[str] = []

    def call(cfg, *, system, user, max_tokens=4096):
        systems.append(user)
        return '{"findings":[]}'

    review(
        cwd=str(repo),
        diff=_diff(repo),
        cfg=_cfg(),
        completer=call,
        options=ReviewOptions(assess="never"),
    )
    assert systems
    assert "project/custom" in systems[0]
    assert "prefer the platform parser" in systems[0]


def test_cli_explain_and_check_config(tmp_path):
    _user_pack(
        """
        schema = 1
        [review]
        max_findings = 9
        """
    )
    runner = CliRunner()
    explained = runner.invoke(app, ["review", "--explain"])
    assert explained.exit_code == 0, explained.output
    assert "Review pack" in explained.output
    assert "max_findings: 9" in explained.output

    checked = runner.invoke(app, ["review", "--check-config"])
    assert checked.exit_code == 0, checked.output
    assert "Review pack OK" in checked.output

    _user_pack("schema = 99\n")
    bad = runner.invoke(app, ["review", "--check-config"])
    assert bad.exit_code == 1
    assert "unsupported schema" in bad.output

    _user_pack(
        """
        schema = 1
        [[rules]]
        id = "no/such-rule"
        disable = true
        """
    )
    warned = runner.invoke(app, ["review", "--check-config"])
    assert warned.exit_code == 0, warned.output
    assert "warnings only" in warned.output


def test_slash_review_explain_and_check_config(tmp_path):
    from sk.slash import handle

    _user_pack(
        """
        schema = 1
        [review]
        fail_on = "major"
        """
    )
    out = handle("/review --explain", session="s", cfg=_cfg(), state={})
    assert out.handled is True
    assert "Review pack" in out.text
    assert "fail_on: major" in out.text

    checked = handle("/review --check-config", session="s", cfg=_cfg(), state={})
    assert checked.handled is True
    assert "OK" in checked.text
