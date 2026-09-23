"""Agent loop: talks to Ollama (OpenAI-compatible) with tool-calling."""

from __future__ import annotations

import json
import os
import platform
from contextvars import ContextVar
from pathlib import Path

from openai import OpenAI

from .config import Config
from .tools import APPROVAL_TOOLS, TOOLS_SCHEMA, dispatch_tool, tool_sysinfo

# Audit session tag. Direct callers pass session= to run_agent; the TUI
# dispatches via asyncio.to_thread with the pre-contextvar 8-arg signature,
# so it sets this instead (to_thread propagates the context into the worker).
audit_session: ContextVar[str] = ContextVar("sk_audit_session", default="")

SYSTEM_PROMPT = """You are Sidekick, a local-first terminal companion.
You run on the user's machine via Ollama (OS: {os}).
Rules:
- Be concise, terminal-friendly (short markdown, no fluff).
- Prefer using tools: sysinfo, list_dir, read_file, exec (read-only), shell (any command, approval), write_file, edit_file, make_dir, delete_file, remember, recall, todo_add, todo_list, todo_done, read_url, web_search, skill.
- SKILLS: the SKILL INDEX lists packs by description. When a task matches one (debugging→systematic-debugging, new feature→brainstorming, plan→writing-plans), call `skill` to load its full instructions and FOLLOW them.
- WEB: for summarize/docs/URL questions, call read_url (public http/https only). For "search the internet / latest / right now" questions, call web_search FIRST, then read_url the best hits. Never fetch localhost/private IPs. You HAVE these tools — never claim you cannot fetch URLs or search.
- GREETINGS: hi/hello/thanks/bye get a direct one-line reply. Never call tools for greetings.
- MEMORY: user facts are in SAVED MEMORIES below. Use them (e.g. preferred model, projects). If user says "remember X", call remember. If asked "what do you remember / my prefs", call recall.
- TODOS: open todos are in OPEN TODOS below. If user says "add todo / my todos / done #N", use todo tools. Proactively offer next todo when asked "what next".
- GROUNDING (mandatory): if the question contains my / my device / my machine / hardware / what LLM / what model can I run, you MUST call sysinfo first. Never guess RAM/GPU/CPU. Use the sysinfo output numbers in your answer.
- PATHS (mandatory): ~/X means {home}/X, NOT ./X. If the user asks about a path under ~, you MUST call list_dir with that exact path (~/X). Never answer "does not exist" from cwd listing. cwd is {cwd} but ~ is {home}. Always try the exact path first.
- exec is READ-ONLY (ls, df, free, git status, etc). Never claim you ran a blocked command.
- WRITES need approval: write_file/edit_file/make_dir/delete_file/shell will ask the user. Announce what you will write + why before calling. Keep writes under HOME or /tmp, max 100KB. Never write to ~/.ssh, ~/.gnupg, /etc, /usr.
- CALL tools, don't ask in prose: to write/create, emit the tool call immediately with a one-line announcement. The approval UI handles permission — a prose "shall I?" stalls forever. {approval_mode}
- Never narrate a denial you did not receive: if no tool result says denied, you have NOT been denied. Past denials in history were UI states at the time, not policy. When in doubt, call the tool — do not pattern-match old refusals.
- If a tool is blocked/denied, explain why and suggest an allowed alternative.
- Recommend only Ollama models (qwen, llama, mistral, phi, gemma). Never recommend GPT-2/GPT-3.5/GPT-4/transformers for local run. VRAM truth: 3-4B fits 4GB VRAM easily and fast; 7-8B CAN run with partial CPU offload (you are {smart_model} doing it now) but slower, needs swap; 14B+ does NOT fit this box.
- To use a tool, use native function calling. If that is unavailable, emit EXACTLY one fenced block: ```json {{"name": "sysinfo", "arguments": {{}}}}``` or {{"name": "list_dir", "arguments": {{"path": "~/neural-hangar"}}}} and nothing else.
- Current working directory: {cwd} — HOME is {home}.
- Today is {today}. Answer date/day questions from this, never tools or memory.
- OS: {os}. Platform: {platform}.
REAL SYSTEM SNAPSHOT (do not re-guess, but still call sysinfo tool if user asks about their device so the trace shows grounding):
{sysinfo}
SAVED MEMORIES (use these, do not re-ask):
{memories}
OPEN TODOS:
{todos}
SKILLS (follow these packs when relevant):
{skills}
PROJECT DOCS (repo conventions from .sidekick.toml — follow them):
{projdocs}
"""


def get_client(cfg: Config) -> OpenAI:
    return OpenAI(base_url=cfg.effective_base_url(), api_key=cfg.effective_api_key(), timeout=300.0)


