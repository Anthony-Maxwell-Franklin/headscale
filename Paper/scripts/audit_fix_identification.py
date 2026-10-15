"""Check the legacy subject-keyword heuristic against explicit closing-commit evidence."""
import csv
import json
import subprocess
from pathlib import Path
from analyze import FIX,write_csv

root=Path(__file__).resolve().parents[2]
rows=list(csv.DictReader((root/'Paper/results/issue-validation/closure_mechanisms.csv').open()))
repo=root/'Paper/data/headscale.git'
audit=[]
for r in rows:
    sha=r['last_closure_commit']
    if not sha:continue
    result=subprocess.run(['git','-C',str(repo),'show','-s','--format=%s%x1f%B',sha],
                           capture_output=True,text=True)
    if result.returncode:
        audit.append(dict(issue_number=r['number'],issue_url=r['url'],commit_sha=sha,
                          git_object_available=0,subject='',legacy_subject_keyword_match=None,
                          interpretation='Closing commit object unavailable locally; not classified'))
        continue
    subject,body=result.stdout.split('\x1f',1)
    audit.append(dict(issue_number=r['number'],issue_url=r['url'],commit_sha=sha,
                      git_object_available=1,subject=subject,
                      legacy_subject_keyword_match=int(bool(FIX.search(subject))),
                      interpretation='Explicit closing-commit link; a keyword miss is not counted as a corpus-wide recall estimate'))
out=root/'Paper/results/issue-validation'
write_csv(out/'legacy_fix_keyword_audit.csv',audit)
summary=dict(explicit_closing_commit_cases=len(audit),locally_available=sum(r['git_object_available'] for r in audit),
             keyword_matches=sum(r['legacy_subject_keyword_match']==1 for r in audit),
             keyword_misses=sum(r['legacy_subject_keyword_match']==0 for r in audit),
             limitations=['Small selected evidence subset, not representative of all repairs.',
                          'Commit linkage and keyword identification do not independently validate defect repair correctness.'])
(out/'fix_keyword_summary.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary,indent=2))
