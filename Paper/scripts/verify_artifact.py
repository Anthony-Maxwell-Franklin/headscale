#!/usr/bin/env python3
"""Verify generated source hashes and the locally compiled working manuscript."""
import csv
import hashlib
import json
import re
from datetime import datetime,timezone
from pathlib import Path

import pymupdf
from analyze import parse_time
from analyze_review_identity_bounds import HELPERS, reviewer_hhi_outer_bounds
from verify_repair_validation import verify_repair_validation
from verify_extended_analyses import verify_calendar_export
from analyze_role_overlap import verify_export as verify_role_overlap_export
from validate_mechanism_review import verify_preparation as verify_mechanism_preparation


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_pdf_freshness(pdf, source_files, paper):
    """Reuse a verified byte-identical build after checkout changes timestamps."""
    inputs = {str(path.relative_to(paper)): digest(path) for path in source_files}
    receipt_path = paper / 'build/verification.json'
    if receipt_path.is_file():
        receipt = json.loads(receipt_path.read_text())
        if (receipt.get('pdf_sha256') == digest(pdf) and
                receipt.get('compiled_input_sha256') == inputs):
            return 'recorded_content_hashes', inputs
    if any(path.stat().st_mtime_ns > pdf.stat().st_mtime_ns for path in source_files):
        raise ValueError('PDF predates one or more manuscript inputs without a matching verification receipt')
    return 'local_build_timestamps', inputs


def verify_review_identity_bounds(root):
    directory=root/'Paper/results/review-identity-sensitivity'
    summary=json.loads((directory/'summary.json').read_text())
    if (not summary['complete'] or summary['sharp_bounds'] or
            summary['confidence_intervals'] or summary['identities_imputed']):
        raise ValueError('Missing-identity sensitivity has an invalid method or status')
    expected_scripts={name:digest(root/'Paper/scripts'/name) for name in HELPERS}
    if summary['provenance']['scripts_sha256']!=expected_scripts:
        raise ValueError('Missing-identity analysis or a helper changed')
    for name,expected in summary['provenance']['source_sha256'].items():
        if digest(root/name)!=expected:
            raise ValueError('Missing-identity analysis input changed: '+name)
    expected_outputs={'quarterly_hhi_outer_bounds.csv','merged_pr_identity_inputs.csv',
                      'unattributed_record_audit.csv'}
    if set(summary['output_sha256'])!=expected_outputs:
        raise ValueError('Missing-identity analysis outputs are incomplete')
    for name,expected in summary['output_sha256'].items():
        if digest(directory/name)!=expected:
            raise ValueError('Missing-identity analysis output changed: '+name)
    def rows(path):
        with path.open() as handle:return list(csv.DictReader(handle))
    inputs=rows(directory/'merged_pr_identity_inputs.csv')
    observations=rows(root/'Paper/results/review-census/human_pr_formal_review_observations.csv')
    merged={r['number']:r for r in observations if r['eventual_merge']=='1'}
    if len(inputs)!=len(merged) or {r['number'] for r in inputs}!=set(merged):
        raise ValueError('Missing-identity sensitivity changed merged-PR membership')
    audit=rows(directory/'unattributed_record_audit.csv')
    if len(audit)!=len({(r['number'],r['review_id']) for r in audit}):
        raise ValueError('Duplicate unattributed-review audit record')
    missing_counts={}
    for row in audit:
        if row['eligibility']=='retained':
            missing_counts[row['number']]=missing_counts.get(row['number'],0)+1
    groups={}
    for row in inputs:
        original=merged[row['number']]
        if any(row[key].casefold()!=original[key].casefold()
               for key in ['author','merged_by','human_reviewers','merge_quarter']):
            raise ValueError('Missing-identity sensitivity changed a known role')
        if int(row['eligible_missing_records'])!=missing_counts.get(row['number'],0):
            raise ValueError('PR identity capacity differs from unattributed-record audit')
        if row['human_reviewers']:
            groups.setdefault(row['merge_quarter'],[]).append(dict(
                known_reviewers=row['human_reviewers'].split(';'),
                eligible_missing_records=int(row['eligible_missing_records'])))
    quarter_rows=rows(directory/'quarterly_hhi_outer_bounds.csv')
    if len(quarter_rows)!=len({r['merge_quarter'] for r in quarter_rows}):
        raise ValueError('Duplicate missing-identity quarter')
    for row in quarter_rows:
        group=groups.get(row['merge_quarter'],[])
        if int(row['reviewed_prs'])!=len(group):
            raise ValueError('Fixed reviewed-PR membership differs by quarter')
        if group:
            expected=reviewer_hhi_outer_bounds(group)
            for key,value in expected.items():
                if abs(float(row[key])-value)>1e-10:
                    raise ValueError('Identity-bound quantity differs: '+key)
    census=json.loads((root/'Paper/results/review-census/summary.json').read_text())
    if (summary['fixed_reviewed_prs']!=sum(map(len,groups.values())) or
            summary['fixed_reviewed_prs']!=census['human_merged_with_observed_submitted_review'] or
            summary['global_unattributed_submitted_records']!=len(audit) or
            summary['global_unattributed_submitted_records']!=census['excluded_review_records']['unknown_actor'] or
            summary['eligible_missing_records_in_fixed_subset']!=sum(
                int(r['eligible_missing_records']) for r in inputs if r['human_reviewers'])):
        raise ValueError('Missing-identity summary differs from fixed-population inputs')
    return summary


