#!/usr/bin/env python3
"""Validate declared human coding before agreement, adjudication and estimation.

Input forms remain read-only. An empty or unverified declaration never produces
agreement or population estimates. Metadata declarations are reported as such,
not treated as independent proof of a person's identity or expertise.
"""
import argparse
import csv
import hashlib
import json
import math
import random
from collections import Counter,defaultdict
from pathlib import Path

from analyze import parse_time,write_csv


FIELDS=('responsibility','outcome','administrative_closure')


def source_name(path,root):
    try:return str(path.resolve().relative_to(root.resolve()))
    except ValueError:return str(path.resolve())


def read_rows(path):
    with path.open() as source:rows=list(csv.DictReader(source))
    numbers=[int(r['number']) for r in rows]
    if len(numbers)!=len(set(numbers)):
        raise ValueError('Duplicate case identifier: '+path.name)
    return {int(r['number']):r for r in rows}


def nominal_agreement(left,right):
    if len(left)!=len(right) or not left:
        raise ValueError('Agreement needs paired nonempty labels')
    n=len(left);a=Counter(left);b=Counter(right)
    observed=sum(x==y for x,y in zip(left,right))/n
    chance=sum(a[k]*b[k] for k in set(a)|set(b))/n**2
    return dict(cases=n,raw_agreement=observed,chance_agreement=chance,
                kappa=(observed-chance)/(1-chance) if chance<1 else None)


def agreement_with_bootstrap(rows,left_key,right_key,replicates=2000):
    result=nominal_agreement([r[left_key] for r in rows],[r[right_key] for r in rows])
    strata=defaultdict(list)
    for row in rows:strata[row['stratum']].append(row)
    rng=random.Random(20261013);values=[]
    for _ in range(replicates):
        draw=[rng.choice(group) for group in strata.values() for _ in group]
        value=nominal_agreement([r[left_key] for r in draw],[r[right_key] for r in draw])['kappa']
        if value is not None:values.append(value)
    values.sort()
    interval=[values[int(.025*(len(values)-1))],values[int(.975*(len(values)-1))]] if values and result['kappa'] is not None else None
    result.update(bootstrap_replicates=replicates,defined_bootstrap_replicates=len(values),
                  kappa_percentile_95=interval,
                  interval_scope='Paired cases resampled within study-sample strata; conditional sample agreement, not population reliability')
    return result


def hypergeom_tail(N,K,n,x,upper=False):
    support=range(max(0,n-(N-K)),min(n,K)+1)
    denominator=math.comb(N,n)
    return sum(math.comb(K,j)*math.comb(N-K,n-j)/denominator for j in support
               if (j>=x if upper else j<=x))


def hypergeom_count_interval(N,n,x,tail_probability):
    """Invert exact SRS-without-replacement tails for a population success count."""
    if not (0<n<=N and 0<=x<=n and 0<tail_probability<.5):
        raise ValueError('Invalid finite-population sampling parameters')
    feasible=range(x,N-n+x+1)
    lower=next(K for K in feasible if hypergeom_tail(N,K,n,x,True)>=tail_probability)
    upper=max(K for K in feasible if hypergeom_tail(N,K,n,x)>=tail_probability)
    return lower,upper


def weighted_category_estimates(rows,field,categories,population_size):
    strata=defaultdict(list)
    for row in rows:strata[row['stratum']].append(row)
    design=[]
    for group in strata.values():
        probabilities={float(r['inclusion_probability']) for r in group}
        if len(probabilities)!=1:raise ValueError('Unequal inclusion probabilities within a sampling stratum')
        probability=probabilities.pop();n=len(group)
        if not 0<probability<=1:raise ValueError('Invalid inclusion probability')
        N=round(n/probability)
        if abs(n/probability-N)>1e-8:raise ValueError('Stratum population is not recoverable from the pinned design')
        design.append((group,n,N))
    if sum(N for _,_,N in design)!=population_size:
        raise ValueError('Sampling design does not cover the declared population')
    output=[];tail=.05/(2*len(design))
    for category in categories:
        total=variance=lower=upper=0.
        for group,n,N in design:
            x=sum(r[field]==category for r in group);mean=x/n
            total+=N*mean
            if n>1:variance+=N*N*(1-n/N)*(n/(n-1)*mean*(1-mean))/n
            lo,hi=hypergeom_count_interval(N,n,x,tail);lower+=lo;upper+=hi
        output.append(dict(field=field,category=category,sample_cases=sum(r[field]==category for r in rows),
                            estimated_population_cases=total,population_proportion=total/population_size,
                            design_standard_error=math.sqrt(variance)/population_size,
                            conservative_lower_95=lower/population_size,conservative_upper_95=upper/population_size))
    return output


