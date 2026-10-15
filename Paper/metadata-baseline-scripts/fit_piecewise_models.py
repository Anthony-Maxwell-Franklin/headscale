#!/usr/bin/env python3
"""Diagnostic sensitivity: integration HHI effects before/after 30 elapsed days.

This is a post-pilot exploratory model, not a causal or confirmatory analysis.
Submission covariates stay fixed; the HHI coefficient can change with PR age.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from statsmodels.duration.hazard_regression import PHReg


def make_intervals(frame, split_days):
    """Create nonoverlapping risk intervals, preserving one outcome per PR."""
    if split_days <= 0:
        raise ValueError('Split time must be positive')
    data=frame.copy()
    data['_subject']=np.arange(len(data))
    early=data.copy()
    early['_entry']=0.
    early['_exit']=np.minimum(data['duration_days'],split_days)
    early['_event']=data['merge_event']*(data['duration_days']<=split_days)
    early['late_hhi_per_01']=0.
    late=data.loc[data['duration_days']>split_days].copy()
    # PHReg admits entry at an event time. The next representable float makes
    # the early interval solely responsible for any event at the split itself.
    late['_entry']=np.nextafter(float(split_days),np.inf)
    late['_exit']=late['duration_days']
    late['_event']=late['merge_event']
    late['late_hhi_per_01']=late['hhi_per_01']
    return pd.concat([early,late],ignore_index=True)


def fit_piecewise(frame, lookback_days=90, split_days=30,exclude_backlog=False):
    if lookback_days <= 0:
        raise ValueError('Lookback window must be positive')
    prefix=f'lag{lookback_days}_'
    data=frame.copy()
    data['hhi_per_01']=data[prefix+'merger_hhi']*10
    data['merges_log2']=np.log2(1+data[prefix+'merges'])
    data['backlog_log2']=np.log2(1+data['backlog_at_submission'])
    experience=np.where(data['prior_author_prs']==0,'new',
                        np.where(data['prior_author_prs']<=4,'returning','established'))
    data['_stratum']=data['prior_integrator_author'].astype(str)+'-'+experience
    columns=['hhi_per_01','late_hhi_per_01','merges_log2','backlog_log2','calendar_years']
    if exclude_backlog:
        columns.remove('backlog_log2')
    required=[c for c in columns if c!='late_hhi_per_01']+['duration_days','merge_event']
    data=data.dropna(subset=required)
    if (data['duration_days']<0).any():
        raise ValueError('Negative duration')
    intervals=make_intervals(data,split_days)
    elapsed_events=dict(through_split=int(intervals.loc[intervals['_entry']==0,'_event'].sum()),
                        after_split=int(intervals.loc[intervals['_entry']>0,'_event'].sum()))
    elapsed_subjects=dict(through_split=len(data),after_split=int((data['duration_days']>split_days).sum()))
    informative=np.zeros(len(intervals),dtype=bool)
    for _,group in intervals.groupby('_stratum'):
        events=np.sort(group.loc[group['_event']==1,'_exit'].unique())
        if not len(events):
            continue
        indices=np.searchsorted(events,group['_entry'].to_numpy(),side='left')
        in_bounds=indices<len(events)
        eligible=np.zeros(len(group),dtype=bool)
        eligible[in_bounds]=events[indices[in_bounds]]<=group['_exit'].to_numpy()[in_bounds]
        informative[group.index.to_numpy()]=eligible
    dropped_intervals=int((~informative).sum())
    intervals=intervals.loc[informative].copy()
    X=intervals[columns]
    if np.linalg.matrix_rank(X.to_numpy())!=len(columns):
        raise ValueError('Rank-deficient time-dependent model')
    model=PHReg(intervals['_exit'],X,status=intervals['_event'],entry=intervals['_entry'],
                strata=intervals['_stratum'],ties='efron')
    result=model.fit(groups=intervals['creation_quarter'].to_numpy())
    covariance=np.asarray(result.cov_params())
    if not np.isfinite(result.params).all() or not np.isfinite(covariance).all():
        raise ValueError('Nonfinite piecewise coefficient or covariance')
    clusters=intervals['creation_quarter'].nunique()
    critical=stats.t.ppf(.975,clusters-1)
    periods=[]
    early_contrast=np.zeros(len(columns));early_contrast[0]=1
    late_contrast=early_contrast.copy();late_contrast[1]=1
    for label,vector in [('through_split',early_contrast),('after_split',late_contrast)]:
        coefficient=float(vector@result.params)
        se=float(np.sqrt(vector@covariance@vector))
        periods.append(dict(period=label,log_hazard_coefficient=coefficient,
                            merge_events_in_period=elapsed_events[label],
                            eligible_subjects_in_period=elapsed_subjects[label],
                            cluster_standard_error=se,hazard_ratio=float(np.exp(coefficient)),
                            lower_95=float(np.exp(coefficient-critical*se)),
                            upper_95=float(np.exp(coefficient+critical*se))))
    return dict(lookback_days=lookback_days,split_days=split_days,
                backlog_approximation_included=not exclude_backlog,
                observations=int(intervals['_subject'].nunique()),interval_rows=len(intervals),
                merge_events=int(intervals['_event'].sum()),quarter_clusters=int(clusters),
                no_event_risk_intervals_excluded=dropped_intervals,
                concentration_effects=periods,log_likelihood=float(result.llf),
                baseline_stratification='prior integration role x prior PR experience (0, 1-4, 5+)',
                interval_method='Quarter-clustered sandwich covariance, Student t critical value',
                limitations=['Post-pilot diagnostic with a 30-day elapsed-time split.',
                             'Other covariates remain proportional within each role/experience stratum.',
                             'Submission-time exposure is fixed; this does not model evolving workload.',
                             'Included backlog is a final-disposition approximation, pending full reopening-history validation.',
                             'Time-dependent hazard ratios are not merge probabilities or causal effects.'])


def main():
    root=Path(__file__).resolve().parents[2]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=root/'Paper/results/pr-census')
    parser.add_argument('--output',type=Path,default=root/'Paper/results/inference')
    parser.add_argument('--lookback-days',type=int,default=90)
    parser.add_argument('--split-days',type=float,default=30)
    args=parser.parse_args()
    coverage=json.loads((args.input/'coverage.json').read_text())
    summary=json.loads((args.input/'summary.json').read_text())
    if not coverage['metadata_complete'] or not summary['metadata_complete']:
        raise ValueError('Complete metadata required')
    if summary['lookback_days']!=args.lookback_days:
        raise ValueError('Feature window mismatch')
    source=args.input/'human_pr_metadata_features.csv'
    frame=pd.read_csv(source)
    result=fit_piecewise(frame,args.lookback_days,args.split_days)
    result['without_backlog_sensitivity']=fit_piecewise(frame,args.lookback_days,args.split_days,exclude_backlog=True)
    result['provenance']=dict(input_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                              script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                              uv_lock_sha256=hashlib.sha256((root/'Paper/uv.lock').read_bytes()).hexdigest())
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'piecewise_merge_model.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
