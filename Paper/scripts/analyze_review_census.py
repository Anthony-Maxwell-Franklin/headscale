#!/usr/bin/env python3
"""Count observed submitted reviews separately from comments and informal review."""
import argparse
import hashlib
import json
import sys
from collections import Counter,defaultdict
from pathlib import Path

from analyze import concentration,parse_time,quarter,write_csv
from analyze_process import human,response_incidence
from collect_process import load_endpoint
from pr_history import reconstruct,in_observed_open_interval


def paired_role_counts(reviewed):
    """One reviewed-PR credit per role; split review credit across known reviewers."""
    counts={'author':Counter(r['author'] for r in reviewed),
            'integrator':Counter(r['merged_by'].casefold() for r in reviewed if r['merged_by']),
            'reviewer':Counter(),'fractional_reviewer':Counter()}
    for row in reviewed:
        actors=row['human_reviewers'].split(';')
        if not actors or not all(actors) or len(set(actors))!=len(actors):
            raise ValueError('Reviewed PR needs unique known reviewer accounts')
        counts['reviewer'].update(actors)
        for actor in actors:counts['fractional_reviewer'][actor]+=1/len(actors)
    if abs(sum(counts['fractional_reviewer'].values())-len(reviewed))>1e-9:
        raise ValueError('Fractional review credit does not conserve PR membership')
    return counts


def review_observation(detail,reviews,cutoff,history=None):
    created=parse_time(detail['created_at'])
    author=detail['user']['login'].casefold()
    merged=parse_time(detail['merged_at']) if detail.get('merged_at') else None
    closed=parse_time(detail['closed_at']) if detail.get('closed_at') else None
    final=min([t for t in [merged,closed] if t is not None and t<=cutoff],default=cutoff)
    if history is not None:
        initial=history['first_terminal_ts'] if history['first_terminal_ts'] is not None else cutoff
        kind=history['first_outcome']
        if history['open_at_cutoff']:
            final=cutoff
        elif merged is None or merged>cutoff:
            final=max(r['time'] for r in history['transitions'] if r['event']=='closed')
    else:
        initial=final
        kind='merge' if merged is not None and merged<=cutoff else 'nonmerge_close' if closed is not None and closed<=cutoff else 'censor'
    excluded=Counter();initial_events=[];all_open_events=[];identifiers=set()
    for review in reviews:
        identifier=review.get('id')
        if identifier is None or identifier in identifiers:
            raise ValueError('Missing or duplicate submitted-review identifier')
        identifiers.add(identifier)
        if review.get('state')=='PENDING' or not review.get('submitted_at'):
            excluded['pending_or_unsubmitted']+=1;continue
        user=review.get('user')
        if not user or not user.get('login'):
            excluded['unknown_actor']+=1;continue
        if not human(user):
            excluded['bot_actor']+=1;continue
        actor=user['login'].casefold()
        if actor==author:
            excluded['author_self_review']+=1;continue
        timestamp=parse_time(review['submitted_at'])
        if timestamp<created:
            raise ValueError('Review precedes PR creation')
        if timestamp>cutoff:
            excluded['after_cutoff']+=1;continue
        if timestamp>final:
            excluded['after_final_disposition']+=1;continue
        if history is not None and not in_observed_open_interval(history,timestamp):
            excluded['during_closed_interval']+=1;continue
        event=(timestamp,actor,identifier)
        all_open_events.append(event)
        if timestamp<=initial:
            initial_events.append(event)
    initial_events.sort();all_open_events.sort()
    if initial_events:
        outcome='response';end=initial_events[0][0]
    else:
        outcome={'merge':'merge_without_response','nonmerge_close':'nonmerge_close_without_response',
                 'censor':'censor'}[kind];end=initial
    return dict(initial_events=initial_events,all_open_events=all_open_events,
                excluded=dict(excluded),outcome=outcome,duration_days=(end-created)/86400,
                final_terminal=final)


