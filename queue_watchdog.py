"""Supervise the authorized research launchd services and raise alerts when work silently stops.

Codex queues (JOBS): stop a live worker with no progress for 30 min, back off, check reachability,
then restart. An enabled queue with no process and no progress for 24 h raises `idle_alert`.
Daily runner (DAILY): reload its launchd agent if it is unloaded; alert when a run crashed, the
runner has been blocked for 24 h, or it has not written status for 24 h; hourly, re-check every
`daily_flow_complete` claim with scripts/daily_completion_check.py (model-free).
Alerts go to a `watchdog-<name>` job-tracker entry and an email on each state change.
"""
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/Users/lichenyu/stock_journal')
STATE = Path('/Users/lichenyu/Library/Logs/research-watchdog')
JOBS = {'screening-queue': 'research_screening'}
# notebook-queue's launchd agent was retired when the daily runner took over dispatch (resume_note 2026-10-26).
# Its control.json `enabled` stays true because the daily runner reads it as the notebook pause switch.
RETIRED = {'notebook-queue': 'daily runner owns notebook dispatch since 2026-10-26'}
IDLE_ALERT = 86400
DAILY = {'name': 'daily-research', 'label': 'com.clawd.daily-company-research',
         'control': ROOT/'docs/notebooklm/daily/control.json', 'status': ROOT/'docs/notebooklm/daily/status.json'}
BLOCKED = {'quota_blocked', 'admission_reconciliation_blocked', 'blocked', 'error'}
ALERT_ACTIONS = {'idle_alert', 'identity_mismatch', 'stop_failed', 'resume_failed', 'reload_failed',
                 'crashed', 'blocked_long', 'silent', 'watchdog_error'}
AUDIT_EVERY = 3600


def backoff_seconds(attempt):
    return min(3600, 60 * 2 ** min(max(0, attempt - 1), 6))


def substantive_event(event):
    return event.get('type') in {'item.completed', 'turn.completed'}


def decide(enabled, alive, matches, age, retry_at, now, recovering):
    if not enabled:
        return 'disabled'
    if alive and not matches:
        return 'identity_mismatch'
    if recovering and now < retry_at:
        return 'backoff'
    if recovering and not alive:
        return 'resume'
    if alive:
        return 'recover' if age > 1800 else 'healthy'
    return 'idle_alert' if age > IDLE_ALERT else 'scheduled'


def decide_daily(enabled, loaded, status, status_age, pid_alive, blocked_for):
    if not enabled:
        return 'disabled'
    if not loaded:
        return 'reload'
    if status == 'running':
        return 'healthy' if pid_alive or status_age < 1800 else 'crashed'
    if blocked_for > IDLE_ALERT:
        return 'blocked_long'
    if status_age > IDLE_ALERT:
        return 'silent'
    return 'healthy'


