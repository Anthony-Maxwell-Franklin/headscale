#!/usr/bin/env python3
"""Exploratory cause-specific merge associations; require census coverage first."""
import json
import hashlib
import argparse
from pathlib import Path
import numpy as np
import pandas as pd
from scipy import stats
from statsmodels.duration.hazard_regression import PHReg


def fit_model(df,stratified=False,lookback_days=90,exclude_backlog=False):
    """PH coefficients with quarter-clustered uncertainty; no final-size leakage."""
    if lookback_days <= 0:
        raise ValueError('The lookback window must be positive')
    prefix=f'lag{lookback_days}_'
    columns=[prefix+'merger_hhi_per_01',prefix+'merges_log2','backlog_log2',
             'prior_author_prs_log2','prior_integrator_author','calendar_years']
    if exclude_backlog:
        columns.remove('backlog_log2')
    data=df.copy()
    if data['prior_integrator_author'].nunique()<2:
        columns.remove('prior_integrator_author')
    data[prefix+'merger_hhi_per_01']=data[prefix+'merger_hhi']*10
    for source,dest in [(prefix+'merges',prefix+'merges_log2'),('backlog_at_submission','backlog_log2'),
                        ('prior_author_prs','prior_author_prs_log2')]:
        data[dest]=np.log2(1+data[source])
    if stratified:
        experience=np.where(data['prior_author_prs']==0,'new',np.where(data['prior_author_prs']<=4,'returning','established'))
        data['_stratum']=data['prior_integrator_author'].astype(str)+'-'+experience
        columns=[c for c in columns if c not in ('prior_author_prs_log2','prior_integrator_author')]
    data=data.dropna(subset=columns+['duration_days','merge_event'])
    data['_model_duration']=data['duration_days'].clip(lower=1/86400)
    # Subjects censored before the first merge enter no event risk set and make
    # exactly zero contribution to the Cox partial likelihood. PHReg exposes
    # their score residuals as NaN, which otherwise contaminates clustered SEs.
    if stratified:
        events=data.loc[data['merge_event']==1].groupby('_stratum')['_model_duration'].min()
        first_event=data['_stratum'].map(events)
        no_risk=(data['merge_event']==0)&(first_event.isna() | (data['_model_duration']<first_event))
    else:
        first_event=data.loc[data['merge_event']==1,'_model_duration'].min()
        no_risk=(data['merge_event']==0)&(data['_model_duration']<first_event)
    no_risk_count=int(no_risk.sum())
    data=data.loc[~no_risk].copy()
    if len(data)<100 or data['merge_event'].sum()<60:
        raise ValueError('Insufficient complete observations for the prespecified exploratory model')
    X=data[columns]
    if np.linalg.matrix_rank(X.to_numpy())!=len(columns):
        raise ValueError('Rank-deficient feature matrix')
    model=PHReg(data['_model_duration'],X,status=data['merge_event'],ties='efron',
                strata=data['_stratum'] if stratified else None)
    result=model.fit(groups=data['creation_quarter'].to_numpy())
    if not np.all(np.isfinite(result.params)) or not np.all(np.isfinite(result.bse)):
        raise ValueError('Nonfinite PH coefficient or uncertainty')
    residuals=np.asarray(result.schoenfeld_residuals)
    clusters=data['creation_quarter'].nunique()
    critical=stats.t.ppf(.975,clusters-1)
    table=[]
    for i,name in enumerate(columns):
        mask=np.isfinite(residuals[:,i]) & (data['merge_event'].to_numpy()==1)
        correlation=stats.spearmanr(residuals[mask,i],np.log1p(data.loc[mask,'duration_days']))
        table.append(dict(variable=name,log_hazard_coefficient=float(result.params[i]),
                          cluster_standard_error=float(result.bse[i]),hazard_ratio=float(np.exp(result.params[i])),
                          lower_95=float(np.exp(result.params[i]-critical*result.bse[i])),
                          upper_95=float(np.exp(result.params[i]+critical*result.bse[i])),
                          residual_time_spearman=float(correlation.statistic),
                          residual_time_p=float(correlation.pvalue)))
    return dict(observations=len(data),merge_events=int(data['merge_event'].sum()),
                lookback_days=lookback_days,
                backlog_covariate_included=not exclude_backlog,
                censored_before_first_event_excluded=no_risk_count,
                quarter_clusters=data['creation_quarter'].nunique(),coefficients=table,
                baseline_stratification='prior integrator role x prior PR experience (0, 1-4, 5+)' if stratified else 'none',
                condition_number=float(np.linalg.cond(X.to_numpy())),log_likelihood=float(result.llf),
                interval_method='Quarter-clustered sandwich SE, Student t critical value with clusters minus one degrees of freedom',
                diagnostics='Residual-time Spearman checks are exploratory, not a formal global PH test',
                interpretation='Cause-specific merge hazard associations; not causal effects or merge probabilities')


