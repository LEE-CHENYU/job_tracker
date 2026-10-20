import unittest
from unittest.mock import patch
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from research_sync import workflow_status, notebook_counts, visible_by_default, compact_title, job_heading

class ResearchSyncTests(unittest.TestCase):
    def test_qc_refresh_survives_unrelated_adapter_failure(self):
        import research_sync as sync
        calls=[]
        def fail():
            calls.append('audio')
            raise ValueError('audio unavailable')
        with TemporaryDirectory() as tmp, patch.object(sync,'HERE',Path(tmp)), \
             patch.object(sync,'save'), patch.object(sync,'sync_audio_qc',fail), \
             patch.object(sync,'sync_notebooks',lambda:calls.append('notebooks')), \
             patch.object(sync,'sync_screens',lambda:calls.append('screening')), \
             patch.object(sync,'sync_research_qc',lambda:calls.append('central_qc')):
            with self.assertRaises(RuntimeError):
                sync.main()
        self.assertEqual(calls,['audio','notebooks','screening','central_qc'])
    def test_menu_title_stays_short_and_keeps_errors_visible(self):
        jobs = [{'status':'queued'}] * 8
        self.assertEqual(compact_title(jobs), 'Jobs 8')
        self.assertEqual(compact_title(jobs + [{'status':'error'}]), 'Jobs 9!')
    def test_review_heading_never_implies_completion(self):
        job = {'title':'Codex redo', 'status':'needs_review',
               'progress':{'current':49,'total':49,'unit':'responses saved'}}
        text = job_heading(job)
        self.assertEqual(text, 'Codex redo · 49/49 responses saved')
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


    def test_heading_uses_dots_preserving_word_hyphens(self):
        job={'title':'Screening — follow-through - batch','status':'queued',
             'progress':{'current':1,'total':2,'unit':'results'},
             'telemetry':{'execution_status':'waiting'}}
        text=job_heading(job)
        self.assertNotIn('—',text)
        self.assertNotIn(' - ',text)
        self.assertIn('follow-through',text)
        self.assertIn('1/2 results',text)


    def test_notebook_heading_shows_outputs_and_acceptance_separately(self):
        job={'title':'Notebooks','progress':{'current':0,'total':21,'unit':'complete notebooks'},
             'telemetry':{'completion_dimensions':{'total':21,'audio_integrity':21,'transcripts':18,'content_accepted':0,'native_reports':0,'complete':0}}}
        self.assertEqual(job_heading(job),'Notebooks · audio 21/21 · transcripts 18/21 · accepted 0/21')

    def test_reference_display_reads_passes_without_promoting_acceptance(self):
        from research_sync import with_display_progress
        with TemporaryDirectory() as tmp:
            source=Path(tmp)/'run.json'
            data={'companies':[{'claude_pass_status':'COMPLETE_RAW_FROZEN','codex_pass_status':'PENDING'},
                               {'claude_pass_status':'PENDING','codex_pass_status':'PENDING'}]}
            source.write_text(json.dumps(data))
            job={'id':'research_intake_reference02','title':'New batch','log_path':str(source),
                 'progress':{'current':0,'total':2,'unit':'reviewed companies'}}
            result=with_display_progress(job)
            self.assertEqual(job_heading(result),'New batch · Claude 1/2 · Codex 0/2 · reviewed 0/2')
            self.assertNotIn('stage_progress',job)
            self.assertEqual(json.loads(source.read_text()),data)
            source.unlink()
            self.assertIn('0/2 reviewed companies',job_heading(with_display_progress(job)))

if __name__=='__main__': unittest.main()