def validate_rater(rows,metadata,sample,allowed,version):
    errors=[];complete=0
    if set(rows)!=set(sample):return ['Case membership differs from the pinned sample'],0
    if not isinstance(metadata.get('reviewer_id'),str) or not metadata['reviewer_id'].strip():errors.append('Reviewer identifier is missing')
    if metadata.get('independent_human_review_completed') is not True:errors.append('Human review completion is not declared')
    if metadata.get('other_rater_codes_consulted') is not False:errors.append('Independence from the other rater is not declared')
    if type(metadata.get('assistant_suggestions_consulted')) is not bool:errors.append('Use of assistant suggestions is not declared')
    if metadata.get('codebook_version')!=version:errors.append('Codebook version differs')
    try:parse_time(metadata.get('completed_at_utc') or '')
    except (ValueError,TypeError):errors.append('Completion timestamp is missing or invalid')
    for number,row in rows.items():
        reference=sample[number]
        probability=float(row['inclusion_probability'])
        if not math.isfinite(probability) or row['stratum']!=reference['stratum'] or abs(probability-float(reference['inclusion_probability']))>1e-12:
            errors.append(f'Case {number}: sampling design changed')
        filled=all(row.get(k,'').strip() for k in (*FIELDS,'evidence_url','evidence_notes'))
        complete+=int(filled)
        if not filled:continue
        for field in FIELDS:
            if row[field] not in allowed[field]:errors.append(f'Case {number}: invalid {field}')
    if complete!=len(sample):errors.append(f'Only {complete}/{len(sample)} cases have complete labels and evidence')
    return errors,complete


