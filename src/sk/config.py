"""Config loader for sidekick. Reads ~/.sidekick/config.toml, creates defaults if missing."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from sk.atomic import atomic_write_text
from sk.fsperm import PRIVATE_FILE, ensure_private_dir, repair_once, tighten

try:
    import tomllib  # py3.11+
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

# Writing merges text into the existing file (see _merge_scalars) instead of
# parse-and-redump, so no TOML writer dependency is needed — which is exactly
# what lets nested tables and the user's comments survive a save (#347).


CONFIG_DIR = Path.home() / ".sidekick"
CONFIG_PATH = CONFIG_DIR / "config.toml"
PROFILE_ENV = "SIDEKICK_PROFILE"


def profiles_dir() -> Path:
    # Profiles hold provider settings and are only written by save(), which
    # tightens the file — but the directory itself was umask-default (#301).
    return ensure_private_dir(CONFIG_DIR / "profiles")


def profile_path(name: str) -> Path:
    """Validated path for a profile name. Raises on bad names."""
    import re as _re

    if not _re.fullmatch(r"[A-Za-z0-9_-]+", (name or "").strip()):
        raise RuntimeError(f"bad profile name {name!r} (use letters, digits, - _).")
    return profiles_dir() / f"{name.strip()}.toml"


def list_profiles() -> list[str]:
    """Sorted profile names on disk. Never raises."""
    try:
        d = profiles_dir()
        if not d.is_dir():
            return []
        return sorted(p.stem for p in d.glob("*.toml") if p.is_file())
    except Exception:
        return []


PROJECT_FILENAME = ".sidekick.toml"
# Keys a project file may never set: traffic diverters and self-granting authority.
# A hostile repo could otherwise point your prompts (incl. memories) at its own
# server, or pre-approve its own shell commands with no prompt shown to the user.
# `provider`/`model` are traffic selectors -- blocking `base_url` alone is not
# enough, since choosing a provider picks a base_url (closes #295).
PROJECT_BLOCKED_KEYS = (
    "api_key",
    "base_url",
    "mcp_servers",
    "hooks",
    "provider",
    "model",
    "max_steps",
    "temperature",
    "history_budget_tokens",
    "egress_allow",
)
# Policy keys a project file may not set either (warned, not security-critical).
PROJECT_POLICY_KEYS = ("spend_cap_usd",)
# Nested under `[project]`, so the top-level loop above cannot catch them.
# `approved_commands` self-grants shell approval with no user interaction
# beyond entering the directory (closes #296). It now lives as a top-level
# key in the global config only.
PROJECT_BLOCKED_SUBKEYS = (("project", "approved_commands"),)
# A project file contributes nothing from its top level. Not a limitation left
# over from #299 — it is the design: a repo must not be able to steer the agent's
# model, budget, or network. Only the [project] table is honoured.
#
# This was previously an (empty) PROJECT_ALLOWED_KEYS tuple, iterated on load
# and guarded by an assert against overlap. Dead scaffolding: it read like an
# extension point, and the obvious next step was to add a key to it and believe
# project files would honour it. The empty case is now stated directly. (#349)
#
# _PROJECT_CONSUMED is what the loader actually reads, used to warn about
# everything else. If a key ever becomes genuinely safe for a repo to set, add
# it here with a comment saying why a hostile repo cannot abuse it.
_PROJECT_CONSUMED: frozenset[str] = frozenset(
    set(PROJECT_BLOCKED_KEYS) | set(PROJECT_POLICY_KEYS) | {"project"}
)


def _is_custom_max_steps(file_vals: dict, project_vals: dict) -> bool:
    """True when the user set max_steps to a non-default value. Never raises.

    A configured value equal to the default is indistinguishable from an
    auto-saved default, so it follows the model profile (this is what lets
    existing installs adopt frontier budgets without editing config).
    """
    try:
        default = int(str(DEFAULTS["max_steps"]))
        for vals in (file_vals, project_vals):
            if "max_steps" in vals and int(str(vals["max_steps"])) != default:
                return True
        return False
    except Exception:
        return False


HOOK_EVENTS = ("SessionStart", "PreToolUse", "PostToolUse")


def _parse_hooks(raw: object) -> tuple[dict, ...]:
    """Normalize [[hooks.<Event>]] handler lists from the global config file.

    Invalid entries are dropped. Project files may not set this (blocked key:
    repos must not auto-run commands). Never raises.
    """
    if not isinstance(raw, dict):
        return ()
    out: list[dict] = []
    for event, handlers in raw.items():
        if str(event) not in HOOK_EVENTS:
            continue
        if isinstance(handlers, dict):
            handlers = [handlers]
        if not isinstance(handlers, list):
            continue
        for h in handlers:
            if not isinstance(h, dict):
                continue
            command = str(h.get("command", "") or "").strip()
            if not command:
                continue
            try:
                timeout = float(h.get("timeout", 30) or 30)
            except (TypeError, ValueError):
                timeout = 30.0
            out.append(
                {
                    "event": str(event),
                    "command": command,
                    "timeout": min(max(timeout, 1.0), 120.0),
                }
            )
    return tuple(out)


# MCP trust tiers. An untrusted server has every tool without a literal
# `readOnlyHint: true` treated as write-capable, which means approval before
# the call fires and no transparent retry after a transport failure. The default
# is `untrusted` (fail closed): a server nobody has classified must not be able
# to mutate external state silently. See #303.
MCP_TRUST_LEVELS = ("full", "untrusted")


def _normalize_trust(raw: object) -> str:
    """'full' | 'untrusted'. Unknown or missing -> 'untrusted'."""
    val = str(raw or "").strip().lower()
    return val if val in MCP_TRUST_LEVELS else "untrusted"


def _parse_inherit_env(raw: object) -> tuple[str, ...]:
    """Glob patterns of extra env vars to pass to a stdio MCP server (#302)."""
    if not isinstance(raw, list):
        return ()
    return tuple(str(p).strip() for p in raw if str(p).strip())


def _validate_mcp_url(url: str) -> bool:
    """True if `url` is a safe remote MCP endpoint.

    Stricter than a scheme prefix test (#303):
    - absolute http(s) only, so file:// and gopher:// cannot be reached
    - no userinfo: `https://evil.example@mcp.internal` resolves to
      mcp.internal while a human skimming the config sees evil.example and
      believes the credential goes there. Rejecting beats stripping.
    - no fragment: fragments never reach the server, so a fragment can carry a
      different value to a log reader than the request actually uses.
    - plain HTTP only for loopback, where traffic never leaves the host.
    Never raises.
    """
    import ipaddress
    from urllib.parse import urlsplit

    try:
        parsed = urlsplit(url)
    except Exception:
        return False
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    if parsed.fragment:
        return False
    host = parsed.hostname or ""
    if not host:
        return False
    if scheme == "http":
        if host == "localhost":
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False
    return True


def _parse_mcp_servers(raw: object) -> tuple[dict, ...]:
    """Normalize [mcp_servers.<name>] tables from the global config file.

    Either `command` (local stdio) or `url` (Streamable HTTP); setting both
    is refused (entry dropped). Invalid entries are dropped. Project files
    may not set this (blocked key: repos must not auto-spawn processes or
    phone home). Never raises.
    """
    import re as _re

    if not isinstance(raw, dict):
        return ()
    out: list[dict] = []
    for name, spec in raw.items():
        if not _re.fullmatch(r"[A-Za-z0-9_-]+", str(name)) or "__" in str(name):
            continue
        if not isinstance(spec, dict):
            continue
        command = str(spec.get("command", "") or "").strip()
        url = str(spec.get("url", "") or "").strip()
        if command and url:
            continue  # ambiguous transport: refuse, don't guess
        if url and not _validate_mcp_url(url):
            continue
        if not command and not url:
            continue
        args = spec.get("args", [])
        args = [str(a) for a in args] if isinstance(args, list) else []
        env = spec.get("env", {})
        env = {str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else {}
        headers = spec.get("headers", {})
        headers = {str(k): str(v) for k, v in headers.items()} if isinstance(headers, dict) else {}
        try:
            timeout = float(spec.get("timeout", 30) or 30)
        except (TypeError, ValueError):
            timeout = 30.0
        out.append(
            {
                "name": str(name),
                "command": command,
                "args": args,
                "env": env,
                "url": url,
                "headers": headers,
                "timeout": min(max(timeout, 1.0), 300.0),
                "trust": _normalize_trust(spec.get("trust")),
                "inherit_env": _parse_inherit_env(spec.get("inherit_env")),
            }
        )
    return tuple(out)


_EGRESS_BLOCKED_FORMS = ("*", "**", "0.0.0.0/0", "::/0")


def _parse_egress_allow(raw: object) -> tuple[str, ...]:
    """Normalise the egress allowlist. Drops unusable entries; never raises.

    A bare `*` is refused rather than honoured: an allow-everything entry is a
    footgun that looks exactly like a narrow rule in a config file, and it would
    silently turn the whole control off. Hosts are lowercased; entries keep a
    leading `*.` for suffix matching.
    """
    if not isinstance(raw, (list, tuple)):
        return ()
    out: list[str] = []
    for item in raw:
        host = str(item).strip().lower().rstrip(".")
        if not host or host in _EGRESS_BLOCKED_FORMS:
            continue
        if host not in out:
            out.append(host)
    return tuple(out)


def _parse_spend_cap(raw: object) -> float:
    """Non-negative USD cap, 0 = unlimited. Unparseable → 0 (a budgeting aid,
    not a security boundary — garbage config must not brick the tool)."""
    try:
        return max(0.0, float(str(raw or "").strip() or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _parse_int_default(raw: object, default: int, key: str) -> int:
    """int() that never bricks the CLI: garbage warns on stderr, default wins."""
    import sys

    try:
        return int(str(raw if raw is not None else default).strip() or default)
    except (TypeError, ValueError):
        print(
            f"warning: ignoring invalid {key}={raw!r} in config, using {default}."
            " Run `sk doctor --fix` to reset it.",
            file=sys.stderr,
        )
        return default


def _parse_float_default(raw: object, default: float, key: str) -> float:
    """float() that never bricks the CLI: garbage warns on stderr, default wins."""
    import sys

    try:
        return float(str(raw if raw is not None else default).strip() or default)
    except (TypeError, ValueError):
        print(
            f"warning: ignoring invalid {key}={raw!r} in config, using {default}."
            " Run `sk doctor --fix` to reset it.",
            file=sys.stderr,
        )
        return default


REASONING_EFFORTS = ("off", "minimal", "low", "medium", "high", "max")


def _parse_reasoning_effort(raw: object) -> str:
    """Reasoning effort level. Unparseable → 'low' (garbage config must not
    brick the tool; unknown levels would 400 on strict providers)."""
    try:
        level = str(raw or "").strip().lower()
    except Exception:
        return "low"
    return level if level in REASONING_EFFORTS else "low"


def find_project_file(start: str | Path = "") -> Path | None:
    """Nearest .sidekick.toml walking up from start (default: cwd). None if absent."""
    cur = Path(start or os.getcwd()).expanduser().resolve()
    for _ in [cur, *cur.parents]:
        candidate = cur / PROJECT_FILENAME
        try:
            if candidate.is_file():
                return candidate
        except Exception:
            pass
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    return None


def load_project_values(path: str | Path | None) -> tuple[dict[str, object], list[str]]:
    """Parse a project file. Returns (values, warnings). Never raises."""
    warnings: list[str] = []
    if not path:
        return ({}, warnings)
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except Exception as e:
        return ({}, [f"ignoring unreadable {path}: {e}"])
    if not isinstance(raw, dict):
        return ({}, [f"ignoring malformed {path}: top level must be a table"])
    vals: dict[str, object] = {}
    for key in PROJECT_BLOCKED_KEYS:
        if key in raw:
            warnings.append(f"ignoring {key} in {path} (global config or env only)")
    for key in PROJECT_POLICY_KEYS:
        if key in raw:
            warnings.append(f"ignoring {key} in {path} (spend policy is global/env only)")
    # Anything else at the top level is ignored too, and used to say nothing
    # about it. Silence reads as "accepted", so a typo or a plausible-but-
    # global-only key gave the user a project file that silently did nothing —
    # with a reduced max_steps looking like deliberate tuning. Warn on all of it.
    for key in raw:
        if key in _PROJECT_CONSUMED:
            continue
        warnings.append(
            f"ignoring {key} in {path} (not a project-file setting; "
            f"project files only carry a [project] table)"
        )
    proj = raw.get("project", {})
    if isinstance(proj, dict):
        for table, key in PROJECT_BLOCKED_SUBKEYS:
            if key in proj:
                warnings.append(
                    f"ignoring [{table}].{key} in {path} "
                    f"(approval policy is global config or env only)"
                )
        docs = proj.get("docs", [])
        if isinstance(docs, list):
            vals["project_docs"] = [str(d) for d in docs if str(d).strip()]
        ns = proj.get("memory_namespace", "")
        if str(ns).strip():
            vals["memory_namespace"] = str(ns).strip()
    return (vals, warnings)


# OpenAI-compatible providers. Anything speaking /v1/chat/completions works,
# including local servers (ollama, LM Studio, llama.cpp --server).
# Exception: "anthropic" speaks the native Messages API (see anthropic_backend);
# its base_url is the API root (paths appended by the backend, not /v1 here).
PRESETS: dict[str, dict[str, str]] = {
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "key": "ollama",
        "model": "qwen2.5-coder:7b",
    },
    "openai": {"base_url": "https://api.openai.com/v1", "key": "", "model": "gpt-4o-mini"},
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "key": "",
        "model": "openai/gpt-oss-20b",
    },
    "together": {
        "base_url": "https://api.together.xyz/v1",
        "key": "",
        "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    },
    "deepseek": {"base_url": "https://api.deepseek.com/v1", "key": "", "model": "deepseek-chat"},
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "key": "",
        "model": "openai/gpt-4o-mini",
    },
    "google": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "key": "",
        "model": "models/gemini-3.6-flash",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "key": "",
        "model": "models/gemini-3.6-flash",
    },
    "lmstudio": {
        "base_url": "http://localhost:1234/v1",
        "key": "lm-studio",
        "model": "local-model",
    },
    "anthropic": {
        "base_url": "https://api.anthropic.com",
        "key": "",
        "model": "claude-sonnet-5",
    },
    "custom": {"base_url": "", "key": "", "model": ""},
}

# OpenCode Zen free models (cost 0, tools-capable, not deprecated today).
# Shipped so users can point time-demanding agent tasks at opencode's free
# tier; requires OPENCODE_API_KEY (free account) — the anonymous tier is
# guarded by opencode itself. Refresh from https://models.dev when in doubt.
OPENCODE_FREE_MODELS: tuple[str, ...] = (
    "ling-3.0-flash-fin-free",
    "mimo-v2.6-flash-free",
    "muse-spark-1.3-contributor-free",
    "nemotron-3-ultra-free",
    "nemotron-3.5-lightning-free",
    "space-bunny-free",
    "big-pickle",
)
PRESETS["opencode"] = {
    "base_url": "https://opencode.ai/zen/v1",
    "key": "",
    "model": OPENCODE_FREE_MODELS[2],
}

# fast/smart tiers per provider. Only verified IDs here; unknown tiers fall
# back to the provider default so aliases never 404.
TIERS: dict[str, dict[str, str]] = {
    "ollama": {"fast": "llama3.2:3b", "smart": "qwen2.5-coder:7b"},
    "openai": {"fast": "gpt-4o-mini", "smart": "gpt-4o"},
    "groq": {"fast": "openai/gpt-oss-20b", "smart": "openai/gpt-oss-120b"},
    "deepseek": {"fast": "deepseek-chat", "smart": "deepseek-reasoner"},
    "google": {"fast": "models/gemini-3.6-flash", "smart": "models/gemini-3.8-flash"},
    "gemini": {"fast": "models/gemini-3.6-flash", "smart": "models/gemini-3.8-flash"},
    "anthropic": {"fast": "claude-haiku-4-5", "smart": "claude-sonnet-5"},
    "opencode": {"fast": "nemotron-3.5-lightning-free", "smart": "muse-spark-1.3-contributor-free"},
}


def provider_tier(provider: str, tier: str, fallback: str) -> str:
    """Resolve fast/smart for a provider, falling back to the saved default."""
    return TIERS.get(provider, {}).get(tier, "") or fallback


def resolve_alias(provider: str, alias: str, fallback: str) -> str:
    """Shared fast/smart alias resolver for CLI + slash. Returns fallback for unknown."""
    m = (alias or "").strip()
    if m in ("fast", "smart"):
        return provider_tier(provider, m, fallback)
    return m or fallback


def is_project_approved(name: str, args: dict, approved_commands: tuple[str, ...]) -> bool:
    """True if a shell command matches the project approved_commands list.

    Exact match, or the entry is a prefix ending at a word boundary
    ("pytest -q" covers "pytest -q tests/x" but not "pytest -qz").
    Shell tool only; writes always ask.
    """
    if name != "shell" or not approved_commands:
        return False
    cmd = str((args or {}).get("cmd", "")).strip()
    for entry in approved_commands:
        e = entry.strip()
        if not e:
            continue
        if cmd == e or cmd.startswith(e + " ") or cmd.startswith(e + "\t"):
            return True
    return False


def parse_allow_list(raw: str | list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Normalize --allow input to entries. Accepts 'a,b c' or a list.

    Entries are tool names ('write_file') or shell scopes ('shell:pytest').
    Never raises; garbage in, empty tuple out.
    """
    try:
        parts: list[str] = []
        items = [raw] if isinstance(raw, str) else list(raw or [])
        for item in items:
            for chunk in str(item).replace(",", " ").split():
                chunk = chunk.strip()
                if chunk:
                    parts.append(chunk)
        return tuple(parts)
    except Exception:
        return ()


def _entry_matches(name: str, args: dict, entries: list[str]) -> bool:
    """Shared tool-name / shell-scope matcher for allow and deny lists."""
    for e in entries:
        if ":" in e:
            tool, scope = e.split(":", 1)
            tool, scope = tool.strip(), scope.strip()
            if tool != name or not scope:
                continue
            if name == "shell":
                cmd = str((args or {}).get("cmd", "")).strip()
                if cmd == scope or cmd.startswith(scope + " ") or cmd.startswith(scope + "\t"):
                    return True
            else:
                return True  # tool:path scope reserved; tool match suffices today
        elif e == name:
            return True
    return False


def is_session_allowed(name: str, args: dict, allow: tuple[str, ...] | list[str]) -> bool:
    """True if a session --allow entry covers this approval-gated tool call.

    'write_file' allows the whole tool; 'shell:pytest' allows shell
    commands starting at a word boundary ('pytest -q' yes, 'pytest-x' no);
    bare 'shell' allows all shell. Non-gated tools need no entry.
    Pure function, safe to unit test.
    """
    try:
        entries = [str(e).strip() for e in (allow or []) if str(e).strip()]
    except Exception:
        return False
    if not entries:
        return False
    try:
        return _entry_matches(name, args, entries)
    except Exception:
        return False


def is_session_denied(name: str, args: dict, deny: tuple[str, ...] | list[str]) -> bool:
    """True if a session --deny entry blocks this tool call. Same matching
    semantics as --allow; where both match, deny wins (checked first by all
    approvers). Pure function, safe to unit test.
    """
    try:
        entries = [str(e).strip() for e in (deny or []) if str(e).strip()]
    except Exception:
        return False
    if not entries:
        return False
    try:
        return _entry_matches(name, args, entries)
    except Exception:
        return False


def _toml_value(v: object) -> str:
    """Render one scalar or flat-array value as TOML (bools lowercase)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, (int, float)):
        return f"{v}"
    if isinstance(v, (list, tuple)):
        # a bare f"{v}" would emit Python repr — invalid TOML
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    return f'"{v}"'


_SCALAR_LINE = re.compile(r"^\s*([A-Za-z0-9_-]+)\s*=")


def _merge_scalars(existing: str, managed: dict) -> str:
    """Return `existing` with `managed`'s keys set, preserving everything else.

    Operates on text rather than parse-and-redump for two reasons: a nested table
    like `[mcp_servers.files]` cannot be re-emitted without a TOML writer, and
    round-tripping through one discards the user's comments and formatting — in a
    file people hand-edit, that is itself a loss.

    Only lines before the first table header are touched. Everything from that
    header onward is copied verbatim, so a managed key name that also appears
    inside a table (`[hooks]` with `command = ...`) is never rewritten.
    """
    lines = existing.split("\n")
    table_start = next((i for i, ln in enumerate(lines) if ln.strip().startswith("[")), len(lines))

    out: list[str] = []
    seen: set[str] = set()
    for i, line in enumerate(lines):
        if i >= table_start:
            break
        m = _SCALAR_LINE.match(line)
        if m and m.group(1) in managed:
            key = m.group(1)
            # preserve any trailing comment on the line we replace
            tail = line.split("#", 1)[1] if "#" in line else ""
            out.append(f"{key} = {_toml_value(managed[key])}" + (f"  #{tail}" if tail else ""))
            seen.add(key)
            continue
        out.append(line)

    missing = [k for k in managed if k not in seen]
    if missing:
        # trim the blank lines we may have just copied past the last value, so we
        # do not accumulate a growing gap between the scalars and the tables
        while out and not out[-1].strip():
            out.pop()
        if out:
            out.append("")
        for k in missing:
            out.append(f"{k} = {_toml_value(managed[k])}")

    head = "\n".join(out).rstrip("\n")
    tail = "\n".join(lines[table_start:]).strip("\n")
    return (head + "\n\n" + tail + "\n") if tail else head + "\n"


def _write_toml_preserving(target: Path, existing: str, managed: dict) -> None:
    """Merge, then write atomically (#316 shares the helper with every other sink)."""
    atomic_write_text(target, _merge_scalars(existing, managed))


DEFAULTS: dict[str, str | int | float] = {
    "provider": "ollama",
    "model": str(PRESETS["ollama"]["model"]),
    "base_url": "",  # empty = preset default; set = override (or custom's URL)
    "api_key": "",
    "max_steps": 5,
    "temperature": 0.2,
    "theme": "dark",
    "history_budget_tokens": 3000,
    # Total prompt window, 0 = take it from the model profile (#308). Raise this
    # on a local model with VRAM to spare: the KV cache grows with it, and the
    # prompt budget is derived from whatever this resolves to.
    "context_window": 0,
    "spend_cap_usd": 0.0,
    "reasoning_effort": "low",
}


@dataclass
class Config:
    provider: str = str(DEFAULTS["provider"])
    model: str = str(DEFAULTS["model"])
    base_url: str = str(DEFAULTS["base_url"])  # override; "" = preset default
    api_key: str = str(DEFAULTS["api_key"])
    max_steps: int = int(DEFAULTS["max_steps"])
    max_steps_custom: bool = False  # runtime provenance: user set max_steps
    # to a non-default value in a config file (global or project). Never
    # persisted; lets the agent prefer explicit config over model-profile
    # budgets. A configured value equal to the default follows the profile
    # (auto-saved defaults must not pin legacy installs to lean budgets).
    temperature: float = float(DEFAULTS["temperature"])
    theme: str = str(DEFAULTS["theme"])
    history_budget_tokens: int = int(DEFAULTS["history_budget_tokens"])
    context_window: int = int(DEFAULTS["context_window"])
    spend_cap_usd: float = float(DEFAULTS["spend_cap_usd"])  # 0 = unlimited; global/env only
    reasoning_effort: str = str(DEFAULTS["reasoning_effort"])  # off|minimal|low|medium|high|max
    # project layer (from .sidekick.toml; empty when outside a project)
    project_root: str = ""
    project_docs: tuple[str, ...] = ()
    memory_namespace: str = ""
    approved_commands: tuple[str, ...] = ()
    # Destinations the MODEL may cause us to fetch (read_url / web_search /
    # image downloads). Empty = no network. Global/env only: a repo must not be
    # able to widen its own egress (#328).
    egress_allow: tuple[str, ...] = ()
    project_warnings: tuple[str, ...] = ()
    profile: str = ""  # active profile name (SIDEKICK_PROFILE / --profile), else ""
    mcp_servers: tuple[dict, ...] = ()  # global config file only; never from projects
    hooks: tuple[dict, ...] = ()  # global config file only; never from projects

    def effective_base_url(self) -> str:
        if self.base_url.strip():
            return self.base_url.strip()
        preset = PRESETS.get(self.provider, PRESETS["custom"])
        return preset["base_url"]

    def effective_api_key(self) -> str:
        if self.api_key.strip():
            return self.api_key.strip()
        if self.provider == "opencode":
            env_key = os.getenv("OPENCODE_API_KEY", "").strip()
            if env_key:
                return env_key
        try:
            from . import keyring as _kr

            key = _kr.get_key(self.provider)
            if key:
                return key
        except Exception:
            pass
        preset = PRESETS.get(self.provider, PRESETS["custom"])
        return preset["key"]

    @classmethod
    def load(cls, cwd: str = "") -> Config:
        """Precedence: env > project file (.sidekick.toml upward from cwd) > global file.

        Sensitive keys (api_key, base_url) never come from project files.
        spend_cap_usd is global/env only: repos must not set their own limits.
        Also publishes the memory namespace for store scoping (see store docs).

        Tightens ~/.sidekick on the way past. It has to be here rather than in
        ensure_created(): that is only called by `sk <command>`, and the TUI —
        the primary interface — calls load() directly, so a migration placed
        there never reached TUI users at all (#301 follow-up). repair_once()
        makes the walk happen at most once per process, so this costs a stat and
        a flag check in steady state.
        """
        ensure_private_dir(CONFIG_DIR)
        repair_once(CONFIG_DIR)
        provider = os.getenv("SIDEKICK_PROVIDER", "")
        model = os.getenv("SIDEKICK_MODEL", "")
        base_url = os.getenv("SIDEKICK_BASE_URL", "")
        api_key = os.getenv("SIDEKICK_API_KEY", "")
        spend_cap = os.getenv("SIDEKICK_SPEND_CAP", "")
        reasoning_effort = os.getenv("SIDEKICK_REASONING_EFFORT", "")

        file_vals: dict[str, object] = {}
        profile = (os.getenv(PROFILE_ENV, "") or "").strip()
        if profile:
            path = profile_path(profile)  # raises on bad names
            if not path.exists():
                raise RuntimeError(f"no such profile {profile!r} (sk config --profiles to list).")
            try:
                with open(path, "rb") as f:
                    file_vals = tomllib.load(f)
            except Exception as e:
                raise RuntimeError(f"unreadable profile {profile!r}: {e}")
        elif CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, "rb") as f:
                    file_vals = tomllib.load(f)
            except Exception:
                file_vals = {}

        project_file = find_project_file(cwd or os.getcwd())
        project_vals, project_warnings = load_project_values(project_file)
        vals: dict[str, object] = dict(file_vals)
        vals.update(project_vals)

        prov = str(provider or vals.get("provider", DEFAULTS["provider"])).strip().lower()
        if prov not in PRESETS:
            prov = "custom"
        from sk.tui.theme import normalize_theme_name

        theme = normalize_theme_name(vals.get("theme", DEFAULTS["theme"]))
        cfg = cls(
            provider=prov,
            model=str(
                model or vals.get("model", "") or PRESETS[prov]["model"] or DEFAULTS["model"]
            ),
            base_url=str(base_url or vals.get("base_url", "")),
            api_key=str(api_key or vals.get("api_key", "")),
            max_steps=_parse_int_default(
                vals.get("max_steps", DEFAULTS["max_steps"]),
                int(DEFAULTS["max_steps"]),
                "max_steps",
            ),
            max_steps_custom=_is_custom_max_steps(file_vals, project_vals),
            temperature=_parse_float_default(
                vals.get("temperature", DEFAULTS["temperature"]),
                float(DEFAULTS["temperature"]),
                "temperature",
            ),
            context_window=_parse_int_default(
                vals.get("context_window", DEFAULTS["context_window"]),
                int(DEFAULTS["context_window"]),
                "context_window",
            ),
            history_budget_tokens=_parse_int_default(
                vals.get("history_budget_tokens", DEFAULTS["history_budget_tokens"]),
                int(DEFAULTS["history_budget_tokens"]),
                "history_budget_tokens",
            ),
            spend_cap_usd=_parse_spend_cap(
                spend_cap or vals.get("spend_cap_usd", DEFAULTS["spend_cap_usd"])
            ),
            reasoning_effort=_parse_reasoning_effort(
                reasoning_effort or vals.get("reasoning_effort", DEFAULTS["reasoning_effort"])
            ),
            theme=theme,
            project_root=str(project_file.parent) if project_file else "",
            project_docs=tuple(vals.get("project_docs", [])),  # type: ignore[arg-type]
            memory_namespace=str(vals.get("memory_namespace", "")),
            approved_commands=tuple(file_vals.get("approved_commands", []) or ()),  # type: ignore[arg-type]
            egress_allow=_parse_egress_allow(file_vals.get("egress_allow", [])),
            project_warnings=tuple(project_warnings),
            mcp_servers=_parse_mcp_servers(file_vals.get("mcp_servers", {})),
            hooks=_parse_hooks(file_vals.get("hooks", {})),
            profile=profile,
        )
        cfg.normalize_model_alias()
        try:
            from .store import set_default_namespace

            set_default_namespace(cfg.memory_namespace)
        except Exception:
            pass
        return cfg

    def project_note(self) -> str:
        """One-liner for `sk config --show`: which project layer (if any) applies."""
        if not self.project_root:
            return ""
        return f"project: {self.project_root} ({PROJECT_FILENAME})"

    def ensure_created(self) -> Path:
        # 0700, not the umask default (#301): every conversation, shell command
        # and checkpoint lives under here. Tightens a directory created by an
        # older version rather than only fixing new installs.
        ensure_private_dir(CONFIG_DIR)
        if not CONFIG_PATH.exists():
            self.save()
        return CONFIG_PATH

    def normalize_model_alias(self) -> bool:
        """A model that names a provider preset was meant as that provider.

        `--model groq`/`/model groq` sets model="groq", which 404s on every API.
        Self-heal: switch provider to it and adopt its default model
        (model=groq -> provider=groq, model=openai/gpt-oss-20b). Returns True
        if a change was applied.
        """
        name = self.model.strip().lower()
        if name in PRESETS and name != "custom":
            default = (PRESETS[name]["model"] or "").strip()
            if default:
                self.provider = name
                self.model = default
                return True
        return False

    def save(self, path=None) -> None:
        """Persist settings. Writes to the active profile file when one is
        active, else the global config file. `path` overrides both.

        Only the scalar keys this class owns are written. Everything else in the
        file — `[mcp_servers]`, `[hooks]`, `[skills]`, comments, the user's own
        formatting — is left exactly as found. Rewriting the file from a fixed
        key set silently deleted those tables on every `sk config` call (#347).
        """
        self.normalize_model_alias()

        target = (
            Path(path) if path else (profile_path(self.profile) if self.profile else CONFIG_PATH)
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            existing = target.read_text(encoding="utf-8")
        except Exception:
            existing = ""

        managed = {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "api_key": self.api_key,
            "max_steps": self.max_steps,
            "temperature": self.temperature,
            "theme": self.theme,
            "history_budget_tokens": self.history_budget_tokens,
            "context_window": self.context_window,
            "spend_cap_usd": self.spend_cap_usd,
            "reasoning_effort": self.reasoning_effort,
            "egress_allow": list(self.egress_allow),
        }
        _write_toml_preserving(target, existing, managed)

        # Not conditional on api_key. Every file under ~/.sidekick is private
        # now (#301), and a chmod that only ran when a key was set read like the
        # guarantee was conditional when it never was — atomic_write_text
        # creates at 0600 via mkstemp regardless.
        tighten(target, want=PRIVATE_FILE)

    @staticmethod
    def mask(key: str) -> str:
        """Redacted key for display. Reveals nothing about the secret.

        Was `first3…last4` — so `sk config --show`, `sk auth`, `sk doctor`, the
        status line and every report carried 7 characters of a live credential.
        Those characters are *confirmatory*, not decorative: an attacker holding a
        suspected key can test a guess against them, so prefix/suffix shortened an
        otherwise high-entropy secret's search space in exchange for nothing. It
        also looked silly on a uniform key, where it printed `xxx…xxxx`.

        The diagnostic value was thinner than it looked. `cfg.provider` already
        says which provider a key belongs to, so a `sk-` prefix adds nothing; and
        `sk auth` separately prints `key_source(...)`, which answers the question
        people actually ask — *where* did this key come from.

        So `(none)` when unset, `****` when set. Set-versus-unset survives, which
        is the only property any call site depended on.

        Deliberately no opt-in flag to reveal more. A "show me my key" flag gets
        pasted into logs, scrollback and bug reports, which is where this leak
        came from in the first place. To see the key, read your own config file.
        (#269)
        """
        key = (key or "").strip()
        return "****" if key else "(none)"