def main():
    root=Path(__file__).resolve().parents[2]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=root/'Paper/results/pr-census')
    parser.add_argument('--output',type=Path,default=root/'Paper/results/inference')
    parser.add_argument('--lookback-days',type=int,default=90)
    args=parser.parse_args()
    directory=args.input
    summary=json.loads((directory/'summary.json').read_text())
    coverage=json.loads((directory/'coverage.json').read_text())
    if not summary['metadata_complete'] or not coverage['metadata_complete']:
        raise ValueError('A partial census cannot supply the planned primary model')
    if coverage.get('timelines_required') and not (coverage.get('timeline_complete') and coverage.get('lifecycle_validated')):
        raise ValueError('Incomplete or anomalous lifecycle history cannot supply the model')
    if summary.get('lookback_days',90)!=args.lookback_days:
        raise ValueError('Feature window differs from the requested model window')
    df=pd.read_csv(directory/'human_pr_metadata_features.csv')
    results={'primary':fit_model(df,lookback_days=args.lookback_days),
             'external_authors_sensitivity':fit_model(df[df['prior_integrator_author']==0],lookback_days=args.lookback_days),
             'role_experience_stratified_sensitivity':fit_model(df,stratified=True,lookback_days=args.lookback_days),
             'without_backlog_sensitivity':fit_model(df,stratified=True,lookback_days=args.lookback_days,exclude_backlog=True),
             'episode_scope':summary.get('model_episode_scope','last recorded disposition'),
             'backlog_reconstructed':summary.get('backlog_reconstructed_from_timelines',False),
             'limitations':['Exploratory model chosen after a motivating pilot; no confirmatory preregistration claim.',
                            'Quarter-clustered uncertainty may be unstable with few clusters; evaluate diagnostics.',
                            'Calendar trend, workload and concentration may be correlated; assess identifiability.',
                            'Observed final PR sizes are deliberately excluded from the primary covariates.',
                            'First disposition terminates the initial episode; later reopenings are separate opportunities.' if summary.get('backlog_reconstructed_from_timelines') else 'Reopening episodes are not reconstructed.',
                            'Backlog uses observed prior lifecycle transitions; deleted history and same-second ordering remain limitations.' if summary.get('backlog_reconstructed_from_timelines') else 'Backlog approximates pending work from last recorded dispositions; closed/reopened gaps are not reconstructed.',
                            'Competing nonmerge closure ends the cause-specific risk set; effects are not cumulative incidence.']}
    results['provenance']={'input_sha256':hashlib.sha256((directory/'human_pr_metadata_features.csv').read_bytes()).hexdigest(),
                           'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                           'uv_lock_sha256':hashlib.sha256((root/'Paper/uv.lock').read_bytes()).hexdigest()}
    output=args.output;output.mkdir(parents=True,exist_ok=True)
    (output/'cause_specific_merge_models.json').write_text(json.dumps(results,indent=2)+'\n')
    print(json.dumps(results,indent=2))


if __name__=='__main__':
    main()
