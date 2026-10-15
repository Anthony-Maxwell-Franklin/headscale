#!/usr/bin/env python3
"""Research measurements with explicit censoring and rename sensitivity.

Issue results measure closure, not verified repair. Review results are exported
only for endpoints whose raw pagination is complete and checksum-valid.
"""
import argparse
import csv
import hashlib
import json
import random
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from analyze import artifact, concentration, parse_time, percentile, quarter, read_commits, write_csv


def dump(path, data):
    path.write_text(json.dumps(data, indent=2) + '\n')


def preserve_coding(path, sample):
    """A rerun may regenerate selection metadata, but must retain human coding."""
    if not path.exists():
        return sample
    with path.open(encoding='utf-8',newline='') as handle:
        existing=list(csv.DictReader(handle))
    by_number={int(r['number']):r for r in existing}
    if set(by_number)!={int(r['number']) for r in sample}:
        raise ValueError('Pinned validation-sample membership changed; choose a new output directory')
    fields=('coder_1_defect_eligible','coder_2_defect_eligible','coder_1_outcome',
            'coder_2_outcome','adjudicated_outcome','repair_evidence_url','reopened_evidence','notes')
    for r in sample:
        prior=by_number[int(r['number'])]
        if prior['stratum']!=r['stratum'] or abs(float(prior['inclusion_probability'])-float(r['inclusion_probability']))>1e-12:
            raise ValueError('Pinned validation sampling design changed')
        for field in fields:
            r[field]=prior.get(field,'')
    return sample


