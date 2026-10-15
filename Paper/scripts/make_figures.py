#!/usr/bin/env python3
"""Produce standalone research figures from auditable exported measurements."""
import csv
import hashlib
import json
import argparse
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from scipy import stats

from research_metrics import km_curve

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'Paper/figures'
OUT.mkdir(exist_ok=True)
SOURCES={}
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':9,
                     'axes.spines.top':False,'axes.spines.right':False,
                     'pdf.fonttype':42,'svg.fonttype':'none'})


def read(path):
    path=ROOT/path
    SOURCES[str(path.relative_to(ROOT))]=hashlib.sha256(path.read_bytes()).hexdigest()
    return list(csv.DictReader(path.open()))


def save(fig,name):
    fig.tight_layout()
    for suffix in ('pdf','png','svg'):
        fig.savefig(OUT/(name+'.'+suffix),dpi=220,bbox_inches='tight')
    plt.close(fig)


def numeric(rows,key):
    return np.array([float(r[key]) if r[key] else np.nan for r in rows])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--review-input',type=Path,default=ROOT/'Paper/results/review-census')
    args=parser.parse_args()
    coverage=json.loads((args.review_input/'coverage.json').read_text())
    if not coverage['complete']:
        raise ValueError('Complete review census required for role figures')
    rows=read(Path('Paper/results/research-v0.29.5/quarterly_rename_sensitivity.csv'))
    source=[r for r in rows if r['category']=='production_go']
    # Exclude boundary quarters from volume/trend comparisons. The full source
    # table remains available, including those partial observation windows.
    source=source[1:-1]
    labels=[r['quarter'] for r in source]
    x=np.arange(len(source))
    fig,axes=plt.subplots(2,1,figsize=(8,4.9),sharex=True)
    axes[0].plot(x,numeric(source,'core_alias_linked_author_identities'),label='Active author identities',marker='o',ms=3)
    axes[0].plot(x,numeric(source,'core_alias_linked_effective_authors'),label='Effective identities after core alias linking',marker='s',ms=3)
    axes[0].plot(x,numeric(source,'effective_authors'),ls=':',color='#777777',label='Effective identities by email',alpha=.8)
    axes[0].set_ylabel('Author identities');axes[0].legend(frameon=False)
    axes[1].plot(x,numeric(source,'core_alias_linked_top_author_percent'),color='#c45d34',label='Top author after core alias linking')
    axes[1].plot(x,numeric(source,'top_author_percent'),color='#755c9f',ls=':',label='Top email identity')
    axes[1].set_ylabel('Commit share (%)');axes[1].set_ylim(0,105);axes[1].legend(frameon=False,ncol=2)
    axes[1].set_xticks(x[::2],labels[::2],rotation=45,ha='right')
    fig.suptitle('Production Go contributions by complete calendar quarter\nGitHub-linked core aliases; remaining email identities; descriptive census',fontsize=10)
    save(fig,'contribution_concentration')

    fig,ax=plt.subplots(figsize=(8,3.6))
    ax.plot(x,numeric(source,'raw_path_hhi'),label='Raw paths',marker='o',ms=3)
    ax.plot(x,numeric(source,'rename_linked_path_hhi'),label='Git-inferred rename groups',marker='s',ms=3)
    ax.set_xticks(x[::2],labels[::2],rotation=45,ha='right')
    ax.set_ylabel('File activity HHI');ax.legend(frameon=False)
    ax.set_title('Sensitivity of production-code activity concentration to rename linking\nActivity concentration does not measure technical debt',fontsize=10)
    save(fig,'rename_sensitivity')

    bugs=read(Path('Paper/results/research-v0.29.5/bug_closure_observations.csv'))
    curve=read(Path('Paper/results/research-v0.29.5/bug_time_to_closure_km.csv'))
    # Independently validate the custom product-limit estimator against SciPy.
    uncensored=[float(r['followup_days']) for r in bugs if r['closure_event_observed']=='1']
    censored=[float(r['followup_days']) for r in bugs if r['closure_event_observed']=='0']
    reference=stats.ecdf(stats.CensoredData(uncensored=uncensored,right=censored)).sf
    custom=km_curve([(float(r['followup_days']),r['closure_event_observed']=='1') for r in bugs])
    last={r['time_days']:r['probability_still_open'] for r in custom}
    assert np.allclose([last[t] for t in reference.quantiles],reference.probabilities,atol=1e-12)
    fig,ax=plt.subplots(figsize=(7,3.8))
    times=numeric(curve,'time_days')
    ax.step(times,numeric(curve,'probability_still_open'),where='post',label='KM including open issues')
    ax.fill_between(times,numeric(curve,'lower_95'),numeric(curve,'upper_95'),step='post',alpha=.16,label='Greenwood log-log 95% interval')
    sorted_closed=np.sort(uncensored)
    ax.step(sorted_closed,1-np.arange(1,len(sorted_closed)+1)/len(sorted_closed),where='post',ls='--',label='Closed-only empirical distribution')
    ax.set_xscale('symlog',linthresh=1);ax.set_xlabel('Days since issue creation')
    ax.set_ylabel('Probability of remaining unclosed');ax.set_ylim(0,1.02)
    ax.legend(frameon=False,fontsize=8)
    ax.set_title('Current bug-labeled issues: time to last observed closure\nClosure is not verified repair; reopening history remains unvalidated',fontsize=10)
    save(fig,'bug_time_to_closure')

    prs=read(Path('Paper/results/pr-census/human_first_disposition_cif.csv'))
    fig,ax=plt.subplots(figsize=(7,3.8))
    t=numeric(prs,'time_days')
    ax.step(t,numeric(prs,'merge_cif'),where='post',label='Merged')
    ax.step(t,numeric(prs,'nonmerge_close_cif'),where='post',label='Closed without merge')
    ax.set_xscale('symlog',linthresh=1);ax.set_xlabel('Days since PR creation')
    ax.set_ylabel('Cumulative incidence');ax.set_ylim(0,1)
    ax.legend(frameon=False)
    ax.set_title('Initial processing episodes of human-authored PRs\nFirst closure terminates the episode; later reopening is separate',fontsize=10)
    save(fig,'pr_dispositions')
    roles=read(Path('Paper/results/pr-census/quarterly_merge_responsibility.csv'))[1:-1]
    fig,ax=plt.subplots(figsize=(8,3.8))
    x=np.arange(len(roles));labels=[r['merge_quarter'] for r in roles]
    ax.plot(x,numeric(roles,'author_hhi_on_same_prs'),label='PR authors',marker='o',ms=3)
    ax.plot(x,numeric(roles,'merger_hhi'),label='PR integrators',marker='s',ms=3)
    ax.set_xticks(x[::2],labels[::2],rotation=45,ha='right')
    ax.set_ylabel('Actor activity HHI');ax.set_ylim(0,1.03);ax.legend(frameon=False)
    ax.set_title('Authorship and integration on the same human-authored merged PRs\nGrouped by merge quarter; roles share identical PR membership',fontsize=10)
    save(fig,'paired_pr_role_concentration')
    reviews=read(args.review_input/'quarterly_paired_review_roles.csv')[1:-1]
    fig,axes=plt.subplots(2,1,figsize=(8,5.2),sharex=True,
                          gridspec_kw={'height_ratios':[2.2,1]})
    x=np.arange(len(reviews));labels=[r['merge_quarter'] for r in reviews]
    for field,label,marker in [('author_hhi','Authors','o'),('integrator_hhi','Integrators','s'),
                               ('fractional_reviewer_hhi','Attributed reviewers','^')]:
        axes[0].plot(x,numeric(reviews,field),label=label,marker=marker,ms=3)
    marked=False
    for index,row in enumerate(reviews):
        if int(row['unknown_actor_records']):
            axes[0].axvspan(index-.45,index+.45,color='gray',alpha=.12,
                           label='Unattributed review records present' if not marked else None)
            marked=True
    axes[0].set_ylim(0,1.03);axes[0].set_ylabel('Activity credit HHI')
    axes[0].legend(frameon=False,fontsize=8,ncol=2)
    total=numeric(reviews,'human_merged_prs');observed=numeric(reviews,'reviewed_human_merged_prs')
    axes[1].bar(x,np.divide(observed,total,out=np.zeros_like(total),where=total>0)*100,color='#517a96')
    axes[1].set_ylabel('Observed review\ncoverage (%)');axes[1].set_ylim(0,105)
    axes[1].set_xticks(x[::2],labels[::2],rotation=45,ha='right')
    fig.suptitle('Roles on the same reviewed human-authored merged PRs\nOne credit per PR; review credit shared equally across attributed reviewers',fontsize=10)
    save(fig,'paired_author_review_integrator')
    (OUT/'manifest.json').write_text(json.dumps(dict(source_sha256=SOURCES,
        matplotlib_version=matplotlib.__version__,numpy_version=np.__version__,
        km_validation='Custom product-limit survival probabilities match SciPy ecdf within 1e-12',
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()),indent=2)+'\n')
    print('Exported six figures as PDF, PNG and SVG; KM independently validated against SciPy.')


if __name__=='__main__':
    main()
