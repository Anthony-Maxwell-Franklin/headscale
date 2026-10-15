#!/usr/bin/env python3
"""Dependency-free CW reproduction and a separate quarterly research analysis.

Legacy results preserve the old definitions, including their limitations. The
research export calls quantities by what they measure, not by causal constructs.
"""
import argparse
import csv
import hashlib
import json
import math
import re
import statistics
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIX = re.compile(r'\b(fix|bug|panic|regression|hotfix|repair|broken|flaky|failure|failing|error)\b', re.I)


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], text=True)


def percentile(values, p):
    if not values:
        return None
    s = sorted(values)
    rank = (len(s) - 1) * p
    low = int(rank)
    high = min(low + 1, len(s) - 1)
    return s[low] + (s[high] - s[low]) * (rank - low)


def stats(values):
    return dict(samples=len(values), mean_days=statistics.mean(values) if values else None,
                median_days=percentile(values, .5), p75_days=percentile(values, .75))


def concentration(counts):
    n = sum(counts.values())
    values = sorted(counts.values(), reverse=True)
    hhi = sum((v / n) ** 2 for v in values) if n else None
    k50, accumulated = 0, 0
    for v in values:
        accumulated += v
        k50 += 1
        if accumulated >= n / 2:
            break
    return dict(events=n, entities=len(values), hhi=hhi,
                effective_entities=1 / hhi if hhi else None,
                top1_percent=100 * sum(values[:1]) / n if n else None,
                top2_percent=100 * sum(values[:2]) / n if n else None,
                top10_percent=100 * sum(values[:10]) / n if n else None,
                contributors_for_50_percent=k50)


def read_commits(repo, revision, identity='raw_name', clock='author'):
    # RS/US delimit the header; Git quotes unusual path names. Decode below.
    name, email = ('%an', '%ae') if identity == 'raw_name' else ('%aN', '%aE')
    timestamp = '%at' if clock == 'author' else '%ct'
    fmt = f'%x1e%H%x1f{name}%x1f{email}%x1f{timestamp}%x1f%s'
    args = ['log', '--no-merges', '--format=' + fmt, '--name-only', revision]
    text = git(repo, *args)
    commits = []
    for block in text.split('\x1e')[1:]:
        header, _, tail = block.partition('\n')
        sha, author, email, ts, subject = header.split('\x1f', 4)
        if '[bot]' in author:
            continue
        files = set()
        for line in tail.splitlines():
            if not line:
                continue
            if line.startswith('"'):
                # Octal escapes in Git quoted paths cannot safely be JSON-decoded.
                raise ValueError('Quoted Git path encountered; path parser must be extended')
            files.add(line)
        key = author.casefold() if identity == 'raw_name' else hashlib.sha256(email.lower().encode()).hexdigest()
        commits.append(dict(sha=sha, author=key, author_name=author, ts=int(ts), subject=subject, files=files))
    return commits


def legacy_metrics(cumulative, segment):
    # PowerShell hashtables and Sort-Object -Unique use case-insensitive keys.
    # Keep this compatibility rule out of the case-sensitive Git research data.
    displays={f.casefold():f for c in cumulative for f in c['files']}
    touches = Counter(f for c in cumulative for f in {p.casefold() for p in c['files']})
    authors = Counter(c['author'] for c in cumulative)
    file_events = defaultdict(list)
    for c in segment:
        for f in {p.casefold() for p in c['files']}:
            file_events[f].append(c)
    transitions, handoffs = 0, []
    for events in file_events.values():
        events.sort(key=lambda c: c['ts'])
        for a, b in zip(events, events[1:]):
            transitions += 1
            if a['author'] != b['author']:
                handoffs.append((b['ts'] - a['ts']) / 86400)
    last, lags = {}, []
    for c in sorted(segment, key=lambda c: c['ts']):
        paths={f.casefold() for f in c['files']}
        prior = [last[f] for f in paths if f in last]
        if prior and FIX.search(c['subject']):
            lags.append((c['ts'] - max(prior)) / 86400)
        for f in paths:
            last[f] = c['ts']
    m1, m2 = concentration(touches), concentration(authors)
    # Original script rounds HHI before taking its reciprocal.
    m2['legacy_effective_authors'] = round(1 / round(m2['hhi'], 4), 2) if m2['hhi'] else None
    m3stats = stats(handoffs)
    if handoffs:
        ordered = sorted(handoffs)
        # Match PowerShell's nearest-rank P75 and integer-cast rounding in
        # Get-Median. Retain the correct median separately for the audit.
        m3stats['mathematical_median_days'] = m3stats['median_days']
        if len(ordered) % 2:
            m3stats['median_days'] = ordered[round(len(ordered) / 2)]
        m3stats['p75_days'] = ordered[math.ceil(.75 * len(ordered)) - 1]
    return dict(m1=m1, top_files=[(displays[f],n) for f,n in touches.most_common(3)], m2=m2,
                m3=dict(handoff_percent=100 * len(handoffs) / transitions if transitions else 0,
                        **m3stats), m4a=stats(lags))


