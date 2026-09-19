"""Agent loop: talks to Ollama (OpenAI-compatible) with tool-calling."""

from __future__ import annotations

import json
import os
from pathlib import Path

from openai import OpenAI

from .config import Config
from .tools import TOOLS_SCHEMA, WRITE_TOOLS, dispatch_tool, tool_sysinfo


SYSTEM_PROMPT = """You are Sidekick, a local-first terminal companion.
You run on the user's Linux machine via Ollama.
Rules:
- Be concise, terminal-friendly (short markdown, no fluff).
- Prefer using tools: sysinfo, list_dir, read_file, exec, write_file, edit_file, remember, recall, todo_add, todo_list, todo_done, read_url.
- WEB: for summarize/docs/URL questions, call read_url (public http/https only). For "search the internet / latest / right now" questions, call web_search FIRST, then read_url the best hits. Never fetch localhost/private IPs. You HAVE these tools — never claim you cannot fetch URLs or search.
- GREETINGS: hi/hello/thanks/bye get a direct one-line reply. Never call tools for greetings.
- MEMORY: user facts are in SAVED MEMORIES below. Use them (e.g. preferred model, projects). If user says "remember X", call remember. If asked "what do you remember / my prefs", call recall.
- TODOS: open todos are in OPEN TODOS below. If user says "add todo / my todos / done #N", use todo tools. Proactively offer next todo when asked "what next".
- GROUNDING (mandatory): if the question contains my / my device / my machine / hardware / what LLM / what model can I run, you MUST call sysinfo first. Never guess RAM/GPU/CPU. Use the sysinfo output numbers in your answer.
- PATHS (mandatory): ~/X means /home/faisal/X, NOT ./X. If user asks about ~/neural-hangar, you MUST call list_dir with path "~/neural-hangar" (or "/home/faisal/neural-hangar"). Never answer "does not exist" from cwd listing. cwd is {cwd} but ~ is /home/faisal. Always try the exact path first.
- exec is READ-ONLY (ls, df, free, git status, etc). Never claim you ran a blocked command.
- WRITES need approval: write_file/edit_file will ask the user. Announce what you will write + why before calling. Keep writes under HOME or /tmp, max 100KB. Never write to ~/.ssh, ~/.gnupg, /etc, /usr.
- If a tool is blocked/denied, explain why and suggest an allowed alternative.
- Recommend only Ollama models (qwen, llama, mistral, phi, gemma). Never recommend GPT-2/GPT-3.5/GPT-4/transformers for local run. VRAM truth: 3-4B fits 4GB VRAM easily and fast; 7-8B CAN run with partial CPU offload (you are qwen2.5-coder:7b doing it now) but slower, needs swap; 14B+ does NOT fit this box.
- To use a tool, use native function calling. If that is unavailable, emit EXACTLY one fenced block: ```json {{"name": "sysinfo", "arguments": {{}}}}``` or {{"name": "list_dir", "arguments": {{"path": "~/neural-hangar"}}}} and nothing else.
- Current working directory: {cwd} — HOME is /home/faisal.
- Today is {today}. Answer date/day questions from this, never tools or memory.
- OS: Linux.
REAL SYSTEM SNAPSHOT (do not re-guess, but still call sysinfo tool if user asks about their device so the trace shows grounding):
{sysinfo}
SAVED MEMORIES (use these, do not re-ask):
{memories}
OPEN TODOS:
{todos}
SKILLS (follow these packs when relevant):
{skills}
"""


def get_client(cfg: Config) -> OpenAI:
    return OpenAI(base_url=cfg.effective_base_url(), api_key=cfg.effective_api_key(), timeout=300.0)


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
    """Deterministic grounding: if user mentions ~/X or /home/faisal/X, list it + read package.json/README.

    This does NOT rely on the model calling tools — it injects facts so the model
    cannot hallucinate 'does not exist'.
    """
    import re

    from .tools import tool_list_dir, tool_read_file

    # find ~/foo/bar and /home/faisal/foo patterns
    paths: list[str] = []
    paths += re.findall(r"(~/[\w\-./~]+)", text)
    paths += re.findall(r"(/home/faisal/[\w\-./]+)", text)
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
            exp = str(Path(p).expanduser().resolve())
        except Exception:
            continue
        listing = tool_list_dir(p)
        chunks.append(f"[path {p} -> {exp}]\n{listing[:1500]}")
        # if dir, try package.json + README.md for scope
        try:
            base = Path(p).expanduser().resolve()
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

    m = re.search(r"search\s+(?:on\s+)?(?:the\s+)?(?:internet|web)\b\s*(?:for\s+)?(.+)", text, re.IGNORECASE)
    query = ""
    if m:
        query = m.group(1).strip().rstrip("?.!")[:200]
    else:
        low = text.lower()
        recency = re.search(r"\b(right now|latest|currently|up[- ]to[- ]date|this week|today|2026)\b", low)
        local = re.search(r"~/|/home/faisal|my (device|machine|files?|todos?|projects?|prefs?)|can i run|do i (have|need)", low)
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
    import re

    allowed = {"sysinfo", "list_dir", "read_file", "exec", "write_file", "edit_file", "remember", "recall", "todo_add", "todo_list", "todo_done", "read_url", "web_search"}
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


