"""A masked credential must reveal nothing (#269).

`Config.mask` returned `first3…last4`, so seven characters of a live API key
reached `sk config --show`, `sk auth`, `sk doctor`, the status line and every
report — all of which end up pasted into issues and screenshots. The leaked
characters are confirmatory rather than decorative: an attacker holding a
suspected key can test a guess against them.
"""

from __future__ import annotations

import pytest

from sk.config import Config

SECRETS = [
    "sk-abcdef123456",
    "sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    "sk-proj-1234567890abcdefghijklmnop",
    "gsk_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
    "AIzaSyA1234567890abcdefghijklmnopqrstuvw",
    "x" * 40,
    "a",
    "ab",
    "0123456789",
]


@pytest.mark.parametrize("secret", SECRETS)
def test_mask_reveals_no_part_of_the_secret(secret: str):
    """Not a prefix, not a suffix, not a length, not a shape."""
    out = Config.mask(secret)
    assert out == "****"
    # the strongest form: no substring of the secret longer than 3 survives
    for n in range(4, min(len(secret), 12) + 1):
        assert not any(secret[i : i + n] in out for i in range(len(secret) - n + 1)), (
            f"mask leaked a {n}-char slice of the secret"
        )


@pytest.mark.parametrize("empty", ["", "   ", "\n", None])
def test_mask_reports_unset(empty):
    assert Config.mask(empty) == "(none)"


def test_mask_does_not_reveal_length():
    """Length narrows a brute-force space and is pure downside if leaked."""
    short = Config.mask("sk-a")
    long = Config.mask("sk-" + "a" * 200)
    assert short == long == "****"


def test_mask_is_not_identity_on_uniform_keys():
    """The old format printed `xxx…xxxx` for a uniform key — technically a
    'mask', and useless as one."""
    assert Config.mask("x" * 40) == "****"


def test_report_redacts_the_key(tmp_path, monkeypatch):
    """`sk report` embeds the config summary — the widest surface for a leak,
    because reports get pasted into issues."""
    import sk.config as config_mod
    import sk.store as store

    from sk.cli.commands import system

    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")

    secret = "sk-ant-api03-SUPERSECRETVALUE1234567890"
    cfg = config_mod.Config.load()
    cfg.api_key = secret
    text = system.build_report(cfg, (True, "valid"))

    assert secret not in text
    assert "SUPERSECRET" not in text, "report leaked a slice of the key"
    assert secret[:3] not in text, "report leaked the prefix"
    assert "****" in text, "report should still show a key is set"


def test_config_show_redacts_the_key(tmp_path, monkeypatch):
    """The surface a user is most likely to paste into a bug report."""
    import sk.config as config_mod
    import sk.store as store

    from typer.testing import CliRunner

    from sk.cli import app

    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")

    secret = "sk-ant-api03-SUPERSECRETVALUE1234567890"
    cfg = config_mod.Config.load()
    cfg.api_key = secret
    cfg.save()

    out = CliRunner().invoke(app, ["config", "--show"]).output
    assert "SUPERSECRET" not in out, "sk config --show leaked a slice of the key"
    assert secret not in out
    assert "****" in out, "key should still show as set"