def _project_docs_block(cfg: Config) -> str:
    """Render project docs (AGENTS.md et al) for the system prompt. Capped, never raises."""
    docs = list(getattr(cfg, "project_docs", None) or [])
    root = str(getattr(cfg, "project_root", "") or "")
    if not docs or not root:
        return "(none)"
    parts: list[str] = []
    budget = 3000
    try:
        root_resolved = Path(root).expanduser().resolve()
    except Exception:
        return "(none)"
    for rel in docs[:8]:
        try:
            p = (root_resolved / rel).expanduser().resolve()
            if root_resolved not in p.parents and p != root_resolved:
                continue  # escape attempt (../../..) — skip
            if not p.is_file() or p.stat().st_size > 100_000:
                continue
            text = p.read_text(errors="replace").strip()[:budget]
            if text:
                parts.append(f"[{rel}]\n{text}")
                budget -= len(text)
                if budget <= 0:
                    break
        except Exception:
            continue
    return "\n\n".join(parts) if parts else "(none)"


def _expand_at_refs(text: str) -> str:
    """Support @path in chat: inline file contents."""
    import re

    def repl(m: re.Match) -> str:
        p = Path(m.group(1)).expanduser()
        try:
            if p.is_file() and p.stat().st_size < 200_000:
                return f"\n--- {p} ---\n{p.read_text(errors='replace')[:6000]}\n--- end ---\n"
            return f"[could not read @{m.group(1)}]"
        except Exception as e:
            return f"[error reading @{m.group(1)}: {e}]"

    return re.sub(r"@([\w\-.~/][\w\-./~]*)", repl, text)


def _auto_local_context(text: str) -> str:
    """Deterministic grounding: if user mentions ~/X or $HOME/X, list it + read package.json/README.

    This does NOT rely on the model calling tools — it injects facts so the model
    cannot hallucinate 'does not exist'.
    """
    import re

    from .tools import tool_list_dir, tool_read_file

    # find ~/foo/bar and $HOME/foo patterns
    home = str(Path.home())
    paths: list[str] = []
    paths += re.findall(r"(~/[\w\-./~]+)", text)
    paths += re.findall(r"(" + re.escape(home) + r"/[\w\-./]+)", text)
    # dedupe, strip trailing punctuation
    seen: set[str] = set()
    uniq: list[str] = []
    for p in paths:
        p = p.rstrip(".,;:!?\"')")
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    if not uniq:
        return ""
    chunks: list[str] = []
    for p in uniq[:3]:  # cap 3 paths
        try:
            if p.startswith("~/"):
                p = str(Path.home() / p[2:])  # resolve ~ against the real home
            exp = str(Path(p).resolve())
        except Exception:
            continue
        listing = tool_list_dir(p)
        chunks.append(f"[path {p} -> {exp}]\n{listing[:1500]}")
        # if dir, try package.json + README.md for scope
        try:
            base = Path(p).resolve()
            if base.is_dir():
                for fname in ("package.json", "README.md", "pyproject.toml", "requirements.txt"):
                    fp = base / fname
                    if fp.is_file() and fp.stat().st_size < 100_000:
                        content = tool_read_file(str(fp), max_chars=2000)
                        chunks.append(f"[{p}/{fname}]\n{content[:2000]}")
                        if len(chunks) > 6:
                            break
        except Exception:
            pass
    return "\n\n".join(chunks)


def _auto_web_context(text: str) -> str:
    """Deterministic grounding: fetch http(s) URLs so the model can never claim inability.

    Mirrors _auto_local_context. Cap 2 URLs x 3000 chars to protect the 8k ctx window.
    """
    import re

    from .tools import tool_read_url

    urls = re.findall(r"https?://[^\s\"')<>]+", text)
    seen: set[str] = set()
    uniq: list[str] = []
    for u in urls:
        u = u.rstrip(".,;:!?\"')")
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    if not uniq:
        return ""
    chunks: list[str] = []
    for u in uniq[:2]:
        try:
            body = tool_read_url(u, max_chars=3000)
        except Exception as e:
            body = f"Error fetching {u}: {e}"
        chunks.append(f"[fetched {u}]\n{body[:3000]}")
    return "\n\n".join(chunks)


def _quick_reply(text: str) -> str | None:
    """Deterministic instant replies for greetings/thanks/date. No LLM, no tools.

    Strict full-match only: 'hi, check ~/x' still goes to the model.
    """
    import re
    from datetime import datetime

    t = (text or "").strip().lower().rstrip("!.~")
    if re.fullmatch(r"(hi|hii+|hello|hey|yo|hiya|namaste)(\s+(there|buddy|mate))?", t or ""):
        return "Hey! What are we working on?"
    if re.fullmatch(r"(thanks|thank you|thx|shukriya|dhanyavaad)", t or ""):
        return "Anytime!"
    if re.fullmatch(r"(bye|goodbye|see you|alvida)", t or ""):
        return "Later!"
    if re.fullmatch(
        r"(what(\s+is|\'s)?\s+(the\s+)?(day|date)(\s+(is\s+)?(it|today))?|"
        r"(today'?s?\s+(day|date))|(what\s+time\s+is\s+it)|(current\s+(day|date|time)))",
        t or "",
    ):
        now = datetime.now()
        return f"Today is {now.strftime('%A, %B %d, %Y')}."
    return None


