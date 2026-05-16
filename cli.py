#!/usr/bin/env python3
"""Job-tracker CLI — for agents / shell scripts that aren't in Python.

Examples:
  # Register a job
  python -m job_tracker.cli register \\
    --id my_job --title "My job" \\
    --purpose "Backfill X for reason Y" \\
    --agent "claude-opus-4-7" \\
    --pid 12345 \\
    --log /tmp/myjob.log \\
    --progress-total 1000 --progress-unit filings

  # Update progress
  python -m job_tracker.cli update --id my_job --current 250 --details "checkpoint 250"

  # Mark complete
  python -m job_tracker.cli done --id my_job

  # List all jobs
  python -m job_tracker.cli list

  # Remove a job
  python -m job_tracker.cli remove --id my_job
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running both `python -m job_tracker.cli` and `python ~/job_tracker/cli.py`
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from job_tracker import (
        register_job, update_job, mark_done, mark_failed,
        unregister_job, list_jobs,
    )
else:
    from . import (
        register_job, update_job, mark_done, mark_failed,
        unregister_job, list_jobs,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                       formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    reg = sub.add_parser("register", help="Register a new job")
    reg.add_argument("--id", required=True)
    reg.add_argument("--title", required=True)
    reg.add_argument("--purpose", default=None,
                      help="Rich multi-line context: WHY this is running")
    reg.add_argument("--agent", default=None)
    reg.add_argument("--pid", type=int, default=None)
    reg.add_argument("--log", dest="log_path", default=None)
    reg.add_argument("--cancel-cmd", default=None,
                      help="Comma-separated argv for cancel action")
    reg.add_argument("--tags", default=None,
                      help="Comma-separated tags")
    reg.add_argument("--progress-total", type=int, default=None)
    reg.add_argument("--progress-unit", default="items")

    upd = sub.add_parser("update", help="Update an existing job")
    upd.add_argument("--id", required=True)
    upd.add_argument("--current", type=int, default=None)
    upd.add_argument("--total", type=int, default=None)
    upd.add_argument("--rate", type=float, default=None,
                      help="Rate in unit/sec")
    upd.add_argument("--status", choices=["running", "paused", "done", "failed", "error"],
                      default=None)
    upd.add_argument("--details", default=None,
                      help="JSON array of detail lines, OR newline-separated")
    upd.add_argument("--telemetry", default=None,
                      help="JSON object of k/v telemetry")
    upd.add_argument("--pid", type=int, default=None)

    done = sub.add_parser("done", help="Mark a job done")
    done.add_argument("--id", required=True)

    fail = sub.add_parser("fail", help="Mark a job failed")
    fail.add_argument("--id", required=True)
    fail.add_argument("--reason", required=True)

    rm = sub.add_parser("remove", help="Remove a job from the tracker")
    rm.add_argument("--id", required=True)

    sub.add_parser("list", help="Print all registered jobs as JSON")

    args = parser.parse_args()

    if args.cmd == "register":
        p = register_job(
            id=args.id, title=args.title, purpose=args.purpose,
            agent=args.agent, pid=args.pid, log_path=args.log_path,
            cancel_cmd=args.cancel_cmd.split(",") if args.cancel_cmd else None,
            tags=args.tags.split(",") if args.tags else None,
            progress_total=args.progress_total, progress_unit=args.progress_unit,
        )
        print(p)
    elif args.cmd == "update":
        details = None
        if args.details:
            try:
                details = json.loads(args.details)
                if not isinstance(details, list):
                    details = [str(details)]
            except json.JSONDecodeError:
                details = args.details.split("\n")
        telemetry = None
        if args.telemetry:
            telemetry = json.loads(args.telemetry)
        update_job(
            id=args.id, current=args.current, total=args.total,
            rate_per_sec=args.rate, status=args.status, details=details,
            telemetry=telemetry, pid=args.pid,
        )
        print(f"updated {args.id}")
    elif args.cmd == "done":
        mark_done(args.id)
        print(f"done {args.id}")
    elif args.cmd == "fail":
        mark_failed(args.id, reason=args.reason)
        print(f"failed {args.id}")
    elif args.cmd == "remove":
        ok = unregister_job(args.id)
        print(f"removed {args.id}" if ok else f"no such job {args.id}")
    elif args.cmd == "list":
        print(json.dumps(list_jobs(), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
