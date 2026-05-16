# Job Tracker

A minimalist macOS menu-bar tracker for long-running background jobs across
all your projects. Any agent — Claude, Codex CLI, GitHub Actions runner,
plain shell script, cron, anything that can write a JSON file — can
register a job and surface progress + rich context in the menu bar.

![menu bar layout](docs/menubar.png) <!-- optional screenshot placeholder -->

## Why this exists

When you have multi-hour or multi-day jobs running in screen sessions,
launchd agents, or backgrounded shells, you lose visibility:

- *"Is that 4-day rescue still running, or did the drive disconnect?"*
- *"What was that DART backfill doing again? When did it start? Who launched it?"*
- *"My agent kicked off something — what was it and why?"*

Job Tracker is the one-line-of-truth: each running job has a JSON state
file, the menu bar shows them all with progress, ETA, throughput,
purpose, and one-click actions (Open log / Cancel / Remove).

It's specifically designed for **agent collaboration** — Claude or Codex
sessions can hand off context to the user (and to each other) by
registering their long-running work, including a free-form `purpose`
field for *why* the job is running.

## Features

- **Zero coupling**: jobs register by writing a single JSON file. No
  server, no daemon to coordinate with.
- **Multiple registration paths**: Python API, CLI, or raw JSON file.
- **Rich context**: each job carries a multi-line `purpose` explaining
  why it's running — surfaced inline in the menu so future sessions
  understand the work.
- **Status filtering**: only `running` jobs shown by default; toggle
  reveals paused / done / failed.
- **Word-wrapped detail lines**: long log tails, URLs, error messages
  wrap automatically (URL-aware breaks on `/ ? & = : ,`).
- **Per-job actions**: Open log file in Console, run cancel command,
  remove from tracker.
- **Auto-refresh** every 30s; manual refresh button.

## Install

Requires **macOS** and **Python 3.10+**.

```bash
# 1. Clone wherever you like (recommended: home directory so jobs
#    can reference a stable path)
git clone https://github.com/<your-username>/job_tracker.git ~/job_tracker

# 2. Install the only runtime dependency
pip install rumps   # macOS menu-bar binding

# 3. Run the menubar app
python ~/job_tracker/menubar.py

# OR — install as a launch agent so it auto-starts on login:
cp ~/job_tracker/contrib/launchd/com.jobtracker.menubar.plist \
   ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/com.jobtracker.menubar.plist
```

The **"Jobs"** label appears in the menu bar. The percentage shown is
the most-progressed currently-running job.

## Quick start: register your first job

### Python API

```python
import os, sys
sys.path.insert(0, os.path.expanduser("~"))   # so `import job_tracker` works
from job_tracker import register_job, update_job, mark_done, mark_failed

# Once, at job start:
register_job(
    id="nightly_backup",
    title="Nightly backup to S3",
    purpose=(
        "Push 200 GB of project artifacts to s3://my-backups/2026/. "
        "Runs from a cron at 02:00 PT. Takes ~6h."
    ),
    agent="cron",
    pid=os.getpid(),
    log_path="/var/log/nightly_backup.log",
    cancel_cmd=["pkill", "-f", "nightly_backup"],
    tags=["backup", "nightly"],
    progress_total=200_000,        # MB to upload
    progress_unit="MB",
)

# Periodically, as work progresses:
update_job(
    "nightly_backup",
    current=42_000,
    rate_per_sec=12.5,
    details=["last file: project_x.tar.gz", "0 failures"],
    telemetry={"files_uploaded": 1234, "skipped_existing": 56},
)

# At the end:
mark_done("nightly_backup")
# or:
mark_failed("nightly_backup", reason="S3 throttled at file 187654")
```

### CLI

```bash
# Register
python -m job_tracker.cli register \
  --id nightly_backup --title "Nightly backup to S3" \
  --purpose "Push 200 GB to s3://my-backups/2026/. Cron 02:00 PT, ~6h." \
  --agent cron --pid $$ --log /var/log/nightly_backup.log \
  --progress-total 200000 --progress-unit MB

# Update
python -m job_tracker.cli update --id nightly_backup \
  --current 42000 --rate 12.5 \
  --details '["last file: project_x.tar.gz", "0 failures"]'

# Mark done
python -m job_tracker.cli done --id nightly_backup

# List everything
python -m job_tracker.cli list

# Remove from tracker
python -m job_tracker.cli remove --id nightly_backup
```

