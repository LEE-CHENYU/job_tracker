"""Job tracker — minimal Python API for registering background jobs.

Agents (Claude, Codex, scripts) call `register_job()` when they kick off a
long-running task, then `update_job()` as it makes progress, and
`mark_done()` / `mark_failed()` at the end. The menubar app polls the
state directory and renders these.

State files live at ``~/job_tracker/state/{id}.json`` (one per job). The
menubar app reads all *.json files there and shows them. Any agent —
even a shell one-liner that just writes JSON — can register a job.

Schema (all fields except `id` and `title` are optional):
  id          str
  title       str           — human-readable
  purpose     str           — WHY this is running; rich multi-line context
  agent       str           — who/what registered this (e.g. "claude-opus-4-7")
  started_at  ISO timestamp
  updated_at  ISO timestamp (auto-set on update)
  status      "running"|"paused"|"done"|"failed"|"error"
  pid         int
  progress    {current, total, unit, rate_per_sec, rate_unit}
  details     [str, ...]    — extra display lines
  log_path    str           — for "Open log" action
  cancel_cmd  [str, ...]    — argv to cancel the job
  tags        [str, ...]
  telemetry   {arbitrary k/v} — extra structured metrics
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

STATE_DIR = Path.home() / "job_tracker" / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _state_path(job_id: str) -> Path:
    safe = job_id.replace("/", "_").replace(" ", "_")
    return STATE_DIR / f"{safe}.json"


def register_job(
    id: str,
    title: str,
    *,
    purpose: Optional[str] = None,
    agent: Optional[str] = None,
    pid: Optional[int] = None,
    log_path: Optional[str] = None,
    cancel_cmd: Optional[list[str]] = None,
    tags: Optional[list[str]] = None,
    progress_total: Optional[int] = None,
    progress_unit: str = "items",
    details: Optional[list[str]] = None,
) -> Path:
    """Register a new job (or overwrite an existing one with same id)."""
    p = _state_path(id)
    data = {
        "id": id,
        "title": title,
        "status": "running",
        "purpose": purpose,
        "agent": agent,
        "pid": pid,
        "log_path": log_path,
        "cancel_cmd": cancel_cmd,
        "tags": tags or [],
        "details": details or [],
        "started_at": _now_iso(),
        "updated_at": _now_iso(),
        "progress": {
            "current": 0,
            "total": progress_total,
            "unit": progress_unit,
            "rate_per_sec": None,
            "rate_unit": f"{progress_unit}/sec",
        },
        "telemetry": {},
    }
    p.write_text(json.dumps(data, indent=2, default=str))
    return p


def update_job(
    id: str,
    *,
    current: Optional[int] = None,
    total: Optional[int] = None,
    rate_per_sec: Optional[float] = None,
    status: Optional[str] = None,
    details: Optional[list[str]] = None,
    telemetry: Optional[dict[str, Any]] = None,
    pid: Optional[int] = None,
) -> Path:
    """Update an existing job. Fields not provided are left unchanged."""
    p = _state_path(id)
    if not p.exists():
        raise FileNotFoundError(f"No job registered for id={id!r}; call register_job first")
    data = json.loads(p.read_text())
    if status is not None:
        data["status"] = status
    if details is not None:
        data["details"] = details
    if telemetry is not None:
        data.setdefault("telemetry", {}).update(telemetry)
    if pid is not None:
        data["pid"] = pid
    prog = data.setdefault("progress", {})
    if current is not None:
        prog["current"] = current
    if total is not None:
        prog["total"] = total
    if rate_per_sec is not None:
        prog["rate_per_sec"] = rate_per_sec
    data["updated_at"] = _now_iso()
    p.write_text(json.dumps(data, indent=2, default=str))
    return p


def mark_done(id: str, *, details: Optional[list[str]] = None) -> None:
    update_job(id, status="done", details=details)


def mark_failed(id: str, *, reason: str) -> None:
    update_job(id, status="failed", details=[f"failed: {reason}"])


def unregister_job(id: str) -> bool:
    p = _state_path(id)
    if p.exists():
        p.unlink()
        return True
    return False


def list_jobs() -> list[dict]:
    out = []
    for f in sorted(STATE_DIR.glob("*.json")):
        try:
            out.append(json.loads(f.read_text()))
        except Exception:
            continue
    return out
