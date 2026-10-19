import unittest
from research_sync import workflow_status, notebook_counts, visible_by_default, compact_title, job_heading

class ResearchSyncTests(unittest.TestCase):
    def test_menu_title_stays_short_and_keeps_errors_visible(self):
        jobs = [{'status':'queued'}] * 8
        self.assertEqual(compact_title(jobs), 'Jobs 8')
        self.assertEqual(compact_title(jobs + [{'status':'error'}]), 'Jobs 9!')
    def test_review_heading_never_implies_completion(self):
        job = {'title':'Codex redo', 'status':'needs_review',
               'progress':{'current':49,'total':49,'unit':'responses saved'}}
        text = job_heading(job)
        self.assertIn('needs review', text)
        self.assertIn('49/49 responses saved', text)
        self.assertNotIn('100%', text)
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
