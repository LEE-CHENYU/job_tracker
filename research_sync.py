"""Read-only research adapters. Refresh tracker snapshots; never launch model jobs."""
import json
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter

HERE = Path(__file__).resolve().parent
ROOT = Path('/Users/lichenyu/stock_journal')
RESEARCH = ROOT / 'docs/research'


def worker_activity(job_id, worker, enabled, alive, now=None):
    """Execution health is independent of saved research/completion gates."""
    now = now or datetime.now(timezone.utc)
    if not enabled:
        return 'paused', 'Execution disabled'
    try:
        retry = datetime.fromisoformat(worker.get('retry_after', ''))
        if retry > now:
            return 'blocked', 'Backoff until ' + retry.isoformat()
    except (ValueError, TypeError):
        pass
    if not alive:
        return 'stalled', 'No matching live queue worker'
    try:
        age = (now - datetime.fromisoformat(worker['heartbeat'])).total_seconds()
    except (KeyError, ValueError, TypeError):
        return 'stalled', 'Worker heartbeat missing or invalid'
    if age < -60 or age > 1800:
        return 'stalled', 'No fresh heartbeat; worker may be stuck in a long call'
    if worker.get('status') in {'blocked', 'waiting', 'paused'}:
        return 'blocked', str(worker.get('gate') or worker.get('status'))
    active = worker.get('current_job_id')
    if not active and job_id.startswith('research_notebook'):
        active = ('research_notebook_030000_KS' if worker.get('current_company') == '030000.KS'
                  else 'research_notebooks_21')
    label = 'working' if active == job_id else 'waiting'
    detail = f"Worker {worker.get('pid')}; heartbeat {int(max(age,0))}s ago"
    if worker.get('current_company'):
        detail += '; ' + str(worker['current_company'])
    if worker.get('gate'):
        detail += ' / ' + str(worker['gate'])
    return label, detail


def execution_telemetry(job_id):
    family = 'notebooklm' if job_id.startswith('research_notebook') else 'research_screening'
    folder = ROOT / 'docs' / family / 'runner'
    try:
        control = read(folder/'control.json')
    except (OSError, ValueError):
        control = {'enabled': True}
    try:
        worker = read(folder/'status.json')
    except (OSError, ValueError):
        worker = {}
    alive = False
    try:
        pid = int(worker.get('pid', 0))
        if pid > 0:
            command = subprocess.run(['ps','-p',str(pid),'-o','command='],
                capture_output=True,text=True,timeout=2).stdout
            alive = 'codex exec' in command and f'docs/{family}/runner/worker_prompt.md' in command
    except (ValueError, TypeError, OSError, subprocess.TimeoutExpired):
        pass
    state, detail = worker_activity(job_id,worker,control.get('enabled',True),alive)
    return {'execution_status':state,'execution_detail':detail,
            'worker_pid':worker.get('pid'),'worker_heartbeat':worker.get('heartbeat')}


def visible_by_default(status):
    return status in {'running', 'queued', 'blocked', 'needs_review', 'error'}


def compact_title(jobs):
    active = sum(visible_by_default(j.get('status')) for j in jobs)
    alert = any(j.get('status') in {'failed','error'} or (j.get('telemetry') or {}).get('execution_status') == 'stalled' for j in jobs)
    return ('Jobs ' + str(active) if active else 'Jobs') + ('!' if alert else '')


def job_heading(job):
    status = job.get('status', 'unknown').replace('_', ' ')
    activity = (job.get('telemetry') or {}).get('execution_status')
    if activity in {'working','waiting','stalled','blocked','paused'}:
        label = {'working':'WORKING','waiting':'WAITING — worker on other queue','stalled':'STALLED','blocked':'BLOCKED / BACKOFF','paused':'PAUSED'}[activity]
        status = label + ' · ' + status
    progress = job.get('progress') or {}
    current, total = progress.get('current'), progress.get('total')
    count = f" — {current}/{total} {progress.get('unit', 'items')}" if current is not None and total else ''
    return f"{job.get('title', job.get('id', '?'))} — {status}{count}"


def workflow_status(results, total, verified):
    if total and verified == total:
        return 'done'
    return 'needs_review' if results else 'queued'


def notebook_counts(rows):
    return sum(r.get('live_report_verified') is True for r in rows), len(rows)


def read(path):
    return json.loads(path.read_text())


def save(job_id, title, status, current, total, unit, source, details, telemetry=None):
    path = HERE / 'state' / (job_id + '.json')
    now = datetime.now(timezone.utc).isoformat()
    previous = read(path) if path.exists() else {}
    data = dict(id=job_id, title=title, status=status, agent='research-sync',
                purpose='Track saved research gates; no model launches or notebook generation. Counts are artifact observations, not final investment verdicts.',
                started_at=previous.get('started_at', now), updated_at=now,
                progress=dict(current=current, total=total, unit=unit),
                log_path=str(source), details=details,
                tags=['research', 'managed-research-sync'],
                telemetry=telemetry or {})
    data['telemetry']['source_path'] = str(source)
    data['telemetry']['source_modified_at'] = datetime.fromtimestamp(source.stat().st_mtime, timezone.utc).isoformat()
    if status != 'done':
        data['telemetry'].update(execution_telemetry(job_id))
    else:
        data['telemetry']['execution_status'] = 'complete'
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=2) + '\n')
    tmp.replace(path)


