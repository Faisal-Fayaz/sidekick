"""Self-update tests: version math, installer detection, upgrade paths. No network, no real installs."""

import sk.upgrade as upgrade


def _resp(payload, status=200):
    class R:
        def raise_for_status(self):
            if status >= 400:
                raise Exception(f"HTTP {status}")

        def json(self):
            return payload

    return R()


def test_parse_version_ordering():
    assert upgrade.parse_version("0.10.0") > upgrade.parse_version("0.9.0")
    assert upgrade.parse_version("0.3.0") > upgrade.parse_version("0.2.0")
    assert upgrade.is_newer("0.3.1", "0.3.0") is True
    assert upgrade.is_newer("0.3.0", "0.3.0") is False
    assert upgrade.is_newer("0.2.9", "0.3.0") is False
    assert upgrade.is_newer("???", "0.3.0") is True  # unparseable -> inequality


def test_pypi_latest_and_offline(monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _resp({"info": {"version": "0.9.9"}}))
    assert upgrade.pypi_latest_version() == "0.9.9"
    assert "pypi.org/pypi/sidekick-agent" in upgrade.PYPI_URL

    def boom(*a, **k):
        raise Exception("no route")

    monkeypatch.setattr(httpx, "get", boom)
    try:
        upgrade.pypi_latest_version()
        assert False, "should raise"
    except RuntimeError as e:
        assert "PyPI" in str(e)


def test_detect_installer_table(tmp_path, monkeypatch):
    import sk
    import sk.upgrade as _u

    # neutralize dev detection (test env itself is a checkout): fake site-packages
    site = tmp_path / "site-packages" / "sk"
    site.mkdir(parents=True)
    (site / "__init__.py").write_text("")
    monkeypatch.setattr(sk, "__file__", str(site / "__init__.py"))
    assert _u.detect_installer("/home/u/.local/share/uv/tools/sidekick/bin/python") == "uv"
    assert _u.detect_installer("/home/u/.local/share/pipx/venvs/sidekick/bin/python") == "pipx"
    assert _u.detect_installer("/usr/bin/python3") == "pip"


def test_detect_dev_checkout(tmp_path, monkeypatch):
    import sk

    fake_pkg = tmp_path / "src" / "sk"
    fake_pkg.mkdir(parents=True)
    (fake_pkg / "__init__.py").write_text("")
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(sk, "__file__", str(fake_pkg / "__init__.py"))
    assert upgrade.detect_installer("/usr/bin/python3") == "dev"


def test_upgrade_argv_and_dev_refusal():
    assert upgrade.upgrade_argv("uv")[:3] == ["uv", "tool", "install"]
    assert upgrade.upgrade_argv("pipx")[:2] == ["pipx", "upgrade"]
    argv = upgrade.upgrade_argv("pip")
    assert argv[-4:] == ["pip", "install", "-U", "sidekick-agent"]
    ok, msg = upgrade.upgrade_package("dev")
    assert ok is False and "git pull" in msg


def test_upgrade_runner_mocked(monkeypatch):
    import subprocess

    def fake_run(argv, **k):
        assert argv[0] == "uv"
        return type("R", (), {"returncode": 0, "stdout": "done", "stderr": ""})()

    monkeypatch.setattr(subprocess, "run", fake_run)
    ok, out = upgrade.upgrade_package("uv")
    assert ok is True and "done" in out


def test_cli_check_reports(monkeypatch):
    from typer.testing import CliRunner

    from sk.cli import app

    monkeypatch.setattr(upgrade, "pypi_latest_version", lambda *a, **k: "99.0.0")
    res = CliRunner().invoke(app, ["upgrade", "--check"])
    assert res.exit_code == 0, res.output
    assert "update available" in res.output and "--check" in res.output


def test_cli_current_is_noop(monkeypatch):
    from typer.testing import CliRunner

    from sk import __version__
    from sk.cli import app

    monkeypatch.setattr(upgrade, "pypi_latest_version", lambda *a, **k: __version__)
    res = CliRunner().invoke(app, ["upgrade"])
    assert res.exit_code == 0, res.output
    assert "already current" in res.output
