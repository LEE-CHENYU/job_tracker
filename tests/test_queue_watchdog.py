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
