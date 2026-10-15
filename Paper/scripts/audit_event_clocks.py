"""Retain event/detail clock differences and assess the exceptional notification lag."""
import csv
import hashlib
import json
from pathlib import Path

from analyze import parse_time
from analyze_process import human
from collect_process import load_endpoint


def main():
    root=Path(__file__).resolve().parents[2]
    raw=root/'Paper/data/pr-census-2026-10-13'
    output=root/'Paper/results/pr-census'
    coverage=json.loads((output/'coverage.json').read_text())
    if not coverage.get('lifecycle_validated'):
        raise ValueError('Lifecycle sequence audit must finish first')
    audits=json.loads((output/'lifecycle_audit.json').read_text())
    prs=load_endpoint(raw/'pr_list')
    examined=[]
    for audit in audits:
        if not any(abs(d['timeline_minus_detail_seconds'])>1 for d in audit['clock_differences']):
            continue
        number=audit['number'];folder=raw/'pulls'/str(number)
        detail=load_endpoint(folder/'detail');events=load_endpoint(folder/'timeline')
        closes=[parse_time(e['created_at']) for e in events if e.get('event')=='closed']
        detail_close=parse_time(detail['closed_at'])
        lower,upper=sorted([detail_close,max(closes)])
        submissions=[p['number'] for p in prs if lower<=parse_time(p['created_at'])<=upper]
        reviews=[r['id'] for r in load_endpoint(folder/'reviews') if r.get('submitted_at')
                 and lower<=parse_time(r['submitted_at'])<=upper]
        item=dict(number=number,human_authored_account=human(detail['user']),
                  lifecycle_close_events=len(closes),detail_closed_at=detail['closed_at'],
                  timeline_last_close_utc=next(e['created_at'] for e in events
                      if e.get('event')=='closed' and parse_time(e['created_at'])==max(closes)),
                  clock_difference_seconds=upper-lower,submissions_in_clock_interval=submissions,
                  same_pr_review_submissions_in_clock_interval=reviews,
                  primary_human_outcome_affected=human(detail['user']),
                  pending_queue_at_submission_affected=bool(submissions))
        examined.append(item)
    result=dict(clock_disagreement_prs=sum(bool(r['clock_differences']) for r in audits),
                 maximum_difference_seconds=max(abs(d['timeline_minus_detail_seconds'])
                     for r in audits for d in r['clock_differences']),
                 disagreements_over_one_second=examined,
                 interpretation='Closure uses the observed timeline clock; merge uses immutable detail.merged_at. Differences remain recorded rather than silently repaired.',
                 script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (output/'event_clock_audit.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
