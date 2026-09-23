"""Init wizard tests: hardware parse, tiers, pull, CLI flows. No downloads."""

import sk.init_wizard as wiz

SYSINFO_NVIDIA = """CPU: AMD Ryzen 7 (16 threads)
RAM:
MemTotal: 7.2 GiB
GPU:
NVIDIA GeForce GTX 1650, 4096 MiB, 512 MiB
DISK (/):
/dev/x 133G
OLLAMA MODELS:
qwen2.5-coder:7b
"""

SYSINFO_MAC = """CPU: Apple M4 (10 threads)
RAM:
MemTotal: 24.0 GiB (hw.memsize)
GPU:
Chipset Model: Apple M4
OLLAMA MODELS:
(no output, exit 1)
"""


def test_snapshot_nvidia():
    snap = wiz.hardware_snapshot(SYSINFO_NVIDIA)
    assert snap["vram_mb"] == 4096
    assert snap["models"] == ["qwen2.5-coder:7b"]


def test_snapshot_mac_ram():
    snap = wiz.hardware_snapshot(SYSINFO_MAC)
    assert snap["vram_mb"] is None and snap["ram_gb"] == 24.0


def test_snapshot_garbage():
    snap = wiz.hardware_snapshot("nothing useful here")
    assert snap["vram_mb"] is None and snap["models"] == []


def test_recommend_tiers():
    name, _, _ = wiz.recommend_model({"vram_mb": 4096, "ram_gb": None, "models": []})
    assert name == "llama3.2:3b"
    name, _, _ = wiz.recommend_model({"vram_mb": 8192, "ram_gb": None, "models": []})
    assert name == "qwen2.5-coder:7b"
    name, _, _ = wiz.recommend_model({"vram_mb": None, "ram_gb": 24.0, "models": []}, is_mac=True)
    assert name == "qwen2.5-coder:7b"
    name, reason, _ = wiz.recommend_model({"vram_mb": None, "ram_gb": None, "models": []})
    assert name == "llama3.2:3b" and "safe default" in reason


def test_recommend_installed_first():
    name, reason, alts = wiz.recommend_model(
        {"vram_mb": 8192, "ram_gb": None, "models": ["qwen2.5-coder:7b"]}
    )
    assert name == "qwen2.5-coder:7b" and "already installed" in reason and alts == []


def test_pull_no_ollama(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda b: None)
    ok, msg = wiz.pull_model("llama3.2:3b")
    assert ok is False and "ollama.com" in msg


def test_pull_runs(monkeypatch):
    import subprocess

    monkeypatch.setattr("shutil.which", lambda b: "/usr/bin/ollama")
    seen = {}
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **k: seen.update({"argv": argv}) or type("R", (), {"returncode": 0})(),
    )
    ok, msg = wiz.pull_model("llama3.2:3b")
    assert ok is True and seen["argv"][:2] == ["ollama", "pull"]


def _sysmod(monkeypatch):
    import sk.cli.commands.system as sys_mod

    monkeypatch.setattr(sys_mod.console, "input", lambda *a, **k: "")
    return sys_mod


def test_init_cloud_handoff(monkeypatch):
    import sk.cli.commands.system as sys_mod

    _sysmod(monkeypatch)
    monkeypatch.setattr("typer.confirm", lambda *a, **k: False)
    called = {}
    monkeypatch.setattr(sys_mod, "_connect_flow", lambda: called.update({"ran": True}))
    from typer.testing import CliRunner

    from sk.cli import app

    res = CliRunner().invoke(app, ["init"])
    assert res.exit_code == 0, res.output
    assert called.get("ran") is True and "init complete" in res.output


def test_init_unreachable_ollama(monkeypatch):
    _sysmod(monkeypatch)
    monkeypatch.setattr("typer.confirm", lambda *a, **k: True)
    monkeypatch.setattr(
        "sk.auth.fetch_models", lambda *a, **k: (_ for _ in ()).throw(Exception("down"))
    )
    from typer.testing import CliRunner

    from sk.cli import app

    res = CliRunner().invoke(app, ["init"])
    assert res.exit_code != 0 and "ollama serve" in res.output


def test_init_full_local_flow(monkeypatch):
    _sysmod(monkeypatch)
    answers = iter([True, False])  # local=yes, hook=no
    monkeypatch.setattr("typer.confirm", lambda *a, **k: next(answers))
    monkeypatch.setattr("sk.auth.fetch_models", lambda *a, **k: [])
    monkeypatch.setattr("sk.tools.tool_sysinfo", lambda: SYSINFO_NVIDIA)
    monkeypatch.setattr("sk.init_wizard.pull_model", lambda *a, **k: (True, "pulled"))
    monkeypatch.setattr("sk.auth.ping", lambda *a, **k: (True, "hi"))
    from typer.testing import CliRunner

    from sk.cli import app
    from sk.config import Config as _C

    res = CliRunner().invoke(app, ["init"])
    assert res.exit_code == 0, res.output
    assert "init complete" in res.output
    saved = _C.load()
    assert saved.provider == "ollama" and saved.model == "llama3.2:3b"