def main():
    root=Path(__file__).resolve().parents[2]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=root/'Paper/validation')
    parser.add_argument('--output',type=Path,default=root/'Paper/results/human-validation')
    parser.add_argument('--mode',choices=['two-rater','single-rater'],default='two-rater')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    sample_path=root/'Paper/results/research-v0.29.5/issue_validation_sample.csv'
    sample=read_rows(sample_path)
    book_path=root/'Paper/data/issue_coding_codebook.json';book=json.loads(book_path.read_text())
    allowed=dict(responsibility=book['defect_eligibility'],outcome=book['closure_outcome'],administrative_closure=book['administrative_closure'])
    inputs={};raters=[];metadatas=[];errors=[];coverage={}
    count=2 if args.mode=='two-rater' else 1
    for number in range(1,count+1):
        path=args.input/f'rater_{number}.csv';meta_path=args.input/f'rater_{number}_metadata.json'
        rows=read_rows(path);metadata=json.loads(meta_path.read_text())
        problems,coded=validate_rater(rows,metadata,sample,allowed,book['version'])
        errors.extend(f'Rater {number}: '+problem for problem in problems)
        coverage[f'rater_{number}_complete_cases']=coded
        raters.append(rows);metadatas.append(metadata)
        for source in [path,meta_path]:inputs[source_name(source,root)]=hashlib.sha256(source.read_bytes()).hexdigest()
    if count==2 and metadatas[0].get('reviewer_id') and str(metadatas[0]['reviewer_id']).strip().casefold()==str(metadatas[1].get('reviewer_id')).strip().casefold():
        errors.append('Distinct reviewer identifiers are required for the declared two-rater design')
    status=dict(mode=args.mode,cases=len(sample),**coverage,errors=errors,
                 origin='Human review and independence are self-reported declarations',
                 source_sha256=inputs,codebook_sha256=hashlib.sha256(book_path.read_bytes()).hexdigest())
    for source in [sample_path,book_path]:
        inputs[source_name(source,root)]=hashlib.sha256(source.read_bytes()).hexdigest()
    if errors:
        status['status']='awaiting_complete_declared_human_coding'
        status['script_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        (args.output/'validation_status.json').write_text(json.dumps(status,indent=2)+'\n')
        print(json.dumps({k:status[k] for k in ['status','cases',*coverage.keys(),'errors']},indent=2))
        return 2
    adjudication_path=args.input/'adjudication.csv'
    coding_source_digest=hashlib.sha256(json.dumps(inputs,sort_keys=True).encode()).hexdigest()
    if adjudication_path.exists():inputs[source_name(adjudication_path,root)]=hashlib.sha256(adjudication_path.read_bytes()).hexdigest()
    script_digest=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    run_id=hashlib.sha256(json.dumps(dict(mode=args.mode,inputs=inputs,script_sha256=script_digest),sort_keys=True).encode()).hexdigest()[:16]
    run_directory=args.output/('run_'+run_id);run_directory.mkdir(exist_ok=True)
    status['analysis_directory']=source_name(run_directory,root)
    paired=[];disagreements=[];final=[]
    for number,reference in sample.items():
        left=raters[0][number];right=raters[-1][number]
        row=dict(number=number,stratum=reference['stratum'],inclusion_probability=reference['inclusion_probability'])
        for field in FIELDS:
            row['rater_1_'+field]=left[field];row['rater_2_'+field]=right[field] if count==2 else ''
        paired.append(row)
        if count==2 and any(left[k]!=right[k] for k in FIELDS):disagreements.append(number)
        else:final.append(dict(number=number,stratum=reference['stratum'],inclusion_probability=reference['inclusion_probability'],
                               **{field:left[field] for field in FIELDS}))
    write_csv(run_directory/'paired_original_labels.csv',paired)
    if count==2:
        agreement={field:agreement_with_bootstrap(paired,'rater_1_'+field,'rater_2_'+field) for field in FIELDS}
        (run_directory/'agreement.json').write_text(json.dumps(agreement,indent=2)+'\n')
    adjudicated=read_rows(adjudication_path) if adjudication_path.exists() else {}
    unresolved=[]
    for number in disagreements:
        row=adjudicated.get(number,{})
        source_pair=next(r for r in paired if r['number']==number)
        anchor_fields=[f'rater_{rater}_{field}' for rater in [1,2] for field in FIELDS]
        anchored=(row.get('original_coding_source_sha256')==coding_source_digest and
                  all(row.get(field)==source_pair[field] for field in anchor_fields))
        if not anchored or not all(row.get(field) in allowed[field] for field in FIELDS) or not row.get('adjudicator_id') or not row.get('evidence_notes') or not row.get('evidence_url'):
            unresolved.append(number);continue
        reference=sample[number]
        final.append(dict(number=number,stratum=reference['stratum'],inclusion_probability=reference['inclusion_probability'],
                           **{field:row[field] for field in FIELDS}))
    if unresolved:
        templates=[]
        for number in unresolved:
            source_pair=next(r for r in paired if r['number']==number)
            anchors={k:v for k,v in source_pair.items() if k.startswith('rater_')}
            templates.append(dict(number=number,original_coding_source_sha256=coding_source_digest,**anchors,
                                  **dict.fromkeys((*FIELDS,'adjudicator_id','evidence_url','evidence_notes'),'')))
        write_csv(run_directory/'adjudication_template.csv',templates)
        status.update(status='awaiting_adjudication',disagreements=len(disagreements),unresolved_cases=unresolved)
    else:
        if len(final)!=len(sample):raise ValueError('Incomplete combined case classification')
        population=json.loads((root/'Paper/results/research-v0.29.5/issue_summary.json').read_text())['currently_bug_labeled']
        estimates=[r for field in FIELDS for r in weighted_category_estimates(final,field,allowed[field],population)]
        write_csv(run_directory/'combined_labels.csv',sorted(final,key=lambda r:r['number']))
        write_csv(run_directory/'weighted_category_estimates.csv',estimates)
        status.update(status='declared_human_coding_analysed',population_size=population,
                      population_scope='Issues with a bug label at the pinned snapshot',disagreements=len(disagreements),
                      uncertainty='Per-category conservative 95% intervals from exact hypergeometric stratum bounds with Bonferroni adjustment across strata',
                      limitations=['Agreement measures consistency, not classification truth or reviewer expertise.',
                                   'Intervals concern sampling error, not coding or label-history error.',
                                   'Single-rater mode supplies no independent inter-rater agreement.'])
    status['script_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (run_directory/'validation_status.json').write_text(json.dumps(status,indent=2)+'\n')
    (args.output/'validation_status.json').write_text(json.dumps(status,indent=2)+'\n')
    print(json.dumps({k:v for k,v in status.items() if k!='source_sha256'},indent=2))
    return 2 if unresolved else 0


if __name__=='__main__':raise SystemExit(main())
