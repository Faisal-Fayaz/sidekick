"""Background runs: detached `sk run --bg` workers with job records. No new deps.

A --bg run persists a job in ~/.sidekick/jobs.json, spawns a detached
`python -m sk run-bg-worker <id>` (stdio detached, own session), and
returns the job id at once. The worker runs the agent turn headless
(prompts fail closed: detached stdin denies approvals), saves the
transcript, marks the job done/error, and fires notify() (DND-aware).
Spend caps (#30) apply: run_agent enforces them before any LLM call.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from sk.atomic import atomic_write_text, quarantine

JOBS_PATH = Path.home() / ".sidekick" / "jobs.json"

# Set by load_jobs() when the file existed but could not be read (#316). Module
# level because load_jobs() is called from many read-only paths that have no
# other way to surface a problem; last_load_error() is the accessor.
_LAST_LOAD_ERROR = ""


def load_jobs() -> dict:
    """All job records. Never raises.

    A missing file is legitimately empty. A *corrupt* one is not: returning {}
    there let the next save_jobs() overwrite the whole registry, so a truncated
    write destroyed every record rather than just the unreadable one (#316). The
    corrupt file is moved aside — a truncated file is usually mostly intact, so
    it is worth keeping — and the condition is recorded for `sk jobs` to show.
    """
    global _LAST_LOAD_ERROR
    try:
        data = json.loads(JOBS_PATH.read_text())
    except FileNotFoundError:
        _LAST_LOAD_ERROR = ""
        return {}
    except Exception as e:
        moved = quarantine(JOBS_PATH, "jobs")
        _LAST_LOAD_ERROR = f"jobs.json was unreadable ({e})" + (
            f"; moved aside to {moved.name}" if moved else "; could not move it aside"
        )
        return {}
    if not isinstance(data, dict):
        moved = quarantine(JOBS_PATH, "jobs")
        _LAST_LOAD_ERROR = "jobs.json did not contain an object" + (
            f"; moved aside to {moved.name}" if moved else ""
        )
        return {}
    _LAST_LOAD_ERROR = ""
    return data


def last_load_error() -> str:
    """Why the last load_jobs() came back empty, if it was not simply absent."""
    return _LAST_LOAD_ERROR


def save_jobs(jobs: dict) -> None:
    atomic_write_text(JOBS_PATH, json.dumps(jobs))


def create_job(
    task: str,
    session: str,
    model: str,
    yes: bool,
    spend_cap: float = 0.0,
    allow: tuple[str, ...] | list[str] = (),
    readonly: bool = False,
    plan: bool = False,
    profile: str = "",
    deny: tuple[str, ...] | list[str] = (),
) -> str:
    """Persist a running job. Returns the job id. Never raises ('' on failure)."""
    try:
        from .store import new_session_id

        job_id = new_session_id("job")
        jobs = load_jobs()
        jobs[job_id] = {
            "task": task,
            "session": session,
            "model": model,
            "yes": bool(yes),
            "readonly": bool(readonly),
            "plan": bool(plan),
            "profile": str(profile or ""),
            "spend_cap": float(spend_cap or 0.0),
            "allow": [str(e) for e in (allow or [])],
            "deny": [str(e) for e in (deny or [])],
            "status": "running",
            "answer": "",
            "error": "",
            "created": time.time(),
            "finished": 0.0,
        }
        save_jobs(jobs)
        return job_id
    except Exception:
        return ""


def finish_job(job_id: str, answer: str = "", error: str = "") -> None:
    """Mark a job done/error with its outcome. Never raises."""
    try:
        jobs = load_jobs()
        job = jobs.get(job_id)
        if not isinstance(job, dict):
            return
        job["status"] = "error" if error else "done"
        job["answer"] = (answer or "")[:8000]
        job["error"] = (error or "")[:500]
        job["finished"] = time.time()
        save_jobs(jobs)
    except Exception:
        pass


def spawn_worker(job_id: str) -> bool:
    """Detach `run-bg-worker <id>` (own session, stdio detached). Never raises."""
    try:
        subprocess.Popen(
            [sys.executable, "-m", "sk", "run-bg-worker", job_id],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except Exception:
        return False


def run_bg_worker(job_id: str) -> str:
    """Execute one background job synchronously. Returns the answer (or error text).

    Factored out of the CLI so tests drive it directly. Never raises.
    """
    try:
        from .agent import run_agent
        from .config import Config
        from .daemon import notify
        from .store import get_history, save_message

        job = load_jobs().get(job_id)
        if not isinstance(job, dict):
            return f"Error: unknown job {job_id}."
        task = str(job.get("task", ""))
        session = str(job.get("session", "default"))
        try:
            cap = float(job.get("spend_cap", 0.0) or 0.0)
        except (TypeError, ValueError):
            cap = 0.0

        from .cli.approvers import _make_approver

        if str(job.get("profile", "") or "").strip():
            import os

            from .config import PROFILE_ENV

            os.environ[PROFILE_ENV] = str(job["profile"]).strip()
        cfg = Config.load()
        cfg.model = str(job.get("model", "")) or cfg.model
        cfg.spend_cap_usd = cap
        allow = tuple(str(e) for e in (job.get("allow", []) or []))
        save_message(session, "user", task)
        readonly = bool(job.get("readonly", False))
        plan = bool(job.get("plan", False))
        approve = _make_approver(
            bool(job.get("yes", False)),
            cfg.approved_commands,
            allow,
            readonly=readonly,
            plan_mode=plan,
            deny=tuple(str(e) for e in (job.get("deny", []) or [])),
        )
        try:
            answer = run_agent(
                task,
                get_history(session),
                cfg,
                on_tool=None,
                on_token=None,
                approve=approve,
                auto_approve=bool(job.get("yes", False)),
                session=session,
                read_only=readonly,
                plan_mode=plan,
            )
        except Exception as e:
            err = str(e)[:500]
            save_message(session, "assistant", f"Error: {err}")
            finish_job(job_id, error=err)
            notify("sidekick job failed", f"{job_id}: {err[:200]}")
            return f"Error: {err}"
        save_message(session, "assistant", answer or "(empty)")
        finish_job(job_id, answer=answer or "")
        preview = (answer or "(empty)").strip().replace("\n", " ")[:200]
        notify("sidekick job done", f"{task[:80]} → {preview}")
        return answer or "(empty)"
    except Exception as e:
        try:
            finish_job(job_id, error=str(e)[:500])
        except Exception:
            pass
        return f"Error: {e}"
