import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
import menubar

class MenuTests(unittest.TestCase):
    def job(self, path, name='Claude backfill'):
        return {'id':name, 'title':name, 'status':'needs_review',
                'purpose':'Repeated generic description',
                'progress':{'current':120,'total':165,'unit':'passes saved'},
                'details':['completed_pending_reconciliation: 120 companies',
                           'needs_attention: 8 companies','pending: 37 companies',
                           'Reconcile saved quotes and rationale.'],
                'telemetry':{'completed_pending_reconciliation':120,'needs_attention':8,
                             'pending':37,'source_path':'/very/long/path',
                             'execution_status':'not_verified_running'},
                '_path':str(path),'log_path':str(path)}

    def test_one_row_per_job_and_progress_only_once(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'job.json';path.write_text('{}')
            items=menubar.JobTrackerApp._render_job(None,self.job(path),0)
            self.assertEqual(len(items),1)
            row=items[0]
            self.assertIn('120/165 passes saved',row.title)
            children=list(row.keys())
            text='\n'.join(children)
            self.assertNotIn('120',text)
            self.assertNotIn('ETA',text)
            self.assertNotIn('source_path',text)
            self.assertNotIn('Repeated generic',text)
            self.assertIn('Open details',children)
            self.assertIn('8 need attention',text)
            self.assertIn('37 pending',text)
            self.assertLessEqual(len(children),7)

    def test_repeated_action_titles_are_scoped_to_each_job(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'job.json';path.write_text('{}')
            menu=menubar.rumps.MenuItem("root")
            for name in ('First','Second'):
                menu.add(menubar.JobTrackerApp._render_job(None,self.job(path,name),0)[0])
            self.assertEqual(len(menu),2)
            for item in menu.values():
                self.assertIn('Open details',item)

    def test_unknown_eta_is_hidden_but_measured_running_eta_is_shown(self):
        job={'id':'work','title':'Work','status':'running',
             'progress':{'current':1,'total':10,'rate_per_sec':1}}
        item=menubar.JobTrackerApp._render_job(None,job,0)[0]
        self.assertTrue(any('ETA: 9s' in key for key in item.keys()))

if __name__=='__main__': unittest.main()
