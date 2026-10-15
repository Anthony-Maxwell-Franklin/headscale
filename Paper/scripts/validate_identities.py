#!/usr/bin/env python3
"""Use GitHub's representative-commit attribution to validate core email aliases."""
import hashlib
import json
import sys
from pathlib import Path
from collect_process import Client,collect_endpoint,load_endpoint,now

root=Path(__file__).resolve().parents[2]
candidates=json.loads((root/'Paper/data/identity_candidates.json').read_text())
client=Client(20)
mapping=[]
for c in candidates:
    sha=c['representative_commit']
    directory=root/'Paper/data/identity-evidence'/sha
    collect_endpoint(client,directory,'https://api.github.com/repos/juanfont/headscale/commits/'+sha,{})
    record=load_endpoint(directory)
    login=(record.get('author') or {}).get('login')
    mapping.append(dict(**c,github_login=login,
                        canonical_identity='github:'+login if login else c['email_identity_hash'],
                        evidence_url='https://api.github.com/repos/juanfont/headscale/commits/'+sha,
                        evidence_sha256=hashlib.sha256((directory/'page_0001.json').read_bytes()).hexdigest(),
                        rule='Same email identity attributed by GitHub to this account for a representative commit',
                        validated=bool(login)))
output=root/'Paper/data/validated_core_identities.json'
output.write_text(json.dumps(dict(observed_at_utc=now(),mapping=mapping,
    limitations=['Validation covers the two named core contributors, not all developer identities.',
                 'GitHub account attribution is observed at collection time.',
                 'A representative commit validates an alias link; it is not proof of knowledge ownership.']),indent=2)+'\n')
print('Validated',sum(r['validated'] for r in mapping),'of',len(mapping),'core email aliases; no emails or credentials printed.')
