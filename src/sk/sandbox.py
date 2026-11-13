"""Declarative sandbox policy for the tool layer (#329).

Permissions decide *whether* a process spawns; a sandbox decides *what* that
process can reach. Sidekick has only the first: the approval gate, the hard
shell blocklist, the protected-path list. That is a floor, not a ceiling — the
shell blocklist is a denylist over strings, and a denylist over shell strings is
structurally incomplete (#288).

This module is Phase 1 of the ceiling: **a policy file, no new dependency,
enforced in one chokepoint** — the tool dispatcher — so a new tool cannot
forget the boundary the way a per-tool check can.

The posture is deny-by-default. A read outside the declared roots is refused.
So is network access. Anything the policy does not name is outside.

## The policy file

``~/.sidekick/sandbox.toml`` — user-owned, never project-owned. A cloned
repository must not be able to widen what the agent can reach, the same reason
every other project-supplied file in Sidekick is untrusted by default.

.. code-block:: toml

    schema = 1
    mode = "enforced"          # enforced | advisory | off

    [filesystem]
    read_roots = ["~/src"]     # extra roots the agent may read
    write_roots = []           # extra roots the agent may write
    deny = ["~/.ssh", "~/.aws"]  # denied even inside an allowed root

    [network]
    allow = ["api.openai.com"]  # hosts the shell may reach; empty = no network

The project root is readable and writable by default; everything else has to
be named.

## Enforcement is not permission

They are separate planes. A command the user approved can still be refused by
the sandbox; a command the sandbox allows still asks for approval. Neither
plane can widen the other.

## The ceiling this does not reach

The shell check reads the command string. It sees the paths and hosts it can
parse, including ones hidden behind variables or quoting, but it cannot see
what a program does once it runs: ``python -c "open('/etc/shadow','w')"`` is a
write to a path the parser never sees. Phase 2 (bubblewrap / Seatbelt) and
Phase 3 (network namespaces) are what close that, and they need optional
system packages, so they are deliberately not here. See ``docs/sandbox.md``.
"""

from __future__ import annotations

import os
import re
import shlex
import tomllib
from dataclasses import dataclass
from pathlib import Path

SCHEMA = 1
MODES = ("off", "advisory", "enforced")
POLICY_FILENAME = "sandbox.toml"

# Paths every process needs at runtime. The parser sees them in ordinary
# commands (`ls /dev/null`), and refusing them would break the tool without
# protecting anything: none of them carries project or user data.
SYSTEM_ALWAYS_READ = (
    "/dev/null",
    "/dev/zero",
    "/dev/stdin",
    "/dev/stdout",
    "/dev/stderr",
    "/dev/urandom",
    "/dev/random",
    "/etc/ld.so.cache",
    "/etc/localtime",
    "/etc/terminfo",
    "/etc/ssl/certs",
    "/proc/self",
    "/proc/self/",
)

# Commands whose arguments are files. For these, even a bare name ("cat
# passwd") is a path, and is checked against the roots.
READ_COMMANDS = frozenset(
    {
        "cat",
        "head",
        "tail",
        "less",
        "more",
        "nl",
        "od",
        "xxd",
        "strings",
        "file",
        "stat",
        "wc",
        "grep",
        "egrep",
        "fgrep",
        "rg",
        "ag",
        "cut",
        "sort",
        "uniq",
        "tr",
        "jq",
        "diff",
        "cmp",
        "md5sum",
        "sha1sum",
        "sha256sum",
        "realpath",
        "readlink",
        "basename",
        "dirname",
        "ls",
    }
)

# Commands that clearly *write*. Their path arguments need a write root, not
# just a readable one. The list is deliberately short: a wrong guess here
# either blocks ordinary work or lets a write through, so ambiguous commands
# (python, node, git) are treated as readers and the gap is stated in the docs.
WRITE_COMMANDS = frozenset(
    {
        "rm",
        "rmdir",
        "unlink",
        "shred",
        "mv",
        "cp",
        "install",
        "dd",
        "tee",
        "truncate",
        "touch",
        "mkdir",
        "chmod",
        "chown",
        "chgrp",
        "ln",
        "mkfs",
        "mount",
        "rsync",
        "tar",
        "zip",
        "unzip",
    }
)

