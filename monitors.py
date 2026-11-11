#!/usr/bin/env python3
"""Central monitor registry: every scheduled job / service on every machine, in the job tracker.

Each refresh (launchd com.clawd.job-tracker.monitors, every 5 min) collects:
  * Starfighter launchd agents (~/Library/LaunchAgents, vendor agents skipped)
  * MacBook launchd agents      (over ssh, same collector script sent on stdin)
  * Hekademia (PS5) Slurm queue + failed systemd units (over ssh)
and writes one job-tracker state file per monitor: state/mon.<host>.<name>.json, tag "monitor".
Health: running (executing / service up) · done (last run ok) · error (non-zero exit, overdue,
service down) · paused (not loaded / disabled) · queued/blocked (Slurm).

Read it anywhere:
  jt                 # monitors that need attention + every machine's summary + active tracked jobs
  jt all             # every monitor, healthy ones too
  jt show <id|name>  # one monitor/job in full;  jt log <id|name> [-n 40]  # tail its log
  jt refresh         # collect now
From the MacBook or PS5: `jt` is a wrapper for `ssh <starfighter> ~/.local/bin/jt`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

STATE_DIR = Path.home() / "job_tracker" / "state"
# ssh targets live outside the (public) repo: ~/.config/jt/hosts.json {"macbook": "user@ip", "ps5": "root@ip"}
try:
    _CFG = json.loads((Path.home() / ".config" / "jt" / "hosts.json").read_text())
except (OSError, ValueError):
    _CFG = {}
HOSTS = {"starfighter": None, **({"macbook": _CFG["macbook"]} if _CFG.get("macbook") else {})}
PS5 = _CFG.get("ps5")
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", "-o", "ServerAliveInterval=5"]

# Logs a job writes itself (launchd stdout is /dev/null or unused); newest mtime = last run.
LOG_OVERRIDES = {
    "com.lichenyu.network-wechat-sync": ["~/personal-network/logs/wechat_sync.log"],
    "com.lichenyu.network-icloud-backup": ["~/personal-network/logs/icloud_backup.log"],
    "com.lichenyu.network-pipeline-status": ["~/personal-network/logs/pipeline_status.json"],
    "com.lichenyu.linkedin-export-import": ["~/personal-network/logs/linkedin_sync.log"],
    "com.lichenyu.linkedin-crawl": ["~/personal-network/logs/linkedin_crawl.log"],
    "com.lichenyu.linkedin-export-auto": ["~/personal-network/logs/linkedin_export.log"],
    "com.lichenyu.conversation-hub": ["~/conversation-hub/logs/run.log"],
    "com.lichenyu.laya-cycle": ["~/conversation-hub/logs/laya_cycle.log"],
    "com.clawd.job-tracker.research-sync": ["~/job_tracker/state/research_sync_screening.json"],
}

PROJECTS = [  # (label regex, project)
    (r"\.network-|\.linkedin-", "personal-network"),
    (r"\.conversation-hub|\.laya-", "conversation-hub"),
    (r"\.hekademia-", "hekademia"),
    (r"\.planner-|\.personal-planning", "personal-planning"),
    (r"^com\.clawd\.job-tracker", "job-tracker"),
    (r"^com\.clawd\.", "clawd"),
]

# Runs on each Mac (locally via exec, remotely on ssh stdin). Prints JSON; stdlib only.
COLLECT_LAUNCHD = r'''
import glob, json, os, plistlib, re, subprocess
SKIP = ("com.apple.", "com.google.", "com.grammarly.", "com.samsung.", "com.valvesoftware.",
        "com.microsoft.", "com.adobe.", "com.dropbox.", "com.docker.", "us.zoom.", "homebrew.")
OVR = OVERRIDES
listed = {}
for line in subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout.splitlines()[1:]:
    parts = line.split("\t")
    if len(parts) == 3:
        pid, ex, label = parts
        listed[label] = (None if pid == "-" else int(pid), None if ex == "-" else int(ex))
disabled = set(re.findall(r'"([^"]+)" => (?:true|disabled)',
               subprocess.run(["launchctl", "print-disabled", "gui/%d" % os.getuid()],
                              capture_output=True, text=True).stdout))
out = []
for p in sorted(glob.glob(os.path.expanduser("~/Library/LaunchAgents/*.plist"))):
    try:
        pl = plistlib.load(open(p, "rb"))
    except Exception:
        continue
    label = pl.get("Label") or os.path.basename(p)[:-6]
    if label.startswith(SKIP):
        continue
    logs = [pl.get("StandardOutPath"), pl.get("StandardErrorPath")] + OVR.get(label, [])
    logs = [os.path.expanduser(l) for l in logs if l and l != "/dev/null"]
    mt = [os.path.getmtime(l) for l in logs if os.path.exists(l)]
    pid, ex = listed.get(label, (None, None))
    args = pl.get("ProgramArguments") or [pl.get("Program", "")]
    out.append({"label": label, "plist": p, "loaded": label in listed, "pid": pid, "last_exit": ex,
                "disabled": label in disabled, "log": (OVR.get(label) and os.path.expanduser(OVR[label][0])) or (logs[0] if logs else None),
                "log_mtime": max(mt) if mt else None, "command": " ".join(str(a) for a in args)[:300],
                "plist_keys": {k: pl[k] for k in ("StartInterval", "StartCalendarInterval", "KeepAlive", "RunAtLoad") if k in pl}})
print(json.dumps(out))
'''


# ---------------------------------------------------------------- pure helpers (tested)
def expected_interval(keys: dict) -> int | None:
    """Seconds between scheduled runs, or None for a service / run-at-load-only job."""
    if keys.get("StartInterval"):
        return int(keys["StartInterval"])
    cal = keys.get("StartCalendarInterval")
    if cal:
        entries = cal if isinstance(cal, list) else [cal]
        per = []
        for e in entries:
            per.append(7 * 86400 if "Weekday" in e or "Day" in e else 86400 if "Hour" in e
                       else 3600 if "Minute" in e else 60)
        base = min(per)
        n_same = sum(1 for x in per if x == base)
        return max(base // n_same, 60) if n_same > 1 else base
    return None


def project_of(label: str) -> str:
    for rx, proj in PROJECTS:
        if re.search(rx, label):
            return proj
    parts = label.split(".")
    return parts[1] if len(parts) > 2 and parts[0] == "com" else parts[0]


def classify(j: dict, now: float) -> tuple[str, str]:
    """(job-tracker status, one-line reason)."""
    if j.get("disabled"):
        return "paused", "disabled in launchd"
    if not j.get("loaded"):
        return "paused", "not loaded in launchd"
    if j.get("pid"):
        return "running", f"running (pid {j['pid']})"
    if j.get("service"):
        return "error", f"service not running (last exit {j.get('last_exit')})"
    ex = j.get("last_exit")
    if ex not in (None, 0):
        return "error", f"last run failed: exit {ex}"
    iv, mt = j.get("interval"), j.get("log_mtime")
    if iv and mt and now - mt > iv * 2.5 + 900:
        if j.get("log_every_run"):
            return "error", f"no run for {_ago(now - mt)} (expected every {_ago(iv)})"
        return "done", f"ok (exit 0; log quiet {_ago(now - mt)})"
    return "done", "ok"


def parse_squeue(text: str) -> list[dict]:
    keys = ["id", "name", "user", "state", "elapsed", "limit", "reason"]
    return [dict(zip(keys, l.split("|"))) for l in text.splitlines() if l.count("|") == 6]


def slurm_status(r: dict) -> str:
    if r["state"] == "RUNNING":
        return "running"
    if r["state"] == "PENDING":
        return "blocked" if "Never" in r["reason"] or "Held" in r["reason"] else "queued"
    return "error" if r["state"] in ("FAILED", "TIMEOUT", "NODE_FAIL", "OUT_OF_MEMORY") else "done"


def _ago(sec: float) -> str:
    sec = int(sec)
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if sec >= n:
            return f"{sec // n}{unit}"
    return f"{sec}s"


# ---------------------------------------------------------------- collection
def _collector_src() -> str:
    return COLLECT_LAUNCHD.replace("OVERRIDES", repr(LOG_OVERRIDES))


def collect_launchd(host: str | None) -> list[dict]:
    src = _collector_src()
    if host is None:
        r = subprocess.run([sys.executable, "-"], input=src, capture_output=True, text=True, timeout=60)
    else:
        r = subprocess.run(SSH + [host, "python3 -"], input=src, capture_output=True, text=True, timeout=90)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[-300:] or f"exit {r.returncode}")
    return json.loads(r.stdout)


def collect_ps5() -> dict:
    if not PS5:
        raise RuntimeError("no ps5 entry in ~/.config/jt/hosts.json")
    cmd = ("squeue -h -o '%i|%j|%u|%T|%M|%l|%R'; echo '@@'; "
           "systemctl --failed --no-legend --plain 2>/dev/null | awk '{print $1}'")
    r = subprocess.run(SSH + [PS5, cmd], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[-300:] or f"exit {r.returncode}")
    sq, _, failed = r.stdout.partition("@@")
    return {"slurm": parse_squeue(sq), "failed_units": [u for u in failed.split() if u and not u.endswith(".scope")]}


def _write(state: dict) -> None:
    p = STATE_DIR / f"{state['id']}.json"
    old = {}
    if p.exists():
        try:
            old = json.loads(p.read_text())
        except Exception:
            pass
    state["started_at"] = old.get("started_at") or state["updated_at"]
    if old.get("status") != state["status"]:
        state["telemetry"]["status_since"] = state["updated_at"]
    else:
        state["telemetry"]["status_since"] = (old.get("telemetry") or {}).get("status_since", state["updated_at"])
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str))
    tmp.replace(p)


def _state(id_, title, status, details, *, host, project, log=None, telemetry=None, purpose=None):
    return {"id": id_, "title": title, "status": status, "purpose": purpose, "agent": "monitors.py",
            "pid": (telemetry or {}).get("pid"), "log_path": log, "cancel_cmd": None,
            "tags": ["monitor", host, project], "details": details,
            "updated_at": datetime.now(tz=timezone.utc).isoformat(),
            "progress": {"current": None, "total": None, "unit": "runs"},
            "telemetry": dict(telemetry or {}, host=host, project=project)}


def refresh() -> dict:
    now = time.time()
    summary, seen, ok_hosts = {}, set(), set()
    for host, target in HOSTS.items():
        try:
            jobs = collect_launchd(target)
        except Exception as e:  # unreachable host: keep its old entries, record the outage
            summary[host] = f"unreachable: {str(e)[:120]}"
            seen.add(f"mon.{host}._reach")
            _write(_state(f"mon.{host}._reach", f"{host} · reachability", "error", [f"collect failed: {e}"],
                          host=host, project="infra"))
            continue
        ok_hosts.add(host)
        for j in jobs:
            keys = j.pop("plist_keys")
            j["interval"] = expected_interval(keys)
            j["service"] = keys.get("KeepAlive") is True and not j["interval"]
            j["log_every_run"] = j["label"] in LOG_OVERRIDES
            status, why = classify(j, now)
            name = re.sub(r"^com\.(lichenyu|clawd)\.(managed\.)?", "", j["label"])
            last = f"last run {_ago(now - j['log_mtime'])} ago" if j.get("log_mtime") else "last run unknown"
            sched = "service" if j["service"] else (f"every {_ago(j['interval'])}" if j["interval"] else "on load/manual")
            id_ = f"mon.{host}.{name}"
            seen.add(id_)
            _write(_state(id_, f"{host} · {name}", status, [why, f"{sched} · {last}", f"launchd {j['label']}"],
                          host=host, project=project_of(j["label"]), log=j.get("log"),
                          telemetry={k: j.get(k) for k in ("label", "pid", "last_exit", "interval", "log_mtime",
                                                           "plist", "command")}))
        summary[host] = f"{len(jobs)} launchd jobs"
    try:
        ps5 = collect_ps5()
        ok_hosts.add("ps5")
        for r in ps5["slurm"]:
            id_ = f"mon.ps5.slurm-{r['id']}"
            seen.add(id_)
            _write(_state(id_, f"ps5 · slurm {r['id']} {r['name']} ({r['user']})", slurm_status(r),
                          [f"{r['state']} {r['reason']}", f"elapsed {r['elapsed']} / limit {r['limit']}",
                           f"scontrol show job {r['id']}  ·  scancel {r['id']}"],
                          host="ps5", project="slurm", telemetry=r))
        for u in ps5["failed_units"]:
            id_ = f"mon.ps5.unit-{u}"
            seen.add(id_)
            _write(_state(id_, f"ps5 · systemd {u}", "error", ["systemd unit failed",
                          f"journalctl -u {u} -n 50"], host="ps5", project="hekademia"))
        summary["ps5"] = f"{len(ps5['slurm'])} slurm jobs, {len(ps5['failed_units'])} failed units"
    except Exception as e:
        summary["ps5"] = f"unreachable: {str(e)[:120]}"
        seen.add("mon.ps5._reach")
        _write(_state("mon.ps5._reach", "ps5 · reachability", "error", [f"collect failed: {e}"],
                      host="ps5", project="infra"))
    # drop entries that vanished from a host we did reach (finished Slurm jobs, removed agents)
    for p in STATE_DIR.glob("mon.*.json"):
        host = p.stem.split(".")[1]
        if p.stem not in seen and (host in ok_hosts or p.stem.endswith("._reach")):
            p.unlink()
    return summary


# ---------------------------------------------------------------- reading
ICON = {"error": "✗", "stalled": "⧗", "blocked": "⊘", "running": "▶", "queued": "…", "needs_review": "?", "paused": "‖",
        "done": "✓", "failed": "✗"}
ORDER = {"error": 0, "stalled": 0, "blocked": 1, "needs_review": 2, "running": 3, "queued": 4, "paused": 5, "done": 6}


STALL_SECONDS = 48 * 3600  # in-progress job whose state file has not changed for 48 h


def effective_status(d: dict, now: float) -> str:
    """Show running/queued jobs that stopped updating as stalled instead of in progress."""
    st = d.get("status")
    if st in ("running", "queued") and now - d.get("_mtime", now) > STALL_SECONDS:
        return "stalled"
    return st


def _load_all() -> list[dict]:
    out = []
    for p in STATE_DIR.glob("*.json"):
        try:
            d = json.loads(p.read_text())
        except Exception:
            continue
        if isinstance(d, dict):
            d["_mtime"] = p.stat().st_mtime
            d.setdefault("id", p.stem)
            out.append(d)
    return out


def _line(d: dict) -> str:
    det = d.get("details") or []
    det = det if isinstance(det, list) else [str(det)]
    return f"  {ICON.get(d.get('status'), '·')} {d.get('title', d['id'])[:58]:58s} {' · '.join(map(str, det[:2]))[:110]}"


def cmd_status(show_all: bool) -> None:
    allj = _load_all()
    mons = [d for d in allj if "monitor" in (d.get("tags") or [])]
    jobs = [d for d in allj if "monitor" not in (d.get("tags") or [])]
    now = time.time()
    newest = max((d["_mtime"] for d in mons), default=0)
    print(f"== Monitors (refreshed {_ago(now - newest)} ago; `jt refresh` to collect now)")
    by_host: dict = {}
    for d in mons:
        by_host.setdefault((d.get("telemetry") or {}).get("host", "?"), []).append(d)
    for host in sorted(by_host):
        ds = sorted(by_host[host], key=lambda d: (ORDER.get(d.get("status"), 9), d.get("title", "")))
        counts: dict = {}
        for d in ds:
            counts[d.get("status")] = counts.get(d.get("status"), 0) + 1
        print(f"[{host}] " + "  ".join(f"{ICON.get(k, k)} {k} {v}" for k, v in sorted(counts.items(), key=lambda kv: ORDER.get(kv[0], 9))))
        for d in ds:
            if show_all or d.get("status") not in ("done", "paused"):
                print(_line(d))
    for d in jobs:
        d["_shown"] = effective_status(d, now)
    act = [d for d in jobs if d["_shown"] in ("running", "queued", "stalled", "blocked", "needs_review", "error")
           or (d["_shown"] == "failed" and now - d["_mtime"] < 86400)]
    act.sort(key=lambda d: (ORDER.get(d["_shown"], 9), -d["_mtime"]))
    print(f"\n== Tracked jobs needing attention or in progress ({len(act)}; failures from last 24h)")
    for d in act[: (None if show_all else 25)]:
        p = d.get("progress") or {}
        prog = f"[{p.get('current')}/{p.get('total')}] " if isinstance(p, dict) and p.get("total") else ""
        tag = "  STALLED: no update" if d["_shown"] == "stalled" else ""
        print(f"  {ICON.get(d['_shown'], '·')} {prog}{d.get('title', d['id'])[:70]}  ({_ago(now - d['_mtime'])} ago){tag}")
    if not show_all and len(act) > 25:
        print(f"  … {len(act) - 25} more: `jt all`")


def _find(q: str) -> dict:
    allj = _load_all()
    for d in allj:
        if d["id"] == q:
            return d
    hits = [d for d in allj if q.lower() in d["id"].lower() or q.lower() in str(d.get("title", "")).lower()]
    if not hits:
        sys.exit(f"no job or monitor matches {q!r}")
    hits.sort(key=lambda d: ("monitor" not in (d.get("tags") or []), -d["_mtime"]))
    if len(hits) > 1:
        print(f"({len(hits)} matches; showing {hits[0]['id']}; others: {', '.join(h['id'] for h in hits[1:6])})\n")
    return hits[0]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="jt", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("refresh"); sub.add_parser("all")
    s = sub.add_parser("show"); s.add_argument("q")
    lg = sub.add_parser("log"); lg.add_argument("q"); lg.add_argument("-n", type=int, default=40)
    a = ap.parse_args(argv)
    if a.cmd == "refresh":
        print(json.dumps(refresh(), indent=1))
    elif a.cmd == "show":
        d = _find(a.q); d.pop("_mtime", None)
        print(json.dumps(d, indent=2, ensure_ascii=False, default=str))
    elif a.cmd == "log":
        d = _find(a.q)
        log = d.get("log_path")
        host = (d.get("telemetry") or {}).get("host")
        if not log:
            sys.exit(f"{d['id']} has no log path")
        if host in HOSTS and HOSTS[host]:
            subprocess.run(SSH + [HOSTS[host], f"tail -n {a.n} {json.dumps(log)}"])
        else:
            subprocess.run(["tail", "-n", str(a.n), os.path.expanduser(log)])
    else:
        cmd_status(a.cmd == "all")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
