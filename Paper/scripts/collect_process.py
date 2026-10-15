#!/usr/bin/env python3
"""Resumable public PR process collector with a hard per-run request budget.

List data cover all PRs. Detailed events are a deterministic stratified pilot
unless --all-details is explicitly supplied. Missing data never become zero
latency. Authentication reads environment or the protected local configuration.
"""
import argparse
import hashlib
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from github_auth import configured_token


def now():
    return datetime.now(timezone.utc).isoformat()


class BudgetReached(Exception):
    pass


class Client:
    def __init__(self, budget, anonymous=False):
        self.requests = 0
        self.budget = budget
        self.headers = {'User-Agent': 'headscale-paper-research',
                        'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'}
        token, self.auth_source = (None, 'explicit anonymous mode') if anonymous else configured_token()
        if token:
            self.headers['Authorization'] = 'Bearer ' + token

    def get(self, url):
        if urllib.parse.urlparse(url).netloc != 'api.github.com':
            raise ValueError('Refusing a non-GitHub API pagination destination')
        for attempt in range(3):
            if self.requests >= self.budget:
                raise BudgetReached()
            self.requests += 1
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=self.headers), timeout=30) as response:
                    raw = response.read()
                    link = response.headers.get('Link', '')
                    match = re.search(r'<([^>]+)>;\s*rel="next"', link)
                    return raw, match.group(1) if match else None, response.headers.get('X-RateLimit-Remaining')
            except urllib.error.HTTPError as error:
                if error.code not in (500, 502, 503, 504) or attempt == 2:
                    raise
            except (urllib.error.URLError, TimeoutError):
                if attempt == 2:
                    raise
            time.sleep(2 ** attempt)


def collect_endpoint(client, directory, start_url, manifest):
    directory.mkdir(parents=True, exist_ok=True)
    status_path = directory / 'coverage.json'
    status = json.loads(status_path.read_text()) if status_path.exists() else {
        'start_url': start_url, 'next_url': start_url, 'pages': [], 'complete': False}
    if status['start_url'] != start_url:
        raise ValueError('Cached endpoint differs from requested endpoint')
    if status['complete']:
        return
    while status['next_url']:
        raw, next_url, quota = client.get(status['next_url'])
        data = json.loads(raw)
        if not isinstance(data, (dict, list)):
            raise ValueError('Unexpected API JSON shape')
        name = f'page_{len(status["pages"])+1:04d}.json'
        (directory / name).write_bytes(raw)
        status['pages'].append(dict(file=name, sha256=hashlib.sha256(raw).hexdigest(), fetched_at_utc=now()))
        status['next_url'] = next_url
        status['complete'] = not next_url
        status_path.write_text(json.dumps(status, indent=2) + '\n')
        manifest['last_quota_remaining'] = quota
        print(f'{directory.name}: {len(status["pages"])} page(s), quota {quota}', flush=True)


