# Job Tracker

Menu-bar app for tracking long-running background jobs across all
projects. Agents (Claude, Codex CLI, shell scripts) register their jobs
by writing JSON state files; the menubar reads them and renders.

## Quick start

```bash
# Start the menubar app
nohup /Users/lichenyu/miniconda3/envs/python310/bin/python \
  ~/job_tracker/menubar.py > /tmp/job_tracker.log 2>&1 &

# Or via launchd (auto-start on login)
launchctl load -w ~/Library/LaunchAgents/com.clawd.job-tracker.plist
```

The 🛠 icon appears in the menu bar. The percentage shown is the
most-progressed running job's progress.

## How agents register jobs

### Python API (preferred)

```python
import sys; sys.path.insert(0, "/Users/lichenyu")
from job_tracker import register_job, update_job, mark_done

register_job(
    id="my_long_job",
    title="Backfill 10K filings",
    purpose=(
        "Re-extract 10K HK filings that had null revenue in the "
        "canonical aggregator. Expected to take ~3 days, ~50% of "
        "Codex weekly cap. Resumable via --auto-resume-on-cap."
    ),
    agent="claude-opus-4-7@2026-05-15",
    pid=12345,
    log_path="/tmp/my_long_job.log",
    cancel_cmd=["pkill", "-f", "my_long_job"],
    tags=["hk-corpus", "long-running"],
    progress_total=10000,
    progress_unit="filings",
)

# Inside the job loop, after each batch:
update_job("my_long_job", current=2500,
           rate_per_sec=0.05,
           details=["last filing: 12345678", "0 failures so far"],
           telemetry={"fields_per_filing": 25.3, "scale_errors": 12})

# At end:
mark_done("my_long_job")
```

### CLI (for shell scripts / non-Python agents)

```bash
# Register
python -m job_tracker.cli register \
  --id my_job --title "My job" \
  --purpose "Why this is running, multi-line OK" \
  --agent "claude-opus-4-7" \
  --pid $$ --log /tmp/my_job.log \
  --progress-total 1000 --progress-unit items

# Update
python -m job_tracker.cli update --id my_job --current 250 \
  --rate 0.5 --details '["checkpoint 250"]'

# Mark done
python -m job_tracker.cli done --id my_job

# List all
python -m job_tracker.cli list

# Remove
python -m job_tracker.cli remove --id my_job
```

### Pure-shell (one-liner)

```bash
cat > ~/job_tracker/state/my_job.json <<EOF
{
  "id": "my_job",
  "title": "My job",
  "purpose": "Why this matters",
  "status": "running",
  "started_at": "$(date -u +%FT%TZ)",
  "progress": {"current": 250, "total": 1000, "unit": "items"}
}
EOF
```

Any process that can write a JSON file can register a job. The menubar
polls every 30s and renders whatever's there.

## Schema reference

| Field | Type | Required | Meaning |
|---|---|---|---|
| `id` | str | yes | Unique identifier (used as filename) |
| `title` | str | yes | Human-readable label |
| `purpose` | str | no | **Rich context: WHY this job is running.** Multi-line OK |
| `agent` | str | no | Who/what registered (e.g. "claude-opus-4-7@2026-05-15") |
| `started_at` | ISO ts | yes (auto) | When registered |
| `updated_at` | ISO ts | yes (auto) | Last update |
| `status` | enum | yes | running/paused/done/failed/error |
| `pid` | int | no | OS process PID |
| `progress.current` | int | no | Items done |
| `progress.total` | int | no | Total items |
| `progress.unit` | str | no | "filings", "rows", etc. |
| `progress.rate_per_sec` | float | no | Throughput |
| `details` | [str] | no | Free-form display lines |
| `telemetry` | dict | no | Arbitrary k/v metrics |
| `log_path` | str | no | Path to log file (enables "Open log") |
| `cancel_cmd` | [str] | no | argv to cancel the job (enables "Cancel job") |
| `tags` | [str] | no | For grouping / filtering |

## For Claude / Codex agents

**When you start a background job, register it.** Treat job registration
as part of the launch protocol — same as writing a log file. Future
Claude/Codex sessions (and the user) will see your jobs in the menubar
and know what's running and why.

Minimal pattern in your scripts:

```python
import os, sys
sys.path.insert(0, "/Users/lichenyu")
from job_tracker import register_job, update_job, mark_done, mark_failed

def main():
    register_job(
        id="<unique-slug>",
        title="<one-line-summary>",
        purpose="<2-4 sentences: what you're computing, why, what consumers depend on it>",
        agent=f"<your-model-id>@{os.environ.get('SESSION_START', '')}",
        pid=os.getpid(),
        log_path="<absolute path>",
        cancel_cmd=["pkill", "-f", "<unique pattern>"],
        progress_total=<int>,
    )
    try:
        for i, item in enumerate(items):
            process(item)
            if i % 25 == 0:
                update_job("<id>", current=i, details=[f"last: {item}"])
        mark_done("<id>")
    except Exception as exc:
        mark_failed("<id>", reason=str(exc))
        raise
```

## Layout

```
~/job_tracker/
├── __init__.py        # Python API (register_job, update_job, …)
├── cli.py             # CLI wrapper around the API
├── menubar.py         # rumps app — the menu-bar UI
├── state/             # one JSON per registered job
│   └── *.json
└── README.md          # this file
```
