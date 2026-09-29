"""Config loader for sidekick. Reads ~/.sidekick/config.toml, creates defaults if missing."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib  # py3.11+
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

try:
    import tomli_w  # for writing; fallback to manual write

    _HAS_TOMLI_W = True
except ImportError:
    _HAS_TOMLI_W = False


CONFIG_DIR = Path.home() / ".sidekick"
CONFIG_PATH = CONFIG_DIR / "config.toml"
PROFILE_ENV = "SIDEKICK_PROFILE"


def profiles_dir() -> Path:
    return CONFIG_DIR / "profiles"


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
# Keys a project file may never set: traffic diverters. A hostile repo could
# otherwise point your prompts (incl. memories) at its own server.
PROJECT_BLOCKED_KEYS = ("api_key", "base_url", "mcp_servers", "hooks")
# Policy keys a project file may not set either (warned, not security-critical).
PROJECT_POLICY_KEYS = ("spend_cap_usd",)


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
        if url and not _re.match(r"https?://", url):
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
            }
        )
    return tuple(out)


def _parse_spend_cap(raw: object) -> float:
    """Non-negative USD cap, 0 = unlimited. Unparseable → 0 (a budgeting aid,
    not a security boundary — garbage config must not brick the tool)."""
    try:
        return max(0.0, float(str(raw or "").strip() or 0.0))
    except (TypeError, ValueError):
        return 0.0


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
    for key in ("provider", "model", "max_steps", "temperature", "history_budget_tokens"):
        if key in raw:
            vals[key] = raw[key]
    for key in PROJECT_BLOCKED_KEYS:
        if key in raw:
            warnings.append(f"ignoring {key} in {path} (global config or env only)")
    for key in PROJECT_POLICY_KEYS:
        if key in raw:
            warnings.append(f"ignoring {key} in {path} (spend policy is global/env only)")
    proj = raw.get("project", {})
    if isinstance(proj, dict):
        docs = proj.get("docs", [])
        if isinstance(docs, list):
            vals["project_docs"] = [str(d) for d in docs if str(d).strip()]
        ns = proj.get("memory_namespace", "")
        if str(ns).strip():
            vals["memory_namespace"] = str(ns).strip()
        cmds = proj.get("approved_commands", [])
        if isinstance(cmds, list):
            vals["approved_commands"] = [str(c) for c in cmds if str(c).strip()]
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


DEFAULTS: dict[str, str | int | float] = {
    "provider": "ollama",
    "model": str(PRESETS["ollama"]["model"]),
    "base_url": "",  # empty = preset default; set = override (or custom's URL)
    "api_key": "",
    "max_steps": 5,
    "temperature": 0.2,
    "theme": "dark",
    "history_budget_tokens": 3000,
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
    spend_cap_usd: float = float(DEFAULTS["spend_cap_usd"])  # 0 = unlimited; global/env only
    reasoning_effort: str = str(DEFAULTS["reasoning_effort"])  # off|minimal|low|medium|high|max
    # project layer (from .sidekick.toml; empty when outside a project)
    project_root: str = ""
    project_docs: tuple[str, ...] = ()
    memory_namespace: str = ""
    approved_commands: tuple[str, ...] = ()
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
        """
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
            max_steps=int(str(vals.get("max_steps", DEFAULTS["max_steps"]))),
            max_steps_custom=_is_custom_max_steps(file_vals, project_vals),
            temperature=float(str(vals.get("temperature", DEFAULTS["temperature"]))),
            history_budget_tokens=int(
                str(vals.get("history_budget_tokens", DEFAULTS["history_budget_tokens"]))
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
            approved_commands=tuple(vals.get("approved_commands", [])),  # type: ignore[arg-type]
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
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
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
        active, else the global config file. `path` overrides both."""
        self.normalize_model_alias()
        import os as _os

        target = (
            Path(path) if path else (profile_path(self.profile) if self.profile else CONFIG_PATH)
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "api_key": self.api_key,
            "max_steps": self.max_steps,
            "temperature": self.temperature,
            "theme": self.theme,
            "history_budget_tokens": self.history_budget_tokens,
            "spend_cap_usd": self.spend_cap_usd,
            "reasoning_effort": self.reasoning_effort,
        }
        if _HAS_TOMLI_W:
            with open(target, "wb") as f:
                tomli_w.dump(data, f)
        else:
            # minimal manual writer, no dependency needed (bools lowercase: valid TOML)
            def _toml(v):
                if isinstance(v, bool):
                    return "true" if v else "false"
                if isinstance(v, str):
                    return f'"{v}"'
                return f"{v}"

            lines = [f"{k} = {_toml(v)}" for k, v in data.items()]
            target.write_text("\n".join(lines) + "\n")
        if self.api_key.strip():
            try:
                _os.chmod(target, 0o600)
            except Exception:
                pass

    @staticmethod
    def mask(key: str) -> str:
        key = (key or "").strip()
        if len(key) <= 8:
            return "****" if key else "(none)"
        return f"{key[:3]}…{key[-4:]}"
