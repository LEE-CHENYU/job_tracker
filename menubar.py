#!/usr/bin/env python3
"""Job tracker menubar — generic across all agents / projects.

Reads state files from ~/job_tracker/state/*.json and renders them. Auto-
refreshes every 30s. Click "Refresh" to force update.

Each registered job shows:
  - status emoji + title + progress %
  - submenu with:
      * purpose (rich multi-line context — why this is running)
      * progress detail (current/total, rate, ETA)
      * arbitrary details / telemetry from the agent
      * actions: Open log, Cancel (if cancel_cmd set), Remove

Job state schema documented in job_tracker/__init__.py.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow running as either `python ~/job_tracker/menubar.py` or `python -m job_tracker.menubar`
HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

import rumps

STATE_DIR = HERE / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)
REFRESH_SEC = 30
ICON_DEFAULT = "🛠"

STATUS_EMOJI = {
    "running": "🟢", "paused": "🟡",
    "done": "✅", "failed": "🔴", "error": "🔴",
    "stopped": "⚪️",
}


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def _human_eta(remaining: float, rate_per_sec: float | None) -> str:
    if not rate_per_sec or rate_per_sec <= 0:
        return "—"
    sec = remaining / rate_per_sec
    if sec < 60:
        return f"{sec:.0f}s"
    if sec < 3600:
        return f"{sec/60:.1f}m"
    if sec < 86400:
        return f"{sec/3600:.1f}h"
    return f"{sec/86400:.1f}d"


def _format_age(ts: datetime | None) -> str:
    if ts is None:
        return "—"
    delta_sec = (datetime.now(tz=timezone.utc) - ts).total_seconds()
    if delta_sec < 60:
        return f"{delta_sec:.0f}s ago"
    if delta_sec < 3600:
        return f"{delta_sec/60:.0f}m ago"
    if delta_sec < 86400:
        return f"{delta_sec/3600:.1f}h ago"
    return f"{delta_sec/86400:.1f}d ago"


def _read_jobs() -> list[dict]:
    out = []
    for f in sorted(STATE_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text())
            data["_path"] = str(f)
            out.append(data)
        except Exception as exc:
            out.append({
                "id": f.stem, "title": f.stem, "status": "error",
                "details": [f"unreadable: {exc}"], "_path": str(f),
            })
    # Order: running first, then paused, then done, then failed
    order = {"running": 0, "paused": 1, "done": 2, "failed": 3, "error": 4}
    out.sort(key=lambda d: (order.get(d.get("status", ""), 5), d.get("title", "")))
    return out


def _progress_pct(job: dict) -> float:
    prog = job.get("progress") or {}
    cur = prog.get("current")
    tot = prog.get("total")
    if cur is None or not tot or tot <= 0:
        return 0.0
    return min(100.0, 100.0 * cur / tot)


def _build_job_section(job: dict) -> rumps.MenuItem:
    title = job.get("title", job.get("id", "?"))
    status = job.get("status", "unknown")
    emoji = STATUS_EMOJI.get(status, "⚪️")
    pct = _progress_pct(job)
    header = f"{emoji} {title}  {pct:.1f}%"
    section = rumps.MenuItem(header)

    purpose = job.get("purpose")
    if purpose:
        section.add(rumps.MenuItem("— Purpose —"))
        for line in str(purpose).split("\n"):
            line = line.strip()
            if line:
                section.add(rumps.MenuItem("  " + line))
        section.add(rumps.separator)

    prog = job.get("progress") or {}
    cur = prog.get("current")
    tot = prog.get("total")
    unit = prog.get("unit", "items")
    rate = prog.get("rate_per_sec")
    if cur is not None and tot:
        section.add(rumps.MenuItem(f"Progress: {cur:,} / {tot:,} {unit}"))
        remaining = max(0, tot - cur)
        section.add(rumps.MenuItem(f"ETA: {_human_eta(remaining, rate)}"))
    if rate is not None:
        section.add(rumps.MenuItem(f"Rate: {rate:.3f} {prog.get('rate_unit', unit + '/sec')}"))

    for d in job.get("details", []) or []:
        section.add(rumps.MenuItem(str(d)))

    telemetry = job.get("telemetry") or {}
    if telemetry:
        section.add(rumps.separator)
        section.add(rumps.MenuItem("— Telemetry —"))
        for k, v in telemetry.items():
            section.add(rumps.MenuItem(f"  {k}: {v}"))

    section.add(rumps.separator)
    section.add(rumps.MenuItem(f"Started: {_format_age(_parse_iso(job.get('started_at')))}"))
    section.add(rumps.MenuItem(f"Updated: {_format_age(_parse_iso(job.get('updated_at')))}"))
    if job.get("agent"):
        section.add(rumps.MenuItem(f"Agent: {job['agent']}"))
    if job.get("pid"):
        section.add(rumps.MenuItem(f"PID: {job['pid']}"))
    if job.get("tags"):
        section.add(rumps.MenuItem(f"Tags: {', '.join(job['tags'])}"))

    # Actions
    section.add(rumps.separator)
    log_path = job.get("log_path")
    if log_path and Path(log_path).exists():
        section.add(rumps.MenuItem(
            "Open log",
            callback=lambda _, p=log_path: subprocess.run(["open", "-a", "Console", p]),
        ))
    cancel_cmd = job.get("cancel_cmd")
    if cancel_cmd:
        section.add(rumps.MenuItem(
            "Cancel job",
            callback=lambda _, cmd=cancel_cmd: subprocess.run(cmd),
        ))
    section.add(rumps.MenuItem(
        "Remove from tracker",
        callback=lambda _, p=job.get("_path"): Path(p).unlink(missing_ok=True),
    ))
    return section


class JobTrackerApp(rumps.App):
    def __init__(self):
        super().__init__(ICON_DEFAULT, quit_button=None)
        rumps.Timer(self._auto_refresh, REFRESH_SEC).start()
        self._rebuild()

    def _rebuild(self):
        for key in list(self.menu.keys()):
            del self.menu[key]
        jobs = _read_jobs()
        if not jobs:
            self.menu.add(rumps.MenuItem("(no jobs registered)"))
            self.menu.add(rumps.MenuItem(
                "Help: write state file to ~/job_tracker/state/",
                callback=None,
            ))
        else:
            for job in jobs:
                self.menu.add(_build_job_section(job))
                self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("Refresh", callback=self._on_refresh))
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("Quit", callback=lambda _: rumps.quit_application()))

        # Title bar: show the most-progressed running job's %
        running = [j for j in jobs if j.get("status") == "running"]
        if running:
            top = max(running, key=_progress_pct)
            self.title = f"{ICON_DEFAULT} {_progress_pct(top):.0f}%"
        else:
            self.title = ICON_DEFAULT

    def _on_refresh(self, _):
        self._rebuild()

    def _auto_refresh(self, _timer):
        self._rebuild()


def main() -> int:
    logging.basicConfig(level="INFO",
                        format="%(asctime)s %(levelname)s %(message)s")
    JobTrackerApp().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
