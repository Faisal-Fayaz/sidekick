"""First-run wizard helpers: hardware detect, model recommend, pull. No CLI deps."""

from __future__ import annotations

import re
import shutil
import subprocess

# (ceiling VRAM MiB, model, why). Mirrors the agent prompt's VRAM truth.
NVIDIA_TIERS: tuple[tuple[int, str, str], ...] = (
    (6144, "llama3.2:3b", "fits comfortably in ≤6 GB VRAM, fast"),
    (12288, "qwen2.5-coder:7b", "best coder in the 7B class for 6–12 GB VRAM"),
    (10**9, "qwen2.5-coder:14b", "room to spare above 12 GB VRAM"),
)

# macOS unified memory tiers (shared RAM, different math than discrete VRAM).
MAC_TIERS: tuple[tuple[int, str, str], ...] = (
    (16, "llama3.2:3b", "safe on <16 GB unified memory"),
    (32, "qwen2.5-coder:7b", "sweet spot for 16–32 GB unified memory"),
    (10**9, "qwen2.5-coder:14b", "room to spare above 32 GB unified memory"),
)

FALLBACK_MODEL = "llama3.2:3b"


def hardware_snapshot(sysinfo_text: str) -> dict:
    """Parse tool_sysinfo() output. Keys: vram_mb|None, ram_gb|None, gpu, models."""
    text = sysinfo_text or ""
    vram_mb: int | None = None
    m = re.search(r"(\d[\d,]*)\s*MiB", text)
    if m:
        try:
            vram_mb = int(m.group(1).replace(",", ""))
        except ValueError:
            vram_mb = None
    ram_gb: float | None = None
    m = re.search(r"MemTotal:\s*([\d.]+)\s*GiB", text)
    if m:
        try:
            ram_gb = float(m.group(1))
        except ValueError:
            ram_gb = None
    gpu = ""
    for line in text.splitlines():
        if line.startswith("Model name:") or "Chipset Model" in line:
            gpu = line.split(":", 1)[-1].strip()[:80]
            break
    models: list[str] = []
    in_models = False
    for line in text.splitlines():
        if line.strip().startswith("OLLAMA MODELS"):
            in_models = True
            continue
        if in_models and line.strip() and not line.strip().lower().startswith("name"):
            models.append(line.split()[0])
    return {"vram_mb": vram_mb, "ram_gb": ram_gb, "gpu": gpu, "models": models}


def recommend_model(snap: dict, is_mac: bool = False) -> tuple[str, str, list[str]]:
    """Return (name, reason, alternatives). Installed models rank first (no re-pull)."""
    installed = snap.get("models", [])
    tiers = MAC_TIERS if is_mac else NVIDIA_TIERS
    key = snap.get("ram_gb") if is_mac else snap.get("vram_mb")
    if key is None:
        name, reason = FALLBACK_MODEL, "could not detect memory — safe default, change anytime"
    else:
        name, reason = FALLBACK_MODEL, ""
        for ceiling, model, why in tiers:
            if key <= ceiling:
                name, reason = model, why
                break
    if name in installed:
        return (name, f"{reason} (already installed — no download)", [])
    alts = [m for _, m, _ in tiers if m != name and m not in installed]
    return (name, reason, alts[:2])


def pull_model(name: str, timeout: int = 1200) -> tuple[bool, str]:
    """Run `ollama pull`. Inherits stdout so progress streams. Returns (ok, tail)."""
    if shutil.which("ollama") is None:
        return (False, "ollama not found — install from https://ollama.com, then `ollama serve`")
    try:
        r = subprocess.run(["ollama", "pull", name], timeout=timeout)
        if r.returncode == 0:
            return (True, f"pulled {name}")
        return (False, f"ollama pull exited {r.returncode} — retry `ollama pull {name}`")
    except subprocess.TimeoutExpired:
        return (False, f"pull timed out after {timeout}s — retry `ollama pull {name}`")
    except Exception as e:
        return (False, f"pull failed: {e}")