def verify_review_materials(root):
    directory=root/'Paper/results/review-materials'
    bundle=json.loads((directory/'manifest.json').read_text())
    summary=json.loads((directory/'summary.json').read_text())
    if summary!=bundle['summary'] or summary['annotations_included'] or summary['repair_labels_inferred']:
        raise ValueError('Review materials have inconsistent status or include inferred labels')
    for name,expected in bundle['source_sha256'].items():
        if re.fullmatch(r'rater_[12]_case_order',name):
            with (root/'Paper/validation'/f'{name[:7]}.csv').open() as handle:
                order=[int(row['number']) for row in csv.DictReader(handle)]
            actual=hashlib.sha256(json.dumps(order).encode()).hexdigest()
        else:
            actual=digest(root/name)
        if actual!=expected:
            raise ValueError('Review-material source changed: '+name)
    for name,expected in bundle['output_sha256'].items():
        if digest(directory/name)!=expected:
            raise ValueError('Review-material output changed: '+name)
    packets=json.loads((directory/'packets.json').read_text())
    commits=json.loads((directory/'commit_context.json').read_text())
    with (root/'Paper/results/research-v0.29.5/issue_validation_sample.csv').open() as handle:
        membership={int(row['number']) for row in csv.DictReader(handle)}
    if len(packets)!=len(membership) or {p['number'] for p in packets}!=membership:
        raise ValueError('Offline review packet membership differs from pinned sample')
    for number in membership:
        if f'issues/{number}.html' not in bundle['output_sha256']:
            raise ValueError('Missing rendered issue evidence')
    refs=[(p['number'],r) for p in packets for r in p['commit_pointers']]
    linked_cases=set();linked_prs=set()
    cutoff=parse_time(summary['cutoff_utc'])
    for number,ref in refs:
        if parse_time(ref['created_at'])>cutoff:
            raise ValueError('Post-cutoff commit pointer entered review linkage')
        for match in ref['matching_prs']:
            pr=json.loads((directory/'prs'/f'{match["number"]}.json').read_text())['detail']
            if pr['merge_commit_sha']!=ref['commit_sha'] or not pr.get('merged_at') or parse_time(pr['merged_at'])>cutoff:
                raise ValueError('PR evidence pointer is not an exact pre-cutoff merge-commit match')
            linked_cases.add(number);linked_prs.add(pr['number'])
    expected=dict(cases=len(packets),commit_pointer_events=len(refs),
                  cases_with_commit_pointers=len({n for n,_ in refs}),
                  distinct_commit_pointers=len({r['commit_sha'] for _,r in refs}),
                  locally_available_commits=sum(c['local_object_available'] for c in commits.values()),
                  cases_with_exact_merge_commit_match=len(linked_cases),
                  distinct_matched_prs=len(linked_prs))
    if any(summary[key]!=value for key,value in expected.items()):
        raise ValueError('Review linkage quantities differ from exported evidence')
    return summary