def _auto_search_context(text: str) -> str:
    """Deterministic grounding for web-search requests.

    Triggers on explicit 'search the internet/web' phrasing, or on recency
    markers (right now/latest/...) for non-local questions. Injects top hits
    so the model answers from results instead of refusing or guessing.
    """
    import re

    from .tools import tool_web_search

    m = re.search(
        r"search\s+(?:on\s+)?(?:the\s+)?(?:internet|web)\b\s*(?:for\s+)?(.+)", text, re.IGNORECASE
    )
    query = ""
    if m:
        query = m.group(1).strip().rstrip("?.!")[:200]
    else:
        low = text.lower()
        recency = re.search(
            r"\b(right now|latest|currently|up[- ]to[- ]date|this week|today|2026)\b", low
        )
        local = re.search(
            r"~/|"
            + re.escape(str(Path.home()))
            + r"|my (device|machine|files?|todos?|projects?|prefs?)|can i run|do i (have|need)",
            low,
        )
        if recency and not local and len(text.split()) > 3:
            from .store import _keywords

            keys = _keywords(text)
            query = " ".join(keys[:8]) if keys else text.strip().rstrip("?.!")[:200]
    if not query or len(query) < 3:
        return ""
    try:
        hits = tool_web_search(query, count=5)
    except Exception as e:
        hits = f"Error searching: {e}"
    return f"[search results for '{query}']\n{hits[:2500]}"


def _balanced_objects(text: str) -> list[str]:
    """Extract top-level {...} spans with balanced braces (handles nesting)."""
    spans: list[str] = []
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    spans.append(text[start : i + 1])
                    start = -1
    return spans


def _coerce_args(raw) -> dict | None:
    import json as _json

    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            obj = _json.loads(raw)
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}
    return {}


def _match_tool_obj(obj: dict, allowed: set[str]) -> tuple[str, dict] | None:
    if isinstance(obj, dict) and obj.get("name") in allowed:
        for key in ("arguments", "parameters", "params", "input"):
            if key in obj:
                args = _coerce_args(obj.get(key))
                if args is not None:
                    return (obj["name"], args)
        return (obj["name"], {})
    if isinstance(obj, dict) and isinstance(obj.get("function"), dict):
        fn = obj["function"]
        if fn.get("name") in allowed:
            for key in ("arguments", "parameters", "params", "input"):
                if key in fn:
                    args = _coerce_args(fn.get(key))
                    if args is not None:
                        return (fn["name"], args)
            return (fn["name"], {})
    return None


def _parse_text_tools(text: str) -> list[tuple[str, dict]]:
    """Parse ALL tool JSON objects in text (fenced or bare, any args key).

    Returns list of (name, args). Only allows known tools.
    """

    allowed = {
        "sysinfo",
        "list_dir",
        "read_file",
        "exec",
        "shell",
        "delete_file",
        "write_file",
        "edit_file",
        "make_dir",
        "remember",
        "recall",
        "todo_add",
        "todo_list",
        "todo_done",
        "read_url",
        "web_search",
        "skill",
    }
    found: list[tuple[str, dict]] = []
    seen: set[str] = set()
    for span in _balanced_objects(text):
        try:
            obj = json.loads(span)
        except Exception:
            continue
        hit = _match_tool_obj(obj, allowed)
        if hit and json.dumps(hit) not in seen:
            seen.add(json.dumps(hit))
            found.append(hit)
    return found


def _parse_text_tool(text: str) -> tuple[str, dict] | None:
    """First match only (kept for compat)."""
    hits = _parse_text_tools(text)
    return hits[0] if hits else None


def _extra_body(cfg: Config) -> dict:
    """Provider-specific request params. Ollama-only knobs (options/num_ctx)
    break cloud APIs with 400s, so they ship for local servers exclusively."""
    if cfg.provider in ("ollama", "lmstudio"):
        return {"options": {"num_ctx": 4096, "num_predict": 350}}
    return {}


def _tool_target(name: str, args: dict) -> str:
    """Canonical repeat-key: same tool + same target, ignoring cosmetic params."""
    for key in ("url", "path", "cmd", "query", "content", "text", "id"):
        if key in args and args[key] not in ("", None):
            return f"{name}|{key}={str(args[key])[:300]}"
    return f"{name}|{json.dumps(args, sort_keys=True)[:300]}"


def format_plan(calls: list[tuple[str, dict]]) -> str:
    """One-line-per-tool plan text for review prompts. Pure, no I/O."""
    lines = []
    for i, (name, args) in enumerate(calls, 1):
        target = args.get("path", args.get("cmd", args.get("url", "?")))
        lines.append(f"{i}. {name} -> {str(target)[:120]}")
    return "\n".join(lines)


