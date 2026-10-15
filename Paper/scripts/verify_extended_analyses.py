"""Check the supplementary calendar audit against pinned inputs and exports."""
import csv
import hashlib
import json

import numpy as np
import pandas as pd

from audit_calendar_identifiability import (HELPERS, assert_baseline_reproduction,
                                             variance_decomposition)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_calendar_export(root):
    paper = root / 'Paper'
    directory = paper / 'results/calendar-identifiability'
    summary = json.loads((directory / 'summary.json').read_text())
    provenance = summary['provenance']
    source = paper / 'results/pr-census/human_pr_metadata_features.csv'
    if provenance['input_sha256'] != digest(source):
        raise ValueError('Calendar audit input changed')
    if provenance['helper_sha256'] != {name: digest(paper / 'scripts' / name) for name in HELPERS}:
        raise ValueError('Calendar audit helpers changed')
    for field, name in [('plan_sha256', 'audit_plan.json'), ('preview_sha256', 'preview_fits.json')]:
        if provenance[field] != digest(directory / name):
            raise ValueError('Calendar audit plan or disclosed previews changed')
    if provenance['uv_lock_sha256'] != digest(paper / 'uv.lock'):
        raise ValueError('Calendar audit dependency lock changed')
    for name, expected in provenance['reference_sha256'].items():
        if digest(paper / 'results/inference' / name) != expected:
            raise ValueError('Calendar audit frozen comparison changed')
    for name, expected in provenance['census_metadata_sha256'].items():
        if digest(paper / 'results/pr-census' / name) != expected:
            raise ValueError('Calendar audit census metadata changed')
    expected_outputs = {'quarter_exposure.csv', 'model_effects.csv', 'model_fits.json'}
    if set(provenance['output_sha256']) != expected_outputs:
        raise ValueError('Calendar audit export membership differs')
    for name, expected in provenance['output_sha256'].items():
        if digest(directory / name) != expected:
            raise ValueError('Calendar audit export changed: ' + name)
    original = pd.read_csv(source)
    required = ['lag90_merger_hhi', 'lag90_merges', 'backlog_at_submission', 'prior_author_prs',
                'prior_integrator_author', 'calendar_years', 'duration_days', 'merge_event', 'creation_quarter']
    frame = original.dropna(subset=required)
    if (summary['input_prs'] != len(original) or summary['eligible_prs'] != len(frame) or
            summary['creation_quarters'] != frame['creation_quarter'].nunique()):
        raise ValueError('Calendar audit population differs from pinned features')
    for name, value in variance_decomposition(frame).items():
        actual = summary['exposure_variation'][name]
        if isinstance(value, str):
            if value != actual:
                raise ValueError('Calendar audit weighting changed')
        elif not np.isclose(value, actual, rtol=1e-12, atol=1e-12):
            raise ValueError('Calendar audit variance partition differs')
    fits = json.loads((directory / 'model_fits.json').read_text())
    planned = {kind + '_' + method for kind in ['primary', 'piecewise']
               for method in ['linear', 'spline4', 'quarter_baseline']}
    if (set(fits) != planned or summary['planned_fits'] != len(planned) or
            summary['identified_fits'] != len(planned) or summary['failed_fits']):
        raise ValueError('Calendar audit no longer supports the reported complete comparison')
    primary = json.loads((paper / 'results/inference/cause_specific_merge_models.json').read_text())
    piecewise = json.loads((paper / 'results/inference/piecewise_merge_model.json').read_text())
    assert_baseline_reproduction(fits, primary, piecewise)
    with (directory / 'model_effects.csv').open() as handle:
        effects = list(csv.DictReader(handle))
    expected = {(name, effect['period']): effect for name, fit in fits.items()
                for effect in fit['concentration_effects']}
    if len(effects) != len(expected) or {(r['model'], r['period']) for r in effects} != set(expected):
        raise ValueError('Calendar audit table omitted or duplicated a planned contrast')
    for row in effects:
        for key, value in expected[row['model'], row['period']].items():
            if key != 'period' and not np.isclose(float(row[key]), value, rtol=1e-12, atol=1e-12):
                raise ValueError('Calendar audit CSV differs from fitted contrast')
    if fits['piecewise_quarter_baseline']['counts']['late_merge_events'] != fits['piecewise_linear']['counts']['late_merge_events']:
        raise ValueError('Calendar audit changed the reported late-event support')
    return summary