def verified_issue_snapshot(directory):
    manifest = json.loads((directory / 'manifest.json').read_text())
    if not manifest['complete']:
        raise ValueError('Issue list snapshot is incomplete')
    records = []
    for page in manifest['pages']:
        raw = (directory / page['file']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != page['sha256']:
            raise ValueError('Issue snapshot checksum mismatch')
        records.extend(json.loads(raw))
    if len(records) != len({r['number'] for r in records}):
        raise ValueError('Duplicate issue numbers')
    return manifest, records


def km_curve(observations):
    """Product-limit closure estimator; ties remove events before censors.

    Returns Greenwood log-log 95% intervals without external dependencies.
    All observations are (nonnegative duration days, closure observed boolean).
    """
    import math
    if any(t < 0 for t, _ in observations):
        raise ValueError('Negative follow-up duration')
    groups = defaultdict(lambda: [0, 0])
    for duration, event in observations:
        groups[duration][0 if event else 1] += 1
    risk, survival, greenwood = len(observations), 1., 0.
    rows = [dict(time_days=0., at_risk=risk, closures=0, censored=0,
                 probability_still_open=1., lower_95=1., upper_95=1.)]
    for t, (events, censors) in sorted(groups.items()):
        if events:
            survival *= 1 - events / risk
            if risk > events:
                greenwood += events / (risk * (risk - events))
        lower = upper = survival
        if 0 < survival < 1:
            se = math.sqrt(greenwood) / abs(math.log(survival))
            loglog = math.log(-math.log(survival))
            lower = math.exp(-math.exp(loglog + 1.95996398454 * se))
            upper = math.exp(-math.exp(loglog - 1.95996398454 * se))
        rows.append(dict(time_days=t, at_risk=risk, closures=events, censored=censors,
                         probability_still_open=survival, lower_95=lower, upper_95=upper))
        risk -= events + censors
    return rows


def rename_edges(repo, upper):
    raw = subprocess.check_output(['git', '-C', str(repo), 'log', '--no-merges',
                                   '--format=%x1e%H%x1f%ct', '--name-status', '-z', '-M', upper])
    tokens = raw.decode('utf-8', errors='surrogateescape').split('\0')
    edges, sha, ts, i = [], None, None, 0
    while i < len(tokens):
        token = tokens[i].strip('\n')
        i += 1
        if not token:
            continue
        if token.startswith('\x1e'):
            sha, timestamp = token[1:].split('\x1f')
            ts = int(timestamp)
            continue
        status = token
        if status.startswith(('R', 'C')):
            old, new = tokens[i:i+2]
            i += 2
            if status.startswith('R'):
                edges.append(dict(sha=sha, ts=ts, old_path=old, new_path=new, similarity=int(status[1:])))
        else:
            i += 1
    return edges


def linked_paths(edges):
    parent = {}
    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for e in edges:
        a, b = find(e['old_path']), find(e['new_path'])
        if a != b:
            parent[max(a, b)] = min(a, b)
    return {path: find(path) for path in parent}


def git_research(repo, upper, output, identity_map=None, clock='committer'):
    commits = read_commits(repo, upper, identity='email', clock=clock)
    validated={}
    if identity_map and identity_map.exists():
        data=json.loads(identity_map.read_text())
        validated={r['email_identity_hash']:r['canonical_identity'] for r in data['mapping'] if r['validated']}
    edges = rename_edges(repo, upper)
    mapping = linked_paths(edges)
    dump(output / 'rename_edges.json', edges)
    buckets = defaultdict(list)
    for c in commits:
        buckets[quarter(c['ts'])].append(c)
    rows = []
    for period, group in sorted(buckets.items()):
        # Quarter-specific links avoid propagating future renames into an earlier
        # exposure. Git's rename inference remains a sensitivity, not identity truth.
        end_of_group = max(c['ts'] for c in group)
        period_mapping = linked_paths([e for e in edges if e['ts'] <= end_of_group])
        for category in ('all', 'production_go', 'test', 'documentation', 'generated', 'other'):
            touches = Counter()
            authors = Counter()
            linked_authors=Counter()
            linked = Counter()
            for c in group:
                paths = {f for f in c['files'] if category == 'all' or artifact(f) == category}
                if paths:
                    authors[c['author']] += 1
                    linked_authors[validated.get(c['author'],c['author'])]+=1
                    touches.update(paths)
                    linked.update({period_mapping.get(f, f) for f in paths})
            raw, ren, auth = concentration(touches), concentration(linked), concentration(authors)
            linked_auth=concentration(linked_authors)
            rows.append(dict(quarter=period, category=category, commits=auth['events'],
                             active_author_identities=auth['entities'], author_hhi=auth['hhi'],
                             effective_authors=auth['effective_entities'], top_author_percent=auth['top1_percent'],
                             top_two_authors_percent=auth['top2_percent'], raw_path_hhi=raw['hhi'],
                             core_alias_linked_author_hhi=linked_auth['hhi'],
                             core_alias_linked_effective_authors=linked_auth['effective_entities'],
                             core_alias_linked_author_identities=linked_auth['entities'],
                             core_alias_linked_top_author_percent=linked_auth['top1_percent'],
                             rename_linked_path_hhi=ren['hhi'], raw_changed_paths=raw['entities'],
                             rename_linked_changed_paths=ren['entities']))
    write_csv(output / 'quarterly_rename_sensitivity.csv', rows)
    live = set(subprocess.check_output(['git', '-C', str(repo), 'ls-tree', '-rz', '--name-only', upper]).decode().split('\0'))
    live_representatives = defaultdict(list)
    for path in sorted(live):
        if path:
            live_representatives[mapping.get(path, path)].append(path)
    total = Counter()
    for c in commits:
        total.update({mapping.get(f, f) for f in c['files'] if artifact(f) == 'production_go'})
    top = [dict(rename_group=key, live_paths=';'.join(live_representatives.get(key, [])),
                historical_commit_touches=count, exists_at_endpoint=bool(live_representatives.get(key)))
           for key, count in total.most_common(30)]
    write_csv(output / 'historical_production_activity_groups.csv', top)
    return dict(commits=len(commits), detected_rename_events=len(edges),
                timestamp_rule=clock,
                validated_core_aliases=len(validated),
                identity_mapping_sha256=hashlib.sha256(identity_map.read_bytes()).hexdigest() if identity_map and identity_map.exists() else None,
                rename_method='Git -M default 50% similarity; transitive path grouping, not validated file identity')


def issue_research(directory, output):
    manifest, records = verified_issue_snapshot(directory)
    # Use completed collection time; do not falsely reconstruct release-time state.
    cutoff = parse_time(manifest['finished_at_utc'])
    issues = [r for r in records if 'pull_request' not in r]
    bugs = [r for r in issues if any(l['name'].casefold() == 'bug' for l in r['labels'])]
    observations, rows = [], []
    for r in bugs:
        created = parse_time(r['created_at'])
        if created > cutoff:
            raise ValueError('Issue created after observation cutoff')
        closed = parse_time(r['closed_at']) if r['closed_at'] else None
        event = r['state'] == 'closed' and closed is not None and closed <= cutoff
        duration = ((closed if event else cutoff) - created) / 86400
        if duration < 0:
            raise ValueError('Negative issue follow-up')
        observations.append((duration, event))
        rows.append(dict(number=r['number'], url=r['html_url'], title=r['title'],
                         created_at=r['created_at'], closed_at=r['closed_at'],
                         creation_quarter=quarter(created), observed_state=r['state'],
                         observed_state_reason=r.get('state_reason'), followup_days=duration,
                         closure_event_observed=int(event), repair_verified='not_assessed',
                         label_basis='bug label at collection time'))
    write_csv(output / 'bug_closure_observations.csv', rows)
    curve = km_curve(observations)
    write_csv(output / 'bug_time_to_closure_km.csv', curve)
    med = next((r['time_days'] for r in curve if r['probability_still_open'] <= .5), None)
    strata = defaultdict(list)
    for r in rows:
        strata[(r['created_at'][:4], r['observed_state'], r['observed_state_reason'])].append(r)
    rng = random.Random(20261013)
    sample = []
    for key, group in sorted(strata.items(), key=lambda item: str(item[0])):
        selected = rng.sample(sorted(group, key=lambda r:r['number']), min(8, len(group)))
        for r in selected:
            sample.append(dict(number=r['number'], url=r['url'], title=r['title'],
                               stratum=str(key), inclusion_probability=len(selected)/len(group),
                               coder_1_defect_eligible='', coder_2_defect_eligible='',
                               coder_1_outcome='', coder_2_outcome='', adjudicated_outcome='',
                               repair_evidence_url='', reopened_evidence='', notes=''))
    sample=preserve_coding(output / 'issue_validation_sample.csv',sample)
    write_csv(output / 'issue_validation_sample.csv', sample)
    cohort = []
    for q in sorted({r['creation_quarter'] for r in rows}):
        group = [r for r in rows if r['creation_quarter']==q]
        closed = [r['followup_days'] for r in group if r['closure_event_observed']]
        cohort.append(dict(creation_quarter=q, issues=len(group), closed=len(closed),
                           open_censored=len(group)-len(closed),
                           closed_only_median_days=percentile(closed,.5),
                           closed_only_mean_days=sum(closed)/len(closed) if closed else None))
    write_csv(output / 'bug_creation_cohorts.csv', cohort)
    summary = dict(issues=len(issues), currently_bug_labeled=len(bugs),
                   open_censored=sum(not e for _,e in observations),
                   closed=sum(e for _,e in observations), closure_km_median_days=med,
                   validation_sample_size=len(sample), observation_cutoff=manifest['finished_at_utc'],
                   observed_closure_reasons=dict(Counter(r.get('state_reason') or 'unknown' for r in bugs)),
                   interpretation='Time to last observed issue closure, not MTTR or verified repair time',
                   limitations=['Current bug labels may differ from labels at issue creation.',
                                'Reopening history is not reconstructed; latest closure can include multiple episodes.',
                                'Current API state is observed over a non-atomic collection interval.',
                                'Open issues are right-censored; closed-only cohort statistics are descriptive.',
                                'Sample coding is pending; administrative closures are not yet separated from fixes.'])
    dump(output / 'issue_summary.json', summary)
    return summary


def main():
    p=argparse.ArgumentParser(description=__doc__)
    root=Path(__file__).resolve().parents[2]
    p.add_argument('--repo',type=Path,default=root/'Paper/data/headscale.git')
    p.add_argument('--upper',default='v0.29.5')
    p.add_argument('--issues',type=Path,default=root/'Paper/data/github-2026-10-13')
    p.add_argument('--output',type=Path,default=root/'Paper/results/research-v0.29.5')
    p.add_argument('--identity-map',type=Path,default=root/'Paper/data/validated_core_identities.json')
    p.add_argument('--clock',choices=['author','committer'],default='committer')
    args=p.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    git_summary=git_research(args.repo,args.upper,args.output,args.identity_map,args.clock)
    issue_summary=issue_research(args.issues,args.output)
    dump(args.output/'research_manifest.json',dict(
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        endpoint_tag=args.upper,
        endpoint_sha=subprocess.check_output(['git','-C',str(args.repo),'rev-parse',args.upper+'^{commit}'],text=True).strip(),
        git=git_summary,issues=issue_summary,
        analysis_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        issue_manifest_sha256=hashlib.sha256((args.issues/'manifest.json').read_bytes()).hexdigest(),
        readiness='Descriptive research measurements; validation, process models and manuscript remain incomplete'))
    print(json.dumps({'git':git_summary,'issues':issue_summary},indent=2))


if __name__=='__main__':
    main()