def _maybe_review_plan(
    calls: list[tuple[str, dict]],
    approve,
    review_plan,
    auto_approve: bool,
    session: str = "",
    provider: str = "",
    host: str = "",
) -> tuple[bool, object]:
    """Gate multi-tool turns with destructive actions behind one plan review.

    Triggers when the turn has >= 2 calls with >= 1 approval-gated tool,
    a review_plan callback is wired, and we are not in auto-approve mode.
    Returns (proceed, approve_fn): on approval, approve_fn auto-passes the
    plan's own targets (no double-prompting) and delegates anything else to
    the original approve. Denial (or reviewer crash: fail closed) logs an
    audit row and returns (False, approve) with nothing executed.
    """
    from .store import log_tool_run

    targets = {_tool_target(name, args) for name, args in calls}
    needs_review = (
        len(calls) >= 2
        and any(name in APPROVAL_TOOLS for name, _ in calls)
        and review_plan is not None
        and not auto_approve
    )
    if not needs_review:
        return (True, approve)
    plan = format_plan(calls)
    try:
        ok = bool(review_plan(plan, list(calls)))
    except Exception:
        ok = False
    if not ok:
        log_tool_run(
            session, "plan", plan[:200], approved=False, provider=provider, host=host, ok=False
        )
        return (False, approve)

    def turn_approve(name: str, args: dict) -> bool:
        if _tool_target(name, args) in targets:
            return True
        if approve is None:
            return True
        try:
            return bool(approve(name, args))
        except Exception:
            return False

    return (True, turn_approve)


def _provider_host(cfg) -> str:
    """Hostname of the provider endpoint. Pure parse, no network."""
    from urllib.parse import urlparse

    try:
        return urlparse(cfg.effective_base_url()).hostname or ""
    except Exception:
        return ""


def _run_tools_batch(
    calls: list[tuple[str, dict]],
    approve,
    on_tool,
    seen: dict[str, str],
    session: str = "",
    cfg=None,
    max_workers: int = 4,
) -> list[tuple[str, bool]]:
    """Run one turn's tool calls, returning [(result, repeated)] in input order.

    Approval-gated tools prompt interactively, so they always run serially in
    order (parallel prompts would overlap). Everything else — reads, web,
    memory — runs concurrently in a bounded thread pool. Cache hits resolve
    immediately without executing. on_tool notifications replay serially in
    input order (they are display-only). Worker crashes become Error strings,
    never exceptions: one tool failing must not kill its siblings.
    """
    from concurrent.futures import ThreadPoolExecutor

    provider = getattr(cfg, "provider", "") if cfg is not None else ""
    host = _provider_host(cfg) if cfg is not None else ""
    keys = [_tool_target(name, args) for name, args in calls]
    results: list[tuple[str, bool] | None] = [None] * len(calls)
    fresh: list[int] = []
    for i, key in enumerate(keys):
        if key in seen:
            results[i] = (
                f"[cached — already ran above]\n{seen[key][:2000]}\nSynthesize the final answer now. Do not call more tools.",
                True,
            )
        else:
            fresh.append(i)

    def needs_gate(i: int) -> bool:
        name = calls[i][0]
        return name in APPROVAL_TOOLS and approve is not None

    def run_one(i: int) -> str:
        name, args = calls[i]
        try:
            result, _ = _gated_dispatch(
                name, args, approve, session=session, provider=provider, host=host
            )
            return result
        except Exception as e:
            return f"Error: tool '{name}' crashed: {e}"

    gated = [i for i in fresh if needs_gate(i)]
    free = [i for i in fresh if not needs_gate(i)]
    if free:
        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(free)))) as pool:
            for i, result in zip(free, pool.map(run_one, free)):
                results[i] = (result, False)
    for i in gated:
        results[i] = (run_one(i), False)
    for i in fresh:
        seen[keys[i]] = results[i][0]  # type: ignore[index]
        if on_tool is not None:
            try:
                on_tool(calls[i][0], calls[i][1])
            except Exception:
                pass
    return [r for r in results if r is not None]


def _run_tool_cached(
    name: str, args: dict, approve, on_tool, seen: dict[str, str], session: str = "", cfg=None
) -> tuple[str, bool]:
    """Execute unless this exact target already ran this turn. Returns (result, repeated)."""
    key = _tool_target(name, args)
    if key in seen:
        return (
            f"[cached — already ran above]\n{seen[key][:2000]}\nSynthesize the final answer now. Do not call more tools.",
            True,
        )
    provider = getattr(cfg, "provider", "") if cfg is not None else ""
    host = _provider_host(cfg) if cfg is not None else ""
    result, _ = _gated_dispatch(name, args, approve, session=session, provider=provider, host=host)
    seen[key] = result
    if on_tool is not None:
        try:
            on_tool(name, args)
        except Exception:
            pass
    return (result, False)


