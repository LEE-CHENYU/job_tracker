"""Supervise only the two explicitly authorized research launchd services."""
import fcntl
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/Users/lichenyu/stock_journal')
STATE = Path('/Users/lichenyu/Library/Logs/research-watchdog')
JOBS = {'screening-queue': 'research_screening', 'notebook-queue': 'notebooklm'}


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
    return 'scheduled'


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


def main():
    STATE.mkdir(parents=True,exist_ok=True)
    with (STATE/'watchdog.lock').open('w') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            return
        for name,family in JOBS.items():
            try:
                run_one(name,family)
            except Exception as exc:
                print(json.dumps({'job':name,'action':'watchdog_error','error':str(exc)}),flush=True)


if __name__ == '__main__':
    main()
