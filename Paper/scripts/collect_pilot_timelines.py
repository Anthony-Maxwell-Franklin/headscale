"""Collect resumable opening/closing and draft-state history for a pinned PR sample."""
import argparse
import hashlib
import json
import sys
import urllib.error
from pathlib import Path
from collect_process import BudgetReached,Client,collect_endpoint,load_endpoint,now


def main():
    root=Path(__file__).resolve().parents[2]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=root/'Paper/data/pr-process-2026-10-13')
    parser.add_argument('--max-requests',type=int,default=200)
    args=parser.parse_args()
    if args.max_requests<1:
        parser.error('Request budget must be positive')
    directory=args.input
    source=directory/'sample.json'
    sample=json.loads(source.read_text())
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    path=directory/'timeline_manifest.json'
    manifest=json.loads(path.read_text()) if path.exists() else dict(complete=False,started_at_utc=now())
    if manifest.get('sample_sha256',digest)!=digest:
        raise ValueError('Pinned sample changed; use a different snapshot directory')
    manifest.update(sample_sha256=digest,planned_prs=len(sample),complete=False,
                    interpretation='Timeline coverage is separate from review/comment coverage')
    client=Client(args.max_requests)
    print('Authentication source: '+client.auth_source,flush=True)
    try:
        for item in sample:
            number=item['number']
            url=f'https://api.github.com/repos/juanfont/headscale/issues/{number}/timeline?per_page=100'
            endpoint=directory/'pulls'/str(number)/'timeline'
            collect_endpoint(client,endpoint,url,manifest)
            load_endpoint(endpoint)
        manifest['complete']=True
        manifest.pop('last_error',None)
    except BudgetReached:
        manifest['last_error']='Per-run request budget reached; resume with the same arguments'
    except (urllib.error.URLError,ValueError,OSError) as error:
        manifest['last_error']=type(error).__name__
        if isinstance(error,urllib.error.HTTPError):
            manifest['last_http_status']=error.code
        print('Incomplete timeline snapshot: '+type(error).__name__,file=sys.stderr)
    finally:
        statuses=[json.loads((directory/'pulls'/str(r['number'])/'timeline/coverage.json').read_text())
                  for r in sample if (directory/'pulls'/str(r['number'])/'timeline/coverage.json').exists()]
        manifest['completed_prs']=sum(r['complete'] for r in statuses)
        manifest.update(last_run_at_utc=now(),last_run_requests=client.requests)
        if manifest['complete']:
            manifest['finished_at_utc']=max(page['fetched_at_utc'] for r in statuses for page in r['pages'])
        path.write_text(json.dumps(manifest,indent=2)+'\n')
    if not manifest['complete']:
        sys.exit(2)
    print('Completed PR timelines.')


if __name__=='__main__':
    main()
