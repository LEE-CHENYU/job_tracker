import unittest
from datetime import datetime, timezone, timedelta
from research_sync import worker_activity, job_heading

class WorkerActivityTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,10,19,tzinfo=timezone.utc)
        self.worker={'pid':123,'heartbeat':self.now.isoformat(),'status':'running','current_job_id':'research_screen_claude'}
    def activity(self,w=None,alive=True,enabled=True,job='research_screen_claude'):
        return worker_activity(job,self.worker if w is None else w,enabled,alive,self.now)
    def test_missing_worker_is_stalled_not_needs_review(self):
        self.assertEqual(self.activity({},False)[0],'stalled')
    def test_dead_pid_is_stalled_even_with_fresh_heartbeat(self):
        self.assertEqual(self.activity(alive=False)[0],'stalled')
    def test_stale_live_worker_flagged(self):
        self.worker['heartbeat']=(self.now-timedelta(minutes=31)).isoformat()
        self.assertEqual(self.activity()[0],'stalled')
    def test_live_current_job_and_other_queue_distinguished(self):
        self.assertEqual(self.activity()[0],'working')
        self.assertEqual(self.activity(job='research_screen_codex49')[0],'waiting')
    def test_disabled_is_paused(self):
        self.assertEqual(self.activity(enabled=False)[0],'paused')
    def test_backoff_not_stalled(self):
        self.worker.update(status='blocked',retry_after=(self.now+timedelta(hours=1)).isoformat())
        self.assertEqual(self.activity(alive=False)[0],'blocked')
    def test_bad_heartbeat_not_live(self):
        self.worker['heartbeat']='garbage'
        self.assertEqual(self.activity()[0],'stalled')
    def test_heading_leaves_status_to_section(self):
        text=job_heading({'title':'Codex','status':'needs_review','telemetry':{'execution_status':'stalled'}})
        self.assertEqual(text,'Codex')