def read(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def write(path, data):
    tmp = path.with_name(path.name + f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(data, indent=2) + '\n')
    tmp.replace(path)


def stamp(value):
    try:
        return datetime.fromisoformat(value).timestamp()
    except (ValueError, TypeError):
        return 0


def command(args):
    return subprocess.run(args, capture_output=True, text=True, timeout=20)


def run_one(name, family):
    now = time.time()
    folder = ROOT/'docs'/family/'runner'
    control = read(folder/'control.json')
    worker = read(folder/'status.json')
    record_path = STATE/(name+'.json')
    record = read(record_path)
    service = f'gui/{os.getuid()}/com.clawd.{name}'
    service_info = command(['launchctl','print',service])
    match = re.search(r'^\s*pid = (\d+)\s*$',service_info.stdout,re.M)
    pid = int(match[1]) if match else None
    cmd = command(['ps','-p',str(pid),'-o','command=']).stdout if pid else ''
    matches = bool(pid and 'codex exec' in cmd and f'docs/{family}/runner/worker_prompt.md' in cmd)
    previous_pid = record.get('observed_pid')
    # Heartbeat only belongs to the process that wrote it. New workers get startup grace.
    if pid and pid != previous_pid:
        record['process_seen_at'] = now
        record['last_progress'] = stamp(worker.get('heartbeat')) if worker.get('pid') == pid else now
    if worker.get('pid') == pid and pid:
        record['last_progress'] = max(record.get('last_progress',0),stamp(worker.get('heartbeat')))
    log = Path('/Users/lichenyu/Library/Logs')/name/'events.jsonl'
    if log.exists():
        size = log.stat().st_size
        offset = record.get('log_offset',size)
        if offset > size:
            offset = 0
        if size > offset:
            with log.open('rb') as stream:
                stream.seek(max(offset,size-262144))
                for line in stream.read().splitlines():
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if substantive_event(event):
                        record['last_progress'] = now
        record['log_offset'] = size
    age = now-record.get('last_progress',now)
    action = decide(control.get('enabled') is True,bool(pid),matches,age,
                    record.get('retry_at',0),now,record.get('recovering',False))
    record.update(observed_pid=pid,checked_at=now,action=action,progress_age_seconds=round(age))
    if action == 'recover':
        # Save recovery context before stopping; launchd owns the process group.
        archive = STATE/f'{name}-{int(now)}-checkpoint.json'
        write(archive,{'worker':worker,'control':control,'pid':pid,'reason':'no heartbeat or substantive event for 30 minutes'})
        result = command(['launchctl','bootout',service])
        if result.returncode:
            record.update(action='stop_failed',error=result.stderr[-1000:])
        else:
            attempt = record.get('attempts',0)+1
            record.update(recovering=True,attempts=attempt,retry_at=now+backoff_seconds(attempt),action='recovery_backoff',checkpoint=str(archive))
    elif action == 'resume':
        # Provider reachability only; no credentials, generation or billing in this probe.
        probe = command(['/usr/bin/curl','--silent','--output','/dev/null','--max-time','10','https://chatgpt.com/'])
        if probe.returncode:
            attempt = record.get('attempts',0)+1
            record.update(attempts=attempt,retry_at=now+backoff_seconds(attempt),action='network_backoff')
        else:
            result = command(['launchctl','bootstrap',f'gui/{os.getuid()}',str(Path.home()/'Library/LaunchAgents'/f'com.clawd.{name}.plist')])
            if result.returncode:
                record.update(action='resume_failed',error=result.stderr[-1000:],retry_at=now+120)
            else:
                record.update(recovering=False,action='restarted',restarted_at=now,last_progress=now)
    elif action == 'healthy' and age < 300 and now-record.get('restarted_at',now) > 1800:
        record['attempts'] = 0
    write(record_path,record)
    print(json.dumps({'job':name,**{k:record.get(k) for k in ['action','observed_pid','progress_age_seconds','retry_at']}}),flush=True)
    return record


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def run_daily(cfg=DAILY):
    now = time.time()
    name = cfg['name']
    record_path = STATE/(name+'.json')
    record = read(record_path)
    service = f"gui/{os.getuid()}/{cfg['label']}"
    loaded = command(['launchctl','print',service]).returncode == 0
    status = read(cfg['status'])
    try:
        status_age = now - cfg['status'].stat().st_mtime
    except OSError:
        status_age = float('inf')
    state = status.get('status')
    if state in BLOCKED:
        if record.get('blocked_status') != state:
            record.update(blocked_status=state, blocked_since=now)
    else:
        record.pop('blocked_status', None); record.pop('blocked_since', None)
    blocked_for = now - record['blocked_since'] if 'blocked_since' in record else 0
    action = decide_daily(read(cfg['control']).get('enabled') is True, loaded, state, status_age,
                          pid_alive(status.get('pid')), blocked_for)
    if action == 'reload' and now < record.get('retry_at', 0):
        action = 'reload_backoff'
    elif action == 'reload':
        plist = Path.home()/'Library/LaunchAgents'/(cfg['label']+'.plist')
        result = command(['launchctl','bootstrap',f'gui/{os.getuid()}',str(plist)])
        attempt = record.get('attempts', 0) + 1
        if result.returncode:
            record.update(attempts=attempt, retry_at=now+backoff_seconds(attempt), error=result.stderr[-1000:])
            action = 'reload_failed' if attempt >= 3 else 'reload_backoff'
        else:
            record.update(attempts=0, reloaded_at=now)
            action = 'reloaded'
    elif action == 'healthy':
        record['attempts'] = 0
    if now - record.get('audit_at', 0) > AUDIT_EVERY:
        record['audit_at'] = now
        try:
            r = subprocess.run([sys.executable, str(ROOT/'scripts/daily_completion_check.py'), '--json'],
                               cwd=str(ROOT), capture_output=True, text=True, timeout=300)
            rows = json.loads(r.stdout)
            record['audit'] = {'unverified': [f"{x['ticker']}: {'; '.join(x['reasons'])}" for x in rows if x['verdict'] == 'unverified'],
                               'counts': {v: sum(x['verdict'] == v for x in rows) for v in {x['verdict'] for x in rows}}}
        except Exception as exc:
            record['audit'] = {'error': str(exc)[:300]}
    record.update(checked_at=now, action=action, runner_status=state, status_age_seconds=round(min(status_age, 1e9)),
                  blocked_for_seconds=round(blocked_for))
    write(record_path, record)
    print(json.dumps({'job':name,'action':action,'runner_status':state}),flush=True)
    return record


def summary(name, record):
    a = record.get('action')
    lines = [f"{name}: {a}"]
    if a == 'idle_alert':
        lines.append(f"Enabled but no worker process or progress for {record.get('progress_age_seconds',0)//3600} h")
    if a == 'blocked_long':
        lines.append(f"Runner {record.get('runner_status')} for {record.get('blocked_for_seconds',0)//3600} h")
    if a == 'silent':
        lines.append(f"No runner status written for {record.get('status_age_seconds',0)//3600} h (queue empty or runner not ticking)")
    if a == 'crashed':
        lines.append('Status says running but the worker pid is gone; inspect the run receipt')
    if record.get('error'):
        lines.append('error: ' + str(record['error'])[:200])
    audit = record.get('audit') or {}
    if audit.get('unverified'):
        lines.append(f"{len(audit['unverified'])} claimed-complete item(s) fail the independent check: " + ' | '.join(audit['unverified'])[:300])
    if audit.get('error'):
        lines.append('completion audit error: ' + audit['error'])
    return lines


def report(name, record):
    """Mirror into the job tracker; email only when the alert state changes."""
    sys.path.insert(0, str(Path.home()))
    from job_tracker import _state_path, register_job, update_job
    job = 'watchdog-' + name
    alert = record.get('action') in ALERT_ACTIONS
    review = bool((record.get('audit') or {}).get('unverified'))
    status = 'error' if alert else 'needs_review' if review else 'done'
    path = _state_path(job)
    previous = (read(path).get('telemetry') or {}).get('state')
    if not path.exists():
        register_job(job, f'Watchdog · {name}', agent='queue_watchdog.py',
                     purpose='Restarts stalled research workers; alerts when enabled work silently stops.')
    lines = summary(name, record)
    update_job(job, status=status, details=lines, telemetry={'state': status, 'action': record.get('action')})
    if previous is not None and previous != status and (alert or previous == 'error'):
        try:
            sys.path.insert(0, str(ROOT))
            from scripts.cron_email import send_alert
            send_alert(f'Research watchdog: {name} {previous} -> {status}', '\n'.join(lines))
        except Exception as exc:
            print(json.dumps({'job':name,'action':'email_failed','error':str(exc)[:200]}),flush=True)


def main():
    STATE.mkdir(parents=True,exist_ok=True)
    with (STATE/'watchdog.lock').open('w') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            return
        runs = [(name, lambda n=name, f=family: run_one(n, f)) for name, family in JOBS.items()]
        runs.append((DAILY['name'], run_daily))
        for name, fn in runs:
            try:
                record = fn()
            except Exception as exc:
                record = {'action': 'watchdog_error', 'error': str(exc)}
                print(json.dumps({'job':name,**record}),flush=True)
            try:
                report(name, record)
            except Exception as exc:
                print(json.dumps({'job':name,'action':'report_failed','error':str(exc)[:200]}),flush=True)
        for name, why in RETIRED.items():
            path = STATE/(name+'.json')
            rec = read(path)
            if rec.get('action') != 'retired':
                rec.update(action='retired', reason=why, checked_at=time.time())
                write(path, rec)


if __name__ == '__main__':
    main()
