import importlib.util
import unittest

@unittest.skipUnless(importlib.util.find_spec('numpy') and importlib.util.find_spec('statsmodels'),
                     'Statistical model verification requires the locked uv environment')
class InferenceTests(unittest.TestCase):
    def test_short_close_reopen_sensitivity_preserves_cutoff_and_merge_ties(self):
        from check_model_robustness import coalesced_disposition
        def history(*events):
            return dict(transitions=[dict(event=kind,time=timestamp) for kind,timestamp in events])
        short=history(('closed',10),('reopened',12),('merged',100))
        self.assertEqual(coalesced_disposition(short,1000,2),('merge',100,1,False))
        self.assertEqual(coalesced_disposition(short,1000,1),('nonmerge_close',10,0,False))
        simultaneous=history(('closed',100),('merged',100))
        self.assertEqual(coalesced_disposition(simultaneous,1000),('merge',100,0,False))
        future=history(('closed',990),('reopened',1002),('merged',1100))
        self.assertEqual(coalesced_disposition(future,1000),('censor',1000,0,True))
        at_boundary=history(('closed',700))
        self.assertEqual(coalesced_disposition(at_boundary,1000),('nonmerge_close',700,0,False))

    def test_piecewise_risk_intervals_preserve_events_and_recover_age_effects(self):
        import numpy as np
        import pandas as pd
        from fit_piecewise_models import fit_piecewise,make_intervals
        rng=np.random.default_rng(20261014)
        n=3000
        hhi=rng.uniform(.2,.8,n)
        early_beta=.08;late_beta=-.06;split=.5
        early_rate=np.exp(early_beta*hhi*10)
        late_rate=np.exp(late_beta*hhi*10)
        draw=rng.exponential(1,n)
        event=np.where(draw<=early_rate*split,draw/early_rate,
                       split+(draw-early_rate*split)/late_rate)
        censor=rng.exponential(3,n)
        frame=pd.DataFrame(dict(lag90_merger_hhi=hhi,lag90_merges=rng.poisson(20,n),
                                backlog_at_submission=rng.poisson(30,n),
                                prior_author_prs=rng.poisson(4,n),prior_integrator_author=np.zeros(n),
                                calendar_years=rng.uniform(0,6,n),duration_days=np.minimum(event,censor),
                                merge_event=(event<=censor).astype(int),
                                creation_quarter=[f'quarter-{i%24}' for i in range(n)]))
        result=fit_piecewise(frame,split_days=split)
        effects=result['concentration_effects']
        self.assertAlmostEqual(effects[0]['log_hazard_coefficient'],early_beta,delta=.04)
        self.assertAlmostEqual(effects[1]['log_hazard_coefficient'],late_beta,delta=.04)
        self.assertEqual(result['merge_events'],int(frame['merge_event'].sum()))
        boundary=pd.DataFrame(dict(duration_days=[split,split+.1],merge_event=[1,1],hhi_per_01=[4.,5.]))
        intervals=make_intervals(boundary,split)
        self.assertEqual(intervals['_event'].sum(),2)
        self.assertEqual(len(intervals),3)
        self.assertEqual(intervals.loc[intervals['_entry']==0,'_event'].sum(),1)
        self.assertTrue((intervals.loc[intervals['_entry']>0,'_entry']>split).all())
        missing_backlog=frame.copy();missing_backlog['backlog_at_submission']=np.nan
        no_backlog=fit_piecewise(missing_backlog,split_days=split,exclude_backlog=True)
        self.assertEqual(no_backlog['merge_events'],int(frame['merge_event'].sum()))
        self.assertFalse(no_backlog['backlog_covariate_included'])

    def test_known_hazard_and_constant_subset_covariate(self):
        import numpy as np
        import pandas as pd
        from fit_maintenance_models import fit_model
        rng=np.random.default_rng(20261013)
        n=1800
        hhi=rng.uniform(.2,.8,n)
        true_log_hr_per_01=.08
        event=rng.exponential(1/np.exp(true_log_hr_per_01*hhi*10))
        censor=rng.exponential(3,n)
        df=pd.DataFrame(dict(lag90_merger_hhi=hhi,lag90_merges=rng.poisson(20,n),
                              backlog_at_submission=rng.poisson(30,n),prior_author_prs=rng.poisson(4,n),
                              prior_integrator_author=np.zeros(n),calendar_years=rng.uniform(0,6,n),
                              duration_days=np.minimum(event,censor),merge_event=(event<=censor).astype(int),
                              creation_quarter=[f'cluster-{i%24}' for i in range(n)]))
        result=fit_model(df)
        coefficient=next(r for r in result['coefficients'] if r['variable']=='lag90_merger_hhi_per_01')
        self.assertAlmostEqual(coefficient['log_hazard_coefficient'],true_log_hr_per_01,delta=.04)
        self.assertTrue(all(np.isfinite(r['cluster_standard_error']) for r in result['coefficients']))
        self.assertNotIn('prior_integrator_author',[r['variable'] for r in result['coefficients']])
        missing_backlog=df.copy();missing_backlog['backlog_at_submission']=np.nan
        no_backlog=fit_model(missing_backlog,exclude_backlog=True)
        self.assertNotIn('backlog_log2',[r['variable'] for r in no_backlog['coefficients']])
        self.assertEqual(no_backlog['observations'],n)
        early=df.iloc[:3].copy()
        early['duration_days']=0
        early['merge_event']=0
        supplemented=fit_model(pd.concat([df,early],ignore_index=True))
        self.assertEqual(supplemented['censored_before_first_event_excluded'],3)
        np.testing.assert_allclose([r['log_hazard_coefficient'] for r in result['coefficients']],
                                   [r['log_hazard_coefficient'] for r in supplemented['coefficients']],atol=1e-10)


if __name__=='__main__':
    unittest.main()