### Pure shell

```bash
mkdir -p ~/job_tracker/state
cat > ~/job_tracker/state/nightly_backup.json <<EOF
{
  "id": "nightly_backup",
  "title": "Nightly backup to S3",
  "purpose": "Push 200 GB to s3://my-backups/2026/. Cron 02:00 PT, ~6h.",
  "status": "running",
  "started_at": "$(date -u +%FT%TZ)",
  "updated_at": "$(date -u +%FT%TZ)",
  "progress": {"current": 42000, "total": 200000, "unit": "MB"}
}
EOF
```

Any process that can write a JSON file can register a job. The menubar
polls every 30s and renders whatever's there.

## Schema reference

| Field | Type | Required | Meaning |
|---|---|---|---|
| `id` | str | yes | Unique identifier (also used as filename) |
| `title` | str | yes | Human-readable label |
| `status` | enum | yes | `running` / `paused` / `done` / `failed` / `error` |
| `purpose` | str | no | **Rich context: WHY this is running.** Multi-line OK |
| `agent` | str | no | Who registered (e.g. `"claude-opus-4-7"`, `"cron"`) |
| `started_at` | ISO timestamp | auto | When registered |
| `updated_at` | ISO timestamp | auto | Last update |
| `pid` | int | no | OS process PID |
| `progress.current` | int | no | Items done so far |
| `progress.total` | int | no | Total items to do |
| `progress.unit` | str | no | `"filings"`, `"MB"`, `"rows"`, etc. |
| `progress.rate_per_sec` | float | no | Throughput; used to compute ETA |
| `details` | `list[str]` | no | Free-form display lines (word-wrapped) |
| `telemetry` | dict | no | Arbitrary k/v metrics (rendered inline) |
| `log_path` | str | no | Absolute path; enables "Open log" action |
| `cancel_cmd` | `list[str]` | no | argv for "Cancel job" action |
| `tags` | `list[str]` | no | For future filtering / grouping |

## Layout

```
~/job_tracker/
├── __init__.py        Python API
├── cli.py             CLI wrapper
├── menubar.py         rumps menu-bar app
├── state/             state files (one *.json per job; gitignored)
├── contrib/
│   └── launchd/
│       └── com.jobtracker.menubar.plist   # sample auto-start plist
└── README.md
```

## Examples: keeping jobs synced

For jobs whose progress is best derived from artifacts (parquet files,
log tails, etc.), you can run a small refresher on a cron / launchd
interval. Two real-world examples live alongside this code:

- `refresh_scenario_b.py` — reads a parquet's row count + extracted-at
  timestamps, computes throughput on the last contiguous window
  (excluding crash-and-restart gaps), pushes via `update_job()`.
- `refresh_codex_backfills.py` — parses an external monitoring JSON
  produced by another supervisor, registers one tracker job per market
  (DART / TWSE / ASX / HKEX / EDINET).

Both fit a common pattern: read state from an artifact → call
`update_job()`. Adapt to your own pipelines.

## Why a menu-bar app and not a web dashboard?

- **Always-on visibility** without opening a browser tab.
- **No port to bind**, no auth to configure, no other service to babysit.
- **Click-to-act**: log in Console, cancel via your own shell command.
- **Survives across logins**: launchd auto-restart with `KeepAlive`.

If you want a web UI, you have all the state files at
`~/job_tracker/state/*.json` — point any frontend at them.

## Design notes / known constraints

- **rumps title-keying gotcha**: macOS NSMenu items are keyed by their
  title string under the hood; if you build a menu where two items have
  the same title (e.g. multiple "Purpose ›" submenus across jobs), they
  collide and ghost items can accumulate. This app sidesteps that by
  rendering everything flat, with values inline that make each line's
  title naturally unique.
- **No central daemon for state**: state files are the only source of
  truth. This means two processes could race on the same file; in
  practice each job writes its own file so there's no contention.
- **Refresh granularity**: the menu refreshes on a 30-second timer.
  For tighter latency, click "Refresh".

## License

MIT