# Commands that reach the network. A URL anywhere in the command is checked
# regardless of the command; this list catches the bare-host forms
# (`ssh host`, `nc host 443`) that carry no scheme.
NETWORK_COMMANDS = frozenset(
    {
        "curl",
        "wget",
        "nc",
        "ncat",
        "netcat",
        "ssh",
        "scp",
        "sftp",
        "rsync",
        "ftp",
        "telnet",
        "socat",
        "openssl",
        "aria2c",
        "httpie",
        "http",
        "https",
    }
)

REDIRECTS = frozenset({">", ">>", ">|", "1>", "2>", "&>", "1>>", "2>>"})

_URL_RE = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s'\"`|;&)]+")
_SPLIT_RE = re.compile(r"[\s,;:'\"()\[\]{}=]+")


@dataclass(frozen=True)
class SandboxPolicy:
    """The effective policy. `mode` decides whether a violation blocks."""

    mode: str = "off"
    read_roots: tuple[str, ...] = ()
    write_roots: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()
    allow_hosts: tuple[str, ...] = ()
    project_root: str = ""
    path: str = ""
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def active(self) -> bool:
        """True when a policy file exists, whatever the mode."""
        return self.mode != "off" or bool(self.path)

    @property
    def enforcing(self) -> bool:
        """True when a violation must block the call."""
        return self.mode == "enforced"

    def describe(self) -> str:
        """One line for `sk doctor` and `sk config --show`."""
        if not self.path:
            return "off (no policy file)"
        roots = f"read={len(self.read_roots)} write={len(self.write_roots)}"
        hosts = f"network={'on' if self.allow_hosts else 'off'}"
        return f"{self.mode} ({roots}, {hosts}, policy={self.path})"


@dataclass
class _Roots:
    """Resolved absolute roots for one project."""

    project: Path
    read: tuple[Path, ...] = ()
    write: tuple[Path, ...] = ()
    deny: tuple[Path, ...] = ()


# --- loading ---------------------------------------------------------------


def policy_path(config_dir: str | Path | None = None) -> Path:
    """Where the policy file lives. User-global, never project-local."""
    if config_dir is not None:
        return Path(config_dir) / POLICY_FILENAME
    from .config import CONFIG_DIR

    return Path(CONFIG_DIR) / POLICY_FILENAME


def _expand(raw: str) -> str:
    try:
        return str(Path(os.path.expanduser(str(raw).strip())))
    except Exception:
        return str(raw)


