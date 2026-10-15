import unittest
from research_metrics import km_curve, linked_paths, preserve_coding
from analyze_process import competing_disposition, first_human_events, response_observation, response_incidence
from analyze_census import prior_features


class ResearchTests(unittest.TestCase):
    def test_censoring_ties_and_loglog_intervals(self):
        curve=km_curve([(1,True),(2,True),(2,False)])
        self.assertAlmostEqual(curve[1]['probability_still_open'],2/3)
        self.assertAlmostEqual(curve[2]['probability_still_open'],1/3)
        self.assertEqual(curve[2]['at_risk'],2)
        self.assertLess(curve[2]['lower_95'],1/3)
        self.assertGreater(curve[2]['upper_95'],1/3)

    def test_all_censored_is_not_zero_latency(self):
        curve=km_curve([(5,False),(10,False)])
        self.assertTrue(all(r['probability_still_open']==1 for r in curve))
        self.assertRaises(ValueError,km_curve,[(-1,True)])

    def test_competing_nonmerge_closure_is_not_merge_censor(self):
        curve=competing_disposition([(1,'merge'),(2,'nonmerge_close'),(3,'censor')])
        self.assertAlmostEqual(curve[-1]['merge_cif'],1/3)
        self.assertAlmostEqual(curve[-1]['nonmerge_close_cif'],1/3)
        self.assertAlmostEqual(curve[-1]['undisposed_probability'],1/3)

    def test_rename_links_transitive(self):
        result=linked_paths([{'old_path':'a.go','new_path':'b.go'},
                             {'old_path':'b.go','new_path':'sub/c.go'}])
        self.assertEqual(result['a.go'],result['sub/c.go'])

    def test_self_and_bot_comments_are_not_human_responses(self):
        detail={'user':{'login':'author'}}
        comments=[{'user':{'login':'author','type':'User'},'created_at':'2026-01-01T00:00:01Z'},
                  {'user':{'login':'ci[bot]','type':'Bot'},'created_at':'2026-01-01T00:00:02Z'},
                  {'user':{'login':'reviewer','type':'User'},'created_at':'2026-01-01T00:00:03Z'}]
        result=first_human_events(detail,[],comments,[])
        self.assertEqual(len(result),1)
        self.assertEqual(result[0][2],'reviewer')

    def test_response_opportunity_ends_at_merge(self):
        detail={'created_at':'2026-01-01T00:00:00Z','merged_at':'2026-01-02T00:00:00Z',
                'closed_at':'2026-01-02T00:00:00Z'}
        created=1767225600.
        first,end,outcome,duration=response_observation(detail,[(created+3*86400,'issue_comment','reviewer')],created+90*86400)
        self.assertIsNone(first)
        self.assertEqual(outcome,'merge_without_response')
        self.assertEqual(duration,1.)

    def test_first_episode_does_not_include_post_close_reply(self):
        detail={'created_at':'2026-01-01T00:00:00Z','merged_at':'2026-01-04T00:00:00Z',
                'closed_at':'2026-01-04T00:00:00Z'}
        start=1767225600.
        events=[{'event':'closed','created_at':'2026-01-02T00:00:00Z'},
                {'event':'reopened','created_at':'2026-01-03T00:00:00Z'},
                {'event':'merged','created_at':'2026-01-04T00:00:00Z'}]
        first,end,outcome,duration=response_observation(detail,[(start+2*86400,'review','reviewer')],start+10*86400,events)
        self.assertIsNone(first)
        self.assertEqual(outcome,'nonmerge_close_without_response')
        self.assertEqual(duration,1.)

    def test_response_competing_incidence_mass(self):
        curve=response_incidence([(1,'response'),(2,'merge_without_response'),
                                  (2,'nonmerge_close_without_response'),(3,'censor')])
        end=curve[-1]
        self.assertAlmostEqual(sum(end[k] for k in ['response_cif','merge_without_response_cif',
                                                  'nonmerge_close_without_response_cif','unresolved_probability']),1.)

    def test_submission_features_do_not_use_future_merges(self):
        records=[{'created_ts':1.,'merged_ts':5.,'terminal_ts':5.,'author':'alice','merged_by':'maintainer'},
                 {'created_ts':2.,'merged_ts':20.,'terminal_ts':20.,'author':'bob','merged_by':'alice'},
                 {'created_ts':10.,'merged_ts':30.,'terminal_ts':30.,'author':'alice','merged_by':'alice'}]
        features=prior_features(records[-1],records)
        self.assertEqual(features['lag90_merges'],1)
        self.assertEqual(features['backlog_at_submission'],1)
        self.assertEqual(features['prior_author_prs'],1)
        self.assertEqual(features['prior_integrator_author'],0)

    def test_lookback_boundaries_and_horizon_names(self):
        day=86400
        target={'created_ts':200*day,'author':'newcomer'}
        records=[{'created_ts':100*day,'merged_ts':170*day,'terminal_ts':170*day,
                  'author':'alice','merged_by':'maintainer-a'},
                 {'created_ts':50*day,'merged_ts':110*day,'terminal_ts':110*day,
                  'author':'bob','merged_by':'maintainer-b'},
                 {'created_ts':190*day,'merged_ts':200*day,'terminal_ts':200*day,
                  'author':'carol','merged_by':'maintainer-c'}]
        short=prior_features(target,records,30)
        primary=prior_features(target,records,90)
        self.assertEqual(short['lag30_merges'],1)
        self.assertEqual(short['lag30_merger_hhi'],1)
        self.assertEqual(primary['lag90_merges'],2)
        self.assertEqual(primary['lag90_merger_hhi'],.5)
        self.assertNotIn('lag90_merger_hhi',short)
        self.assertRaises(ValueError,prior_features,target,records,0)

    def test_backlog_uses_reconstructed_history_instead_of_future_final_closure(self):
        prior=dict(created_ts=1.,merged_ts=30.,terminal_ts=30.,author='prior',merged_by='integrator',
                   history=dict(transition_times=[5.,20.,30.],transitions=[
                       dict(time=5.,open=False),dict(time=20.,open=True),dict(time=30.,open=False)]))
        target=dict(created_ts=10.,author='newcomer')
        self.assertEqual(prior_features(target,[prior])['backlog_at_submission'],0)
        self.assertEqual(prior_features(dict(target,created_ts=21.),[prior])['backlog_at_submission'],1)

    def test_rerun_preserves_human_annotations_and_rejects_changed_sample(self):
        import tempfile,csv
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'coding.csv'
            existing={'number':123,'stratum':'2025 closed','inclusion_probability':.1,
                      'coder_1_outcome':'duplicate_or_superseded','notes':'Reviewed evidence'}
            with path.open('w') as f:
                writer=csv.DictWriter(f,fieldnames=list(existing));writer.writeheader();writer.writerow(existing)
            regenerated=dict(existing,coder_1_outcome='',notes='')
            result=preserve_coding(path,[regenerated])
            self.assertEqual(result[0]['notes'],'Reviewed evidence')
            self.assertEqual(result[0]['coder_1_outcome'],'duplicate_or_superseded')
            self.assertRaises(ValueError,preserve_coding,path,[dict(regenerated,number=124)])


if __name__=='__main__':
    unittest.main()