def parse_time(s):
    if '/' in s:
        d = datetime.strptime(s, '%Y/%m/%d %H:%M:%S').replace(tzinfo=timezone.utc)
    else:
        d = datetime.fromisoformat(s.replace('Z', '+00:00'))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


def load_legacy_issues(path):
    with path.open(encoding='utf-8-sig', newline='') as f:
        return [dict(number=int(r['Number']), created=parse_time(r['CreatedAt']),
                     closed=parse_time(r['ClosedAt'])) for r in csv.DictReader(f)]


def write_csv(path, rows):
    if not rows:
        return
    with path.open('w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def quarter(ts):
    d = datetime.fromtimestamp(ts, timezone.utc)
    return f'{d.year}-Q{(d.month - 1) // 3 + 1}'


def artifact(path):
    if path.endswith('.go'):
        if path.startswith('gen/') or path.endswith(('_generated.go', '_view.go', '_clone.go')):
            return 'generated'
        if path.endswith('_test.go'):
            return 'test'
        return 'production_go'
    if path.endswith(('.md', '.rst', '.txt', '.tex')) or path.startswith('docs/'):
        return 'documentation'
    return 'other'


def quarterly(repo, upper):
    # Email-based identity is a sensitivity analysis, not a validated developer
    # identity oracle. No raw emails are written to the output artifacts.
    commits = read_commits(repo, upper, identity='email', clock='committer')
    buckets = defaultdict(list)
    for c in commits:
        buckets[quarter(c['ts'])].append(c)
    rows = []
    for period, group in sorted(buckets.items()):
        for category in ('all', 'production_go', 'test', 'documentation', 'generated', 'other'):
            selected = [(c, {f for f in c['files'] if category == 'all' or artifact(f) == category}) for c in group]
            selected = [(c, fs) for c, fs in selected if fs]
            touches = Counter(f for _, fs in selected for f in fs)
            authors = Counter(c['author'] for c, _ in selected)
            fc, ac = concentration(touches), concentration(authors)
            rows.append(dict(period=period, category=category, commits=len(selected),
                             active_author_identities=ac['entities'], author_hhi=ac['hhi'],
                             author_top1_percent=ac['top1_percent'], author_top2_percent=ac['top2_percent'],
                             effective_authors=ac['effective_entities'], contributors_for_50_percent=ac['contributors_for_50_percent'],
                             changed_paths=fc['entities'], file_touch_events=fc['events'], file_hhi=fc['hhi'],
                             file_top10_percent=fc['top10_percent']))
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', type=Path, default=ROOT / 'Paper/data/headscale.git')
    p.add_argument('--upper', default='v0.28.0', help='Pinned stable tag; never silently advances')
    p.add_argument('--issues-csv', type=Path, default=ROOT / 'headscale_bug_only_issue_mttr_data_live.csv')
    p.add_argument('--output', type=Path, default=ROOT / 'Paper/results/v0.28.0')
    args = p.parse_args()
    if not re.fullmatch(r'v\d+\.\d+\.\d+', args.upper):
        p.error('--upper must be a stable semantic version tag')
    args.output.mkdir(parents=True, exist_ok=True)
    anchors = ['v0.1.0', 'v0.12.1', 'v0.17.1', 'v0.22.3', 'v0.28.0']
    if args.upper != 'v0.28.0':
        if tuple(map(int, args.upper[1:].split('.'))) <= (0, 28, 0):
            p.error('An additional endpoint must follow v0.28.0')
        anchors.append(args.upper)
    labels = ['Start', 'Early growth', 'Expansion', 'Maturity', 'Original cutoff', 'Update']
    issues = load_legacy_issues(args.issues_csv)
    rows, flat = [], []
    lower_ts = None
    metadata = []
    for i, tag in enumerate(anchors):
        sha = git(args.repo, 'rev-parse', tag + '^{commit}').strip()
        ts = int(git(args.repo, 'show', '-s', '--format=%at', sha).strip())
        cumulative = tag if i == 0 else f'{anchors[0]}..{tag}'
        segment = tag if i == 0 else f'{anchors[i-1]}..{tag}'
        m = legacy_metrics(read_commits(args.repo, cumulative), read_commits(args.repo, segment))
        issue_lags = [(r['closed'] - r['created']) / 86400 for r in issues
                      if r['closed'] <= ts and (lower_ts is None or r['closed'] > lower_ts)]
        m['m4b_cached_closed_issues'] = stats(issue_lags)
        rows.append(dict(stage=labels[i], tag=tag, cumulative_range=cumulative, segment_range=segment, **m))
        flat.append(dict(stage=labels[i], tag=tag, commits=m['m2']['events'], authors=m['m2']['entities'],
                         touch_events=m['m1']['events'], file_hhi=m['m1']['hhi'], author_hhi=m['m2']['hhi'],
                         author_top1_percent=m['m2']['top1_percent'], author_top2_percent=m['m2']['top2_percent'],
                         effective_authors_legacy=m['m2']['legacy_effective_authors'],
                         contributors_for_50_percent=m['m2']['contributors_for_50_percent'],
                         cross_author_edit_median_days=m['m3']['median_days'],
                         fix_previous_touch_median_days=m['m4a']['median_days'],
                         cached_issue_samples=len(issue_lags), cached_issue_mean_days=m['m4b_cached_closed_issues']['mean_days']))
        metadata.append(dict(tag=tag, sha=sha, tag_target_author_time_utc=datetime.fromtimestamp(ts, timezone.utc).isoformat()))
        lower_ts = ts
    (args.output / 'legacy_metrics.json').write_text(json.dumps(rows, indent=2) + '\n')
    write_csv(args.output / 'legacy_metrics.csv', flat)
    write_csv(args.output / 'quarterly_metrics.csv', quarterly(args.repo, args.upper))
    manifest = dict(schema_version=1, generated_at_utc=datetime.now(timezone.utc).isoformat(),
                    upstream='https://github.com/juanfont/headscale', anchors=metadata,
                    analyzer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    cached_issues_sha256=hashlib.sha256(args.issues_csv.read_bytes()).hexdigest(),
                    issue_dataset_status='Legacy closed-bug-only cache; no new issue observations collected',
                    limitations=['Legacy uses author time and raw author names.',
                                 'Quarterly uses committer time and lowercased email identities; aliases need validation.',
                                 'File concentration is activity concentration, not technical debt.',
                                 'Paths are not linked across renames; quarterly path results are descriptive only.',
                                 'Cross-author edit intervals and previous-touch lag are retained only for legacy comparison.',
                                 'Cached issue closure durations are not verified repairs and omit open issues.',
                                 'Boundary quarters may be partial; do not compare raw counts without exposure adjustment.'])
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(f'Wrote pinned analysis to {args.output}; issue data remains the legacy cache.')


if __name__ == '__main__':
    main()
