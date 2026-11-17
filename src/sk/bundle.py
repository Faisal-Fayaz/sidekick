"""The air-gapped bundle: build, verify and install an offline Sidekick (#475).

A connected machine builds the bundle: every pinned dependency as a wheel, the
built wheel of Sidekick itself, the skills and the documentation. A manifest
holds the sha256 of every file. A disconnected machine verifies the manifest
and installs with ``--no-index --find-links``, so no request leaves the box.

The bundle is built **for the machine it will be installed on**: same OS,
architecture and Python version. A wheel built for Linux x86-64 does not
install on macOS ARM, and pretending otherwise would be worse than saying so.
Model weights are not included — they are large and have their own distribution
path; the bundle carries the model *documentation* and says what is missing.

Nothing here adds a runtime dependency. ``uv`` and ``uvx pip`` are build-time
tools, and the install uses only wheels in the bundle.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

SCHEMA = 1
WHEELS_DIR = "wheels"
MANIFEST = "manifest.json"
PAYLOAD_DIRS = ("skills", "docs")
PAYLOAD_FILES = ("README.md", "LICENSE")


def _run(argv: list[str], *, cwd: str | Path | None = None) -> tuple[int, str, str]:
    """Run a command with argv. Never raises."""
    try:
        result = subprocess.run(
            argv,
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
    except Exception as e:  # noqa: BLE001 - tool failures are messages
        return 127, "", f"{type(e).__name__}: {e}"
    return result.returncode, result.stdout or "", result.stderr or ""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def locked_requirements(source: str | Path) -> list[str]:
    """The pinned dependency set from `uv.lock`, without the project itself."""
    code, out, err = _run(
        [
            "uv",
            "export",
            "--frozen",
            "--no-emit-project",
            "--no-hashes",
            "--format",
            "requirements-txt",
        ],
        cwd=source,
    )
    if code != 0:
        raise RuntimeError(f"uv export failed: {(err or out).strip()[:400]}")
    lines = []
    for line in out.splitlines():
        text = line.strip()
        if not text or text.startswith("#") or text.startswith("-"):
            continue
        lines.append(text)
    return lines


def payload_files(bundle: Path) -> list[Path]:
    """Every file in the bundle except the manifest itself."""
    return sorted(
        p for p in bundle.rglob("*") if p.is_file() and p.relative_to(bundle).as_posix() != MANIFEST
    )


def build_manifest(bundle: Path, version: str) -> dict:
    """Record the hash and size of every file in the bundle."""
    files = []
    for path in payload_files(bundle):
        files.append(
            {
                "path": path.relative_to(bundle).as_posix(),
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
            }
        )
    return {
        "schema": SCHEMA,
        "version": version,
        "files": files,
        "size": sum(item["size"] for item in files),
        "platform": "built for this machine (OS, architecture and Python version)",
        "models": "not included — model weights have their own distribution path",
    }


def write_manifest(bundle: Path, version: str) -> Path:
    path = bundle / MANIFEST
    path.write_text(json.dumps(build_manifest(bundle, version), indent=2) + "\n")
    return path


def read_manifest(bundle: Path) -> dict:
    path = bundle / MANIFEST
    if not path.is_file():
        raise RuntimeError(f"{path} is missing — this is not a bundle")
    data = json.loads(path.read_text())
    if data.get("schema") != SCHEMA:
        raise RuntimeError(f"{path}: unsupported schema {data.get('schema')!r}")
    return data


def verify_bundle(bundle: Path) -> tuple[bool, list[str]]:
    """Verify every file against the manifest. A mismatch is a failure.

    Extra files are a failure too: a tampered bundle is not one that merely
    changed a hash, it is one that has something in it the manifest does not
    know about.
    """
    bundle = Path(bundle)
    try:
        manifest = read_manifest(bundle)
    except Exception as e:  # noqa: BLE001 - a broken bundle is a report
        return False, [str(e)]
    problems: list[str] = []
    declared: set[str] = set()
    for item in manifest.get("files") or []:
        rel = str(item.get("path") or "")
        declared.add(rel)
        path = bundle / rel
        if not path.is_file():
            problems.append(f"missing: {rel}")
            continue
        if sha256_file(path) != item.get("sha256"):
            problems.append(f"hash mismatch: {rel}")
        if path.stat().st_size != int(item.get("size", -1)):
            problems.append(f"size mismatch: {rel}")
    for path in payload_files(bundle):
        rel = path.relative_to(bundle).as_posix()
        if rel not in declared:
            problems.append(f"undeclared file: {rel}")
    return (not problems), problems


def build_bundle(
    out: Path,
    *,
    source: str | Path | None = None,
    requirements: list[str] | None = None,
) -> Path:
    """Build a verified bundle in `out`. Returns its directory."""
    from sk import __version__

    root = Path(source) if source is not None else Path(__file__).resolve().parent.parent.parent
    bundle = Path(out).expanduser().resolve()
    wheels = bundle / WHEELS_DIR
    wheels.mkdir(parents=True, exist_ok=True)

    code, out_text, err = _run(["uv", "build", "--wheel", "--out-dir", str(wheels)], cwd=root)
    if code != 0:
        raise RuntimeError(f"uv build failed: {(err or out_text).strip()[:600]}")

    reqs = requirements if requirements is not None else locked_requirements(root)
    if reqs:
        req_file = bundle / "requirements.txt"
        req_file.write_text("\n".join(reqs) + "\n")
        code, out_text, err = _run(
            [
                "uvx",
                "pip",
                "download",
                "--only-binary=:all:",
                "--dest",
                str(wheels),
                "-r",
                str(req_file),
            ],
            cwd=root,
        )
        if code != 0:
            raise RuntimeError(f"wheel download failed: {(err or out_text).strip()[:600]}")
        # requirements.txt is an input, not a payload needed for the install.
        req_file.unlink()

    for name in PAYLOAD_DIRS:
        src = root / name
        if src.is_dir():
            shutil.copytree(src, bundle / name, dirs_exist_ok=True)
    for name in PAYLOAD_FILES:
        src = root / name
        if src.is_file():
            shutil.copy2(src, bundle / name)

    write_manifest(bundle, __version__)
    return bundle


def install_bundle(bundle: Path, target: Path, *, python: str = "python3") -> tuple[bool, str]:
    """Verify the bundle, then install into a fresh venv with no index access.

    Returns (ok, message). The install never reaches PyPI: ``--no-index`` with
    ``--find-links`` pointing only at the bundle's wheels.
    """
    ok, problems = verify_bundle(bundle)
    if not ok:
        return False, "the bundle failed verification:\n  - " + "\n  - ".join(problems)
    version = str(read_manifest(bundle).get("version") or "")
    bundle = Path(bundle)
    target = Path(target).expanduser().resolve()
    wheels = bundle / WHEELS_DIR

    code, out, err = _run(["uv", "venv", str(target), "--python", python])
    if code != 0:
        return False, f"could not create the virtual environment: {(err or out).strip()[:400]}"
    venv_python = target / "bin" / "python"
    code, out, err = _run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(venv_python),
            "--no-index",
            "--find-links",
            str(wheels),
            f"sidekick-agent=={version}",
        ]
    )
    if code != 0:
        return False, f"offline install failed: {(err or out).strip()[:600]}"
    return True, f"installed sidekick-agent {version} into {target}"