def _run_tool_cached(
    name: str, args: dict, approve, on_tool, seen: dict[str, str]
) -> tuple[str, bool]:
    """Execute unless this exact target already ran this turn. Returns (result, repeated)."""
    key = _tool_target(name, args)
    if key in seen:
        return (f"[cached — already ran above]\n{seen[key][:2000]}\nSynthesize the final answer now. Do not call more tools.", True)
    result, _ = _gated_dispatch(name, args, approve)
    seen[key] = result
    if on_tool is not None:
        try:
            on_tool(name, args)  # type: ignore
        except Exception:
            pass
    return (result, False)


def _gated_dispatch(name: str, args: dict, approve: object = None) -> tuple[str, bool]:
    """Run dispatch_tool with approval gate. Returns (result, approved)."""
    if name in WRITE_TOOLS and approve is not None:
        try:
            ok = approve(name, args)  # type: ignore
        except Exception:
            ok = False
        if not ok:
            return (f"Denied by user: {name} {args} not executed. Explain and suggest --yes or manual command.", False)
    return (dispatch_tool(name, args), True)


class _TC:
    def __init__(self, id: str, name: str, args: str):
        self.id = id
        self.function = type("F", (), {"name": name, "arguments": args})()


class _Msg:
    def __init__(self, content: str, tool_calls: list | None, reasoning: str = ""):
        self.content = content
        self.tool_calls = tool_calls
        self.reasoning = reasoning


def _stream_chat(client, model: str, messages: list[dict], tools, temperature: float, max_tokens: int, extra: dict, on_token=None, on_reasoning=None) -> _Msg:
    """Streaming chat.completions with tool accumulation.

    Content deltas -> on_token, reasoning deltas -> on_reasoning (falls back
    to on_token when no separate sink is given, so old callers keep working).
    """
    _on_r = on_reasoning if on_reasoning is not None else on_token
    acc_text = ""
    acc_reason = ""
    tc_buf: dict[int, dict] = {}  # idx -> {id, name, args}
    try:
        stream = client.chat.completions.create(
            model=model, messages=messages, tools=tools, tool_choice="auto" if tools else "none",  # type: ignore
            temperature=temperature, max_tokens=max_tokens, stream=True, extra_body=extra,  # type: ignore
        )
        for chunk in stream:
            try:
                choice = chunk.choices[0]
            except Exception:
                continue
            delta = getattr(choice, "delta", None)
            if delta is None:
                continue
            # reasoning field (qwen3 via Ollama) — separate sink when provided
            r = getattr(delta, "reasoning", None) or (delta.get("reasoning") if isinstance(delta, dict) else None)
            if r:
                acc_reason += r if isinstance(r, str) else str(r)
                if _on_r is not None:
                    try:
                        _on_r(r if isinstance(r, str) else str(r))  # type: ignore
                    except Exception:
                        pass
            c = getattr(delta, "content", None)
            if c is None and isinstance(delta, dict):
                c = delta.get("content")
            if c:
                acc_text += c
                if on_token is not None:
                    try:
                        on_token(c)  # type: ignore
                    except Exception:
                        pass
            tcs = getattr(delta, "tool_calls", None)
            if tcs is None and isinstance(delta, dict):
                tcs = delta.get("tool_calls")
            if tcs:
                for tc in tcs:
                    idx = tc.index if hasattr(tc, "index") else tc.get("index", 0)
                    buf = tc_buf.setdefault(idx, {"id": "", "name": "", "args": ""})
                    tid = getattr(tc, "id", None) or (tc.get("id") if isinstance(tc, dict) else None)
                    if tid:
                        buf["id"] = tid
                    fn = getattr(tc, "function", None) or (tc.get("function") if isinstance(tc, dict) else None)
                    if fn:
                        n = getattr(fn, "name", None) or (fn.get("name") if isinstance(fn, dict) else None)
                        a = getattr(fn, "arguments", None) or (fn.get("arguments") if isinstance(fn, dict) else None)
                        if n:
                            buf["name"] = (buf["name"] or "") + n
                        if a:
                            buf["args"] = (buf["args"] or "") + a
    except Exception as e:
        # fallback to non-streaming on error
        resp = client.chat.completions.create(
            model=model, messages=messages, tools=tools, tool_choice="auto" if tools else "none",  # type: ignore
            temperature=temperature, max_tokens=max_tokens, stream=False, extra_body=extra,  # type: ignore
        )
        m = resp.choices[0].message
        return _Msg(m.content or "", getattr(m, "tool_calls", None), getattr(m, "reasoning", "") or "")
    tool_calls = None
    if tc_buf:
        tool_calls = [_TC(b["id"] or f"call_{i}", b["name"], b["args"]) for i, b in sorted(tc_buf.items()) if b["name"]]
        if not tool_calls:
            tool_calls = None
    return _Msg(acc_text, tool_calls, acc_reason)


