#!/usr/bin/env python3
"""Collect public issues/PR list pages using configured credentials or anonymous access.

This endpoint includes PR summaries, not review events or verified repair links.
Every page is retained verbatim. A manifest is marked complete only after the
pagination finishes. --resume continues a previously interrupted snapshot.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from github_auth import configured_token


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--resume', action='store_true')
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output / 'manifest.json'
    if manifest_path.exists() and not args.resume:
        p.error('Snapshot exists; use --resume or choose a new output directory')
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {
        'schema_version': 1, 'repository': 'juanfont/headscale',
        'started_at_utc': datetime.now(timezone.utc).isoformat(), 'complete': False,
        'endpoint': '/repos/juanfont/headscale/issues?state=all&sort=created&direction=asc&per_page=100',
        'pages': [], 'limitations': [
            'Collection is not an atomic server snapshot; records may change during pagination.',
            'Labels and state are observed at collection time, not reconstructed as of a release.',
            'PR list entries omit reviews, merge metadata and linked repair evidence.',
            'Closed issue is not equivalent to repaired defect.']}
    if manifest['complete']:
        print('Existing complete snapshot retained.')
        return
    headers = {'User-Agent': 'headscale-research-reproduction',
               'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'}
    token, auth_source = configured_token()
    if token:
        headers['Authorization'] = 'Bearer ' + token
    print('Authentication source: ' + auth_source, flush=True)
    page = len(manifest['pages']) + 1
    try:
        next_url = manifest.get('next_url')
        if not next_url and manifest['pages']:
            # Migrate older page-number snapshots: obtain the server's cursor
            # link from the last successfully collected page, without appending
            # that page a second time.
            previous_url = 'https://api.github.com' + manifest['endpoint'] + '&page=' + str(page - 1)
            with urllib.request.urlopen(urllib.request.Request(previous_url, headers=headers), timeout=45) as response:
                links = response.headers.get('Link', '')
                match = re.search(r'<([^>]+)>;\s*rel="next"', links)
                next_url = match.group(1) if match else None
            if not next_url:
                raise ValueError('Interrupted snapshot has no next cursor; validate before finalization')
        while True:
            url = next_url or ('https://api.github.com' + manifest['endpoint'])
            parsed = urllib.parse.urlparse(url)
            allowed_path = parsed.path == '/repos/juanfont/headscale/issues' or re.fullmatch(r'/repositories/\d+/issues', parsed.path)
            if parsed.scheme != 'https' or parsed.netloc != 'api.github.com' or not allowed_path:
                raise ValueError('Unexpected pagination host or endpoint')
            req = urllib.request.Request(url, headers=headers)
            for attempt in range(3):
                try:
                    with urllib.request.urlopen(req, timeout=45) as response:
                        raw = response.read()
                        links = response.headers.get('Link', '')
                        remaining = response.headers.get('X-RateLimit-Remaining')
                    break
                except urllib.error.HTTPError as error:
                    if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                        raise
                    time.sleep(2 ** attempt)
                except urllib.error.URLError:
                    if attempt == 2:
                        raise
                    time.sleep(2 ** attempt)
            data = json.loads(raw)
            if not isinstance(data, list):
                raise ValueError('Expected a list response')
            name = f'issues_page_{page:04d}.json'
            (args.output / name).write_bytes(raw)
            manifest['pages'].append(dict(file=name, records=len(data), sha256=hashlib.sha256(raw).hexdigest(),
                                          fetched_at_utc=datetime.now(timezone.utc).isoformat()))
            match = re.search(r'<([^>]+)>;\s*rel="next"', links)
            next_url = match.group(1) if match else None
            manifest['next_url'] = next_url
            manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
            print(f'Page {page}: {len(data)} records; API quota remaining {remaining}', flush=True)
            if not next_url:
                break
            page += 1
        records = []
        for entry in manifest['pages']:
            records.extend(json.loads((args.output / entry['file']).read_bytes()))
        numbers = [r['number'] for r in records]
        if len(numbers) != len(set(numbers)):
            raise ValueError('Duplicate issue numbers across pages; recollect a new snapshot')
        (args.output / 'issues_and_prs.json').write_text(json.dumps(records, indent=2) + '\n')
        manifest.update(complete=True, finished_at_utc=datetime.now(timezone.utc).isoformat(),
                        records=len(records), issues=sum('pull_request' not in r for r in records),
                        pull_requests=sum('pull_request' in r for r in records))
        manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
        print(f'Complete public list snapshot: {len(records)} records.')
    except (urllib.error.URLError, ValueError, OSError) as error:
        # Do not print request headers, token, or environment contents.
        manifest['last_error'] = type(error).__name__
        if isinstance(error, urllib.error.HTTPError):
            manifest['last_http_status'] = error.code
        if isinstance(error, ValueError):
            manifest['last_validation_error'] = str(error)
        manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
        print(f'Collection incomplete ({type(error).__name__}); preserved pages can be resumed.', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
