import unittest
from research_sync import notebook_output_progress, job_heading

class NotebookOutputs(unittest.TestCase):
    def test_three_roles_count_episodes_not_notebooks(self):
        row={'historical_audio_episodes':[
            {'role':'current','audio_downloaded_validated':True},
            {'role':'history','audio_downloaded_validated':True}],
            'specialist_audio_episodes':[{'role':'specialist','status':'queued'}],
            'generated_reports':[{'id':'r','status':'completed','content_review_status':'failed'}]}
        self.assertEqual(notebook_output_progress([row]), {'audio_done':2,'audio_total':3,'reports_generated':1,'report_total':1})
        job={'title':'Focus Media','telemetry':{'output_progress':notebook_output_progress([row])}}
        self.assertEqual(job_heading(job),'Focus Media · audio 2/3 · report generated')

    def test_submission_is_not_generation(self):
        row={'audio_artifact_id':'pending','live_report_artifact_id':'pending',
             'generated_reports':[{'id':'pending','status':'in_progress'}]}
        self.assertEqual(notebook_output_progress([row])['reports_generated'],0)
        self.assertEqual(job_heading({'title':'Test','telemetry':{'output_progress':notebook_output_progress([row])}}),
                         'Test · audio 0/1 · report pending')

    def test_plan_counts_missing_episodes_without_counting_legacy_audio(self):
        row={'audio_downloaded_validated':True,
             'audio_plan':{'episodes':[{'role':'current'},{'role':'history'},{'role':'specialist'}]},
             'historical_audio_episodes':[{'role':'current','audio_downloaded_validated':True}]}
        self.assertEqual(notebook_output_progress([row])['audio_total'],3)
        self.assertEqual(notebook_output_progress([row])['audio_done'],1)

    def test_single_integrated_and_group_report_counts(self):
        rows=[{'audio_downloaded_validated':True,'live_report_generated':True},{}]
        self.assertEqual(job_heading({'title':'Group','telemetry':{'output_progress':notebook_output_progress(rows)}}),
                         'Group · audio 1/2 · reports 1/2')
