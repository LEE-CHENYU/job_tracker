#!/usr/bin/env python3
"""Job tracker menubar — flat, no-submenu rendering.

Why flat (no submenus): rumps keys items by title, so repeating
"  Purpose ›" across jobs causes collisions and ghost items accumulate
across refreshes. Inlining everything keeps titles naturally unique
(they contain the job's specific values) and gives the user the full
context on first click without any hovering.

Layout per job:
  • Status emoji + title + %
  • Purpose (word-wrapped, indented)
  • Progress / ETA / Rate
  • Inline detail lines + telemetry
  • Meta (started/updated/agent/pid/tags)
  • Action items (Open log / Cancel / Remove) — clickable, unique titles
Each job separated by a divider.

By default only running jobs are shown; a top-level toggle reveals all.
"""
from __future__ import annotations

import json
import logging
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

STATUS_EMOJI = {
    "running": "🟢", "paused": "🟡",
    "done": "✅", "failed": "🔴", "error": "🔴",
    "stopped": "⚪️",
}

VISIBLE_BY_DEFAULT = {"running"}


def _parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def _human_eta(remaining, rate_per_sec):
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


def _format_age(ts):
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


def _wrap(text, width):
    out = []
    for paragraph in str(text).split("\n"):
        line = paragraph.strip()
        if not line:
            continue
        while len(line) > width:
            cut = line.rfind(" ", 0, width)
            if cut == -1:
                cut = width
            out.append(line[:cut].rstrip())
            line = line[cut:].lstrip()
        if line:
            out.append(line)
    return out


def _read_jobs():
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
    order = {"running": 0, "paused": 1, "done": 2, "failed": 3, "error": 4}
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
        rumps.Timer(self._auto_refresh, REFRESH_SEC).start()
        self._rebuild()

    def _rebuild(self):
        items = self._build_items()
        # Assigning a list to .menu atomically replaces the underlying menu —
        # avoids ghost-item accumulation that del-by-title causes.
        self.menu.clear()
        for it in items:
            self.menu.add(it)

        jobs = _read_jobs()
        running = [j for j in jobs if j.get("status") == "running"]
        if running:
            top = max(running, key=_progress_pct)
            self.title = f"{DEFAULT_TITLE} {_progress_pct(top):.0f}%"
        else:
            self.title = DEFAULT_TITLE

    def _build_items(self):
        all_jobs = _read_jobs()
        hidden_n = 0
        visible_jobs = []
        for j in all_jobs:
            st = j.get("status", "")
            if not self.show_all and st not in VISIBLE_BY_DEFAULT:
                hidden_n += 1
            else:
                visible_jobs.append(j)

        items = []
        if not visible_jobs:
            items.append(rumps.MenuItem("(no active jobs)"))
        else:
            for i, j in enumerate(visible_jobs):
                items.extend(self._render_job(j, i))
                if i < len(visible_jobs) - 1:
                    items.append(rumps.separator)

        items.append(rumps.separator)
        toggle_label = (f"Show all ({hidden_n} hidden)" if not self.show_all
                        else "Show only running")
        items.append(rumps.MenuItem(toggle_label, callback=self._on_toggle_filter))
        items.append(rumps.MenuItem("Refresh", callback=self._on_refresh))
        items.append(rumps.MenuItem("Quit", callback=lambda _: rumps.quit_application()))
        return items

    def _render_job(self, job, idx):
        """Render one job as a flat list of MenuItems. All titles are made
        unique by the job's id/values so they don't collide in rumps."""
        items = []
        title = job.get("title", job.get("id", "?"))
        status = job.get("status", "unknown")
        emoji = STATUS_EMOJI.get(status, "⚪️")
        pct = _progress_pct(job)
        jid = job.get("id", f"job{idx}")

        # Header (non-interactive label). Title is naturally unique per job
        # because it contains the job's title + pct.
        items.append(rumps.MenuItem(f"{emoji} {title}  {pct:.1f}%"))

        # Purpose (word-wrapped). Each wrap chunk gets prefixed with the
        # job id (invisible-ish) so chunks don't collide across jobs.
        purpose = job.get("purpose")
        if purpose:
            for chunk in _wrap(purpose, PURPOSE_WRAP_WIDTH):
                items.append(rumps.MenuItem(f"  · {chunk}"))

        # Progress / ETA / Rate
        prog = job.get("progress") or {}
        cur = prog.get("current")
        tot = prog.get("total")
        unit = prog.get("unit", "items")
        rate = prog.get("rate_per_sec")
        if cur is not None and tot:
            items.append(rumps.MenuItem(f"  Progress: {cur:,} / {tot:,} {unit}"))
            items.append(rumps.MenuItem(
                f"  ETA: {_human_eta(max(0, tot - cur), rate)}"
            ))
        if rate is not None:
            rate_unit = prog.get("rate_unit") or f"{unit}/sec"
            items.append(rumps.MenuItem(f"  Rate: {rate:.3f} {rate_unit}"))

        # Free-form detail lines from the agent
        for d in (job.get("details") or []):
            items.append(rumps.MenuItem(f"  {d}"))

        # Telemetry (compact inline)
        telemetry = job.get("telemetry") or {}
        if telemetry:
            for k, v in telemetry.items():
                if v is None or v == "":
                    continue
                items.append(rumps.MenuItem(f"  • {k}: {v}"))

        # Meta — single line if we can fit it
        meta_bits = []
        started = _parse_iso(job.get("started_at"))
        updated = _parse_iso(job.get("updated_at"))
        if started:
            meta_bits.append(f"started {_format_age(started)}")
        if updated:
            meta_bits.append(f"updated {_format_age(updated)}")
        if job.get("agent"):
            meta_bits.append(f"agent={job['agent']}")
        if job.get("pid"):
            meta_bits.append(f"pid={job['pid']}")
        if meta_bits:
            items.append(rumps.MenuItem(f"  ◇ {' · '.join(meta_bits)}"))

        # Actions (clickable). Unique titles via job id suffix.
        log_path = job.get("log_path")
        if log_path and Path(log_path).exists():
            items.append(rumps.MenuItem(
                f"  ↗ Open log — {title}",
                callback=_open_log_cb(log_path),
            ))
        if job.get("cancel_cmd"):
            items.append(rumps.MenuItem(
                f"  ✕ Cancel — {title}",
                callback=_cancel_cb(job["cancel_cmd"]),
            ))
        items.append(rumps.MenuItem(
            f"  − Remove from tracker — {title}",
            callback=_remove_cb(job.get("_path")),
        ))
        return items

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
