#!/usr/bin/env python3
"""Keep the `codex` CLI that scripts run aligned with the running Codex daemon (user 2026-11-10).

Incident 2026-11-10: the Homebrew cask binary (/opt/homebrew/bin/codex, 0.166.0 then 0.167.0) hung at launch —
even `codex --version` — while the app-server daemon ran 0.167.1. The identical bytes ran fine from another path.
Summaries, labels and the identity judge all stalled on Codex timeouts for hours, which looked like a usage limit.

Every hour (launchd com.clawd.job-tracker.version-align) this:
  1. probes every installed codex binary (Homebrew cask versions + daemon releases) with `--version`, 20 s timeout;
  2. picks the working binary matching the running daemon's version (else the newest one that works);
  3. points ~/.local/bin/codex at it — pipeline scripts put ~/.local/bin first on PATH;
  4. writes job-tracker state `align.codex` (tag monitor, shown by `jt`): done = Homebrew copy works and matches
     the daemon · needs_review = routed around a hanging / older Homebrew copy · error = no working codex;
  5. sends Telegram only when the routing or status changes. Also checks the `claude` CLI answers.

  version_align.py [--dry-run]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
LINK = HOME / ".local" / "bin" / "codex"
STATE = HOME / "job_tracker" / "state" / "align.codex.json"
BREW = "/opt/homebrew/Caskroom/codex/*/bin/codex"
DAEMON = str(HOME / ".codex/packages/app-server-daemon/releases/*/bin/codex")
PROBE_TIMEOUT = 20
JAIL = HOME / "personal-network" / "codex_jail.sb"          # same jail hub/llm.py and profile_agy.py run codex in
WORKDIR = HOME / ".cache" / "network-codex-empty"


def vkey(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v or "0"))


def _version(cmd: list[str]) -> str | None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=PROBE_TIMEOUT, stdin=subprocess.DEVNULL)
    except (subprocess.TimeoutExpired, OSError):
        return None
    m = re.search(r"(\d+\.\d+\.\d+)", r.stdout)
    return m.group(1) if r.returncode == 0 and m else None


def probe(path: str) -> str | None:
    """Version string, or None if the binary hangs / fails — plain AND inside the hub/network sandbox-exec jail.

    Incident 2026-11-11: the Homebrew cask 0.167.1 answered `--version` plainly but hung inside the jail (its
    com.apple.quarantine attribute: the Gatekeeper check never completes under sandbox-exec), so every hub/session
    Codex call timed out after routing to it. The jail is how the pipelines run codex, so it is the probe that counts."""
    v = _version([path, "--version"])
    if v is None or not JAIL.exists():
        return v
    WORKDIR.mkdir(parents=True, exist_ok=True)
    return _version(["sandbox-exec", "-f", str(JAIL), "-D", f"HOME={HOME}", "-D", f"WORKDIR={WORKDIR}", path, "--version"])


def daemon_version() -> str | None:
    out = subprocess.run(["ps", "-axo", "command"], capture_output=True, text=True).stdout
    m = re.findall(r"app-server-daemon/releases/(\d+\.\d+\.\d+)-[^/]+/bin/codex app-server", out)
    return max(m, key=vkey) if m else None


def candidates() -> list[dict]:
    out = [{"path": p, "source": "brew"} for p in sorted(glob.glob(BREW))]
    out += [{"path": p, "source": "daemon"} for p in sorted(glob.glob(DAEMON))]
    for c in out:
        c["version"] = probe(c["path"])
    return out


def choose(cands: list[dict], daemon: str | None):
    """(picked candidate or None, job-tracker status, one-line reason)."""
    ok = [c for c in cands if c["version"]]
    if not ok:
        return None, "error", "no codex binary answers `--version` (all hang or fail)"
    want = daemon or max(c["version"] for c in ok)
    match = [c for c in ok if c["version"] == want] or ok
    match.sort(key=lambda c: (vkey(c["version"]), c["source"] == "brew"), reverse=True)
    pick = match[0]
    brew = [c for c in cands if c["source"] == "brew"]
    newest_brew = max(brew, key=lambda c: vkey(c["path"]), default=None)
    if newest_brew is None:
        return pick, "needs_review", "no Homebrew codex installed; using daemon copy"
    if not newest_brew["version"]:
        return pick, "needs_review", f"Homebrew codex hangs ({newest_brew['path']}); routed to {pick['source']} {pick['version']}"
    if daemon and newest_brew["version"] != daemon:
        return pick, "needs_review", (f"Homebrew codex {newest_brew['version']} ≠ daemon {daemon}; routed to "
                                      f"{pick['source']} {pick['version']} (fix: brew upgrade --cask codex)")
    return pick, "done", f"aligned: codex {pick['version']} (daemon {daemon or 'not running'})"


def _ver_of(path: str) -> str:
    m = re.search(r"/(\d+\.\d+\.\d+)", path)
    return m.group(1) if m else "?"


def claude_ok() -> str:
    try:
        r = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=PROBE_TIMEOUT)
        return r.stdout.strip().split()[0] if r.returncode == 0 and r.stdout.strip() else "error"
    except (subprocess.TimeoutExpired, OSError):
        return "hang"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    cands, daemon = candidates(), daemon_version()
    pick, status, why = choose(cands, daemon)
    cur = os.path.realpath(LINK) if LINK.exists() or LINK.is_symlink() else None
    changed = False
    if pick and cur != os.path.realpath(pick["path"]) and not a.dry_run:
        LINK.parent.mkdir(parents=True, exist_ok=True)
        tmp = LINK.with_name(".codex.tmp")
        if tmp.is_symlink() or tmp.exists():
            tmp.unlink()
        tmp.symlink_to(pick["path"])
        tmp.replace(LINK)
        changed = True
    cl = claude_ok()
    if cl in ("hang", "error") and status == "done":
        status, why = "needs_review", why + f"; claude CLI {cl}"
    details = [why, f"~/.local/bin/codex → {pick['path'] if pick else 'unchanged'}",
               "probed: " + ", ".join(f"{c['source']} {_ver_of(c['path'])}={c['version'] or 'HANG'}" for c in cands),
               f"claude CLI: {cl}"]
    prev = {}
    try:
        prev = json.loads(STATE.read_text())
    except (OSError, ValueError):
        pass
    now = datetime.now(timezone.utc).isoformat()
    state = {"id": "align.codex", "title": "starfighter · codex version alignment", "status": status,
             "agent": "version_align.py", "tags": ["monitor", "starfighter", "infra"], "details": details,
             "log_path": str(HOME / "job_tracker/logs/version_align.log"), "updated_at": now,
             "started_at": prev.get("started_at", now), "progress": {"current": None, "total": None, "unit": "checks"},
             "telemetry": {"host": "starfighter", "project": "infra", "daemon": daemon,
                           "pick": pick and pick["path"], "candidates": cands, "claude": cl}}
    print(json.dumps({"status": status, "why": why, "pick": pick and pick["path"], "changed": changed, "claude": cl}))
    if a.dry_run:
        return 0
    STATE.write_text(json.dumps(state, indent=2))
    if changed or prev.get("status") != status:
        subprocess.run(["notify-telegram", f"🔧 codex alignment: {status} — {why}" +
                        (f" (now → {pick['path']})" if changed and pick else "")], capture_output=True)
    return 0 if status != "error" else 1


if __name__ == "__main__":
    raise SystemExit(main())
