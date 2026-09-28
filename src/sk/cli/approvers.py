"""CLI approval + streaming callbacks (split from sk/cli.py, pure move)."""

from __future__ import annotations

import typer
from rich.panel import Panel

from .base import console


def _deny_readonly(name: str) -> bool:
    """Deny + explain for read-only mode. Read-only beats every allow path."""
    console.print(f"[dim]read-only mode: denied {name}[/dim]")
    return False


PLAN_DENIED_TOOLS = ("write_file", "edit_file", "delete_file", "make_dir", "generate_image")


def _deny_plan(name: str) -> bool:
    """Deny + explain for plan mode (file writes only; shell still asks)."""
    console.print(f"[dim]plan mode: denied {name} (propose, don't implement)[/dim]")
    return False


def _make_approver(
    auto_yes: bool,
    preapproved: tuple[str, ...] = (),
    allow: tuple[str, ...] = (),
    readonly: bool = False,
    plan_mode: bool = False,
):
    from sk.config import is_project_approved, is_session_allowed
    from sk.tools import approval_tools

    def approve(name: str, args: dict) -> bool:
        if name not in approval_tools():
            return True
        if readonly:
            return _deny_readonly(name)
        if plan_mode and name in PLAN_DENIED_TOOLS:
            return _deny_plan(name)
        if is_project_approved(name, args, preapproved):
            console.print(f"[dim]project-approved {name} -> {args.get('cmd', '?')}[/dim]")
            return True
        if is_session_allowed(name, args, allow):
            console.print(
                f"[dim]allow-approved {name} -> {args.get('cmd', args.get('path', '?'))}[/dim]"
            )
            return True
        target = args.get("path", args.get("cmd", "?"))
        preview = ""
        if name == "write_file":
            c = str(args.get("content", ""))
            preview = c[:600] + ("... [truncated]" if len(c) > 600 else "")
        elif name == "make_dir":
            preview = "(new directory)"
        elif name == "shell":
            preview = f"$ {str(args.get('cmd', ''))[:600]}"
        elif name == "delete_file":
            preview = "(PERMANENT delete)"
        else:
            old = str(args.get("old_string", ""))[:300]
            new = str(args.get("new_string", ""))[:300]
            preview = f"OLD:\n{old}\nNEW:\n{new}"
        console.print(
            Panel(
                f"[bold yellow]approval[/] {name} -> [cyan]{target}[/cyan]\n{preview}", expand=False
            )
        )
        if auto_yes:
            console.print("[dim]--yes: auto-approved[/dim]")
            return True
        try:
            return typer.confirm("Allow?", default=False)
        except (EOFError, KeyboardInterrupt, OSError):
            return False

    return approve


def _make_approver_state(
    state: dict,
    preapproved: tuple[str, ...] = (),
    allow: tuple[str, ...] = (),
):
    """Like _make_approver but reads live state['yolo']/state['readonly']/
    state['plan'] (for slash toggling)."""

    def approve(name: str, args: dict) -> bool:
        from sk.config import is_project_approved, is_session_allowed
        from sk.tools import approval_tools

        if name not in approval_tools():
            return True
        if state.get("readonly"):
            return _deny_readonly(name)
        if state.get("plan") and name in PLAN_DENIED_TOOLS:
            return _deny_plan(name)
        if state.get("yolo"):
            console.print(f"[dim]yolo: auto-approved {name} -> {args.get('path', '?')}[/dim]")
            return True
        if is_project_approved(name, args, preapproved):
            console.print(f"[dim]project-approved {name} -> {args.get('cmd', '?')}[/dim]")
            return True
        if is_session_allowed(name, args, allow):
            console.print(
                f"[dim]allow-approved {name} -> {args.get('cmd', args.get('path', '?'))}[/dim]"
            )
            return True
        return _make_approver(False)(name, args)

    return approve


def _make_on_tool(state: dict | None = None):
    """Print a tool line. If `state` opts into streamed status lines
    ({"clear_line": True}), wipe the current status line first so stale
    "thinking..." never survives the turn."""
    import sys

    def _clear_line() -> None:
        if state and state.get("clear_line") and not state.get("cleared"):
            sys.stdout.write("\r\x1b[2K")
            state["cleared"] = True

    def on_tool(name, args):
        _clear_line()
        # newline first since tokens stream without newlines
        console.print()
        console.print(
            f"[dim]○ tool: {name} {args if name not in ('write_file',) else {'path': args.get('path')}}[/dim]"
        )

    return on_tool


def _make_plan_reviewer(state: dict):
    """One confirmation for a whole multi-tool plan (no per-tool re-prompts).

    Reads live state['yolo'] like the approvers: yolo mode proceeds silently.
    Read-only mode denies any plan containing an approval-gated tool; plan
    mode denies plans containing file writes (shell-only plans still ask).
    """

    def review(plan_text: str, calls: list) -> bool:
        from sk.tools import approval_tools

        gated = approval_tools()
        if state.get("readonly") and any(n in gated for n, _ in calls):
            console.print("[dim]read-only mode: denied plan with file writes[/dim]")
            return False
        if state.get("plan") and any(n in PLAN_DENIED_TOOLS for n, _ in calls):
            console.print("[dim]plan mode: denied plan with file writes[/dim]")
            return False
        if state.get("yolo"):
            console.print(f"[dim]yolo: auto-approved plan ({len(calls)} tools)[/dim]")
            return True
        console.print(
            Panel(f"[bold yellow]plan[/] ({len(calls)} tools)\n{plan_text}", expand=False)
        )
        try:
            return typer.confirm("Run this plan?", default=False)
        except (EOFError, KeyboardInterrupt, OSError):
            return False

    return review


def _make_on_token(state: dict | None = None):
    import sys

    state = state if state is not None else {}
    state.setdefault("n", 0)
    state.setdefault("cleared", False)

    def on_token(tok: str):
        if state.get("clear_line") and not state["cleared"]:
            sys.stdout.write("\r\x1b[2K")
            state["cleared"] = True
        state["n"] += len(tok)
        sys.stdout.write(tok)
        sys.stdout.flush()

    on_token.state = state  # type: ignore[attr-defined]
    return on_token


def _make_on_reasoning():
    """Dim-italic live reasoning writer (qwen3/Claude thinking deltas).

    Display-only: never counted in streamed-fraction checks, never saved.
    Callers must pass None instead for --json output purity.
    """
    from rich.text import Text

    def on_reasoning(chunk: str):
        try:
            console.print(Text(str(chunk or ""), style="dim italic"), end="")
        except Exception:
            pass

    return on_reasoning
