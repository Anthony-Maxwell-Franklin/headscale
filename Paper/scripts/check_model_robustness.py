#!/usr/bin/env python3
"""Post-pilot endpoint-definition and quarter-influence diagnostics.

Covariates keep the complete observed history. Dropping a quarter removes target
observations from fitting; it is neither cross-validation nor a counterfactual.
"""
import hashlib
import json
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from analyze import parse_time,write_csv
from analyze_process import human
from collect_process import load_endpoint
from fit_maintenance_models import fit_model
from fit_piecewise_models import fit_piecewise
from pr_history import reconstruct


def coalesced_disposition(history,cutoff,grace_seconds=300):
    """Ignore a closure followed by reopening within the specified short window.

    A close too near the cutoff to observe the complete grace window stays
    censored. Merge/close at the same second is classified as merge.
    """
    if grace_seconds<0:
        raise ValueError('Grace window must be nonnegative')
    transitions=[e for e in history['transitions'] if e['time']<=cutoff];ignored=0
    for index,event in enumerate(transitions):
        if event['event'] not in ('closed','merged'):continue
        timestamp=event['time']
        if event['event']=='merged' or any(e['event']=='merged' and e['time']==timestamp for e in transitions):
            return 'merge',timestamp,ignored,False
        following=next((e for e in transitions[index+1:] if e['event'] in ('reopened','merged')),None)
        if following is not None and following['event']=='reopened' and following['time']-timestamp<=grace_seconds:
            ignored+=1;continue
        if cutoff-timestamp<grace_seconds:
            return 'censor',cutoff,ignored,True
        return 'nonmerge_close',timestamp,ignored,False
    return 'censor',cutoff,ignored,False


def concentration_effect(result):
    return next(r for r in result['coefficients'] if r['variable']=='lag90_merger_hhi_per_01')


def concise_effect(effect):
    return {k:effect[k] for k in ['hazard_ratio','lower_95','upper_95']}


