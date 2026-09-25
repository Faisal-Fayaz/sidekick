"""User-defined tools (#49): TOOLS.md manifests compiled onto the agent loop.

Implements docs/plugins.md: declarations in skill dirs (never project
files) compile to TOOLS_SCHEMA entries + dispatcher closures over the
*existing* url/exec pipelines, so every allowlist/SSRF/approval guard
and property test covers plugins free. Unparseable manifests error
clearly and never brick the tool. Cached per process; tests reset via
clear_plugin_cache().
"""

from __future__ import annotations

import re
from pathlib import Path

TOOLS_FILENAME = "TOOLS.md"
MAX_TOOLS_PER_PACK = 10
MAX_PACKS = 30
MAX_DESC = 200
MAX_BODY_CHARS = 2000

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
KINDS = ("url-template", "shell-template")

_cache: dict | None = None


def clear_plugin_cache() -> None:
    """Drop cached specs (tests, and after new installs). Never raises."""
    global _cache
    try:
        _cache = None
    except Exception:
        pass


def _skills_dir() -> Path:
    import sk.skills as _skills

    return _skills.SKILLS_DIR


def find_manifests() -> list[Path]:
    """All TOOLS.md under the global skills dir only. Never project files."""
    try:
        base = _skills_dir()
        found = sorted(p for p in base.glob(f"**/{TOOLS_FILENAME}") if p.is_file())
        return found[:MAX_PACKS]
    except Exception:
        return []


def _builtins() -> set[str]:
    try:
        from .tools.registry import TOOLS_SCHEMA

        names: set[str] = set()
        for e in TOOLS_SCHEMA:
            if isinstance(e, dict):
                fn = e.get("function", {})
                if isinstance(fn, dict) and fn.get("name"):
                    names.add(str(fn["name"]))
        return names
    except Exception:
        return set()


def parse_manifest(text: str, source: str = "") -> tuple[dict | None, str | None]:
    """Validate one manifest. Returns (spec, None) or (None, warning)."""
    try:
        import sk.skills as _skills

        meta, _ = _skills.parse_frontmatter(text or "")
    except Exception as e:
        return (None, f"{source}: unreadable ({e})".strip())
    name = str(meta.get("tool", "")).strip()
    if not NAME_RE.match(name):
        return (None, f"{source}: bad tool name '{name}' (want {NAME_RE.pattern})".strip())
    if name in _builtins():
        return (None, f"{source}: '{name}' collides with a builtin (builtin wins)".strip())
    desc = str(meta.get("description", "")).strip()[:MAX_DESC] or name
    approval = str(meta.get("approval", "ask")).strip().lower() or "ask"
    if approval not in ("ask", "auto"):
        return (None, f"{source}: '{name}' bad approval '{approval}' (ask|auto)".strip())
    kind = str(meta.get("kind", "")).strip()
    if kind not in KINDS:
        return (
            None,
            f"{source}: '{name}' unknown kind '{kind}' (url-template|shell-template)".strip(),
        )
    template = str(meta.get("url" if kind == "url-template" else "cmd", "")).strip()
    if not template:
        return (
            None,
            f"{source}: '{name}' missing {'url' if kind == 'url-template' else 'cmd'}".strip(),
        )
    params: dict[str, dict] = {}
    raw_params = meta.get("params", "")
    if isinstance(raw_params, str) and raw_params.strip():
        # inline form: "city: string required, units: string" (no yaml dep)
        for chunk in raw_params.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            pname, _, rest = chunk.partition(":")
            pname = pname.strip()
            if not NAME_RE.match(pname):
                return (None, f"{source}: '{name}' bad param name '{pname}'".strip())
            words = rest.lower().split()
            ptype = "integer" if "integer" in words else "string"
            params[pname] = {
                "type": ptype,
                "required": ("required" in words),
                "max_len": 500,
            }
    if kind == "shell-template":
        approval = "ask"  # shell-shaped tools always gate, whatever the manifest says
    return (
        {
            "name": name,
            "description": desc,
            "approval": approval,
            "kind": kind,
            "template": template,
            "params": params,
            "source": source,
        },
        None,
    )


