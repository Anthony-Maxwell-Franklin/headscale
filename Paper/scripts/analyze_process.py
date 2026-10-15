#!/usr/bin/env python3
"""Analyse PR dispositions and completely collected pilot process endpoints."""
import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from analyze import concentration, parse_time, percentile, quarter, write_csv
from collect_process import load_endpoint
from research_metrics import km_curve


def human(user):
    return bool(user) and user.get('type') != 'Bot' and not user.get('login','').endswith('[bot]')


def competing_disposition(observations):
    """Aalen--Johansen merge/nonmerge-closure cumulative incidence.

    Each input is (duration, outcome), with outcome merge, nonmerge_close, censor.
    Administrative/nonmerge closure is a competing event, not a verified rejection.
    """
    groups=defaultdict(Counter)
    for duration,outcome in observations:
        if duration<0:
            raise ValueError('Negative PR follow-up')
        groups[duration][outcome]+=1
    risk=len(observations)
    survival,merge,closed=1.,0.,0.
    rows=[dict(time_days=0.,at_risk=risk,merge_cif=0.,nonmerge_close_cif=0.,undisposed_probability=1.)]
    for duration,counts in sorted(groups.items()):
        dm=counts['merge'];dc=counts['nonmerge_close']
        merge+=survival*dm/risk
        closed+=survival*dc/risk
        survival*=1-(dm+dc)/risk
        rows.append(dict(time_days=duration,at_risk=risk,merge_cif=merge,
                         nonmerge_close_cif=closed,undisposed_probability=survival))
        risk-=sum(counts.values())
    return rows


def complete(directory):
    path=directory/'coverage.json'
    return path.exists() and json.loads(path.read_text())['complete']


def first_human_events(detail,reviews,issue_comments,review_comments):
    author=detail['user']['login']
    responses=[]
    for r in reviews:
        if human(r.get('user')) and r['user']['login']!=author and r.get('submitted_at'):
            responses.append((parse_time(r['submitted_at']),'review',r['user']['login']))
    for name,comments in [('issue_comment',issue_comments),('review_comment',review_comments)]:
        for c in comments:
            if human(c.get('user')) and c['user']['login']!=author:
                responses.append((parse_time(c['created_at']),name,c['user']['login']))
    return sorted(responses)


def response_observation(detail, responses, observed, timeline=None):
    """Stop the response opportunity at disposition, rather than years later.

    Merge and nonmerge closure without a prior response are competing events.
    Reopening history is not reconstructed by a current detail record.
    """
    created=parse_time(detail['created_at'])
    merged=parse_time(detail['merged_at']) if detail.get('merged_at') else None
    closed=parse_time(detail['closed_at']) if detail.get('closed_at') else None
    terminal=min([t for t in (merged,closed) if t is not None and t<=observed],default=observed)
    terminal_kind='merge' if merged is not None and merged<=observed else 'close' if closed is not None and closed<=observed else None
    if timeline is not None:
        ends=[(parse_time(e['created_at']),0 if e['event']=='merged' else 1,e['event'])
              for e in timeline if e.get('event') in ('merged','closed') and e.get('created_at')
              and created<=parse_time(e['created_at'])<=observed]
        if ends:
            terminal,_,event_kind=min(ends)
            terminal_kind='merge' if event_kind=='merged' else 'close'
    eligible=[r for r in responses if created<=r[0]<=terminal]
    first=eligible[0] if eligible else None
    if first:
        outcome='response'
    elif terminal_kind=='merge':
        outcome='merge_without_response'
    elif terminal_kind=='close':
        outcome='nonmerge_close_without_response'
    else:
        outcome='censor'
    return first,terminal,outcome,((first[0] if first else terminal)-created)/86400


