#!/usr/bin/env python3
"""Collect timestamped comments/timelines for the pinned issue validation sample."""
import argparse
import csv
import hashlib
import json
import sys
import urllib.error
from pathlib import Path

from collect_process import BudgetReached, Client, collect_endpoint, now


def main():
    root=Path(__file__).resolve().parents[2]
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sample',type=Path,default=root/'Paper/results/research-v0.29.5/issue_validation_sample.csv')
    p.add_argument('--output',type=Path,default=root/'Paper/data/issue-evidence-2026-10-13')
    p.add_argument('--max-requests',type=int,default=350)
    args=p.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    path=args.output/'manifest.json'
    sample=list(csv.DictReader(args.sample.open()))
    sha=hashlib.sha256(args.sample.read_bytes()).hexdigest()
    design=[{k:r[k] for k in ('number','stratum','inclusion_probability')} for r in sample]
    design_sha=hashlib.sha256(json.dumps(design,sort_keys=True).encode()).hexdigest()
    manifest=json.loads(path.read_text()) if path.exists() else {
        'repository':'juanfont/headscale','started_at_utc':now(),'complete':False,
        'sample_sha256':sha,'sample_size':len(sample),
        'limitations':['Timeline cross-reference is not by itself verified defect repair.',
                       'Edited/deleted comments and events may be missing.',
                       'Human outcome coding and independent validation remain pending.']}
    if manifest.get('sampling_design_sha256',design_sha)!=design_sha:
        raise ValueError('Pinned sample changed; select a new evidence directory')
    if 'sampling_design_sha256' not in manifest and manifest['sample_sha256']!=sha:
        raise ValueError('Legacy sample hash changed before its design could be pinned')
    manifest['sampling_design_sha256']=design_sha
    manifest['sampling_design']=design
    if manifest['complete']:
        path.write_text(json.dumps(manifest,indent=2)+'\n')
        print('Existing complete issue-evidence snapshot retained; annotations do not alter sampling membership.')
        return
    client=Client(args.max_requests)
    try:
        for row in sample:
            number=int(row['number'])
            prefix=f'https://api.github.com/repos/juanfont/headscale/issues/{number}'
            directory=args.output/'issues'/str(number)
            for name,url in [('comments',prefix+'/comments?per_page=100'),
                             ('timeline',prefix+'/timeline?per_page=100')]:
                collect_endpoint(client,directory/name,url,manifest)
        manifest.update(complete=True,finished_at_utc=now())
        manifest.pop('last_error',None)
    except BudgetReached:
        manifest['last_error']='Per-run request budget reached'
    except (urllib.error.URLError,ValueError,OSError) as error:
        manifest['last_error']=type(error).__name__
        if isinstance(error,urllib.error.HTTPError):
            manifest['last_http_status']=error.code
        print('Issue evidence collection incomplete: '+type(error).__name__,file=sys.stderr)
    finally:
        manifest.update(last_run_requests=client.requests,last_run_at_utc=now())
        path.write_text(json.dumps(manifest,indent=2)+'\n')
    if not manifest['complete']:
        sys.exit(2)


if __name__=='__main__':
    main()
