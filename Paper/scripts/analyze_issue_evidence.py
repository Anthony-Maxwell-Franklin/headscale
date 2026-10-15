#!/usr/bin/env python3
"""Export auditable closure mechanisms; leave defect adjudication to reviewers."""
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from collect_process import load_endpoint
from analyze import write_csv


def main():
    root=Path(__file__).resolve().parents[2]
    evidence=root/'Paper/data/issue-evidence-2026-10-13'
    manifest=json.loads((evidence/'manifest.json').read_text())
    if not manifest['complete']:
        raise ValueError('Validation-sample evidence collection is incomplete')
    source=json.loads((root/'Paper/data/github-2026-10-13/issues_and_prs.json').read_text())
    by_number={r['number']:r for r in source}
    sample=list(csv.DictReader((root/'Paper/results/research-v0.29.5/issue_validation_sample.csv').open()))
    rows=[];packets=[]
    for item in sample:
        number=int(item['number']);record=by_number[number]
        directory=evidence/'issues'/str(number)
        timeline=load_endpoint(directory/'timeline');comments=load_endpoint(directory/'comments')
        closures=sorted([e for e in timeline if e.get('event')=='closed'],key=lambda e:e['created_at'])
        last=closures[-1] if closures else {}
        actor=last.get('actor') or {}
        bot=actor.get('type')=='Bot' or actor.get('login','').endswith('[bot]')
        rows.append(dict(number=number,url=record['html_url'],title=record['title'],
                         stratum=item['stratum'],inclusion_probability=item['inclusion_probability'],
                         observed_state=record['state'],observed_state_reason=record.get('state_reason'),
                         closure_events=len(closures),reopened_events=sum(e.get('event')=='reopened' for e in timeline),
                         last_closure_actor=actor.get('login'),last_closure_actor_type=actor.get('type'),
                         last_closure_by_bot=int(bot) if closures else None,
                         last_closure_commit=last.get('commit_id'),last_closure_event_url=last.get('url'),
                         comments_after_last_closure=sum(c['created_at']>last['created_at'] for c in comments) if last else None,
                         comment_count=len(comments),human_outcome_adjudication='pending'))
        packets.append(dict(number=number,url=record['html_url'],title=record['title'],body=record.get('body'),
                            observed_state_reason=record.get('state_reason'),comments=comments,
                            closure_events=closures,reopened_events=[e for e in timeline if e.get('event')=='reopened'],
                            referenced_commits=[{k:e.get(k) for k in ['event','commit_id','commit_url','created_at']} for e in timeline if e.get('commit_id')]))
    out=root/'Paper/results/issue-validation';out.mkdir(exist_ok=True)
    write_csv(out/'closure_mechanisms.csv',rows)
    (out/'review_packets.json').write_text(json.dumps(packets,indent=2)+'\n')
    summary=dict(sample_size=len(rows),closed_in_sample=sum(r['observed_state']=='closed' for r in rows),
                 last_closure_by_bot=sum(r['last_closure_by_bot']==1 for r in rows),
                 explicit_closing_commit=sum(bool(r['last_closure_commit']) for r in rows),
                 reopened_issues=sum(r['reopened_events']>0 for r in rows),
                 comments_after_closure_issues=sum(bool(r['comments_after_last_closure']) for r in rows),
                 sampling='Year, observed-state and closure-reason stratified; raw counts are not population rates',
                 evidence_manifest_sha256=hashlib.sha256((evidence/'manifest.json').read_bytes()).hexdigest(),
                 limitations=['Bot closure does not establish either repair or lack of repair.',
                              'No closing commit does not mean no repair occurred.',
                              'Independent outcome coding remains pending; no coder agreement is claimed.'])
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':
    main()
