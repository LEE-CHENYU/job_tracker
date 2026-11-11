import unittest
from queue_watchdog import decide, backoff_seconds, substantive_event

class WatchdogTests(unittest.TestCase):
 def test_disabled_never_restarted(self):
  self.assertEqual(decide(False, True, True, 5000, 0, 6000, False),'disabled')
 def test_live_but_stalled_is_stopped(self):
  self.assertEqual(decide(True, True, True, 5000, 0, 6000, False),'recover')
 def test_long_call_with_recent_progress_is_not_stopped(self):
  self.assertEqual(decide(True,True,True,100,0,6000,False),'healthy')
 def test_pid_mismatch_not_killed(self):
  self.assertEqual(decide(True,True,False,5000,0,6000,False),'identity_mismatch')
 def test_backoff_holds_unloaded_job(self):
  self.assertEqual(decide(True,False,False,5000,7000,6000,True),'backoff')
 def test_recovery_resumes_when_due(self):
  self.assertEqual(decide(True,False,False,5000,5000,6000,True),'resume')
 def test_normal_scheduler_gap_is_not_failure(self):
  self.assertEqual(decide(True,False,False,100,0,6000,False),'scheduled')
 def test_backoff_bounded(self):
  self.assertEqual([backoff_seconds(n) for n in [1,2,3,100]],[60,120,240,3600])
 def test_network_errors_do_not_count_as_progress(self):
  self.assertFalse(substantive_event({'type':'error','message':'Reconnecting'}))
  self.assertTrue(substantive_event({'type':'item.completed','item':{'type':'command_execution'}}))

class RecoveryVisibilityTests(unittest.TestCase):
 def test_backoff_overlay_preserves_saved_progress(self):
  from research_sync import recovery_activity
  self.assertEqual(recovery_activity({'recovering':True,'retry_at':200,'action':'network_backoff'},100)[0],'blocked')
 def test_recovered_no_overlay(self):
  from research_sync import recovery_activity
  self.assertIsNone(recovery_activity({'recovering':False},100))

class RecoveryIntegrationTests(unittest.TestCase):
 def test_stale_worker_archived_before_unload_and_not_restarted_early(self):
  import json
  from pathlib import Path
  from tempfile import TemporaryDirectory
  from unittest.mock import patch
  from types import SimpleNamespace
  import queue_watchdog as q
  with TemporaryDirectory() as tmp:
   root=Path(tmp); folder=root/'docs/research_screening/runner';folder.mkdir(parents=True)
   state=root/'state';state.mkdir()
   (folder/'control.json').write_text('{"enabled":true}')
   (folder/'status.json').write_text('{"pid":123,"heartbeat":"1970-01-01T00:00:00+00:00","gate":"remote_request_uncertain"}')
   calls=[]
   def cmd(args):
    calls.append(args)
    if args[:2]==['launchctl','print']:
     return SimpleNamespace(stdout=' pid = 123\n',stderr='',returncode=0)
    if args[0]=='ps':
     return SimpleNamespace(stdout='codex exec Read docs/research_screening/runner/worker_prompt.md',returncode=0)
    self.assertTrue(list(state.glob('*-checkpoint.json')))
    return SimpleNamespace(stdout='',stderr='',returncode=0)
   with patch.object(q,'ROOT',root),patch.object(q,'STATE',state),patch.object(q,'command',cmd),patch.object(q.time,'time',return_value=6000):
    q.run_one('screening-queue','research_screening')
   receipt=json.loads((state/'screening-queue.json').read_text())
   self.assertTrue(receipt['recovering'])
   self.assertEqual(receipt['retry_at'],6060)
   self.assertTrue(any(c[:2]==['launchctl','bootout'] for c in calls))
   self.assertFalse(any(c[:2]==['launchctl','bootstrap'] for c in calls))


class IdleAndDailyTests(unittest.TestCase):
 def test_enabled_but_idle_over_24h_alerts(self):
  self.assertEqual(decide(True,False,False,90000,0,100000,False),'idle_alert')
 def test_disabled_idle_never_alerts(self):
  self.assertEqual(decide(False,False,False,90000,0,100000,False),'disabled')
 def test_retired_queue_not_supervised(self):
  import queue_watchdog as q
  self.assertNotIn('notebook-queue',q.JOBS)
  self.assertIn('notebook-queue',q.RETIRED)
 def test_daily_disabled(self):
  from queue_watchdog import decide_daily
  self.assertEqual(decide_daily(False,False,'quota_blocked',10,False,0),'disabled')
 def test_daily_unloaded_service_is_reloaded(self):
  from queue_watchdog import decide_daily
  self.assertEqual(decide_daily(True,False,'quota_blocked',10,False,0),'reload')
 def test_daily_running_worker_healthy(self):
  from queue_watchdog import decide_daily
  self.assertEqual(decide_daily(True,True,'running',20000,True,0),'healthy')
 def test_daily_running_but_worker_dead_is_crashed(self):
  from queue_watchdog import decide_daily
  self.assertEqual(decide_daily(True,True,'running',3600,False,0),'crashed')
 def test_daily_dead_worker_grace(self):
  from queue_watchdog import decide_daily
  self.assertEqual(decide_daily(True,True,'running',600,False,0),'healthy')
 def test_daily_blocked_over_24h_alerts(self):
  from queue_watchdog import decide_daily
  self.assertEqual(decide_daily(True,True,'quota_blocked',60,False,90000),'blocked_long')
 def test_daily_short_block_ok(self):
  from queue_watchdog import decide_daily
  self.assertEqual(decide_daily(True,True,'quota_blocked',60,False,3600),'healthy')
 def test_daily_silent_over_24h(self):
  from queue_watchdog import decide_daily
  self.assertEqual(decide_daily(True,True,'worker_finished',90000,False,0),'silent')
 def test_alert_actions(self):
  from queue_watchdog import ALERT_ACTIONS
  for a in ['idle_alert','crashed','blocked_long','silent','reload_failed','stop_failed','resume_failed']:
   self.assertIn(a,ALERT_ACTIONS)
  self.assertNotIn('healthy',ALERT_ACTIONS)