def _gated_dispatch(
    name: str,
    args: dict,
    approve: object = None,
    session: str = "",
    provider: str = "",
    host: str = "",
) -> tuple[str, bool]:
    """Run dispatch_tool with approval gate. Returns (result, approved)."""
    from .store import log_tool_run

    target = _tool_target(name, args)
    if name in APPROVAL_TOOLS and approve is not None:
        try:
            ok = approve(name, args)  # type: ignore
        except Exception:
            ok = False
        if not ok:
            log_tool_run(
                session, name, target, approved=False, provider=provider, host=host, ok=False
            )
            return (
                f"Denied by user: {name} {args} not executed. Explain and suggest --yes or manual command.",
                False,
            )
    result = dispatch_tool(name, args)
    failed = result.startswith("Error") or "blocked" in result[:60].lower()
    log_tool_run(session, name, target, approved=True, provider=provider, host=host, ok=not failed)
    return (result, True)


class _TC:
    def __init__(self, id: str, name: str, args: str):
        self.id = id
        self.function = type("F", (), {"name": name, "arguments": args})()


class _Msg:
    def __init__(
        self, content: str, tool_calls: list | None, reasoning: str = "", finish: str = ""
    ):
        self.content = content
        self.tool_calls = tool_calls
        self.reasoning = reasoning
        self.finish = finish  # stop | length | tool_calls | ...


def _retryable_status(exc: BaseException) -> int:
    """Seconds to wait before retry, 0 = don't retry. Honors RetryInfo hints."""
    import re

    msg = str(exc)
    # explicit server hint wins, whatever the code
    m = re.search(r"retry\s*(?:in|after)?\s*(\d+(?:\.\d+)?)\s*s", msg, re.IGNORECASE)
    if m:
        try:
            return max(1, min(30, int(float(m.group(1)))))
        except Exception:
            pass
    code = getattr(exc, "status_code", 0) or 0
    if (
        code in (429, 503)
        or "429" in msg
        or "503" in msg
        or "overloaded" in msg.lower()
        or "rate limit" in msg.lower()
        or "RESOURCE_EXHAUSTED" in msg
    ):
        return 5
    return 0


def _create_with_retry(client, kwargs: dict, tries: int = 3, on_token=None) -> object:
    """chat.completions.create with backoff on rate limits. Streams status via on_token."""
    import time as _t

    last: BaseException | None = None
    for attempt in range(tries):
        try:
            return client.chat.completions.create(**kwargs)
        except Exception as e:
            wait = _retryable_status(e)
            last = e
            if not wait or attempt == tries - 1:
                raise
            note = f"[rate limited, retrying in {wait}s...]"
            if on_token is not None:
                try:
                    on_token(note)
                except Exception:
                    pass
            _t.sleep(wait)
    assert last is not None
    raise last


def _stream_chat(
    client,
    model: str,
    messages: list[dict],
    tools,
    temperature: float,
    max_tokens: int,
    extra: dict,
    on_token=None,
    on_reasoning=None,
) -> _Msg:
    """Streaming chat.completions with tool accumulation.

    Content deltas -> on_token, reasoning deltas -> on_reasoning (falls back
    to on_token when no separate sink is given, so old callers keep working).
    """
    _on_r = on_reasoning if on_reasoning is not None else on_token
    acc_text = ""
    acc_reason = ""
    finish = ""
    tc_buf: dict[int, dict] = {}  # idx -> {id, name, args}
    try:
        stream = _create_with_retry(
            client,
            dict(
                model=model,
                messages=messages,
                tools=tools,
                tool_choice="auto" if tools else "none",
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
                extra_body=extra,
            ),
            on_token=on_token,
        )
        for chunk in stream:  # type: ignore[attr-defined]
            try:
                choice = chunk.choices[0]
            except Exception:
                continue
            fr = getattr(choice, "finish_reason", None)
            if fr:
                finish = str(fr)
            delta = getattr(choice, "delta", None)
            if delta is None:
                continue
            # reasoning field (qwen3 via Ollama) — separate sink when provided
            r = getattr(delta, "reasoning", None) or (
                delta.get("reasoning") if isinstance(delta, dict) else None
            )
            if r:
                acc_reason += r if isinstance(r, str) else str(r)
                if _on_r is not None:
                    try:
                        _on_r(r if isinstance(r, str) else str(r))
                    except Exception:
                        pass
            c = getattr(delta, "content", None)
            if c is None and isinstance(delta, dict):
                c = delta.get("content")
            if c:
                acc_text += c
                if on_token is not None:
                    try:
                        on_token(c)
                    except Exception:
                        pass
            tcs = getattr(delta, "tool_calls", None)
            if tcs is None and isinstance(delta, dict):
                tcs = delta.get("tool_calls")
            if tcs:
                for tc in tcs:
                    idx = tc.index if hasattr(tc, "index") else tc.get("index", 0)
                    buf = tc_buf.setdefault(idx, {"id": "", "name": "", "args": ""})
                    tid = getattr(tc, "id", None) or (
                        tc.get("id") if isinstance(tc, dict) else None
                    )
                    if tid:
                        buf["id"] = tid
                    fn = getattr(tc, "function", None) or (
                        tc.get("function") if isinstance(tc, dict) else None
                    )
                    if fn:
                        n = getattr(fn, "name", None) or (
                            fn.get("name") if isinstance(fn, dict) else None
                        )
                        a = getattr(fn, "arguments", None) or (
                            fn.get("arguments") if isinstance(fn, dict) else None
                        )
                        if n:
                            buf["name"] = (buf["name"] or "") + n
                        if a:
                            buf["args"] = (buf["args"] or "") + a
    except Exception:
        # fallback to non-streaming on error
        resp = _create_with_retry(
            client,
            dict(
                model=model,
                messages=messages,
                tools=tools,
                tool_choice="auto" if tools else "none",
                temperature=temperature,
                max_tokens=max_tokens,
                stream=False,
                extra_body=extra,
            ),
            on_token=on_token,
        )
        m = resp.choices[0].message  # type: ignore[attr-defined]
        return _Msg(
            m.content or "",
            getattr(m, "tool_calls", None),
            getattr(m, "reasoning", "") or "",
            str(getattr(resp.choices[0], "finish_reason", "") or ""),  # type: ignore[attr-defined]
        )
    tool_calls = None
    if tc_buf:
        tool_calls = [
            _TC(b["id"] or f"call_{i}", b["name"], b["args"])
            for i, b in sorted(tc_buf.items())
            if b["name"]
        ]
        if not tool_calls:
            tool_calls = None
    return _Msg(acc_text, tool_calls, acc_reason, finish)


