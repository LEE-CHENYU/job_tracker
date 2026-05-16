#!/usr/bin/env python3
"""Job tracker menubar — generic across all agents / projects.

Reads state files from ~/job_tracker/state/*.json and renders them. Auto-
refreshes every 30s.

Layout per job (top-level items in the dropdown):
  • Status emoji + title + progress %
  • Progress: <current> / <total> <unit>
  • ETA: ...
  • Rate: ...
  • detail lines (free-form, from agent)
  Submenus:
    • Purpose ›    — multi-line rich context (why the job is running)
    • Telemetry ›  — full dict
    • Meta ›       — started/updated/agent/pid/tags
    • Actions ›    — Open log / Cancel / Remove

Multiple jobs are separated by a divider line.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow running as `python ~/job_tracker/menubar.py` OR `python -m job_tracker.menubar`
HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

import rumps

STATE_DIR = HERE / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)
REFRESH_SEC = 30
# Plain ASCII title — renders reliably across dark/light/transparent menu bars.
# Emoji icons sometimes show as empty space on certain macOS configs.
DEFAULT_TITLE = "Jobs"

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
    # Order: running first, then paused, then done, then failed/error
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


def _open_log_cb(path):
    def cb(_):
        subprocess.run(["open", "-a", "Console", path])
    return cb


def _cancel_cb(cmd):
    def cb(_):
        subprocess.run(cmd)
    return cb


def _remove_cb(path):
    def cb(_):
        try:
            Path(path).unlink()
        except FileNotFoundError:
            pass
    return cb


class JobTrackerApp(rumps.App):
    def __init__(self):
        super().__init__(DEFAULT_TITLE, title=DEFAULT_TITLE, quit_button=None)
        rumps.Timer(self._auto_refresh, REFRESH_SEC).start()
        self._rebuild()

    def _rebuild(self):
        # Wipe current menu items
        for key in list(self.menu.keys()):
            del self.menu[key]

        jobs = _read_jobs()
        if not jobs:
            self.menu.add(rumps.MenuItem("(no jobs registered)"))
            self.menu.add(rumps.MenuItem(
                "Drop JSON files in ~/job_tracker/state/"
            ))
            self.menu.add(rumps.separator)
            self.menu.add(rumps.MenuItem("Refresh", callback=self._on_refresh))
            self.menu.add(rumps.MenuItem("Quit", callback=lambda _: rumps.quit_application()))
            self.title = DEFAULT_TITLE
            return

        for i, job in enumerate(jobs):
            self._add_job_inline(job)
            if i < len(jobs) - 1:
                self.menu.add(rumps.separator)

        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("Refresh", callback=self._on_refresh))
        self.menu.add(rumps.MenuItem("Quit", callback=lambda _: rumps.quit_application()))

        # Title bar: show the most-progressed running job's %
        running = [j for j in jobs if j.get("status") == "running"]
        if running:
            top = max(running, key=_progress_pct)
            self.title = f"{DEFAULT_TITLE} {_progress_pct(top):.0f}%"
        else:
            self.title = DEFAULT_TITLE

    def _add_job_inline(self, job: dict):
        title = job.get("title", job.get("id", "?"))
        status = job.get("status", "unknown")
        emoji = STATUS_EMOJI.get(status, "⚪️")
        pct = _progress_pct(job)

        # Header (non-interactive label)
        header = rumps.MenuItem(f"{emoji} {title}  {pct:.1f}%")
        self.menu.add(header)

        # Inline metrics
        prog = job.get("progress") or {}
        cur = prog.get("current")
        tot = prog.get("total")
        unit = prog.get("unit", "items")
        rate = prog.get("rate_per_sec")
        if cur is not None and tot:
            self.menu.add(rumps.MenuItem(f"  Progress: {cur:,} / {tot:,} {unit}"))
            remaining = max(0, tot - cur)
            self.menu.add(rumps.MenuItem(f"  ETA: {_human_eta(remaining, rate)}"))
        if rate is not None:
            rate_unit = prog.get("rate_unit") or f"{unit}/sec"
            self.menu.add(rumps.MenuItem(f"  Rate: {rate:.3f} {rate_unit}"))

        for d in (job.get("details") or []):
            self.menu.add(rumps.MenuItem(f"  {d}"))

        # Purpose / Telemetry / Meta / Actions go into submenus
        purpose = job.get("purpose")
        if purpose:
            sub = rumps.MenuItem("  Purpose ›")
            for line in str(purpose).split("\n"):
                line = line.strip()
                if line:
                    # Wrap long lines so the submenu doesn't grow absurdly wide
                    for chunk in _wrap(line, 80):
                        sub.add(rumps.MenuItem(chunk))
            self.menu.add(sub)

        telemetry = job.get("telemetry") or {}
        if telemetry:
            sub = rumps.MenuItem("  Telemetry ›")
            for k, v in telemetry.items():
                sub.add(rumps.MenuItem(f"{k}: {v}"))
            self.menu.add(sub)

        # Meta submenu
        meta_sub = rumps.MenuItem("  Meta ›")
        meta_sub.add(rumps.MenuItem(f"Started: {_format_age(_parse_iso(job.get('started_at')))}"))
        meta_sub.add(rumps.MenuItem(f"Updated: {_format_age(_parse_iso(job.get('updated_at')))}"))
        if job.get("agent"):
            meta_sub.add(rumps.MenuItem(f"Agent: {job['agent']}"))
        if job.get("pid"):
            meta_sub.add(rumps.MenuItem(f"PID: {job['pid']}"))
        if job.get("tags"):
            meta_sub.add(rumps.MenuItem(f"Tags: {', '.join(job['tags'])}"))
        self.menu.add(meta_sub)

        # Actions submenu
        actions_sub = rumps.MenuItem("  Actions ›")
        log_path = job.get("log_path")
        if log_path and Path(log_path).exists():
            actions_sub.add(rumps.MenuItem("Open log", callback=_open_log_cb(log_path)))
        if job.get("cancel_cmd"):
            actions_sub.add(rumps.MenuItem("Cancel job", callback=_cancel_cb(job["cancel_cmd"])))
        actions_sub.add(rumps.MenuItem("Remove from tracker",
                                         callback=_remove_cb(job.get("_path"))))
        self.menu.add(actions_sub)

    def _on_refresh(self, _):
        self._rebuild()

    def _auto_refresh(self, _timer):
        self._rebuild()


def _wrap(text: str, width: int) -> list[str]:
    """Crude word-wrap so a long purpose line doesn't blow out the submenu width."""
    out = []
    while len(text) > width:
        # Break on last space before the cutoff
        cut = text.rfind(" ", 0, width)
        if cut == -1:
            cut = width
        out.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        out.append(text)
    return out


def main() -> int:
    logging.basicConfig(level="INFO",
                        format="%(asctime)s %(levelname)s %(message)s")
    JobTrackerApp().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
