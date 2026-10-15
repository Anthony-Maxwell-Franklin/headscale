#!/usr/bin/env python3
"""Require a complete PR metadata census before responsibility/inference exports."""
import argparse
import csv
import json
import math
import hashlib
import sys
from collections import Counter,defaultdict
from pathlib import Path

from analyze import concentration,parse_time,quarter,write_csv
from collect_process import load_endpoint
from pr_history import reconstruct,pending_before


def prior_features(target, records, lookback_days=90):
    """Use prior activity; timelines reconstruct pending work when available.

    Without timelines the backlog is explicitly a final-disposition proxy.
    """
    if lookback_days <= 0:
        raise ValueError('The lookback window must be positive')
    t=target['created_ts'];since=t-lookback_days*86400
    prior=[r for r in records if r['created_ts']<t]
    merges=[r for r in prior if r['merged_ts'] is not None and since<=r['merged_ts']<t]
    known=[r for r in merges if r['merged_by']]
    actor_counts=Counter(r['merged_by'] for r in known)
    c=concentration(actor_counts)
    author_prior=sum(r['author']==target['author'] for r in prior)
    integrator=any(r['merged_by']==target['author'] and r['merged_ts'] is not None and r['merged_ts']<t for r in prior)
    backlog=sum(pending_before(r['history'],t) if 'history' in r else
                (r['terminal_ts'] is None or r['terminal_ts']>=t) for r in prior)
    prefix=f'lag{lookback_days}_'
    return {prefix+'merger_hhi':c['hhi'],prefix+'merges':len(merges),
            prefix+'known_merger_events':len(known),
            'prior_author_prs':author_prior,'prior_integrator_author':int(integrator),
            'backlog_at_submission':backlog,
            prefix+'submissions':sum(r['created_ts']>=since for r in prior)}


