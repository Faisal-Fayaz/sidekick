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

JOBS_PATH = Path.home() / ".sidekick" / "jobs.json"


def load_jobs() -> dict:
    """All job records. {} on any failure. Never raises."""
    try:
        data = json.loads(JOBS_PATH.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_jobs(jobs: dict) -> None:
    JOBS_PATH.parent.mkdir(parents=True, exist_ok=True)
    JOBS_PATH.write_text(json.dumps(jobs))


def create_job(
    task: str,
    session: str,
    model: str,
    yes: bool,
    spend_cap: float = 0.0,
    allow: tuple[str, ...] | list[str] = (),
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
            "spend_cap": float(spend_cap or 0.0),
            "allow": [str(e) for e in (allow or [])],
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

        cfg = Config.load()
        cfg.model = str(job.get("model", "")) or cfg.model
        cfg.spend_cap_usd = cap
        allow = tuple(str(e) for e in (job.get("allow", []) or []))
        save_message(session, "user", task)
        approve = _make_approver(bool(job.get("yes", False)), cfg.approved_commands, allow)
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
