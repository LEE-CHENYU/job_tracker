#!/usr/bin/env python3
"""Compact job menu: one status/progress row per job, details on demand.

Each job owns a separate submenu, so common action names cannot collide across
jobs. Full purpose, telemetry and timestamps remain in the job's JSON snapshot.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

import rumps

STATE_DIR = HERE / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)
REFRESH_SEC = 30
DEFAULT_TITLE = "Jobs"
PURPOSE_WRAP_WIDTH = 70
DETAIL_WRAP_WIDTH = 90  # detail / telemetry lines (longer than purpose)

PROJECT_DOTS = {"screening": "🔴", "notebooklm": "🔵"}


def project_dot(job):
    """Project identity only; execution/workflow status never changes the color."""
    project = str(job.get("project") or "").lower()
    if not project:
        job_id = str(job.get("id") or "")
        title = str(job.get("title") or "").lower()
        if job_id.startswith("research_notebook") or job_id in {"research_audio_qc", "research_transcription"} or title.startswith("notebook"):
            project = "notebooklm"
        elif job_id.startswith(("research_screen", "research_specialist", "research_either_check", "research_intake", "research_previous_batch")) or title.startswith("screening"):
            project = "screening"
    return PROJECT_DOTS.get(project, "⚪")


from research_sync import visible_by_default, compact_title, job_heading, with_display_progress

VISIBLE_BY_DEFAULT = {"running", "queued", "blocked", "needs_review", "error"}


def _parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def _human_eta(remaining, rate_per_sec):
    if not rate_per_sec or rate_per_sec <= 0:
        return "Not available"
    sec = remaining / rate_per_sec
    if sec < 60:
        return f"{sec:.0f}s"
    if sec < 3600:
        return f"{sec/60:.1f}m"
    if sec < 86400:
        return f"{sec/3600:.1f}h"
    return f"{sec/86400:.1f}d"


def _format_age(ts):
    if ts is None:
        return "Not available"
    delta_sec = (datetime.now(tz=timezone.utc) - ts).total_seconds()
    if delta_sec < 60:
        return f"{delta_sec:.0f}s ago"
    if delta_sec < 3600:
        return f"{delta_sec/60:.0f}m ago"
    if delta_sec < 86400:
        return f"{delta_sec/3600:.1f}h ago"
    return f"{delta_sec/86400:.1f}d ago"


def _wrap(text, width):
    """Word-wrap; for unbreakable strings (e.g. URLs), also accept
    breaks at /, ?, &, = before hard-cutting at width."""
    out = []
    for paragraph in str(text).split("\n"):
        line = paragraph.strip()
        if not line:
            continue
        while len(line) > width:
            # 1) Try a regular space break first
            cut = line.rfind(" ", 0, width)
            if cut == -1 or cut < width // 2:
                # 2) Fall back to URL-friendly break chars (keeps long
                #    URLs readable instead of mid-character cuts)
                best = -1
                for ch in ("/", "?", "&", "=", ":", ",", ";"):
                    pos = line.rfind(ch, 0, width)
                    if pos > best:
                        best = pos
                if best > width // 2:
                    cut = best + 1  # break after the delimiter
                elif cut == -1:
                    # 3) Hard-cut at width
                    cut = width
            out.append(line[:cut].rstrip())
            line = line[cut:].lstrip()
        if line:
            out.append(line)
    return out


def _emit_wrapped(items, text, first_prefix="  ", cont_prefix="    ",
                   width=DETAIL_WRAP_WIDTH):
    """Append `text` to `items` as one or more MenuItems with word-wrap.
    First chunk uses `first_prefix`, subsequent use `cont_prefix`."""
    chunks = _wrap(text, width)
    for i, c in enumerate(chunks):
        prefix = first_prefix if i == 0 else cont_prefix
        items.append(rumps.MenuItem(f"{prefix}{c}"))


def _read_jobs():
    out = []
    for f in sorted(STATE_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text())
            data["_path"] = str(f)
            out.append(with_display_progress(data))
        except Exception as exc:
            out.append({
                "id": f.stem, "title": f.stem, "status": "error",
                "details": [f"unreadable: {exc}"], "_path": str(f),
            })
    order = {"error": 0, "blocked": 1, "running": 2, "needs_review": 3, "queued": 4, "paused": 5, "done": 6, "failed": 7}
    out.sort(key=lambda d: (order.get(d.get("status", ""), 5), d.get("title", "")))
    return out


def _progress_pct(job):
    prog = job.get("progress") or {}
    cur = prog.get("current")
    tot = prog.get("total")
    if cur is None or not tot or tot <= 0:
        return 0.0
    return min(100.0, 100.0 * cur / tot)


# Module-level callback factories so closures don't leak across refreshes.
def _open_log_cb(path):
    def cb(_):
        subprocess.run(["open", "-a", "Console", path])
    return cb


def _open_details_cb(path):
    def cb(_):
        subprocess.run(["open", "-a", "TextEdit", path])
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
        self.show_all = False
        self._refresh_timer = rumps.Timer(self._auto_refresh, REFRESH_SEC)
        self._refresh_timer.start()
        self._rebuild()

    def _rebuild(self):
        items = self._build_items()
        # Assigning a list to .menu atomically replaces the underlying menu —
        # avoids ghost-item accumulation that del-by-title causes.
        self.menu.clear()
        for it in items:
            self.menu.add(it)

        jobs = _read_jobs()
        self.title = compact_title(jobs)

    def _build_items(self):
        all_jobs = _read_jobs()
        hidden_n = 0
        visible_jobs = []
        for j in all_jobs:
            st = j.get("status", "")
            if not self.show_all and not visible_by_default(st):
                hidden_n += 1
            else:
                visible_jobs.append(j)

        items = []
        if not visible_jobs:
            items.append(rumps.MenuItem("(no active jobs)"))
        else:
            sections = {}
            for job in visible_jobs:
                activity = (job.get("telemetry") or {}).get("execution_status")
                key = activity if activity in {"working", "waiting", "blocked", "stalled", "paused"} else job.get("status", "unknown")
                key = {"running": "working", "done": "completed", "error": "failed"}.get(key, key)
                sections.setdefault(key, []).append(job)
            order = ["stalled", "failed", "blocked", "working", "waiting", "needs_review", "queued", "paused", "completed", "stopped", "stale"]
            for key in sorted(sections, key=lambda k: (order.index(k) if k in order else len(order), k)):
                jobs = sections[key]
                items.append(rumps.MenuItem(f"{key.replace('_', ' ').capitalize()} ({len(jobs)})"))
                for i, job in enumerate(jobs):
                    items.extend(self._render_job(job, i))

        stamp = datetime.now().strftime("%H:%M:%S")
        items.append(rumps.MenuItem(f"Menu refreshed {stamp} · auto {REFRESH_SEC}s · data every 60s"))
        toggle_label = (f"Show all ({hidden_n} hidden)" if not self.show_all
                        else "Show current work")
        items.append(rumps.MenuItem(toggle_label, callback=self._on_toggle_filter))
        items.append(rumps.MenuItem("Refresh", callback=self._on_refresh))
        items.append(rumps.MenuItem("Quit", callback=lambda _: rumps.quit_application()))
        return items

    def _render_job(self, job, idx):
        """One root row; short context and actions live in its own submenu."""
        status = job.get("status", "unknown")
        row = rumps.MenuItem(f"{project_dot(job)} {job_heading(job)}")
        telemetry = job.get("telemetry") or {}

        # Only outstanding counts; completed progress is already in the heading.
        outstanding = []
        for key, label in (("needs_attention", "need attention"),
                           ("pending", "pending"), ("no_result", "missing results")):
            count = telemetry.get(key)
            if isinstance(count, (int, float)) and count > 0:
                outstanding.append(f"{count:g} {label}")
        if outstanding:
            row.add(rumps.MenuItem(" · ".join(outstanding)))

        # One useful explanation, not every machine field and repeated count.
        candidates = [job.get("summary", "")] + (job.get("details") or [])
        for detail in candidates:
            text = str(detail).strip()
            if not text or re.fullmatch(r"[\w_ ]+:\s*\d+(?:\s+companies)?", text):
                continue
            if text.startswith(("http://", "https://", "/")):
                continue
            if text == status or text == status.replace("_", " "):
                continue
            row.add(rumps.MenuItem(text if len(text) <= 100 else text[:97] + "…"))
            break

        prog = job.get("progress") or {}
        rate = prog.get("rate_per_sec")
        current, total = prog.get("current"), prog.get("total")
        if status == "running" and rate and rate > 0 and current is not None and total:
            row.add(rumps.MenuItem(f"ETA: {_human_eta(max(0, total-current), rate)}"))
        if telemetry.get("execution_detail"):
            row.add(rumps.MenuItem(telemetry["execution_detail"][:150]))
        elif telemetry.get("execution_status") == "not_verified_running":
            row.add(rumps.MenuItem("Saved artifacts; worker activity unverified"))

        state_path = job.get("_path")
        if state_path and Path(state_path).exists():
            row.add(rumps.MenuItem("Open details", callback=_open_details_cb(state_path)))
        log_path = job.get("log_path")
        if log_path and Path(log_path).exists():
            row.add(rumps.MenuItem("Open source / log", callback=_open_log_cb(log_path)))
        if job.get("cancel_cmd"):
            row.add(rumps.MenuItem("Cancel job", callback=_cancel_cb(job["cancel_cmd"])))
        if state_path:
            row.add(rumps.MenuItem("Remove from tracker", callback=_remove_cb(state_path)))
        return [row]

    def _on_toggle_filter(self, _):
        self.show_all = not self.show_all
        self._rebuild()

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
