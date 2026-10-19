import unittest
from research_sync import workflow_status, notebook_counts, visible_by_default

class ResearchSyncTests(unittest.TestCase):
    def test_saved_results_do_not_imply_complete(self):
        self.assertEqual(workflow_status(20, 20, 0), 'needs_review')
    def test_no_worker_proof_means_not_running(self):
        self.assertEqual(workflow_status(0, 20, 0), 'queued')
    def test_verification_required_for_notebook(self):
        rows=[{'status':'completed','generated_reports':['id']}, {'live_report_verified':True}]
        self.assertEqual(notebook_counts(rows), (1,2))
    def test_pending_visible_but_history_hidden(self):
        for status in ['queued','blocked','needs_review','running']:
            self.assertTrue(visible_by_default(status))
        for status in ['done','stale','superseded']:
            self.assertFalse(visible_by_default(status))

if __name__=='__main__': unittest.main()
