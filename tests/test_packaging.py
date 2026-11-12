"""The packaging pins must match what was published (#443).

The AUR `PKGBUILD` pins a version, a source URL, and a sha256. Any of the
three can rot silently, and `yay -Syu` then serves an old package. The
checker is what the release smoke test runs; these tests keep the checker
honest without touching the network.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PKGBUILD = ROOT / "packaging" / "aur" / "PKGBUILD"
CHECKER = ROOT / "packaging" / "check_aur_pin.py"


def _checker():
    spec = importlib.util.spec_from_file_location("check_aur_pin", CHECKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _release(version: str = "1.2.3", sha: str = "a" * 64) -> dict:
    return {
        "info": {"version": version},
        "urls": [
            {
                "packagetype": "sdist",
                "url": (
                    f"https://files.pythonhosted.org/packages/aa/bb/sidekick_agent-{version}.tar.gz"
                ),
                "digests": {"sha256": sha},
            }
        ],
    }


def test_the_shipped_pkgbuild_is_internally_consistent():
    """A pin that is malformed is worse than a stale one: it fails at build
    time, on the user's machine."""
    pin = _checker().read_pin(PKGBUILD)
    assert pin["pkgver"], "the PKGBUILD has no pkgver"
    assert pin["source"].startswith("https://files.pythonhosted.org/"), pin["source"]
    assert len(pin["sha256"]) == 64, "the sha256 is not a sha256"
    assert pin["source"].endswith("/${_pkgname}-${pkgver}.tar.gz")


def test_the_checker_reports_a_mismatched_version():
    module = _checker()
    pin = {"pkgver": "1.0.0", "source": "", "sha256": ""}
    problems = module.compare(pin, _release("1.2.3"))
    assert any("pkgver" in p for p in problems)


def test_the_checker_reports_a_mismatched_hash():
    module = _checker()
    pin = {
        "pkgver": "1.2.3",
        "source": "",
        "sha256": "b" * 64,
    }
    problems = module.compare(pin, _release("1.2.3", sha="a" * 64))
    assert any("sha256" in p for p in problems)


def test_the_checker_reports_a_mismatched_url():
    module = _checker()
    pin = {
        "pkgver": "1.2.3",
        "source": "https://files.pythonhosted.org/packages/ff/ee/sidekick-agent-1.2.3.tar.gz",
        "sha256": "a" * 64,
    }
    problems = module.compare(pin, _release("1.2.3", sha="a" * 64))
    assert any("source URL" in p for p in problems)


def test_a_matching_pin_has_no_problems():
    module = _checker()
    pin = {
        "pkgver": "1.2.3",
        "source": ("https://files.pythonhosted.org/packages/aa/bb/${_pkgname}-${pkgver}.tar.gz"),
        "sha256": "a" * 64,
    }
    assert module.compare(pin, _release("1.2.3", sha="a" * 64)) == []


def test_the_template_source_is_expanded_before_comparing():
    """The PKGBUILD writes the URL as a template; PyPI names the file with
    underscores. Both have to resolve to the same artifact."""
    module = _checker()
    pin_url = "https://files.pythonhosted.org/packages/aa/bb/${_pkgname}-${pkgver}.tar.gz"
    pypi_url = "https://files.pythonhosted.org/packages/aa/bb/sidekick_agent-1.2.3.tar.gz"
    assert module.same_source(pin_url, pypi_url, "1.2.3")
    assert not module.same_source(
        pin_url,
        "https://files.pythonhosted.org/packages/ff/ee/sidekick_agent-1.2.3.tar.gz",
        "1.2.3",
    )


def test_the_checker_runs_offline_against_a_json_file(tmp_path):
    """The smoke test needs the checker to work without the network when it is
    handed the release metadata."""
    module = _checker()
    meta = tmp_path / "release.json"
    meta.write_text(json.dumps(_release("1.2.3", sha="a" * 64)))
    pkgbuild = tmp_path / "PKGBUILD"
    pkgbuild.write_text(
        "pkgname=python-sidekick-agent\n"
        "_pkgname=sidekick-agent\n"
        "pkgver=1.2.3\n"
        'source=("${_pkgname}-${pkgver}.tar.gz::https://files.pythonhosted.org/packages/aa/bb/'
        '${_pkgname}-${pkgver}.tar.gz")\n'
        f'sha256sums=("{"a" * 64}")\n'
    )
    code = module.main(["--json", str(meta), "--pkgbuild", str(pkgbuild)])
    assert code == 0


def test_the_smoke_workflow_exists_and_installs_the_artifact():
    """The smoke test has to install what was published, not the checkout."""
    text = (ROOT / ".github" / "workflows" / "smoke.yml").read_text()
    assert "uv tool install" in text, "the smoke test does not install the package"
    assert "sidekick-agent==" in text, "the smoke test does not pin a version"
    assert "sk doctor" in text
    assert "check_aur_pin.py" in text, "the AUR pin is not checked"
    # The version that was released comes from the source, not from the pin:
    # the pin is what the job checks.
    assert '__version__ = "\\(.*\\)"' in text or "__version__" in text
    # It must not install the checkout: that would prove nothing about the
    # published artifact.
    assert "uv sync" not in text
    assert "pip install -e" not in text
    # And it must run after a successful release, not after a failed one.
    assert "workflow_run" in text
    assert "conclusion == 'success'" in text
    # PyPI's index lags the upload, so the job waits for the artifact before
    # installing it. Without the wait, the smoke test fails on an artifact that
    # exists.
    assert "pypi.org/pypi/sidekick-agent" in text
    assert "Wait for the artifact" in text


@pytest.mark.parametrize("name", ["smoke.yml"])
def test_the_workflow_file_is_present(name):
    assert (ROOT / ".github" / "workflows" / name).is_file()