def estimate_tokens(text: str) -> int:
    """~4 chars/token. Documented approximation: exact tokenizers are
    model-specific new deps, and the codebase already budgets by chars."""
    return max(1, (len(text or "") + 3) // 4)


_SUMMARY_PROMPT = (
    "Condense this chat history into a rolling summary for future turns. "
    "Reply with ONLY the summary, under 400 words: key facts, user preferences, "
    "decisions made, open todos and questions."
)


def _render_turns(msgs: list[dict]) -> str:
    return "\n".join(f"{m.get('role', '?')}: {m.get('content', '')}" for m in msgs)


def _as_role_content(msgs: list[dict]) -> list[dict]:
    return [{"role": m.get("role", "user"), "content": m.get("content", "")} for m in msgs]


def compact_history(
    prior: str, msgs: list[dict], budget_tokens: int, summarizer
) -> tuple[list[dict], str | None]:
    """Budget-bounded prompt view of oldest-first turns. Pure logic; summarizer
    does the single model call. Returns (prompt_msgs, new_summary|None).

    Under budget: everything passes through, no summarizer call. Over budget:
    task anchor + newest turns fitting half the budget stay verbatim, the
    middle folds into the prior summary. Empty middle or summarizer failure
    falls back to plain truncation (compaction must never break a turn).
    """
    as_role = _as_role_content(msgs)
    base = (
        [{"role": "user", "content": f"[Session summary so far]:\n{prior}"}]
        if (prior or "").strip()
        else []
    )
    if estimate_tokens(_render_turns(base + as_role)) <= budget_tokens:
        return (base + as_role, None)
    n = len(msgs)
    if n == 0:
        return (base, None)
    ai = next((i for i, m in enumerate(msgs) if m.get("role") == "user"), 0)
    half = max(500, budget_tokens // 2)
    acc, ri = 0, n
    while ri > 0 and (acc + estimate_tokens(msgs[ri - 1].get("content", "")) <= half or ri > n - 2):
        acc += estimate_tokens(msgs[ri - 1].get("content", ""))
        ri -= 1
    anchor = [] if ai >= ri else [msgs[ai]]
    middle = msgs[ai + 1 : ri]
    if not middle:
        return (base + _as_role_content(anchor + msgs[ri:]), None)
    context = (prior + "\n" if (prior or "").strip() else "") + _render_turns(middle)
    try:
        new_summary = summarizer(context).strip()
    except Exception:
        return (base + _as_role_content(anchor + msgs[ri:]), None)
    if not new_summary:
        return (base + _as_role_content(anchor + msgs[ri:]), None)
    out = [{"role": "user", "content": f"[Session summary so far]:\n{new_summary}"}]
    out += _as_role_content(anchor + msgs[ri:])
    return (out, new_summary)


def prepare_history(session: str, history: list[dict], cfg, summarize_fn) -> list[dict]:
    """Rolling-compaction view for one turn over the full session. Never raises.

     Reads the prior summary + watermark, compacts only new overflow, persists
     the merged summary. Falls back to the passed history on any failure.
    DB history stays complete — compaction is a view, never destructive."""
    try:
        budget = max(500, int(getattr(cfg, "history_budget_tokens", 3000) or 3000))
        if not (session or "").strip():
            prompt, _ = compact_history("", history, budget, summarize_fn)
            return prompt
        from .store import get_history_full, get_summary, save_summary

        full = get_history_full(session)
        if not full:
            return history
        prior, up_to = get_summary(session)
        uncovered = [m for m in full if m.get("id", 0) > up_to]
        if not uncovered:
            if (prior or "").strip():
                return [{"role": "user", "content": f"[Session summary so far]:\n{prior}"}]
            return history
        prompt, new_summary = compact_history(prior, uncovered, budget, summarize_fn)
        if new_summary is not None:
            top = max([m.get("id", 0) for m in full] + [up_to])
            save_summary(session, new_summary, top)
        return prompt
    except Exception:
        return history


def build_messages(
    user_msg: str, history: list[dict], cfg: Config, auto_approve: bool = False
) -> list[dict]:
    """Assemble system + history + user messages with all grounding. Pure I/O, no LLM.

    Extracted for the eval harness: every quality regression (unguessed specs,
    ~/ hallucinations, link refusals) is assertable here without a model.
    """
    user_msg = _expand_at_refs(user_msg)
    try:
        snapshot = tool_sysinfo()
    except Exception as e:
        snapshot = f"(sysinfo failed: {e})"
    if len(snapshot) > 2500:
        snapshot = snapshot[:2500] + "\n... [truncated]"
    auto_ctx = _auto_local_context(user_msg)
    if auto_ctx:
        user_msg = (
            user_msg
            + f"\n\n[AUTO LOCAL FACTS — these paths DO exist, never say otherwise]:\n{auto_ctx[:5000]}"
        )
    web_ctx = _auto_web_context(user_msg)
    if web_ctx:
        user_msg = (
            user_msg
            + f"\n\n[AUTO WEB FACTS — already fetched, summarize directly, never claim inability]:\n{web_ctx[:6500]}"
        )
    search_ctx = _auto_search_context(user_msg)
    if search_ctx:
        user_msg = (
            user_msg
            + f"\n\n[AUTO SEARCH — results below, answer from them + read_url the best hit if needed]:\n{search_ctx[:3000]}"
        )
    try:
        from .store import list_todos, recall_memories

        mem_hits = recall_memories(user_msg, limit=5)
        mem_block = "\n".join(f"- {m}" for m in mem_hits) if mem_hits else "(none yet)"
        todo_rows = list_todos(open_only=True)[:5]
        todo_block = "\n".join(f"#{i}: {t}" for i, t, _ in todo_rows) if todo_rows else "(none)"
        from .skills import load_skills

        skill_block = load_skills(user_msg)
    except Exception:
        mem_block = "(none)"
        todo_block = "(none)"
        skill_block = "(none)"
    if len(mem_block) > 1500:
        mem_block = mem_block[:1500] + "\n... [truncated]"
    proj_block = _project_docs_block(cfg)
    from datetime import datetime as _dt

    today = _dt.now().strftime("%A, %Y-%m-%d")
    approval_mode = (
        "Approval mode: AUTOMATIC — call write tools directly, do not ask."
        if auto_approve
        else "Approval mode: CONFIRM — each write triggers a user prompt, but still CALL the tool (never ask in prose)."
    )
    try:
        from .config import TIERS as _TIERS

        smart_model = _TIERS.get("ollama", {}).get("smart", "qwen2.5-coder:7b")
    except Exception:
        smart_model = "qwen2.5-coder:7b"
    messages: list[dict] = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT.format(
                cwd=os.getcwd(),
                home=str(Path.home()),
                os=platform.system(),
                platform=platform.platform(),
                sysinfo=snapshot,
                memories=mem_block,
                todos=todo_block,
                skills=skill_block,
                projdocs=proj_block,
                today=today,
                approval_mode=approval_mode,
                smart_model=smart_model,
            ),
        },
        *history[-20:],
        {"role": "user", "content": user_msg},
    ]
    return messages


