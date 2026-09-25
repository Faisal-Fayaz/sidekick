"""CLI approval + streaming callbacks (split from sk/cli.py, pure move)."""

from __future__ import annotations

import typer
from rich.panel import Panel

from .base import console


def _make_approver(
    auto_yes: bool,
    preapproved: tuple[str, ...] = (),
    allow: tuple[str, ...] = (),
):
    from sk.config import is_project_approved, is_session_allowed
    from sk.tools import approval_tools

    def approve(name: str, args: dict) -> bool:
        if name not in approval_tools():
            return True
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
    """Like _make_approver but reads live state['yolo'] (for /yolo toggling)."""

    def approve(name: str, args: dict) -> bool:
        from sk.config import is_project_approved, is_session_allowed
        from sk.tools import approval_tools

        if name not in approval_tools():
            return True
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


def _make_on_tool():
    def on_tool(name, args):
        # newline first since tokens stream without newlines
        console.print()
        console.print(
            f"[dim]○ tool: {name} {args if name not in ('write_file',) else {'path': args.get('path')}}[/dim]"
        )

    return on_tool


def _make_plan_reviewer(state: dict):
    """One confirmation for a whole multi-tool plan (no per-tool re-prompts).

    Reads live state['yolo'] like the approvers: yolo mode proceeds silently.
    """

    def review(plan_text: str, calls: list) -> bool:
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


def _make_on_token():
    import sys

    state = {"n": 0}

    def on_token(tok: str):
        state["n"] += len(tok)
        sys.stdout.write(tok)
        sys.stdout.flush()

    on_token.state = state
    return on_token
