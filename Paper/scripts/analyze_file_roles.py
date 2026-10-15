"""Require complete file membership before comparing roles within artifact scopes."""
import argparse
import csv
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

from analyze import artifact,concentration,write_csv
from analyze_review_census import paired_role_counts
from collect_process import load_endpoint


def touched_scopes(files):
    scopes={'all_merged'};paths=[]
    for file in files:
        for path in [file['filename'],file.get('previous_filename')]:
            if not path:continue
            paths.append(path)
            kind=artifact(path)
            if kind!='other':scopes.add(kind+'_touched')
            basename=path.rsplit('/',1)[-1]
            if path.endswith(('.yml','.yaml','.json','.toml','.nix')) or basename.startswith('Dockerfile') or basename in ['go.mod','go.sum','flake.lock','Makefile']:
                scopes.add('configuration_or_infrastructure_touched')
    if 'production_go_touched' in scopes and paths and all(path.endswith('.go') for path in paths):
        scopes.add('production_with_only_go_files')
    return scopes


def main():
    root=Path(__file__).resolve().parents[2]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=root/'Paper/data/pr-files-2026-10-13')
    parser.add_argument('--reviews',type=Path,default=root/'Paper/results/review-census')
    parser.add_argument('--output',type=Path,default=root/'Paper/results/file-role-sensitivity')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    manifest=json.loads((args.input/'manifest.json').read_text())
    plan=json.loads((args.input/'plan.json').read_text())
    missing=[];sources={};files_by_pr={}
    for item in plan:
        folder=args.input/'pulls'/str(item['number'])/'files';status=folder/'coverage.json'
        if not status.exists() or not json.loads(status.read_text())['complete']:
            missing.append(item['number']);continue
        files=load_endpoint(folder)
        if len(files)!=item['expected_files']:
            raise ValueError('Pinned file count mismatch')
        files_by_pr[item['number']]=files
        sources[str(status.relative_to(root))]=hashlib.sha256(status.read_bytes()).hexdigest()
    coverage=dict(planned_prs=len(plan),complete_file_lists=len(files_by_pr),missing=missing,
                   complete=not missing and manifest['complete'])
    (args.output/'coverage.json').write_text(json.dumps(coverage,indent=2)+'\n')
    if not coverage['complete']:
        print(f'File lists incomplete: {len(files_by_pr)}/{len(plan)}. Component role estimates withheld.')
        sys.exit(2)
    review_summary=json.loads((args.reviews/'summary.json').read_text())
    if not review_summary['complete'] or not review_summary['lifecycle_reconstructed']:
        raise ValueError('Complete lifecycle-bounded review observations required')
    review_csv=args.reviews/'human_pr_formal_review_observations.csv'
    with review_csv.open() as handle:merged=[r for r in csv.DictReader(handle) if r['eventual_merge']=='1']
    if {int(r['number']) for r in merged}!=set(files_by_pr):
        raise ValueError('File and review populations differ')
    groups=defaultdict(list);membership=[]
    for row in merged:
        scopes=touched_scopes(files_by_pr[int(row['number'])])
        membership.append(dict(number=row['number'],merge_quarter=row['merge_quarter'],
                               touched_scopes=';'.join(sorted(scopes))))
        for scope in scopes:groups[(row['merge_quarter'],scope)].append(row)
    estimates=[];actors=[]
    for (q,scope),group in sorted(groups.items()):
        reviewed=[r for r in group if r['human_reviewers']]
        counts=paired_role_counts(reviewed)
        item=dict(merge_quarter=q,artifact_scope=scope,merged_prs=len(group),reviewed_merged_prs=len(reviewed),
                   unattributed_review_records=sum(int(r['unknown_actor_records']) for r in group))
        for role,values in counts.items():
            c=concentration(values)
            item.update({role+'_hhi':c['hhi'],role+'_accounts':c['entities'],role+'_credits':c['events']})
            actors.extend(dict(merge_quarter=q,artifact_scope=scope,role=role,account=account,credit=credit)
                          for account,credit in sorted(values.items()))
        estimates.append(item)
    write_csv(args.output/'quarterly_roles_by_artifact_scope.csv',estimates)
    write_csv(args.output/'pr_scope_membership.csv',membership)
    write_csv(args.output/'role_account_credits_by_scope.csv',actors)
    summary=dict(complete=True,merged_human_prs=len(merged),
                  interpretation='Roles on matched reviewed PRs within nonexclusive final-file scopes',
                  limitations=['One PR can touch multiple artifact categories; categories are not additive.',
                               'Final file membership is descriptive and is not a submission-time model covariate.',
                               'Both old and new paths of renames contribute touched categories.',
                               'Fractional review credit is shared among attributable reviewers, not measured effort.',
                               'Git commits and PRs remain different event units even within production Go.'],
                  provenance=dict(script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                      classifier_script_sha256=hashlib.sha256((Path(__file__).parent/'analyze.py').read_bytes()).hexdigest(),
                      review_csv_sha256=hashlib.sha256(review_csv.read_bytes()).hexdigest(),
                      source_coverage_sha256=sources))
    (args.output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps({k:v for k,v in summary.items() if k!='provenance'},indent=2))


if __name__=='__main__':main()
