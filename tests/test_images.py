"""Image generation tests (fixes #192): backend variants + degrades, tool
wiring + approval, sk imagine CLI. Fully offline (httpx mocked)."""

import base64
from pathlib import Path
from types import SimpleNamespace

import pytest

import sk.images as images

# These tests fake HTTP to exercise redirect/SSRF logic; they need the
# egress policy to say yes to the public decoy hosts. Deny-by-default
# (#328) would otherwise short-circuit every case here.
pytestmark = pytest.mark.usefixtures("egress_test_hosts")


def _cfg(provider="openai"):
    return SimpleNamespace(
        provider=provider,
        effective_base_url=lambda: "https://api.example.com/v1",
        effective_api_key=lambda: "k",
    )


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class _Resp:
    def __init__(self, status=200, payload=None, content=b""):
        self.status_code = status
        self._payload = payload
        self.content = content
        self.text = "" if payload is None else "err"

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _public_dns(monkeypatch):
    """Make every hostname resolve to a public IP, offline."""
    import socket

    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))]
    )


def _iso(tmp_path, monkeypatch):
    import sk.config as config_mod
    import sk.store as store

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")
    monkeypatch.chdir(tmp_path)


def test_b64_success_saves_exact_bytes(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    import httpx

    seen = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        seen.update({"url": url, "json": json})
        b64 = base64.b64encode(PNG).decode()
        return _Resp(200, {"data": [{"b64_json": b64}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    out = tmp_path / "panda.png"
    res = images.generate_image(_cfg(), "a red panda", out=str(out))
    assert res.startswith("Saved image to") and out.read_bytes() == PNG
    assert seen["url"].endswith("/images/generations")
    assert seen["json"]["model"] == "gpt-image-1" and seen["json"]["size"] == "1024x1024"


def test_url_variant_fetched(tmp_path, monkeypatch, egress_test_hosts):
    _iso(tmp_path, monkeypatch)
    import httpx

    egress_test_hosts("img")  # the provider hands back https://img/x.png
    _public_dns(monkeypatch)  # the provider-supplied URL now passes the SSRF guard (#294)
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: _Resp(200, {"data": [{"url": "https://img/x.png"}]})
    )
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(200, content=PNG))
    out = tmp_path / "x.png"
    res = images.generate_image(_cfg("together"), "a cat", out=str(out))
    assert res.startswith("Saved image to") and out.read_bytes() == PNG


def test_provider_url_cannot_reach_metadata_service(tmp_path, monkeypatch):
    """A hostile provider returning a metadata URL is an SSRF primitive (#294).

    The bytes used to be written to disk, and read_file has no path jail, so
    the model could read them straight back into context.
    """
    _iso(tmp_path, monkeypatch)
    import httpx

    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: _Resp(
            200,
            {
                "data": [
                    {"url": "http://169.254.169.254/latest/meta-data/iam/security-credentials/"}
                ]
            },
        ),
    )
    fetched = []
    monkeypatch.setattr(httpx, "get", lambda *a, **k: fetched.append(a) or _Resp(200, content=PNG))
    out = tmp_path / "creds.png"
    res = images.generate_image(_cfg(), "x", out=str(out))
    assert not res.startswith("Saved image to")
    assert not out.exists()
    assert fetched == [], "metadata URL was fetched"


def test_cgnat_metadata_endpoint_blocked(tmp_path, monkeypatch):
    """100.100.100.200 (Alibaba/Tencent metadata) is not is_private on CPython."""
    _iso(tmp_path, monkeypatch)
    import httpx

    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: _Resp(200, {"data": [{"url": "http://100.100.100.200/latest/meta-data/"}]}),
    )
    out = tmp_path / "c.png"
    assert not images.generate_image(_cfg(), "x", out=str(out)).startswith("Saved image to")


def test_validation_and_degrades(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    import httpx

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(404, None))
    assert images.generate_image(_cfg(), "").startswith("Error: empty")
    assert "256x256" in images.generate_image(_cfg(), "x", size="huge")
    assert "native API" in images.generate_image(_cfg("anthropic"), "x")
    assert "--model" in images.generate_image(_cfg("groq"), "x")
    assert "HTTP 404" in images.generate_image(_cfg(), "x", out=str(tmp_path / "a.png"))


def test_path_guards(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    assert "blocked" in images.generate_image(_cfg(), "x", out=str(Path.home() / ".ssh" / "x.png"))
    assert "only allowed" in images.generate_image(_cfg(), "x", out="/opt/x.png")
    big = base64.b64encode(b"0" * 10).decode()
    import httpx

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(200, {"data": [{"b64_json": big}]}))
    monkeypatch.setattr(images, "MAX_IMAGE_BYTES", 5)
    assert "too large" in images.generate_image(_cfg(), "x", out=str(tmp_path / "b.png"))


def test_tool_wiring_and_approval(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    from sk.cli.approvers import _make_approver
    from sk.tools import approval_tools, dispatch_tool

    assert "generate_image" in approval_tools()
    approve = _make_approver(False, (), (), readonly=True)
    assert approve("generate_image", {"path": "x"}) is False
    approve = _make_approver(False, (), (), plan_mode=True)
    assert approve("generate_image", {"path": "x"}) is False
    out = dispatch_tool("generate_image", {"path": str(tmp_path / "g.png"), "prompt": "x"})
    assert "no default image model" in out  # real config here is ollama
    from sk.checkpoints import snapshot_before

    f = tmp_path / "new.png"
    assert snapshot_before("s", "generate_image", {"path": str(f)}) == 1


def test_schema_entry():
    from sk.tools import tools_schema

    entry = next(e for e in tools_schema() if e["function"]["name"] == "generate_image")
    assert set(entry["function"]["parameters"]["required"]) == {"path", "prompt"}


def test_cli_imagine(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    import sk.images as images_mod
    from typer.testing import CliRunner

    from sk.cli import app

    seen = {}

    def fake_generate(cfg, prompt, size="", model="", out=""):
        seen.update({"prompt": prompt, "size": size, "model": model, "out": out})
        return "Saved image to /tmp/fake.png (10 bytes)"

    monkeypatch.setattr(images_mod, "generate_image", fake_generate)
    res = CliRunner().invoke(app, ["imagine", "a cat", "--size", "512x512", "--model", "m"])
    assert res.exit_code == 0, res.output
    assert "Saved image to /tmp/fake.png" in res.output
    assert seen == {"prompt": "a cat", "size": "512x512", "model": "m", "out": ""}
    monkeypatch.setattr(images_mod, "generate_image", lambda *a, **k: "Error: nope")
    res = CliRunner().invoke(app, ["imagine", "a cat"])
    assert res.exit_code == 1 and "Error: nope" in res.output