def main():
    root=Path(__file__).resolve().parents[2]
    raw=root/'Paper/data/pr-census-2026-10-13'
    directory=root/'Paper/results/pr-census'
    output=root/'Paper/results/inference'
    coverage=json.loads((directory/'coverage.json').read_text())
    if not coverage.get('lifecycle_validated'):
        raise ValueError('Validated lifecycle census required')
    feature_path=directory/'human_pr_metadata_features.csv'
    original=pd.read_csv(feature_path)
    coalesced=original.copy();lookup={int(n):i for i,n in enumerate(coalesced['number'])}
    status=json.loads((raw/'pr_list/coverage.json').read_text())
    cutoff=max(parse_time(p['fetched_at_utc']) for p in status['pages'])
    gaps=[];modified=[];coalesced_closures=0;cutoff_ambiguous=0
    for pr in load_endpoint(raw/'pr_list'):
        folder=raw/'pulls'/str(pr['number'])
        detail=load_endpoint(folder/'detail')
        if not human(detail['user']):continue
        history=reconstruct(detail,load_endpoint(folder/'timeline'),cutoff)
        if history['anomalies']:raise ValueError('Lifecycle anomaly')
        for index,event in enumerate(history['transitions']):
            if event['event']!='closed':continue
            following=next((e for e in history['transitions'][index+1:] if e['event'] in ('reopened','merged')),None)
            if following and following['event']=='reopened':
                gaps.append(dict(number=detail['number'],gap_seconds=following['time']-event['time']))
        outcome,end,ignored,incomplete=coalesced_disposition(history,cutoff)
        index=lookup[detail['number']]
        duration=(end-parse_time(detail['created_at']))/86400
        if outcome!=coalesced.loc[index,'outcome'] or abs(duration-coalesced.loc[index,'duration_days'])>1e-10:
            modified.append(dict(number=detail['number'],strict_outcome=coalesced.loc[index,'outcome'],
                                  coalesced_outcome=outcome,ignored_short_closures=ignored))
        coalesced.loc[index,['outcome','merge_event','duration_days']]=[outcome,int(outcome=='merge'),duration]
        coalesced_closures+=ignored;cutoff_ambiguous+=int(incomplete)
    coalesced.to_csv(output/'coalesced_episode_features.csv',index=False)
    write_csv(output/'close_reopen_gaps.csv',gaps)
    coalesced_models=dict(primary=fit_model(coalesced),
                          role_experience_stratified=fit_model(coalesced,stratified=True),
                          piecewise=fit_piecewise(coalesced))
    # Only quarters with primary-model information are influence clusters.
    eligible=original.dropna(subset=['lag90_merger_hhi','duration_days','merge_event'])
    first_event=eligible.loc[eligible['merge_event']==1,'duration_days'].min()
    eligible=eligible[(eligible['merge_event']==1)|(eligible['duration_days']>=first_event)]
    quarters=sorted(eligible['creation_quarter'].unique())
    influence=[];failures=[]
    for quarter in quarters:
        subset=original[original['creation_quarter']!=quarter]
        try:
            constant=fit_model(subset)
            piece=fit_piecewise(subset)
            early,late=piece['concentration_effects']
            row=dict(excluded_quarter=quarter,observations=constant['observations'],
                     merge_events=constant['merge_events'],late_merge_events=late['merge_events_in_period'])
            for label,effect in [('constant',concentration_effect(constant)),('early',early),('late',late)]:
                row.update({label+'_'+k:v for k,v in concise_effect(effect).items()})
            influence.append(row)
        except (ValueError,np.linalg.LinAlgError) as error:
            failures.append(dict(excluded_quarter=quarter,error=type(error).__name__,reason=str(error)))
    write_csv(output/'leave_quarter_out.csv',influence)
    ranges={}
    for label in ['constant','early','late']:
        values=[r[label+'_hazard_ratio'] for r in influence]
        ranges[label]=dict(minimum_hazard_ratio=min(values),maximum_hazard_ratio=max(values),
                           intervals_containing_one=sum(r[label+'_lower_95']<=1<=r[label+'_upper_95'] for r in influence))
    result=dict(grace_window_seconds=300,coalesced_short_closures=coalesced_closures,
                 modified_prs=modified,cutoff_ambiguous_prs=cutoff_ambiguous,
                 close_reopen_transitions=len(gaps),reopen_gaps_at_most_five_minutes=sum(r['gap_seconds']<=300 for r in gaps),
                 coalesced_dispositions=dict(Counter(coalesced['outcome'])),coalesced_models=coalesced_models,
                 planned_influence_quarters=len(quarters),completed_influence_fits=len(influence),
                 failed_influence_fits=failures,leave_quarter_out_ranges=ranges,
                 interpretation='Exploratory endpoint and influence diagnostics; not confirmatory tests, predictions or causal interventions',
                 limitations=['Five-minute coalescing is an operational sensitivity, not evidence about the intent of closing a PR.',
                              'Brief closed periods remain included in the coalesced elapsed-time clock.',
                              'Historical queue and concentration exposures remain based on actual prior transitions.',
                              'Leave-quarter-out intervals are related fits, not independent replications.',
                              'Small-cluster uncertainty, omitted covariates and proportionality limitations remain.'],
                 provenance=dict(input_sha256=hashlib.sha256(feature_path.read_bytes()).hexdigest(),
                    script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    constant_fit_script_sha256=hashlib.sha256((root/'Paper/scripts/fit_maintenance_models.py').read_bytes()).hexdigest(),
                    piecewise_fit_script_sha256=hashlib.sha256((root/'Paper/scripts/fit_piecewise_models.py').read_bytes()).hexdigest(),
                    history_script_sha256=hashlib.sha256((root/'Paper/scripts/pr_history.py').read_bytes()).hexdigest(),
                    uv_lock_sha256=hashlib.sha256((root/'Paper/uv.lock').read_bytes()).hexdigest()))
    (output/'lifecycle_and_quarter_sensitivity.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ['coalesced_short_closures','coalesced_dispositions',
           'planned_influence_quarters','completed_influence_fits','failed_influence_fits','leave_quarter_out_ranges']},indent=2))


if __name__=='__main__':main()