def run_agent(
    user_msg: str,
    history: list[dict],
    cfg: Config,
    on_tool: object = None,
    on_token: object = None,
    approve: object = None,
    on_reasoning: object = None,
    auto_approve: bool = False,
    session: str = "",
    review_plan=None,
) -> str:
    """One agent turn with up to cfg.max_steps tool iterations. Returns final text.

    approve(name, args) -> bool: gate for APPROVAL_TOOLS. If None, auto-approve.
    on_tool(name, args, result_or_denied) is notification only.
    on_reasoning(chunk) receives thinking deltas separately when given.
    auto_approve only changes the prompt line (tool gating is the caller's
    approve callback); pass True when --yes/yolo so the model calls directly.
    session tags audit rows (tool_runs) for `sk audit`. Empty session falls
    back to the audit_session context var (used by the TUI worker path).
    review_plan(plan_text, calls) -> bool: one confirmation for multi-tool
    turns with destructive actions (skipped when None or auto_approve).
    """
    session = session or audit_session.get()
    quick = _quick_reply(user_msg)
    if quick is not None:
        if on_token is not None:
            try:
                on_token(quick)  # type: ignore
            except Exception:
                pass
        return quick
    if cfg.provider == "anthropic":
        from .anthropic_backend import run_anthropic_agent

        return run_anthropic_agent(
            user_msg,
            history,
            cfg,
            on_tool=on_tool,
            on_token=on_token,
            approve=approve,
            on_reasoning=on_reasoning,
            auto_approve=auto_approve,
            session=session,
            review_plan=review_plan,
        )
    client = get_client(cfg)
    # perf: small ctx keeps KV cache off VRAM so more 7B layers fit on GPU.
    # Token cap is provider-aware: tight on CPU offload, roomy on cloud GPUs
    # so plans don't get cut off mid-tool-call.
    # (Ollama-only knobs live in _extra_body; cloud gets plain {}.)
    extra = _extra_body(cfg)
    max_tokens = 350 if cfg.provider in ("ollama", "lmstudio") else 800

    def _summarize(text: str) -> str:
        m = _stream_chat(
            client,
            cfg.model,
            [{"role": "user", "content": _SUMMARY_PROMPT + "\n\n" + text[:6000]}],
            None,
            cfg.temperature,
            400,
            extra,
            None,
            None,
        )
        out = ((m.content or "") + "\n" + (m.reasoning or "")).strip()
        if not out:
            raise RuntimeError("empty summary")
        return out

    history = prepare_history(session, history, cfg, _summarize)
    messages = build_messages(user_msg, history, cfg, auto_approve=auto_approve)
    try:
        from .store import log_tool_run

        log_tool_run(
            session,
            "llm_call",
            cfg.model,
            approved=True,
            provider=cfg.provider,
            host=_provider_host(cfg),
        )
    except Exception:
        pass

    final_text = ""
    seen: dict[str, str] = {}  # target-key -> result; stops re-fetch loops
    continued = 0
    for _ in range(cfg.max_steps):
        msg = _stream_chat(
            client,
            cfg.model,
            messages,
            TOOLS_SCHEMA,
            cfg.temperature,
            max_tokens,
            extra,
            on_token,
            on_reasoning,
        )

        # qwen3-style reasoning models put text in .reasoning, content empty
        msg_text = (msg.content or "").strip()
        if not msg_text:
            reason = getattr(msg, "reasoning", "") or ""
            if isinstance(reason, str) and reason.strip():
                msg_text = reason.strip()[-1500:]  # fallback so we never return ""

        # fallback: some Ollama models (qwen2.5-coder via OpenAI endpoint)
        # emit tool JSON as text instead of native tool_calls. Parse ALL of them.
        text_tools = _parse_text_tools(msg_text)
        if getattr(msg, "tool_calls", None) is None and text_tools:
            batch = list(text_tools[:4])  # cap 4 per turn
            proceed, turn_approve = _maybe_review_plan(
                batch,
                approve,
                review_plan,
                auto_approve,
                session,
                cfg.provider,
                _provider_host(cfg),
            )
            if not proceed:
                return "Plan denied by user — nothing was executed."
            messages.append({"role": "assistant", "content": msg_text})
            outs = _run_tools_batch(batch, turn_approve, on_tool, seen, session, cfg)
            combined = [
                f"[tool {tname} result]\n{result}" for (tname, _), (result, _) in zip(batch, outs)
            ]
            messages.append(
                {
                    "role": "user",
                    "content": "\n".join(combined)
                    + "\nAnswer the original question concisely using these results. Do not emit more tool JSON.",
                }
            )
            continue

        # no tool call -> done, unless cut off mid-thought (finish=length):
        # then ask for continuation instead of accepting a plan with no action.
        if not getattr(msg, "tool_calls", None):
            if msg.finish == "length" and continued < 2:
                continued += 1
                messages.append({"role": "assistant", "content": msg_text})
                messages.append(
                    {"role": "user", "content": "Continue: emit the tool calls now, no more prose."}
                )
                continue
            final_text = msg_text
            messages.append({"role": "assistant", "content": final_text})
            break

        # has tool calls: parse, plan-review gate, then append assistant turn + execute
        parsed: list[tuple[str, str, dict]] = []
        for tc in msg.tool_calls:  # type: ignore
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            parsed.append((tc.id, tc.function.name, args))
        batch = [(name, args) for _, name, args in parsed]
        proceed, turn_approve = _maybe_review_plan(
            batch, approve, review_plan, auto_approve, session, cfg.provider, _provider_host(cfg)
        )
        if not proceed:
            return "Plan denied by user — nothing was executed."
        messages.append(
            {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in msg.tool_calls  # type: ignore
                ],
            }
        )
        outs = _run_tools_batch(batch, turn_approve, on_tool, seen, session, cfg)
        for (tid, _, _), (result, _) in zip(parsed, outs):
            messages.append({"role": "tool", "tool_call_id": tid, "content": result})

        # after tools, loop to let model synthesize (next iteration)
        # peek: if last iteration, force final synthesis
        if _ == cfg.max_steps - 1:
            m2 = _stream_chat(
                client,
                cfg.model,
                messages,
                None,
                cfg.temperature,
                max_tokens,
                extra,
                on_token,
                on_reasoning,
            )
            final_text = m2.content or m2.reasoning or ""
            messages.append({"role": "assistant", "content": final_text})
    else:
        final_text = final_text or "(max steps reached)"

    return final_text
