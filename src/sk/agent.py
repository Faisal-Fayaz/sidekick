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
- Prefer using tools: sysinfo, list_dir, read_file, exec, write_file, edit_file, remember, recall, todo_add, todo_list, todo_done.
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
- OS: Linux.
REAL SYSTEM SNAPSHOT (do not re-guess, but still call sysinfo tool if user asks about their device so the trace shows grounding):
{sysinfo}
SAVED MEMORIES (use these, do not re-ask):
{memories}
OPEN TODOS:
{todos}
"""


def get_client(cfg: Config) -> OpenAI:
    return OpenAI(base_url=cfg.base_url, api_key=cfg.api_key, timeout=300.0)


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


def _parse_text_tool(text: str) -> tuple[str, dict] | None:
    """Parse ```json {"name": "list_dir", "arguments": {...}}``` or bare JSON.

    Returns (name, args) or None. Only allows known tools.
    """
    import re

    allowed = {"sysinfo", "list_dir", "read_file", "exec", "write_file", "edit_file", "remember", "recall", "todo_add", "todo_list", "todo_done"}
    # try fenced block first
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidates = [m.group(1)] if m else []
    # plus try whole text as JSON
    candidates.append(text.strip())
    for cand in candidates:
        try:
            obj = json.loads(cand)
        except Exception:
            continue
        # direct form: {"name": "list_dir", "arguments": {...}}
        if isinstance(obj, dict) and obj.get("name") in allowed:
            args = obj.get("arguments", {}) or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            if isinstance(args, dict):
                return (obj["name"], args)
        # openai form: {"function": {"name":..., "arguments":...}}
        if isinstance(obj, dict) and isinstance(obj.get("function"), dict):
            fn = obj["function"]
            if fn.get("name") in allowed:
                args = fn.get("arguments", {}) or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {}
                if isinstance(args, dict):
                    return (fn["name"], args)
    return None


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


def _stream_chat(client, model: str, messages: list[dict], tools, temperature: float, max_tokens: int, extra: dict, on_token=None) -> _Msg:
    """Streaming chat.completions with tool accumulation. Calls on_token per text delta."""
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
            # reasoning field (qwen3 via Ollama) — stream it too so UI never looks dead
            r = getattr(delta, "reasoning", None) or (delta.get("reasoning") if isinstance(delta, dict) else None)
            if r:
                acc_reason += r if isinstance(r, str) else str(r)
                if on_token is not None:
                    try:
                        on_token(r if isinstance(r, str) else str(r))  # type: ignore
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


def run_agent(
    user_msg: str,
    history: list[dict],
    cfg: Config,
    on_tool: object = None,
    on_token: object = None,
    approve: object = None,
) -> str:
    """One agent turn with up to cfg.max_steps tool iterations. Returns final text.

    approve(name, args) -> bool: gate for WRITE_TOOLS. If None, auto-approve.
    on_tool(name, args, result_or_denied) is notification only.
    """
    client = get_client(cfg)
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
    try:
        from .store import list_todos, recall_memories

        mem_hits = recall_memories(user_msg, limit=5)
        mem_block = "\n".join(f"- {m}" for m in mem_hits) if mem_hits else "(none yet)"
        todo_rows = list_todos(open_only=True)[:5]
        todo_block = "\n".join(f"#{i}: {t}" for i, t, _ in todo_rows) if todo_rows else "(none)"
    except Exception:
        mem_block = "(none)"
        todo_block = "(none)"
    if len(mem_block) > 1500:
        mem_block = mem_block[:1500] + "\n... [truncated]"
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT.format(cwd=os.getcwd(), sysinfo=snapshot, memories=mem_block, todos=todo_block)},
        *history[-20:],
        {"role": "user", "content": user_msg},
    ]

    final_text = ""
    # perf: smaller ctx + cap output so 4GB VRAM box stays fast
    extra = {"options": {"num_ctx": 8192, "num_predict": 600}}
    for _ in range(cfg.max_steps):
        msg = _stream_chat(client, cfg.model, messages, TOOLS_SCHEMA, cfg.temperature, 600, extra, on_token)

        # qwen3-style reasoning models put text in .reasoning, content empty
        msg_text = (msg.content or "").strip()
        if not msg_text:
            reason = getattr(msg, "reasoning", "") or ""
            if isinstance(reason, str) and reason.strip():
                msg_text = reason.strip()[-1500:]  # fallback so we never return ""

        # fallback: some Ollama models (qwen2.5-coder via OpenAI endpoint)
        # emit tool JSON as text instead of native tool_calls. Parse it.
        text_tool = _parse_text_tool(msg_text)
        if getattr(msg, "tool_calls", None) is None and text_tool is not None:
            tname, targs = text_tool
            result, _ = _gated_dispatch(tname, targs, approve)
            messages.append({"role": "assistant", "content": msg_text})
            if on_tool is not None:
                try:
                    on_tool(tname, targs)  # type: ignore
                except Exception:
                    pass
            messages.append({"role": "user", "content": f"[tool {tname} result]\n{result}\nAnswer the original question concisely using this result. Do not emit more tool JSON."})
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
            result, _ = _gated_dispatch(name, args, approve)
            if on_tool is not None:
                try:
                    on_tool(name, args)  # type: ignore
                except Exception:
                    pass
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

        # after tools, loop to let model synthesize (next iteration)
        # peek: if last iteration, force final synthesis
        if _ == cfg.max_steps - 1:
            m2 = _stream_chat(client, cfg.model, messages, None, cfg.temperature, 600, extra, on_token)
            final_text = m2.content or m2.reasoning or ""
            messages.append({"role": "assistant", "content": final_text})
    else:
        final_text = final_text or "(max steps reached)"

    return final_text