def build_messages(user_msg: str, history: list[dict], cfg: Config) -> list[dict]:
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
        user_msg = user_msg + f"\n\n[AUTO LOCAL FACTS — these paths DO exist, never say otherwise]:\n{auto_ctx[:5000]}"
    web_ctx = _auto_web_context(user_msg)
    if web_ctx:
        user_msg = user_msg + f"\n\n[AUTO WEB FACTS — already fetched, summarize directly, never claim inability]:\n{web_ctx[:6500]}"
    search_ctx = _auto_search_context(user_msg)
    if search_ctx:
        user_msg = user_msg + f"\n\n[AUTO SEARCH — results below, answer from them + read_url the best hit if needed]:\n{search_ctx[:3000]}"
    try:
        from .store import list_todos, recall_memories

        mem_hits = recall_memories(user_msg, limit=5)
        mem_block = "\n".join(f"- {m}" for m in mem_hits) if mem_hits else "(none yet)"
        todo_rows = list_todos(open_only=True)[:5]
        todo_block = "\n".join(f"#{i}: {t}" for i, t, _ in todo_rows) if todo_rows else "(none)"
        from .skills import load_skills

        skill_block = load_skills()
    except Exception:
        mem_block = "(none)"
        todo_block = "(none)"
        skill_block = "(none)"
    if len(mem_block) > 1500:
        mem_block = mem_block[:1500] + "\n... [truncated]"
    from datetime import datetime as _dt

    today = _dt.now().strftime("%A, %Y-%m-%d")
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT.format(cwd=os.getcwd(), sysinfo=snapshot, memories=mem_block, todos=todo_block, skills=skill_block, today=today)},
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
) -> str:
    """One agent turn with up to cfg.max_steps tool iterations. Returns final text.

    approve(name, args) -> bool: gate for WRITE_TOOLS. If None, auto-approve.
    on_tool(name, args, result_or_denied) is notification only.
    on_reasoning(chunk) receives thinking deltas separately when given.
    """
    quick = _quick_reply(user_msg)
    if quick is not None:
        if on_token is not None:
            try:
                on_token(quick)  # type: ignore
            except Exception:
                pass
        return quick
    client = get_client(cfg)
    messages = build_messages(user_msg, history, cfg)

    final_text = ""
    # perf: small ctx keeps KV cache off VRAM so more 7B layers fit on GPU;
    # 350-token cap bounds worst-case generation time on CPU offload.
    # (Ollama-only knobs live in _extra_body; cloud gets plain {}.)
    extra = _extra_body(cfg)
    seen: dict[str, str] = {}  # target-key -> result; stops re-fetch loops
    for _ in range(cfg.max_steps):
        msg = _stream_chat(client, cfg.model, messages, TOOLS_SCHEMA, cfg.temperature, 350, extra, on_token, on_reasoning)

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
            messages.append({"role": "assistant", "content": msg_text})
            combined: list[str] = []
            for tname, targs in text_tools[:4]:  # cap 4 per turn
                result, _ = _run_tool_cached(tname, targs, approve, on_tool, seen)
                combined.append(f"[tool {tname} result]\n{result}")
            messages.append({"role": "user", "content": "\n".join(combined) + "\nAnswer the original question concisely using these results. Do not emit more tool JSON."})
            continue

        # no tool call -> done
        if not getattr(msg, "tool_calls", None):
            final_text = msg_text
            messages.append({"role": "assistant", "content": final_text})
            break

        # has tool calls: append assistant turn, execute each
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
        for tc in msg.tool_calls:  # type: ignore
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            result, _ = _run_tool_cached(name, args, approve, on_tool, seen)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

        # after tools, loop to let model synthesize (next iteration)
        # peek: if last iteration, force final synthesis
        if _ == cfg.max_steps - 1:
            m2 = _stream_chat(client, cfg.model, messages, None, cfg.temperature, 350, extra, on_token, on_reasoning)
            final_text = m2.content or m2.reasoning or ""
            messages.append({"role": "assistant", "content": final_text})
    else:
        final_text = final_text or "(max steps reached)"

    return final_text
