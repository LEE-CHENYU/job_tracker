import time

from job_tracker import monitors as m


def test_interval_from_start_interval():
    assert m.expected_interval({"StartInterval": 10800}) == 10800


def test_interval_from_calendar():
    assert m.expected_interval({"StartCalendarInterval": {"Minute": 5}}) == 3600
    assert m.expected_interval({"StartCalendarInterval": {"Hour": 2, "Minute": 0}}) == 86400
    assert m.expected_interval({"StartCalendarInterval": [{"Weekday": 1, "Hour": 9}]}) == 7 * 86400
    assert m.expected_interval({"StartCalendarInterval": [{"Hour": 9}, {"Hour": 21}]}) == 43200


def test_interval_service():
    assert m.expected_interval({"KeepAlive": True}) is None


def test_project_of_label():
    assert m.project_of("com.lichenyu.network-wechat-sync") == "personal-network"
    assert m.project_of("com.lichenyu.conversation-hub") == "conversation-hub"
    assert m.project_of("com.clawd.notebook-queue") == "clawd"
    assert m.project_of("com.wacli.sync") == "wacli"


NOW = 1_800_000_000


def job(**kw):
    base = {"label": "x", "loaded": True, "pid": None, "last_exit": 0, "interval": 3600,
            "service": False, "log_mtime": NOW - 600, "disabled": False}
    base.update(kw)
    return base


def test_classify_ok_between_runs():
    assert m.classify(job(), NOW)[0] == "done"


def test_classify_running_now():
    assert m.classify(job(pid=123), NOW)[0] == "running"


def test_classify_nonzero_exit_is_error():
    st, why = m.classify(job(last_exit=1), NOW)
    assert st == "error" and "exit 1" in why


def test_classify_stale_log_known_heartbeat():
    st, why = m.classify(job(log_mtime=NOW - 4 * 3600, log_every_run=True), NOW)
    assert st == "error" and "no run" in why


def test_classify_quiet_log_with_clean_exit_is_ok():
    st, why = m.classify(job(log_mtime=NOW - 4 * 3600), NOW)
    assert st == "done" and "quiet" in why


def test_classify_service_down():
    st, _ = m.classify(job(service=True, interval=None, pid=None, last_exit=-15), NOW)
    assert st == "error"


def test_classify_service_up():
    assert m.classify(job(service=True, interval=None, pid=77, last_exit=-15), NOW)[0] == "running"


def test_classify_not_loaded():
    st, why = m.classify(job(loaded=False), NOW)
    assert st == "paused" and "not loaded" in why


def test_classify_signal_exit_after_kill_is_error_only_for_scheduled():
    # a scheduled job killed by SIGKILL (-9) is a failure
    assert m.classify(job(last_exit=-9), NOW)[0] == "error"


def test_slurm_rows_parse():
    out = "474|sp3-checks|pompeii|PENDING|0:00|1:00:00|(DependencyNeverSatisfied)\n"
    rows = m.parse_squeue(out)
    assert rows == [{"id": "474", "name": "sp3-checks", "user": "pompeii", "state": "PENDING",
                     "elapsed": "0:00", "limit": "1:00:00", "reason": "(DependencyNeverSatisfied)"}]


def test_slurm_status():
    assert m.slurm_status({"state": "RUNNING", "reason": "node1"}) == "running"
    assert m.slurm_status({"state": "PENDING", "reason": "(Priority)"}) == "queued"
    assert m.slurm_status({"state": "PENDING", "reason": "(DependencyNeverSatisfied)"}) == "blocked"


def test_tracked_job_without_update_for_48h_is_stalled():
    now = 1_000_000.0
    assert m.effective_status({"status": "running", "_mtime": now - 49 * 3600}, now) == "stalled"
    assert m.effective_status({"status": "queued", "_mtime": now - 49 * 3600}, now) == "stalled"


def test_recent_or_terminal_jobs_keep_status():
    now = 1_000_000.0
    assert m.effective_status({"status": "running", "_mtime": now - 3600}, now) == "running"
    assert m.effective_status({"status": "done", "_mtime": now - 99 * 3600}, now) == "done"
    assert m.effective_status({"status": "needs_review", "_mtime": now - 99 * 3600}, now) == "needs_review"