def sync_notebooks():
    plan = RESEARCH / 'close_read_companions_20261018/creation_audit/creation_plan.json'
    rows = read(plan)['companies']
    current, total = notebook_counts(rows)
    save('research_notebooks_21', 'NotebookLM — original 18 + three picks',
         workflow_status(sum(bool(r.get('generated_reports')) for r in rows), total, current),
         current, total, 'verified live reports', plan,
         ['Input analytical report and verified live Studio report are separate gates.',
          'Original 18: reuse reviewed input after corrections; three picks: complete missing input work.',
          'Local HTML/PDF reader remains a separate deliverable.',
          'Saved artifact snapshot only; reconcile live NotebookLM before generating.'],
         {'companies': [r['ticker'] for r in rows]})
    source = ROOT / 'docs/notebooklm/queue.json'
    for row in read(source)['jobs']:
        current, total = notebook_counts([row])
        save('research_notebook_' + row['ticker'].replace('.', '_'),
             'NotebookLM — ' + row['company'], 'done' if current else 'queued',
             current, total, 'verified live reports', source,
             [row['status'], row.get('blocking_reason',''), row['notebook_url']],
             {'stage':row['status'], 'notebook_id':row['notebook_id']})


def sync_screens():
    source = RESEARCH / 'claude_backfill_20261018/queue.json'
    batches = read(source)['batches']
    counts = Counter()
    for b in batches:
        counts[b['status']] += len(b['symbols'])
    total = sum(counts.values())
    results = sum(v for k,v in counts.items() if k.startswith('completed'))
    save('research_screen_claude', 'Screening — Claude comparison backfill',
         workflow_status(results,total,0), results,total,'passes saved',source,
         [f'{k}: {v} companies' for k,v in counts.items()] +
         ['Saved passes still require quote/rationale reconciliation; not final verdicts.',
          '200-company universe; 165 new Claude passes plus 35 prior passes reused.'],dict(counts))
    folder = RESEARCH / 'codex_legacy49_redo_20261018'
    run = read(folder/'run.json')
    total = sum(len(c['symbols']) for c in run['cohorts'])
    results = 0
    for c in run['cohorts']:
        p = folder/c['id']/'response.json'
        if p.exists():
            response = read(p)
            # A saved response is not proof that every requested company was returned.
            records = response if isinstance(response,list) else response.get('assessments',response.get('companies',response.get('results',[])))
            if isinstance(records,list):
                symbols={r.get('symbol',r.get('ticker')) for r in records if isinstance(r,dict)}
                results += len(set(c['symbols']) & symbols)
    save('research_screen_codex49','Screening — Codex 49-company redo',workflow_status(results,total,0),results,total,'responses saved',folder/'run.json',
         ['Reuses previous Claude passes; coordinator review remains separate.', 'Run manifest RUNNING is not worker-liveness proof.'])
    for name,title in [('either_check_specialists_20261018','Screening — either-model specialist checks'),
                       ('previous_batch_followthrough_20261018','Screening — previous batch follow-through')]:
        folder=RESEARCH/name; run=read(folder/'run.json'); tasks=run['tasks']
        symbols=[t if isinstance(t,str) else t['symbol'] for t in tasks]
        counts=Counter()
        for symbol in symbols:
            p=folder/symbol/'result.json'
            counts[read(p).get('status','result_saved_unverified') if p.exists() else 'no_result']+=1
        results=len(symbols)-counts['no_result']
        save('research_'+name.replace('_20261018',''),title,workflow_status(results,len(symbols),0),results,len(symbols),'results saved',folder/'run.json',
             [f'{k}: {v}' for k,v in counts.items()]+['Specialist proposals require coordinator review; do not overwrite original votes.'],dict(counts))
    folder=RESEARCH/'paired_specialists_20261018';run=read(folder/'run.json')
    results=sum((folder/r['symbol']/'run_receipts.json').exists() for r in run['queue'])
    save('research_specialists_paired','Screening — paired specialist comparison',workflow_status(results,len(run['queue']),0),results,len(run['queue']),'receipt files present',folder/'run.json',
         ['Receipt-file presence does not certify valid outputs, matched prompts or agreement.', 'Inspect both model results and coordinator verdict; first wave is a subset of this queue.'])
    source=RESEARCH/'review_batches_20261018/index.json'; index=read(source)
    batches=index['sets']['screening_universe']['batches']
    companies=[]
    for b in batches:
        companies.extend(read(ROOT/b['register'])['companies'])
    verified=sum(r.get('final_verdict') is not None for r in companies)
    save('research_screen_review','Screening — final review in groups of 20',workflow_status(1,len(companies),verified),verified,len(companies),'recorded final verdicts',source,
         [f'{len(batches)} review groups; keep SHORTLIST, CHECK and DEFER distinct.', 'Review registers may lag model artifacts; do not infer verdicts from majority votes.'])


def main():
    # Preserve previous state if source access fails; show an explicit adapter error.
    for name, fn in [('notebooks',sync_notebooks),('screening',sync_screens)]:
        try:
            fn()
        except Exception as exc:
            save('research_sync_'+name, 'Research sync — '+name, 'error',0,1,'sync',Path(__file__),[str(exc)])
            raise
        else:
            p=HERE/'state'/('research_sync_'+name+'.json')
            if p.exists():
                x=read(p);x['status']='done';p.write_text(json.dumps(x,indent=2)+'\n')

if __name__ == '__main__':
    main()