def main():
    root=Path(__file__).resolve().parents[2]
    paper=root/'Paper'
    review_materials=verify_review_materials(root)
    identity_bounds=verify_review_identity_bounds(root)
    repairs=verify_repair_validation(root)
    calendar=verify_calendar_export(root)
    role_overlap=verify_role_overlap_export(root)
    mechanism_materials=verify_mechanism_preparation(root)
    manifest=json.loads((paper/'generated/data_manifest.json').read_text())
    for name,expected in manifest['source_sha256'].items():
        if digest(paper/name)!=expected:
            raise ValueError('Generated manuscript source changed: '+name)
    macros=dict(re.findall(r'\\newcommand\{\\([A-Za-z]+)\}\{([^{}]+)\}',
                           (paper/'generated/data_macros.tex').read_text()))
    if macros!={key:str(value) for key,value in manifest['values'].items()}:
        raise ValueError('Generated macros differ from their source manifest')
    census=json.loads((paper/'results/pr-census/summary.json').read_text())
    coverage=json.loads((paper/'results/pr-census/coverage.json').read_text())
    if not coverage.get('lifecycle_validated') or not coverage.get('timeline_complete'):
        raise ValueError('Primary lifecycle history is incomplete or inconsistent')
    if census['script_sha256']!=digest(paper/'scripts/analyze_census.py') or census['history_script_sha256']!=digest(paper/'scripts/pr_history.py'):
        raise ValueError('Lifecycle feature generation script changed')
    reviews=json.loads((paper/'results/review-census/summary.json').read_text())
    review_coverage=json.loads((paper/'results/review-census/coverage.json').read_text())
    if not review_coverage['complete'] or not reviews['lifecycle_reconstructed']:
        raise ValueError('Primary review census is incomplete')
    if reviews['provenance']['script_sha256']!=digest(paper/'scripts/analyze_review_census.py'):
        raise ValueError('Review analysis script changed')
    for name,expected in reviews['provenance']['source_coverage_sha256'].items():
        path=root/name
        if digest(path)!=expected:
            raise ValueError('Review/lifecycle raw input coverage changed')
        status=json.loads(path.read_text())
        if not status['complete']:
            raise ValueError('Raw endpoint no longer complete')
        for page in status['pages']:
            if digest(path.parent/page['file'])!=page['sha256']:
                raise ValueError('Raw endpoint page content changed')
    file_roles=json.loads((paper/'results/file-role-sensitivity/summary.json').read_text())
    file_coverage=json.loads((paper/'results/file-role-sensitivity/coverage.json').read_text())
    if not file_roles['complete'] or not file_coverage['complete']:
        raise ValueError('Component role sensitivity is incomplete')
    file_provenance=file_roles['provenance']
    if file_provenance['script_sha256']!=digest(paper/'scripts/analyze_file_roles.py') or file_provenance['classifier_script_sha256']!=digest(paper/'scripts/analyze.py'):
        raise ValueError('File-scope analysis or classifier changed')
    if file_provenance['review_csv_sha256']!=digest(paper/'results/review-census/human_pr_formal_review_observations.csv'):
        raise ValueError('File-scope roles use different review observations')
    for name,expected in file_provenance['source_coverage_sha256'].items():
        path=root/name
        if digest(path)!=expected:raise ValueError('File-list coverage changed')
        for page in json.loads(path.read_text())['pages']:
            if digest(path.parent/page['file'])!=page['sha256']:
                raise ValueError('Raw PR file-list page changed')
    model_files=[]
    robustness_path=paper/'results/inference/lifecycle_and_quarter_sensitivity.json'
    robustness=json.loads(robustness_path.read_text())
    expected_robustness=dict(input_sha256=digest(paper/'results/pr-census/human_pr_metadata_features.csv'),
        script_sha256=digest(paper/'scripts/check_model_robustness.py'),
        constant_fit_script_sha256=digest(paper/'scripts/fit_maintenance_models.py'),
        piecewise_fit_script_sha256=digest(paper/'scripts/fit_piecewise_models.py'),
        history_script_sha256=digest(paper/'scripts/pr_history.py'),uv_lock_sha256=digest(paper/'uv.lock'))
    if robustness['provenance']!=expected_robustness:
        raise ValueError('Endpoint or influence diagnostic provenance changed')
    if robustness['failed_influence_fits'] or robustness['completed_influence_fits']!=robustness['planned_influence_quarters']:
        raise ValueError('Quarter influence diagnostic is incomplete')
    model_files.append(str(robustness_path.relative_to(paper)))
    for days in [30,90,180]:
        suffix='' if days==90 else f'-window{days}'
        feature=paper/f'results/pr-census{suffix}/human_pr_metadata_features.csv'
        for filename,script in [('cause_specific_merge_models.json','fit_maintenance_models.py'),
                                ('piecewise_merge_model.json','fit_piecewise_models.py')]:
            path=paper/f'results/inference{suffix}'/filename
            model=json.loads(path.read_text())
            expected=dict(input_sha256=digest(feature),script_sha256=digest(paper/'scripts'/script),
                          uv_lock_sha256=digest(paper/'uv.lock'))
            if model['provenance']!=expected:
                raise ValueError('Model provenance changed: '+str(path.relative_to(paper)))
            model_files.append(str(path.relative_to(paper)))
    figures=json.loads((paper/'figures/manifest.json').read_text())
    for name,expected in figures['source_sha256'].items():
        if digest(root/name)!=expected:
            raise ValueError('Figure source changed: '+name)
    if figures['script_sha256']!=digest(paper/'scripts/make_figures.py'):
        raise ValueError('Figure script differs from recorded provenance')
    pdf=paper/'build/manuscript.pdf'
    source_files=[paper/'manuscript.tex',paper/'references.bib',*sorted((paper/'generated').glob('*.tex')),
                  *sorted((paper/'figures').glob('*.pdf'))]
    freshness_evidence, compiled_inputs = check_pdf_freshness(pdf, source_files, paper)
    log=(paper/'build/manuscript.log').read_text()
    if re.search(r'undefined|Overfull',log,re.IGNORECASE):
        raise ValueError('Unresolved reference or layout warning in final compilation log')
    document=pymupdf.open(pdf)
    text='\n'.join(page.get_text() for page in document)
    if '\ufffd' in text or any(not page.get_text().strip() for page in document):
        raise ValueError('Empty or unreadable PDF text')
    result=dict(verified_at_utc=datetime.now(timezone.utc).isoformat(),
                pdf_sha256=digest(pdf),pages=len(document),words=sum(len(page.get_text('words')) for page in document),
                generated_quantities=len(macros),generated_data_sources_match=True,
                model_provenance_matches=True,verified_model_exports=model_files,
                figure_sources_match=True,metadata_census_complete=json.loads(
                    (paper/'results/pr-census/coverage.json').read_text())['metadata_complete'],
                timeline_census_complete=True,review_census_complete=True,
                merged_pr_file_census_complete=True,
                offline_review_materials_verified=True,
                offline_review_cases=review_materials['cases'],
                review_identity_outer_bounds_verified=True,
                review_identity_affected_quarters=identity_bounds['affected_quarters'],
                component_repair_checks_verified=repairs['semantic_failures_verified'],
                component_repair_cases=repairs['cases'],
                component_repair_regression_checks=repairs['regression_checks'],
                calendar_identifiability_audit_verified=calendar['frozen_baselines_reproduced'],
                joint_role_audit_verified=role_overlap['complete'],
                joint_role_merged_prs=role_overlap['all_merged']['merged_human_prs'],
                mechanism_materials_source_catalogs_verified=mechanism_materials['source_catalogs_verified'],
                mechanism_materials_source_records=mechanism_materials['source_records'],
                mechanism_semantic_truth_verified=mechanism_materials['semantic_truth_verified'],
                compiled_input_sha256=compiled_inputs,
                pdf_freshness_evidence=freshness_evidence,
                status='Working draft; independent coding, explanatory mechanism validation and contribution assessment remain pending')
    (paper/'build/verification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({key:result[key] for key in ['pages','words','generated_quantities',
                                                'generated_data_sources_match','model_provenance_matches','status']},indent=2))


if __name__=='__main__':
    main()