def _split_docs(text: str) -> list[str]:
    """Split a manifest file into frontmatter-bearing doc chunks.

    Pairs consecutive `---` fence lines; filler between docs parses to
    nothing and is skipped silently by the caller.
    """
    lines = (text or "").splitlines(keepends=True)
    fences = [i for i, ln in enumerate(lines) if ln.strip() == "---"]
    if len(fences) < 2:
        return [text]
    return ["".join(lines[a : b + 1]) + "body\n" for a, b in zip(fences, fences[1:])]


def load_specs() -> tuple[list[dict], list[str]]:
    """All valid specs + warnings. Cached. Never raises."""
    global _cache
    try:
        if _cache is not None:
            return (_cache["specs"], _cache["warnings"])
        specs: list[dict] = []
        warnings: list[str] = []
        seen: set[str] = set()
        for manifest in find_manifests():
            try:
                if manifest.stat().st_size > 20_000:
                    warnings.append(f"{manifest}: too large, skipped")
                    continue
                text = manifest.read_text(errors="replace")
            except Exception as e:
                warnings.append(f"{manifest}: unreadable ({e})")
                continue
            pack_specs = 0
            for doc in _split_docs(text):
                if pack_specs >= MAX_TOOLS_PER_PACK:
                    warnings.append(f"{manifest}: >{MAX_TOOLS_PER_PACK} tools, rest skipped")
                    break
                spec, warning = parse_manifest(doc, str(manifest))
                if spec is None:
                    if warning and ("tool" in doc and "kind" in doc):
                        warnings.append(warning)
                    continue
                if spec["name"] in seen:
                    warnings.append(f"{manifest}: duplicate '{spec['name']}' (first wins)")
                    continue
                seen.add(spec["name"])
                specs.append(spec)
                pack_specs += 1
        _cache = {"specs": specs, "warnings": warnings}
        return (specs, warnings)
    except Exception:
        return ([], [])


def plugin_names() -> set[str]:
    """Names of loaded plugin tools. Never raises."""
    try:
        specs, _ = load_specs()
        return {s["name"] for s in specs}
    except Exception:
        return set()


def approval_names() -> set[str]:
    """Plugin tools requiring approval. Never raises."""
    try:
        specs, _ = load_specs()
        return {s["name"] for s in specs if s.get("approval") == "ask"}
    except Exception:
        return set()


def schema_extra() -> list[dict]:
    """TOOLS_SCHEMA-style entries for loaded plugins. Never raises."""
    out: list[dict] = []
    try:
        specs, _ = load_specs()
        for spec in specs:
            props: dict[str, dict] = {}
            required: list[str] = []
            for pname, pdef in (spec.get("params") or {}).items():
                props[pname] = {
                    "type": pdef.get("type", "string"),
                    "description": f"Template slot {{{pname}}}",
                }
                if pdef.get("required"):
                    required.append(pname)
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": spec["name"],
                        "description": f"{spec['description']} (plugin from {Path(spec.get('source', '')).parent.name})",
                        "parameters": {"type": "object", "properties": props, "required": required},
                    },
                }
            )
    except Exception:
        pass
    return out


def _render(spec: dict, args: dict) -> str | None:
    """Substitute validated params into the template. None + error on failure."""
    try:
        values: dict[str, str] = {}
        for pname, pdef in (spec.get("params") or {}).items():
            raw = (args or {}).get(pname, "")
            if (raw is None or str(raw) == "") and pdef.get("required"):
                return None
            text = str(raw or "")
            if pdef.get("type") == "integer":
                try:
                    text = str(int(float(text))) if text.strip() else ""
                except (TypeError, ValueError):
                    return None
            values[pname] = text[: int(pdef.get("max_len", 500))]
        out = str(spec.get("template", ""))
        for pname, value in values.items():
            out = out.replace("{" + pname + "}", value)
        return out
    except Exception:
        return None


def dispatch_plugin(name: str, args: dict) -> str | None:
    """Run a plugin tool. None when name is not a plugin. Never raises."""
    try:
        specs, _ = load_specs()
        spec = next((s for s in specs if s["name"] == name), None)
        if spec is None:
            return None
        rendered = _render(spec, args or {})
        if rendered is None:
            return f"Error: missing/invalid params for plugin tool '{name}'."
        if spec.get("kind") == "url-template":
            from .tools.web import tool_read_url

            return tool_read_url(rendered)
        from .tools.read import tool_exec

        return tool_exec(rendered)
    except Exception as e:
        return f"Error: plugin tool '{name}' crashed: {e}"
