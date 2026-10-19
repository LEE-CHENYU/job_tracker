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