def main():
    root=Path(__file__).resolve().parents[2]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=root/'Paper/data/pr-census-2026-10-13')
    parser.add_argument('--output',type=Path,default=root/'Paper/results/review-census')
    parser.add_argument('--require-timelines',action='store_true')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    prs=load_endpoint(args.input/'pr_list');missing=[];records=[]
    names=['detail','reviews']+(['timeline'] if args.require_timelines else [])
    for pr in prs:
        folder=args.input/'pulls'/str(pr['number'])
        absent=[]
        for name in names:
            coverage=folder/name/'coverage.json'
            if not coverage.exists() or not json.loads(coverage.read_text())['complete']:
                absent.append(name)
        if absent:
            missing.append(dict(number=pr['number'],endpoints=absent))
        else:
            records.append((pr['number'],*[load_endpoint(folder/name) for name in names]))
    coverage=dict(planned_prs=len(prs),complete_prs=len(records),missing=missing,
                   complete=not missing,timelines_required=args.require_timelines)
    (args.output/'coverage.json').write_text(json.dumps(coverage,indent=2)+'\n')
    if missing:
        print(f'Review census incomplete: {len(records)}/{len(prs)}. Role and response estimates withheld.')
        sys.exit(2)
    status=json.loads((args.input/'pr_list/coverage.json').read_text())
    cutoff=max(parse_time(r['fetched_at_utc']) for r in status['pages'])
    rows=[];quarters=defaultdict(list);exclusions=Counter();audit=[];all_reviews=0
    list_coverage=args.input/'pr_list/coverage.json'
    raw_sources={str(list_coverage.relative_to(root)):hashlib.sha256(list_coverage.read_bytes()).hexdigest()}
    for number,detail,reviews,*timeline in records:
        if detail['number']!=number:
            raise ValueError('Detail and list membership differ')
        history=reconstruct(detail,timeline[0],cutoff) if timeline else None
        if history and history['anomalies']:
            audit.append(dict(number=number,anomalies=history['anomalies']));continue
        result=review_observation(detail,reviews,cutoff,history)
        all_reviews+=len(reviews);exclusions.update(result['excluded'])
        if not human(detail['user']):continue
        actors=sorted({r[1] for r in result['all_open_events']})
        merged=parse_time(detail['merged_at']) if detail.get('merged_at') else None
        is_merged=merged is not None and merged<=cutoff
        row=dict(number=number,author=detail['user']['login'].casefold(),url=detail['html_url'],
                  human_reviewers=';'.join(actors),reviewer_pr_pairs=len(actors),
                  submitted_review_records=len(result['all_open_events']),
                  initial_submitted_review_records=len(result['initial_events']),
                  unknown_actor_records=result['excluded'].get('unknown_actor',0),
                  formal_review_event=int(result['outcome']=='response'),
                  first_formal_review_days=result['duration_days'],
                  first_review_opportunity_outcome=result['outcome'],
                  response_boundary='first lifecycle disposition' if history else 'last detail disposition',
                  eventual_merge=int(is_merged),
                  merged_by=(detail.get('merged_by') or {}).get('login') if is_merged else None,
                  merge_quarter=quarter(merged) if is_merged else None)
        rows.append(row)
        if is_merged:quarters[row['merge_quarter']].append(row)
    if audit:
        coverage.update(complete=False,lifecycle_anomalies=audit)
        (args.output/'coverage.json').write_text(json.dumps(coverage,indent=2)+'\n')
        print(f'{len(audit)} lifecycle histories require evidence review. Census estimates withheld.')
        sys.exit(2)
    for number,*_ in records:
        for name in names:
            path=args.input/'pulls'/str(number)/name/'coverage.json'
            raw_sources[str(path.relative_to(root))]=hashlib.sha256(path.read_bytes()).hexdigest()
    write_csv(args.output/'human_pr_formal_review_observations.csv',rows)
    paired=[]
    for q,group in sorted(quarters.items()):
        reviewed=[r for r in group if r['reviewer_pr_pairs']]
        counts=paired_role_counts(reviewed)
        item=dict(merge_quarter=q,human_merged_prs=len(group),reviewed_human_merged_prs=len(reviewed),
                  unknown_actor_records=sum(r['unknown_actor_records'] for r in group),
                  merged_without_observed_submitted_review=len(group)-len(reviewed))
        for role,actors in counts.items():
            c=concentration(actors)
            item.update({role+'_hhi':c['hhi'],role+'_accounts':c['entities'],role+'_events':c['events']})
        paired.append(item)
    write_csv(args.output/'quarterly_paired_review_roles.csv',paired)
    incidence=response_incidence([(r['first_formal_review_days'],r['first_review_opportunity_outcome']) for r in rows])
    rename={'response_cif':'formal_review_cif','merge_without_response_cif':'merge_without_formal_review_cif',
            'nonmerge_close_without_response_cif':'close_without_formal_review_cif'}
    write_csv(args.output/'human_first_formal_review_cif.csv',[{rename.get(k,k):v for k,v in row.items()} for row in incidence])
    merged_rows=[r for r in rows if r['eventual_merge']]
    summary=dict(complete=True,total_prs=len(prs),human_prs=len(rows),raw_review_records=all_reviews,
                 excluded_review_records=dict(exclusions),
                 human_merged_prs=len(merged_rows),
                 human_merged_with_observed_submitted_review=sum(r['reviewer_pr_pairs']>0 for r in merged_rows),
                 observed_reviewer_accounts=len({a for r in rows if r['human_reviewers'] for a in r['human_reviewers'].split(';')}),
                 human_prs_with_unknown_review_actor=sum(r['unknown_actor_records']>0 for r in rows),
                 first_formal_review_outcomes=dict(Counter(r['first_review_opportunity_outcome'] for r in rows)),
                 lifecycle_reconstructed=args.require_timelines,
                 limitations=['Submitted reviews are separate from issue comments, inline discussions and informal review.',
                              'Absence of an observed submitted review does not establish absence of review.',
                              'DISMISSED records identify submitted activity, not current approval.',
                              'Authors/integrators count PRs; reviewers count distinct reviewer-PR pairs on the same reviewed-PR subset.',
                              'Fractional reviewer credit assigns one PR equally across its observed reviewers; it is not an effort estimate.',
                              'Deleted reviews/accounts and non-atomic collection limit historical completeness.',
                              'First-disposition boundaries and closed/reopened gaps are reconstructed only when timelines are required.'],
                 provenance=dict(script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                                 source_coverage_sha256=raw_sources))
    (args.output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps({k:v for k,v in summary.items() if k!='provenance'},indent=2))


if __name__=='__main__':
    main()