def main():
    root=Path(__file__).resolve().parents[2]
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,default=root/'Paper/data/pr-census-2026-10-13')
    p.add_argument('--output',type=Path,default=root/'Paper/results/pr-census')
    p.add_argument('--lookback-days',type=int,default=90,
                   help='Window for prior integration activity, in days (default: 90)')
    p.add_argument('--require-timelines',action='store_true',
                   help='Require complete validated lifecycle history; model first-disposition episodes and reconstruct backlog')
    args=p.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    prs=load_endpoint(args.input/'pr_list')
    missing=[];missing_timelines=[];details=[];timelines={}
    for pr in prs:
        directory=args.input/'pulls'/str(pr['number'])/'detail'
        path=directory/'coverage.json'
        if not path.exists() or not json.loads(path.read_text())['complete']:
            missing.append(pr['number'])
        else:
            detail=load_endpoint(directory)
            if detail['number']!=pr['number']:
                raise ValueError('PR detail number does not match the pinned list')
            details.append(detail)
        if args.require_timelines:
            directory=args.input/'pulls'/str(pr['number'])/'timeline'
            path=directory/'coverage.json'
            if not path.exists() or not json.loads(path.read_text())['complete']:
                missing_timelines.append(pr['number'])
            else:
                timelines[pr['number']]=load_endpoint(directory)
    coverage=dict(planned_prs=len(prs),complete_details=len(details),missing=missing,
                   metadata_complete=not missing,
                   timelines_required=args.require_timelines,
                   timeline_complete=not missing_timelines if args.require_timelines else None,
                   missing_timelines=missing_timelines,
                   interpretation='Complete list membership, not complete review-event coverage')
    (args.output/'coverage.json').write_text(json.dumps(coverage,indent=2)+'\n')
    if missing:
        print(f'Metadata census incomplete: {len(details)}/{len(prs)}. Responsibility estimates and models withheld.')
        sys.exit(2)
    if missing_timelines:
        print(f'Timeline census incomplete: {len(prs)-len(missing_timelines)}/{len(prs)}. Lifecycle estimates withheld.')
        sys.exit(2)
    status=json.loads((args.input/'pr_list/coverage.json').read_text())
    cutoff=max(parse_time(page['fetched_at_utc']) for page in status['pages'])
    records=[];history_audit=[]
    for detail in details:
        created=parse_time(detail['created_at'])
        merged=parse_time(detail['merged_at']) if detail.get('merged_at') else None
        closed=parse_time(detail['closed_at']) if detail.get('closed_at') else None
        if merged is not None and merged>cutoff:merged=None
        if closed is not None and closed>cutoff:closed=None
        terminal=merged if merged is not None else closed
        user=detail['user']
        records.append(dict(number=detail['number'],url=detail['html_url'],author=user['login'],
                            human_authored=user.get('type')!='Bot' and not user['login'].endswith('[bot]'),
                            created_ts=created,merged_ts=merged,terminal_ts=terminal,
                            merged_by=(detail.get('merged_by') or {}).get('login') if merged else None,
                            final_additions=detail['additions'],final_deletions=detail['deletions'],
                            final_changed_files=detail['changed_files']))
        if args.require_timelines:
            record=records[-1]
            history=reconstruct(detail,timelines[detail['number']],cutoff)
            record['history']=history
            if merged is None:
                record['terminal_ts']=None if history['open_at_cutoff'] else max(
                    r['time'] for r in history['transitions'] if r['event']=='closed')
            history_audit.append(dict(number=detail['number'],anomalies=history['anomalies'],
                                      clock_differences=history['metadata_clock_differences']))
    if args.require_timelines:
        failures=[r for r in history_audit if r['anomalies']]
        (args.output/'lifecycle_audit.json').write_text(json.dumps(history_audit,indent=2)+'\n')
        coverage['lifecycle_validated']=not failures
        coverage['lifecycle_anomalous_prs']=[r['number'] for r in failures]
        (args.output/'coverage.json').write_text(json.dumps(coverage,indent=2)+'\n')
        if failures:
            print(f'{len(failures)} lifecycle histories require evidence review. Lifecycle models withheld.')
            sys.exit(2)
    rows=[]
    for r in records:
        if not r['human_authored']:continue
        outcome='merge' if r['merged_ts'] is not None else 'nonmerge_close' if r['terminal_ts'] is not None else 'censor'
        duration=((r['terminal_ts'] if r['terminal_ts'] is not None else cutoff)-r['created_ts'])/86400
        final_outcome=outcome;final_duration=duration
        if args.require_timelines:
            outcome=r['history']['first_outcome']
            end=r['history']['first_terminal_ts']
            duration=((end if end is not None else cutoff)-r['created_ts'])/86400
        features=prior_features(r,records,args.lookback_days)
        rows.append(dict(number=r['number'],url=r['url'],author=r['author'],
                         creation_quarter=quarter(r['created_ts']),creation_year=int(quarter(r['created_ts'])[:4]),
                         calendar_years=(r['created_ts']-parse_time('2020-01-01T00:00:00Z'))/(365.25*86400),
                         outcome=outcome,duration_days=duration,merge_event=int(outcome=='merge'),
                         final_observed_outcome=final_outcome,final_duration_days=final_duration,
                         reopened_events=r['history']['reopened_events'] if args.require_timelines else None,
                         merged_by=r['merged_by'],self_merged=int(r['merged_by']==r['author']) if r['merged_by'] else None,
                         **features,final_additions=r['final_additions'],final_deletions=r['final_deletions'],
                         final_changed_files=r['final_changed_files']))
    write_csv(args.output/'human_pr_metadata_features.csv',rows)
    quarters=defaultdict(list)
    for r in records:
        if r['human_authored'] and r['merged_ts'] is not None:
            quarters[quarter(r['merged_ts'])].append(r)
    responsibility=[]
    for q,group in sorted(quarters.items()):
        counts=Counter(r['merged_by'] for r in group if r['merged_by'])
        c=concentration(counts)
        a=concentration(Counter(r['author'] for r in group))
        responsibility.append(dict(merge_quarter=q,human_pr_merges=len(group),known_merger_events=c['events'],
                                   merger_identities=c['entities'],merger_hhi=c['hhi'],
                                   author_identities_on_same_prs=a['entities'],author_hhi_on_same_prs=a['hhi'],
                                   effective_authors_on_same_prs=a['effective_entities'],
                                   top_author_percent_on_same_prs=a['top1_percent'],
                                   effective_mergers=c['effective_entities'],top_merger_percent=c['top1_percent'],
                                   self_merges=sum(r['merged_by']==r['author'] for r in group),
                                   top_merger=counts.most_common(1)[0][0] if counts else None))
    write_csv(args.output/'quarterly_merge_responsibility.csv',responsibility)
    eventual_merges=[r for r in records if r['human_authored'] and r['merged_ts'] is not None]
    summary=dict(metadata_complete=True,total_prs=len(prs),human_prs=len(rows),
                 lookback_days=args.lookback_days,
                 known_mergers=sum(bool(r['merged_by']) for r in eventual_merges),
                 merge_events=len(eventual_merges),
                 unique_merger_accounts=len({r['merged_by'] for r in eventual_merges if r['merged_by']}),
                 self_merges=sum(r['merged_by']==r['author'] for r in eventual_merges),
                 model_episode_scope='first disposition' if args.require_timelines else 'last recorded disposition',
                 backlog_reconstructed_from_timelines=args.require_timelines,
                 model_dispositions=dict(Counter(r['outcome'] for r in rows)),
                 human_final_dispositions=dict(Counter(r['final_observed_outcome'] for r in rows)),
                 human_reopened_prs=sum(bool(r['reopened_events']) for r in rows) if args.require_timelines else None,
                 first_closed_then_eventually_merged=sum(r['outcome']=='nonmerge_close' and r['final_observed_outcome']=='merge' for r in rows),
                 clock_disagreement_prs=sum(bool(r['clock_differences']) for r in history_audit),
                 max_clock_difference_seconds=max((abs(d['timeline_minus_detail_seconds'])
                     for r in history_audit for d in r['clock_differences']),default=0),
                 script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                 history_script_sha256=hashlib.sha256((Path(__file__).parent/'pr_history.py').read_bytes()).hexdigest(),
                 analysis_scope='Complete PR metadata with validated lifecycle histories' if args.require_timelines else 'Complete PR metadata; lifecycle history not reconstructed',
                 limitations=['PR labels/current draft states are not historical submission covariates.',
                              'Final size variables are post-submission descriptions and excluded from the primary model.',
                              'First-disposition models stop at the first observed closure, including briefly closed/reopened cases.' if args.require_timelines else 'Reopened PR first episodes and exact pending queues are not reconstructed.',
                              'User logins are observed at collection time; account rename effects remain a sensitivity.',
                              'Prior integrator means a recorded earlier merge action, not verified organisational authority.',
                              'Review/comment coverage is independent of metadata and lifecycle validation.'])
    (args.output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':
    main()
