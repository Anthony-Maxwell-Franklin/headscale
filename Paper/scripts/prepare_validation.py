#!/usr/bin/env python3
"""Prepare separate human coding forms without prefilled assistant interpretations."""
import argparse
import csv
import hashlib
import json
import random
from pathlib import Path

from analyze import write_csv


HUMAN_FIELDS=['responsibility','outcome','administrative_closure','evidence_url',
              'evidence_notes','reviewed_at_utc']


def preserve_form(path,rows):
    if not path.exists():return rows
    with path.open() as source:
        previous=list(csv.DictReader(source))
    old={int(r['number']):r for r in previous}
    if len(old)!=len(previous):
        raise ValueError('Existing human form has duplicate case identifiers; no annotations overwritten')
    if set(old)!={r['number'] for r in rows}:
        raise ValueError('Existing human form has a different case membership')
    for row in rows:
        prior=old[row['number']]
        if prior['stratum']!=row['stratum'] or abs(float(prior['inclusion_probability'])-row['inclusion_probability'])>1e-12:
            raise ValueError('Existing human form has a different sampling design')
        for name in HUMAN_FIELDS:row[name]=prior.get(name,'')
    return rows


def main():
    root=Path(__file__).resolve().parents[2]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=root/'Paper/validation')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    source=root/'Paper/results/research-v0.29.5/issue_validation_sample.csv'
    with source.open() as handle:sample=list(csv.DictReader(handle))
    codebook=root/'Paper/data/issue_coding_codebook.json'
    book=json.loads(codebook.read_text())
    identity=[dict(number=int(r['number']),stratum=r['stratum'],
                   inclusion_probability=float(r['inclusion_probability'])) for r in sample]
    identity.sort(key=lambda r:r['number'])
    sampling_hash=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    manifest_path=args.output/'manifest.json'
    if manifest_path.exists() and json.loads(manifest_path.read_text())['sampling_design_sha256']!=sampling_hash:
        raise ValueError('Pinned human validation design changed')
    coverage={}
    for rater in [1,2]:
        rows=[dict(number=int(r['number']),url=r['url'],title=r['title'],stratum=r['stratum'],
                   inclusion_probability=float(r['inclusion_probability']),
                   **dict.fromkeys(HUMAN_FIELDS,'')) for r in sample]
        random.Random(20261013+rater).shuffle(rows)
        form=args.output/f'rater_{rater}.csv'
        rows=preserve_form(form,rows)
        write_csv(form,rows)
        metadata=args.output/f'rater_{rater}_metadata.json'
        if not metadata.exists():
            metadata.write_text(json.dumps(dict(reviewer_id='',independent_human_review_completed=False,
                    other_rater_codes_consulted=None,assistant_suggestions_consulted=None,
                    completed_at_utc='',codebook_version=book['version']),indent=2)+'\n')
        coverage[f'rater_{rater}_fully_coded_cases']=sum(all(r[name] for name in
            ['responsibility','outcome','administrative_closure','evidence_url','evidence_notes']) for r in rows)
    manifest=dict(cases=len(sample),sampling_design_sha256=sampling_hash,
                   codebook_sha256=hashlib.sha256(codebook.read_bytes()).hexdigest(),
                   codebook_version=book['version'],**coverage,
                   status='Prepared forms; field completion alone does not establish independent human coding',
                   evidence_packets='Paper/results/issue-validation/review_packets.json',
                   population='Issues with the bug label at the pinned collection snapshot',
                   limitations=['The two reviewers must work separately before adjudication.',
                                'Sample category frequencies require inclusion weights before population inference.',
                                'Supported repair evidence does not imply runtime reproduction.',
                                'Assistant-generated case suggestions are excluded from these forms.'])
    manifest_path.write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({k:manifest[k] for k in ['cases',*coverage.keys(),'status']},indent=2))


if __name__=='__main__':main()