def load_endpoint(directory):
    status = json.loads((directory / 'coverage.json').read_text())
    if not status['complete']:
        raise ValueError('Incomplete endpoint cannot supply analysis input')
    result = []
    for page in status['pages']:
        raw = (directory / page['file']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != page['sha256']:
            raise ValueError('Cached endpoint checksum mismatch')
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
        result.extend(data)
    return result


def choose_sample(prs, per_year, seed):
    strata = {}
    for pr in prs:
        strata.setdefault(pr['created_at'][:4], []).append(pr['number'])
    rng = random.Random(seed)
    sample = []
    for year, numbers in sorted(strata.items()):
        numbers.sort()
        chosen = sorted(rng.sample(numbers, min(per_year, len(numbers))))
        sample.extend(dict(number=n, year=year, stratum_size=len(numbers),
                           inclusion_probability=len(chosen) / len(numbers)) for n in chosen)
    return sample


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--max-requests', type=int, default=50)
    p.add_argument('--per-year', type=int, default=12)
    p.add_argument('--seed', type=int, default=20261013)
    p.add_argument('--list-only', action='store_true')
    p.add_argument('--all-details', action='store_true')
    p.add_argument('--detail-only', action='store_true', help='Collect all selected PR metadata first; process events remain incomplete')
    p.add_argument('--reviews-only', action='store_true', help='Collect metadata and submitted reviews; comment endpoints remain separate')
    p.add_argument('--anonymous', action='store_true', help='Explicitly ignore configured credentials for public reads')
    args = p.parse_args()
    if args.detail_only and args.reviews_only:
        p.error('--detail-only and --reviews-only are mutually exclusive')
    if args.max_requests < 1 or args.per_year < 1:
        p.error('Budgets and sample sizes must be positive')
    args.output.mkdir(parents=True, exist_ok=True)
    path = args.output / 'manifest.json'
    manifest = json.loads(path.read_text()) if path.exists() else {
        'schema_version': 1, 'repository': 'juanfont/headscale', 'started_at_utc': now(),
        'complete': False, 'list_complete': False, 'detailed_complete': False,
        'limitations': ['API collection is not an atomic snapshot.',
                        'Detailed sample is a pilot, not sufficient evidence for population-wide conclusions.',
                        'Reviews/comments may be deleted or edited; draft readiness needs timeline data.',
                        'PR activity does not establish issue repair linkage.']}
    client = Client(args.max_requests, args.anonymous)
    print('Authentication source: ' + client.auth_source, flush=True)
    prefix = 'https://api.github.com/repos/juanfont/headscale'
    try:
        collect_endpoint(client, args.output / 'pr_list', prefix + '/pulls?state=all&sort=created&direction=asc&per_page=100', manifest)
        prs = load_endpoint(args.output / 'pr_list')
        if len(prs) != len({pr['number'] for pr in prs}):
            raise ValueError('Duplicate PR numbers in list pagination')
        manifest.update(list_complete=True, pull_requests=len(prs))
        if args.all_details:
            sample = [dict(number=pr['number'], year=pr['created_at'][:4], inclusion_probability=1.0) for pr in sorted(prs, key=lambda x: x['number'])]
        else:
            sample = choose_sample(prs, args.per_year, args.seed)
        sample_path = args.output / 'sample.json'
        if sample_path.exists() and json.loads(sample_path.read_text()) != sample:
            raise ValueError('Sampling configuration changed; select a new output directory')
        sample_path.write_text(json.dumps(sample, indent=2) + '\n')
        manifest.update(detail_scope='all' if args.all_details else 'year-stratified pilot',
                        sample_size=len(sample), seed=args.seed, per_year=args.per_year)
        if not args.list_only:
            for item in sample:
                n = item['number']
                directory = args.output / 'pulls' / str(n)
                endpoints=[('detail', f'{prefix}/pulls/{n}'),
                                  ('reviews', f'{prefix}/pulls/{n}/reviews?per_page=100'),
                                  ('issue_comments', f'{prefix}/issues/{n}/comments?per_page=100'),
                                  ('review_comments', f'{prefix}/pulls/{n}/comments?per_page=100')]
                if args.detail_only:
                    endpoints=endpoints[:1]
                elif args.reviews_only:
                    endpoints=endpoints[:2]
                for name, url in endpoints:
                    collect_endpoint(client, directory / name, url, manifest)
            manifest['metadata_complete']=True
            if not args.detail_only and not args.reviews_only:
                manifest.update(detailed_complete=True, complete=True, finished_at_utc=now())
            elif args.detail_only:
                manifest['metadata_finished_at_utc']=now()
            else:
                manifest['review_census_complete']=True
                manifest['review_finished_at_utc']=now()
        manifest.pop('last_error', None)
    except BudgetReached:
        manifest['last_error'] = 'Per-run request budget reached; resume with the same arguments'
        print(manifest['last_error'], flush=True)
    except (urllib.error.URLError, ValueError, OSError) as error:
        manifest['last_error'] = type(error).__name__
        if isinstance(error, urllib.error.HTTPError):
            manifest['last_http_status'] = error.code
            manifest['rate_limit_reset_epoch'] = error.headers.get('X-RateLimit-Reset')
        if isinstance(error, ValueError):
            manifest['last_validation_error'] = str(error)
        print(f'Incomplete process snapshot: {type(error).__name__}', file=sys.stderr)
    finally:
        manifest.update(last_run_at_utc=now(), last_run_requests=client.requests)
        path.write_text(json.dumps(manifest, indent=2) + '\n')
    phase_complete=manifest.get('metadata_complete') if args.detail_only else manifest.get('review_census_complete') if args.reviews_only else manifest['detailed_complete']
    if not manifest['list_complete'] or (not args.list_only and not phase_complete):
        sys.exit(2)


if __name__ == '__main__':
    main()
