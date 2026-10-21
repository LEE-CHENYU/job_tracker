import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
import menubar
from unittest.mock import patch
from types import SimpleNamespace

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
            self.assertEqual(row.title,'⚪ Claude backfill · 120/165 passes saved')
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


    def test_menu_groups_by_execution_status_without_dash_separators(self):
        jobs=[{'id':'queued','title':'Queued job','status':'queued'},
              {'id':'working','title':'Working job','status':'needs_review', 'telemetry':{'execution_status':'working'}},
              {'id':'blocked','title':'Blocked job','status':'queued', 'telemetry':{'execution_status':'blocked'}}]
        app=SimpleNamespace(show_all=False, _on_toggle_filter=lambda _:None, _on_refresh=lambda _:None,
                            _render_job=lambda j,i:menubar.JobTrackerApp._render_job(None,j,i))
        with patch.object(menubar,'_read_jobs',return_value=jobs):
            items=menubar.JobTrackerApp._build_items(app)
        titles=[item.title for item in items]
        for heading in ['Working (1)','Blocked (1)','Queued (1)']:
            self.assertIn(heading,titles)
        self.assertLess(titles.index('Blocked (1)'),titles.index('Working (1)'))
        self.assertTrue(any('Menu refreshed' in title and '30s' in title for title in titles))
        self.assertNotIn(menubar.rumps.separator,items)
        for item in items:
            self.assertNotIn(menubar.rumps.separator,list(item.keys()))

    def test_unknown_display_values_do_not_use_dash_placeholders(self):
        self.assertEqual(menubar._human_eta(10,0),'Not available')
        self.assertEqual(menubar._format_age(None),'Not available')


    def test_project_color_is_stable_across_statuses(self):
        for status in ['running','blocked','needs_review','queued']:
            screen={'id':'research_screen_claude','title':'Screening · Claude','status':status}
            audio={'id':'research_transcription','title':'Notebook audio · Whisper','status':status}
            self.assertTrue(menubar.JobTrackerApp._render_job(None,screen,0)[0].title.startswith('🔴 '))
            self.assertTrue(menubar.JobTrackerApp._render_job(None,audio,0)[0].title.startswith('🔵 '))
        for name in ['research_audio_qc','research_notebooks_21','research_notebook_030000_KS']:
            self.assertEqual(menubar.project_dot({'id':name}), '🔵')
        self.assertEqual(menubar.project_dot({'id':'research_specialists_paired'}), '🔴')

if __name__=='__main__': unittest.main()

class BacklogTests(unittest.TestCase):
    def test_backlog_collapses_but_active_and_stalled_stay_individual(self):
        def job(key, activity, done=0):
            return {'id':'research_notebook_'+key, 'title':'NotebookLM — '+key,
                    'status':'queued','_path':__file__,'telemetry':{'execution_status':activity,
                    'output_progress':{'audio_done':done,'audio_total':1,'reports_generated':done,'report_total':1}}}
        jobs=[job('Active','working'),job('Stalled','stalled'),job('Pending','waiting'),job('Generated','waiting',1)]
        app=SimpleNamespace(show_all=False,_on_toggle_filter=lambda _:None,_on_refresh=lambda _:None,
            _render_job=lambda j,i:menubar.JobTrackerApp._render_job(None,j,i))
        with patch.object(menubar,'_read_jobs',return_value=jobs):
            items=menubar.JobTrackerApp._build_items(app)
        titles=[x.title for x in items]
        self.assertTrue(any('Active' in t for t in titles))
        self.assertTrue(any('Stalled ·' in t for t in titles))
        self.assertFalse(any('NotebookLM · Pending ·' in t or 'NotebookLM · Generated ·' in t for t in titles))
        groups=[x for x in items if 'Waiting notebooks (1)' in x.title or 'Generated · QC backlog (1)' in x.title]
        self.assertEqual(len(groups),2)
        self.assertEqual(sum(len(g) for g in groups),2)
        for g in groups:
            self.assertIn('Open details',next(iter(g.values())))

    def test_short_name_keeps_progress_and_does_not_mutate_source(self):
        title='NotebookLM — Ayvens (société anonyme; formerly ALD S.A.); registered office '+('address '*30)
        job={'id':'research_notebook_AYV_PA','title':title,'status':'queued','progress':{'current':0,'total':1,'unit':'reports'}}
        row=menubar.JobTrackerApp._render_job(None,job,0)[0]
        self.assertIn('Ayvens',row.title)
        self.assertIn('0/1 reports',row.title)
        self.assertLess(len(row.title),90)
        self.assertEqual(job['title'],title)

    def test_show_all_reveals_hidden_jobs_without_expanding_backlog(self):
        jobs=[{'id':'research_notebook_pending','title':'NotebookLM — Pending',
               'status':'queued','telemetry':{'execution_status':'waiting'}},
              {'id':'research_notebook_generated','title':'NotebookLM — Generated',
               'status':'queued','telemetry':{'execution_status':'waiting',
                 'output_progress':{'audio_done':1,'audio_total':1,'reports_generated':1,'report_total':1}}},
              {'id':'archived','title':'Archived job','status':'done'}]
        app=SimpleNamespace(show_all=True,_on_toggle_filter=lambda _:None,_on_refresh=lambda _:None,
            _render_job=lambda j,i:menubar.JobTrackerApp._render_job(None,j,i))
        with patch.object(menubar,'_read_jobs',return_value=jobs):
            items=menubar.JobTrackerApp._build_items(app)
        titles=[item.title for item in items]
        self.assertIn('🔵 Waiting notebooks (1)',titles)
        self.assertIn('🔵 Generated · QC backlog (1)',titles)
        self.assertFalse(any('NotebookLM · Pending' in t or 'NotebookLM · Generated' in t for t in titles))
        self.assertTrue(any('Archived job' in t for t in titles))