def load_policy(
    *,
    project_root: str = "",
    path: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> SandboxPolicy:
    """Read the policy file. Never raises; a broken file yields mode `off`."""
    target = Path(path) if path is not None else policy_path(config_dir)
    root = _expand(project_root or os.getcwd())
    if not target.is_file():
        return SandboxPolicy(mode="off", project_root=root)
    try:
        raw = tomllib.loads(target.read_text(errors="replace"))
    except Exception as e:
        return SandboxPolicy(
            mode="off",
            project_root=root,
            path=str(target),
            errors=(f"{target}: {e}",),
        )
    if not isinstance(raw, dict):
        return SandboxPolicy(
            mode="off",
            project_root=root,
            path=str(target),
            errors=(f"{target}: not a table",),
        )
    errors: list[str] = []
    warnings: list[str] = []
    schema = raw.get("schema", SCHEMA)
    if schema != SCHEMA:
        errors.append(f"{target}: schema {schema!r} is not supported (expected {SCHEMA})")
    mode = str(raw.get("mode", "enforced")).strip().lower() or "enforced"
    if mode not in MODES:
        errors.append(f"{target}: mode {mode!r} is not one of {', '.join(MODES)}")
        mode = "off"
    fs = raw.get("filesystem")
    fs = fs if isinstance(fs, dict) else {}
    net = raw.get("network")
    net = net if isinstance(net, dict) else {}

    def _roots(key: str) -> tuple[str, ...]:
        value = fs.get(key, [])
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            errors.append(f"{target}: filesystem.{key} must be a list of paths")
            return ()
        return tuple(_expand(item) for item in value if str(item).strip())

    def _hosts(key: str) -> tuple[str, ...]:
        value = net.get(key, [])
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            errors.append(f"{target}: network.{key} must be a list of hosts")
            return ()
        return tuple(str(item).strip().lower() for item in value if str(item).strip())

    read_roots = _roots("read_roots")
    write_roots = _roots("write_roots")
    deny = _roots("deny")
    allow_hosts = _hosts("allow")
    if mode != "off" and not read_roots and not write_roots:
        warnings.append(f"{target}: no extra roots declared — only the project root is reachable")
    return SandboxPolicy(
        mode=mode,
        read_roots=read_roots,
        write_roots=write_roots,
        deny=deny,
        allow_hosts=allow_hosts,
        project_root=root,
        path=str(target),
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


# --- the effective policy for a working directory ---------------------------

_cache: dict[tuple[str, str, int], tuple[SandboxPolicy, _Roots]] = {}


def reset_cache() -> None:
    """Test seam: forget the cached policies."""
    _cache.clear()


def _git_root(cwd: str) -> str:
    """The project root for `cwd`: the git root, else the cwd itself."""
    try:
        from .check.gitutil import project_root

        return project_root(cwd)
    except Exception:
        return cwd or os.getcwd()


def get_policy(cwd: str = "") -> tuple[SandboxPolicy, _Roots]:
    """The policy and resolved roots for `cwd`.

    Cached per directory, but the cache key includes the policy file and its
    modification time: a cached policy that outlives the file it came from
    would keep enforcing rules the user has since deleted.
    """
    key = os.path.abspath(cwd or os.getcwd())
    try:
        path = policy_path()
        stamp = path.stat().st_mtime_ns if path.is_file() else 0
        cache_key = (key, str(path), stamp)
    except Exception:
        cache_key = (key, "", 0)
    hit = _cache.get(cache_key)
    if hit is not None:
        return hit
    project = _git_root(key)
    policy = load_policy(project_root=project)
    roots = _Roots(
        project=Path(project),
        read=tuple({Path(project), *(Path(p) for p in policy.read_roots)}),
        write=tuple({Path(project), *(Path(p) for p in policy.write_roots)}),
        deny=tuple(Path(p) for p in policy.deny),
    )
    _cache[cache_key] = (policy, roots)
    return policy, roots


# --- path checks -----------------------------------------------------------


def _resolve(raw: str, cwd: str) -> Path:
    """Absolute, symlink-resolved path. Resolution is what stops a symlink
    inside the project from pointing out of it."""
    text = str(raw or "").strip()
    if not text:
        return Path(cwd or os.getcwd())
    try:
        p = Path(os.path.expanduser(text))
    except Exception:
        return Path("/")
    if not p.is_absolute():
        p = Path(cwd or os.getcwd()) / p
    try:
        return p.resolve()
    except Exception:
        return p


def _under(path: Path, root: Path) -> bool:
    try:
        return path == root or root in path.parents
    except Exception:
        return False


def _is_system(path: Path) -> bool:
    text = str(path)
    return any(text == item or text.startswith(item) for item in SYSTEM_ALWAYS_READ)


def check_path(
    policy: SandboxPolicy,
    roots: _Roots,
    raw: str,
    *,
    write: bool,
    cwd: str,
) -> str | None:
    """Return a denial reason for one path, or None when it is allowed."""
    if not policy.active:
        return None
    text = str(raw or "").strip()
    if not text:
        return None
    target = _resolve(text, cwd)
    if _is_system(target):
        return None
    for deny_root in roots.deny:
        if _under(target, deny_root):
            return (
                f"sandbox: denied — {target} is in the sandbox deny list "
                f"({deny_root}). It stays denied even inside an allowed root."
            )
    allowed = roots.write if write else roots.read
    if any(_under(target, root) for root in allowed):
        return None
    kind = "write" if write else "read"
    return (
        f"sandbox: {kind} denied — {target} is outside the sandbox roots "
        f"(project={roots.project}). Declare a root in "
        f"{policy.path or '~/.sidekick/sandbox.toml'} to allow it."
    )


# --- network checks --------------------------------------------------------


def _host_of(url: str) -> str:
    match = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://([^/:?#]+)", str(url or ""))
    return match.group(1).lower() if match else ""


def _host_allowed(host: str, allow: tuple[str, ...]) -> bool:
    if not host:
        return False
    for entry in allow:
        if host == entry or host.endswith("." + entry):
            return True
    return False


def check_host(policy: SandboxPolicy, host: str) -> str | None:
    """Return a denial reason for one host, or None when it is allowed."""
    if not policy.active:
        return None
    name = str(host or "").strip().lower()
    if not name:
        return None
    if _host_allowed(name, policy.allow_hosts):
        return None
    allowed = ", ".join(policy.allow_hosts) if policy.allow_hosts else "none"
    return (
        f"sandbox: network denied — {name} is not in the sandbox allow list "
        f"({allowed}). Add it under [network] allow in "
        f"{policy.path or '~/.sidekick/sandbox.toml'} to allow it."
    )


# --- command checks --------------------------------------------------------


def _tokens(command: str) -> list[str]:
    try:
        lex = shlex.shlex(command, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        return list(lex)
    except ValueError:
        # Unbalanced quotes: fall back to whitespace splitting rather than
        # refusing to look at the command at all.
        return command.split()


def _expand_vars(piece: str) -> str:
    """Expand the variables that decide where a path really is.

    `$HOME/.ssh/id_rsa` is a path under the home directory, not a relative
    name under the project, so it has to be expanded before it is checked.
    A variable we cannot resolve stays in the string, and the caller treats
    that as unverifiable rather than as safe.
    """
    text = str(piece)
    home = str(Path.home())
    text = text.replace("${HOME}", home).replace("$HOME", home)
    text = text.replace("${PWD}", os.getcwd()).replace("$PWD", os.getcwd())
    return text


def _path_pieces(text: str, *, bare: bool = False) -> list[str]:
    """Path-looking pieces inside one token (a quoted string can hold several).

    With `bare`, a plain name counts as a path too — for commands whose
    arguments are files, "cat passwd" means the file passwd.
    """
    out: list[str] = []
    for piece in _SPLIT_RE.split(str(text or "")):
        piece = piece.strip()
        if not piece or piece.startswith("-"):
            continue
        if piece.startswith(("/", "./", "../", "~/")) or piece == "~":
            out.append(_expand_vars(piece))
        elif "/" in piece and ":" not in piece.split("/")[0]:
            # A relative path with a directory part. Resolved against the
            # working directory later, so `../` escapes are caught.
            out.append(_expand_vars(piece))
        elif piece.startswith("$") and re.search(r"[A-Za-z]", piece):
            # A variable we may not be able to resolve. Kept so the caller can
            # refuse it rather than assume it is safe.
            out.append(piece)
        elif bare and not piece.startswith("$") and "=" not in piece:
            out.append(_expand_vars(piece))
    return out


def _segments(tokens: list[str]) -> list[list[str]]:
    """Split a token list into command segments on shell separators."""
    out: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in (";", "&&", "||", "|", "&", "\n"):
            if current:
                out.append(current)
                current = []
            continue
        current.append(token)
    if current:
        out.append(current)
    return out


def check_command(
    policy: SandboxPolicy,
    roots: _Roots,
    command: str,
    *,
    cwd: str,
) -> str | None:
    """Return a denial reason for a shell command, or None when it is allowed.

    The command is read as text: every path and every URL it mentions is
    checked, wherever they appear — behind a variable, inside quotes, after a
    redirect. That is what makes the obvious indirections useless.
    """
    if not policy.active:
        return None
    text = str(command or "")
    if not text.strip():
        return None

    # URLs first: a URL contains slashes, and it is a network question, not a
    # filesystem one. The URL is cut out of the text before the path scan, or
    # its path would be read as a file.
    for url in _URL_RE.findall(text):
        host = _host_of(url)
        if host:
            denial = check_host(policy, host)
            if denial:
                return denial
    text = _URL_RE.sub(" ", text)

    workdir = cwd or os.getcwd()
    unverifiable: list[str] = []
    for segment in _segments(_tokens(text)):
        words = [w for w in segment if w not in ("&&", "||")]
        if not words:
            continue
        command_word = words[0].lstrip("-").split("/")[-1].lower()
        if command_word in ("cd", "pushd"):
            # Track the directory: `cd /etc && cat passwd` reads /etc/passwd.
            for piece in _path_pieces(" ".join(words[1:])):
                workdir = str(_resolve(piece, workdir))
                break
            continue
        if command_word in NETWORK_COMMANDS:
            denial = _check_bare_host(policy, words[1:])
            if denial:
                return denial
        writing = command_word in WRITE_COMMANDS
        bare = command_word in READ_COMMANDS or writing
        for index, word in enumerate(words):
            if word in REDIRECTS:
                writing = True
                continue
            previous = words[index - 1] if index else ""
            if previous in REDIRECTS:
                writing = True
            for piece in _path_pieces(word, bare=bare and index > 0):
                if "$" in piece:
                    unverifiable.append(piece)
                    continue
                denial = check_path(
                    policy,
                    roots,
                    piece,
                    write=writing,
                    cwd=workdir,
                )
                if denial:
                    return denial
    if unverifiable:
        # A path we cannot resolve is not a path we can vouch for. Reported
        # after the resolvable ones, so the message names the real target when
        # there is one.
        return (
            f"sandbox: denied — {unverifiable[0]} contains a variable the sandbox "
            f"cannot resolve, so the target is unverifiable. Use an absolute path, "
            f"or declare the root in {policy.path or '~/.sidekick/sandbox.toml'}."
        )
    return None


def _check_bare_host(policy: SandboxPolicy, args: list[str]) -> str | None:
    """A network command with a host but no scheme (`ssh host`, `nc host 443`).

    The URL form is caught by the URL scan; this catches the bare-host form,
    where the first argument is a name to connect to.
    """
    for word in args:
        if word.startswith("-"):
            continue
        if "://" in word or "/" in word:
            continue
        if word.isdigit():
            continue
        name = word.split("@")[-1]
        if not name or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", name):
            continue
        return check_host(policy, name)
    return None


# --- the tool layer --------------------------------------------------------

# Tools whose `path` argument is a filesystem location, and whether the
# access is a read or a write.
PATH_TOOLS = {
    "list_dir": "read",
    "read_file": "read",
    "search_code": "read",
    "glob_files": "read",
    "repo_map": "read",
    "delete_file": "write",
    "write_file": "write",
    "edit_file": "write",
    "make_dir": "write",
}

COMMAND_TOOLS = frozenset({"shell", "exec", "shell_session"})


def check_tool(
    name: str,
    args: dict,
    *,
    cwd: str = "",
    policy: SandboxPolicy | None = None,
) -> str | None:
    """The sandbox check for one tool call. Returns the violation, or None.

    The violation is reported in every mode; the *caller* decides whether it
    blocks. In `enforced` mode the call is refused, in `advisory` mode it runs
    and the note says what would have been blocked.

    Called from the tool dispatcher, so every model-facing tool passes through
    here — including the ones added later.
    """
    try:
        if policy is None:
            policy, roots = get_policy(cwd)
        else:
            _policy, roots = get_policy(cwd)
    except Exception:
        return None
    if not policy.active:
        return None
    if policy.errors:
        # A policy that does not parse must not silently widen access.
        return "sandbox: policy file could not be read — " + "; ".join(policy.errors)
    args = args if isinstance(args, dict) else {}
    workdir = str(args.get("cwd") or cwd or os.getcwd())

    kind = PATH_TOOLS.get(name)
    if kind:
        return check_path(
            policy,
            roots,
            str(args.get("path", "")),
            write=(kind == "write"),
            cwd=workdir,
        )
    if name in COMMAND_TOOLS:
        return check_command(
            policy,
            roots,
            str(args.get("cmd", "")),
            cwd=workdir,
        )
    return None


def note_for(policy: SandboxPolicy, denial: str | None) -> str:
    """The advisory-mode note appended to a tool result."""
    if not denial or policy.mode != "advisory":
        return ""
    return f"[sandbox advisory] {denial}"


def describe(policy: SandboxPolicy | None = None) -> str:
    """The one-line status used by `sk doctor` and `sk config --show`."""
    if policy is None:
        try:
            policy, _roots = get_policy()
        except Exception:
            return "off (no policy file)"
    return policy.describe()


def enforcement_mode() -> str:
    """The effective enforcement: enforced, advisory, or off.

    ``enforced`` means the kernel is enforcing the write boundary as well as
    the policy. ``advisory`` means the policy is enforced where it can be (the
    tool layer) and the OS layer is not available or was asked not to.
    """
    try:
        policy, _roots = get_policy()
    except Exception:
        return "off"
    if not policy.active or policy.mode == "off":
        return "off"
    if policy.mode == "advisory":
        return "advisory"
    try:
        from .sandbox_os import detect

        return "enforced" if detect().available else "advisory"
    except Exception:
        return "advisory"


def status_line() -> str:
    """The line `sk doctor` and `sk config --show` print."""
    base = describe()
    mode = enforcement_mode()
    if mode == "off":
        return base
    return f"{base} · os enforcement: {mode}"