def response_incidence(observations):
    groups=defaultdict(Counter)
    outcomes=('response','merge_without_response','nonmerge_close_without_response')
    for duration,outcome in observations:
        if duration<0:
            raise ValueError('Negative response opportunity duration')
        groups[duration][outcome]+=1
    risk=len(observations);survival=1.;incidence=dict.fromkeys(outcomes,0.)
    rows=[dict(time_days=0.,at_risk=risk,**{k+'_cif':0. for k in outcomes},unresolved_probability=1.)]
    for duration,counts in sorted(groups.items()):
        for outcome in outcomes:
            incidence[outcome]+=survival*counts[outcome]/risk
        survival*=1-sum(counts[k] for k in outcomes)/risk
        rows.append(dict(time_days=duration,at_risk=risk,
                         **{k+'_cif':incidence[k] for k in outcomes},unresolved_probability=survival))
        risk-=sum(counts.values())
    return rows


def main():
    root=Path(__file__).resolve().parents[2]
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,default=root/'Paper/data/pr-process-2026-10-13')
    p.add_argument('--output',type=Path,default=root/'Paper/results/pr-process')
    args=p.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    manifest=json.loads((args.input/'manifest.json').read_text())
    prs=load_endpoint(args.input/'pr_list')
    list_status=json.loads((args.input/'pr_list/coverage.json').read_text())
    cutoff=max(parse_time(r['fetched_at_utc']) for r in list_status['pages'])
    rows=[];cohorts=defaultdict(list);observations=[]
    for r in prs:
        created=parse_time(r['created_at'])
        merged=parse_time(r['merged_at']) if r.get('merged_at') else None
        closed=parse_time(r['closed_at']) if r.get('closed_at') else None
        if merged is not None and merged<=cutoff:
            outcome='merge';end=merged
        elif r['state']=='closed' and closed is not None and closed<=cutoff:
            outcome='nonmerge_close';end=closed
        else:
            outcome='censor';end=cutoff
        if created>cutoff or end<created:
            raise ValueError('PR timestamp outside observation interval')
        duration=(end-created)/86400
        row=dict(number=r['number'],url=r['html_url'],created_at=r['created_at'],
                 creation_quarter=quarter(created),author=r['user']['login'],
                 human_authored=int(human(r['user'])),observed_draft=int(r['draft']),
                 outcome=outcome,followup_days=duration,observed_author_association=r['author_association'])
        rows.append(row);cohorts[row['creation_quarter']].append(row)
        observations.append((duration,outcome))
    write_csv(args.output/'pr_disposition_observations.csv',rows)
    write_csv(args.output/'pr_disposition_cif.csv',competing_disposition(observations))
    human_rows=[r for r in rows if r['human_authored']]
    write_csv(args.output/'human_pr_disposition_cif.csv',competing_disposition(
        [(r['followup_days'],r['outcome']) for r in human_rows]))
    cohort_rows=[]
    for q,group in sorted(cohorts.items()):
        authors=Counter(r['author'] for r in group if r['human_authored'])
        c=concentration(authors)
        merged=[r['followup_days'] for r in group if r['outcome']=='merge']
        cohort_rows.append(dict(creation_quarter=q,prs=len(group),human_prs=c['events'],
                                human_authors=c['entities'],human_author_hhi=c['hhi'],
                                human_top_author_percent=c['top1_percent'],
                                merged=len(merged),closed_without_merge=sum(r['outcome']=='nonmerge_close' for r in group),
                                open_censored=sum(r['outcome']=='censor' for r in group),
                                merged_only_median_days=percentile(merged,.5)))
    write_csv(args.output/'pr_creation_cohorts.csv',cohort_rows)
    sample=json.loads((args.input/'sample.json').read_text())
    pilot=[];responses_for_km=[];incomplete=[]
    for item in sample:
        directory=args.input/'pulls'/str(item['number'])
        names=('detail','reviews','issue_comments','review_comments')
        if not all(complete(directory/name) for name in names):
            incomplete.append(item['number']);continue
        detail,reviews,comments,inline=[load_endpoint(directory/name) for name in names]
        endpoints=[json.loads((directory/name/'coverage.json').read_text()) for name in names]
        # Minimum endpoint retrieval time gives a conservative common cutoff.
        observed=min(max(parse_time(page['fetched_at_utc']) for page in status['pages']) for status in endpoints)
        created=parse_time(detail['created_at'])
        responses=first_human_events(detail,reviews,comments,inline)
        timeline=load_endpoint(directory/'timeline') if complete(directory/'timeline') else None
        first,terminal,response_outcome,duration=response_observation(detail,responses,observed,timeline)
        responses=[r for r in responses if created<=r[0]<=terminal]
        review_events=[r for r in responses if r[1]=='review']
        author=detail['user']['login']
        reviewers=sorted({r['user']['login'] for r in reviews if human(r.get('user'))
                          and r['user']['login']!=author and r.get('submitted_at')
                          and parse_time(r['submitted_at'])<=terminal})
        responses_for_km.append((duration,bool(first)))
        pilot.append(dict(number=detail['number'],url=detail['html_url'],creation_year=item['year'],
                          inclusion_probability=item['inclusion_probability'],author=author,
                          human_authored=int(human(detail['user'])),observed_draft=int(detail['draft']),
                          first_human_response_event=int(bool(first)),
                          first_response_opportunity_outcome=response_outcome,
                          response_boundary_basis='first timeline disposition' if timeline is not None else 'latest detail disposition',
                          reopened_events=sum(e.get('event')=='reopened' for e in timeline) if timeline is not None else None,
                          draft_history_events=sum(e.get('event') in ('ready_for_review','convert_to_draft','converted_to_draft') for e in timeline) if timeline is not None else None,
                          first_human_response_days=duration,first_response_type=first[1] if first else '',
                          first_response_actor=first[2] if first else '',
                          first_submitted_review_days=(review_events[0][0]-created)/86400 if review_events else None,
                          human_reviewers=';'.join(reviewers),merged_at=detail['merged_at'],
                          merged_by=(detail.get('merged_by') or {}).get('login'),
                          additions=detail['additions'],deletions=detail['deletions'],changed_files=detail['changed_files'],
                          observation_cutoff_utc=datetime.fromtimestamp(observed,timezone.utc).isoformat()))
    write_csv(args.output/'complete_pilot_process_observations.csv',pilot)
    write_csv(args.output/'pilot_first_human_response_km.csv',km_curve(responses_for_km))
    human_pilot=[r for r in pilot if r['human_authored']]
    write_csv(args.output/'human_pilot_response_cif.csv',response_incidence(
        [(r['first_human_response_days'],r['first_response_opportunity_outcome']) for r in human_pilot]))
    summary=dict(list_prs=len(prs),observed_dispositions=dict(Counter(r['outcome'] for r in rows)),
                 human_authored_prs=len(human_rows),human_dispositions=dict(Counter(r['outcome'] for r in human_rows)),
                 list_observation_cutoff_utc=datetime.fromtimestamp(cutoff,timezone.utc).isoformat(),
                 detailed_scope=manifest.get('detail_scope'),planned_sample=len(sample),complete_pilot_prs=len(pilot),
                 incomplete_pilot_prs=incomplete,
                 pilot_human_prs=len(human_pilot),
                 pilot_human_response_outcomes=dict(Counter(r['first_response_opportunity_outcome'] for r in human_pilot)),
                 pilot_timeline_coverage=sum(r['response_boundary_basis']=='first timeline disposition' for r in pilot),
                 pilot_reopened_prs=sum(bool(r['reopened_events']) for r in pilot),
                 pilot_with_draft_history=sum(bool(r['draft_history_events']) for r in pilot),
                 limitations=['PR list snapshot and issue list snapshot differ in collection time.',
                              'Closed without merge does not necessarily mean rejection.',
                              'Timeline availability is reported; draft history is observed but the primary clock begins at creation.',
                              'Incomplete pilot observations are excluded, not coded as zero response.',
                              'Year-stratified pilot requires inclusion weights before population estimates.',
                              'Response KM is cause-specific with disposition censoring; the response CIF handles competing closure.',
                              'Observed user association does not establish historical maintainer role.'])
    (args.output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps({k:v for k,v in summary.items() if k!='incomplete_pilot_prs'},indent=2))


if __name__=='__main__':
    main()
