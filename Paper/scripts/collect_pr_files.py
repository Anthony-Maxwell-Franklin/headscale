"""Collect immutable merged-PR file lists for component-matched role sensitivity."""
import argparse
import hashlib
import json
import sys
import urllib.error
from pathlib import Path

from analyze import parse_time
from analyze_process import human
from collect_process import BudgetReached,Client,collect_endpoint,load_endpoint,now


def main():
    root=Path(__file__).resolve().parents[2]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=root/'Paper/data/pr-census-2026-10-13')
    parser.add_argument('--output',type=Path,default=root/'Paper/data/pr-files-2026-10-13')
    parser.add_argument('--max-requests',type=int,default=1500)
    args=parser.parse_args()
    if args.max_requests<1:parser.error('Request budget must be positive')
    args.output.mkdir(parents=True,exist_ok=True)
    status=json.loads((args.input/'pr_list/coverage.json').read_text())
    cutoff=max(parse_time(r['fetched_at_utc']) for r in status['pages'])
    plan=[]
    for pr in load_endpoint(args.input/'pr_list'):
        detail=load_endpoint(args.input/'pulls'/str(pr['number'])/'detail')
        if human(detail['user']) and detail.get('merged_at') and parse_time(detail['merged_at'])<=cutoff:
            plan.append(dict(number=detail['number'],expected_files=detail['changed_files'],merged_at=detail['merged_at']))
    plan.sort(key=lambda r:r['number'])
    digest=hashlib.sha256(json.dumps(plan,sort_keys=True).encode()).hexdigest()
    manifest_path=args.output/'manifest.json'
    manifest=json.loads(manifest_path.read_text()) if manifest_path.exists() else dict(complete=False,started_at_utc=now())
    if manifest.get('plan_sha256',digest)!=digest:
        raise ValueError('Pinned merged-PR population changed')
    manifest.update(plan_sha256=digest,planned_prs=len(plan),complete=False,
                    interpretation='File membership of human-account-authored PRs merged before the pinned cutoff')
    (args.output/'plan.json').write_text(json.dumps(plan,indent=2)+'\n')
    client=Client(args.max_requests)
    print('Authentication source: '+client.auth_source,flush=True)
    try:
        for item in plan:
            number=item['number'];directory=args.output/'pulls'/str(number)/'files'
            collect_endpoint(client,directory,
                f'https://api.github.com/repos/juanfont/headscale/pulls/{number}/files?per_page=100',manifest)
            files=load_endpoint(directory)
            if len(files)!=item['expected_files'] or len({r['filename'] for r in files})!=len(files):
                raise ValueError('File-list coverage differs from the pinned metadata count')
        manifest['complete']=True;manifest.pop('last_error',None)
    except BudgetReached:
        manifest['last_error']='Per-run request budget reached; resume with the same arguments'
    except (urllib.error.URLError,ValueError,OSError) as error:
        manifest['last_error']=type(error).__name__
        if isinstance(error,ValueError):manifest['validation_error']=str(error)
        if isinstance(error,urllib.error.HTTPError):manifest['last_http_status']=error.code
        print('Incomplete file-list census: '+type(error).__name__,file=sys.stderr)
    finally:
        statuses=[json.loads((args.output/'pulls'/str(p['number'])/'files/coverage.json').read_text())
                  for p in plan if (args.output/'pulls'/str(p['number'])/'files/coverage.json').exists()]
        manifest.update(completed_prs=sum(r['complete'] for r in statuses),last_run_requests=client.requests,last_run_at_utc=now())
        if manifest['complete']:
            manifest['finished_at_utc']=max(p['fetched_at_utc'] for r in statuses for p in r['pages'])
        manifest_path.write_text(json.dumps(manifest,indent=2)+'\n')
    if not manifest['complete']:sys.exit(2)
    print('Completed merged-PR file-list census.')


if __name__=='__main__':main()
