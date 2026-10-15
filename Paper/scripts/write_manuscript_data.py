"""Generate manuscript quantities from verified analysis exports, without invented values."""
import csv
import hashlib
import json
from pathlib import Path
from verify_artifact import verify_review_identity_bounds
from verify_repair_validation import verify_repair_validation

root=Path(__file__).resolve().parents[2]
paper=root/'Paper'
sources={}


def read(name):
    path=paper/name
    sources[name]=hashlib.sha256(path.read_bytes()).hexdigest()
    return json.loads(path.read_text())


def main():
    issues=read('results/research-v0.29.5/issue_summary.json')
    prs=read('results/pr-process/summary.json')
    evidence=read('results/issue-validation/summary.json')
    materials=read('results/review-materials/summary.json')
    if materials['cases']!=evidence['sample_size'] or materials['annotations_included'] or materials['repair_labels_inferred']:
        raise ValueError('Human review materials must retain neutral pinned sample evidence')
    keywords=read('results/issue-validation/fix_keyword_summary.json')
    repairs=verify_repair_validation(root)
    repair_latest=read('results/repair-validation/latest.json')
    repair_run=read(str(Path(repair_latest['run_directory']).relative_to('Paper')/'summary.json'))
    legacy=read('results/v0.29.5/legacy_metrics.json')
    identity=read('data/validated_core_identities.json')
    timing=read('results/timestamp_sensitivity.json')
    census=read('results/pr-census/summary.json')
    reviews=read('results/review-census/summary.json')
    review_coverage=read('results/review-census/coverage.json')
    identity_bounds=read('results/review-identity-sensitivity/summary.json')
    if identity_bounds!=verify_review_identity_bounds(root):
        raise ValueError('Reviewer identity bounds failed provenance validation')
    identity_bounds_file=paper/'results/review-identity-sensitivity/quarterly_hhi_outer_bounds.csv'
    sources[str(identity_bounds_file.relative_to(paper))]=hashlib.sha256(identity_bounds_file.read_bytes()).hexdigest()
    with identity_bounds_file.open() as handle:identity_bound_rows=list(csv.DictReader(handle))
    affected_identity_rows=[r for r in identity_bound_rows if int(r['eligible_missing_records'])]
    identity_q2=next(r for r in affected_identity_rows if r['merge_quarter']=='2025-Q2')
    clocks=read('results/pr-census/event_clock_audit.json')
    file_roles=read('results/file-role-sensitivity/summary.json')
    file_coverage=read('results/file-role-sensitivity/coverage.json')
    if not file_roles['complete'] or not file_coverage['complete']:
        raise ValueError('Complete merged-PR file coverage is required for component claims')
    scope_file=paper/'results/file-role-sensitivity/quarterly_roles_by_artifact_scope.csv'
    sources[str(scope_file.relative_to(paper))]=hashlib.sha256(scope_file.read_bytes()).hexdigest()
    with scope_file.open() as handle:scope_rows=list(csv.DictReader(handle))
    go_q2=next(r for r in scope_rows if r['merge_quarter']=='2026-Q2' and r['artifact_scope']=='production_go_touched')
    models=read('results/inference/cause_specific_merge_models.json')
    piecewise=read('results/inference/piecewise_merge_model.json')
    robustness=read('results/inference/lifecycle_and_quarter_sensitivity.json')
    window_models={90:models,30:read('results/inference-window30/cause_specific_merge_models.json'),
                   180:read('results/inference-window180/cause_specific_merge_models.json')}
    window_piecewise={90:piecewise,30:read('results/inference-window30/piecewise_merge_model.json'),
                      180:read('results/inference-window180/piecewise_merge_model.json')}
    coverage=read('results/pr-census/coverage.json')
    if not coverage['metadata_complete'] or not census['metadata_complete']:
        raise ValueError('Cannot generate census claims from incomplete metadata')
    if not coverage.get('lifecycle_validated') or not census['backlog_reconstructed_from_timelines']:
        raise ValueError('Primary manuscript now requires validated lifecycle reconstruction')
    if not review_coverage['complete'] or not reviews['lifecycle_reconstructed']:
        raise ValueError('Primary review claims require complete reviews and timelines')
    if models['episode_scope']!='first disposition' or piecewise['episode_scope']!='first disposition':
        raise ValueError('Primary models use an unexpected episode boundary')
    feature_file=paper/'results/pr-census/human_pr_metadata_features.csv'
    if models['provenance']['input_sha256']!=hashlib.sha256(feature_file.read_bytes()).hexdigest():
        raise ValueError('Model results do not match the current metadata features')
    if robustness['provenance']['input_sha256']!=hashlib.sha256(feature_file.read_bytes()).hexdigest():
        raise ValueError('Robustness models use different features')
    if robustness['failed_influence_fits'] or robustness['completed_influence_fits']!=robustness['planned_influence_quarters']:
        raise ValueError('Quarter influence diagnostic is incomplete')
    if piecewise['provenance']['input_sha256']!=hashlib.sha256(feature_file.read_bytes()).hexdigest():
        raise ValueError('Piecewise model does not match the current metadata features')
    for days in [30,180]:
        feature_path=paper/f'results/pr-census-window{days}/human_pr_metadata_features.csv'
        if window_models[days]['provenance']['input_sha256']!=hashlib.sha256(feature_path.read_bytes()).hexdigest():
            raise ValueError('Window model does not match its feature export')
        if window_piecewise[days]['provenance']['input_sha256']!=hashlib.sha256(feature_path.read_bytes()).hexdigest():
            raise ValueError('Piecewise window model does not match its feature export')
    main_model=models['primary']
    main_hhi=next(r for r in main_model['coefficients'] if r['variable']=='lag90_merger_hhi_per_01')
    strat_hhi=next(r for r in models['role_experience_stratified_sensitivity']['coefficients'] if r['variable']=='lag90_merger_hhi_per_01')
    author_clock=next(r for r in timing if r['quarter']=='2026-Q2')
    qfile=paper/'results/research-v0.29.5/quarterly_rename_sensitivity.csv'
    sources[str(qfile.relative_to(paper))]=hashlib.sha256(qfile.read_bytes()).hexdigest()
    qs=list(csv.DictReader(qfile.open()))
    role_file=paper/'results/pr-census/quarterly_merge_responsibility.csv'
    sources[str(role_file.relative_to(paper))]=hashlib.sha256(role_file.read_bytes()).hexdigest()
    with role_file.open() as handle:
        role_quarter=next(r for r in csv.DictReader(handle) if r['merge_quarter']=='2026-Q2')
    review_file=paper/'results/review-census/quarterly_paired_review_roles.csv'
    sources[str(review_file.relative_to(paper))]=hashlib.sha256(review_file.read_bytes()).hexdigest()
    with review_file.open() as handle:
        review_quarter=next(r for r in csv.DictReader(handle) if r['merge_quarter']=='2026-Q2')
    review_obs_file=paper/'results/review-census/human_pr_formal_review_observations.csv'
    sources[str(review_obs_file.relative_to(paper))]=hashlib.sha256(review_obs_file.read_bytes()).hexdigest()
    with review_obs_file.open() as handle:
        review_observations=[r for r in csv.DictReader(handle) if r['merge_quarter']=='2026-Q2']
    if review_quarter['reviewed_human_merged_prs']!=role_quarter['human_pr_merges']:
        raise ValueError('The selected quarter does not have matched full review-role membership')
    q=next(r for r in qs if r['quarter']=='2026-Q2' and r['category']=='production_go')
    piece_early,piece_late=piecewise['concentration_effects']
    no_backlog_late=piecewise['without_backlog_sensitivity']['concentration_effects'][1]
    coalesced_constant=next(r for r in robustness['coalesced_models']['primary']['coefficients']
                           if r['variable']=='lag90_merger_hhi_per_01')
    coalesced_late=robustness['coalesced_models']['piecewise']['concentration_effects'][1]
    previous=legacy[-2]['m2'];current=legacy[-1]['m2']
    values={
        'IssueCount':issues['issues'],'BugLabelCount':issues['currently_bug_labeled'],
        'BugClosedCount':issues['closed'],'BugOpenCount':issues['open_censored'],
        'BugNotPlannedCount':issues['observed_closure_reasons']['not_planned'],
        'BugDuplicateCount':issues['observed_closure_reasons']['duplicate'],
        'BugClosureMedian':f"{issues['closure_km_median_days']:.2f}",
        'PRCount':prs['list_prs'],'HumanPRCount':prs['human_authored_prs'],
        'HumanPRMerged':prs['human_dispositions']['merge'],
        'HumanPRUnmergedClosed':prs['human_dispositions']['nonmerge_close'],
        'HumanPROpen':prs['human_dispositions']['censor'],
        'PilotPRCount':prs['complete_pilot_prs'],'PilotHumanPRCount':prs['pilot_human_prs'],
        'PilotResponseCount':prs['pilot_human_response_outcomes']['response'],
        'PilotMergeNoResponse':prs['pilot_human_response_outcomes']['merge_without_response'],
        'PilotCloseNoResponse':prs['pilot_human_response_outcomes']['nonmerge_close_without_response'],
        'PilotNoResponseOpen':prs['pilot_human_response_outcomes']['censor'],
        'EvidenceIssueCount':evidence['sample_size'],'EvidenceBotClosures':evidence['last_closure_by_bot'],
        'EvidenceClosingCommits':evidence['explicit_closing_commit'],'EvidenceReopened':evidence['reopened_issues'],
        'EvidencePostClosureDiscussion':evidence['comments_after_closure_issues'],
        'EvidenceCommitRefEvents':materials['commit_pointer_events'],
        'EvidenceCommitRefCases':materials['cases_with_commit_pointers'],
        'EvidenceDistinctCommitRefs':materials['distinct_commit_pointers'],
        'EvidenceLocalCommitRefs':materials['locally_available_commits'],
        'EvidenceMergeCommitLinkedCases':materials['cases_with_exact_merge_commit_match'],
        'EvidenceMergeCommitLinkedPRs':materials['distinct_matched_prs'],
        'ClosingCommitKeywordCases':keywords['explicit_closing_commit_cases'],
        'ClosingCommitKeywordMatches':keywords['keyword_matches'],
        'RepairValidatedCases':repairs['cases'],
        'RepairRegressionChecks':repairs['regression_checks'],
        'RepairControlChecks':repairs['control_checks'],
        'RepairGoVersion':repairs['go_toolchain'].removeprefix('go'),
        'MergerAccountCount':census['unique_merger_accounts'],
        'RoleQuarterPRs':role_quarter['human_pr_merges'],
        'RoleQuarterAuthors':role_quarter['author_identities_on_same_prs'],
        'RoleQuarterMergers':role_quarter['merger_identities'],
        'RoleQuarterAuthorHHI':f"{float(role_quarter['author_hhi_on_same_prs']):.3f}",
        'RoleQuarterMergerHHI':f"{float(role_quarter['merger_hhi']):.3f}",
        'SelfMergeCount':census['self_merges'],
        'ReviewRecordCount':reviews['raw_review_records'],
        'ReviewAccountCount':reviews['observed_reviewer_accounts'],
        'MergedReviewedCount':reviews['human_merged_with_observed_submitted_review'],
        'MergedReviewCoverage':f"{100*reviews['human_merged_with_observed_submitted_review']/reviews['human_merged_prs']:.2f}",
        'UnknownReviewActorRecords':reviews['excluded_review_records']['unknown_actor'],
        'UnknownReviewActorHumanPRs':reviews['human_prs_with_unknown_review_actor'],
        'MissingIdentityFixedPRs':identity_bounds['fixed_reviewed_prs'],
        'MissingIdentityAffectedPRs':identity_bounds['affected_fixed_subset_prs'],
        'MissingIdentityRetainedRecords':identity_bounds['eligible_missing_records_in_fixed_subset'],
        'MissingIdentityAffectedQuarters':len(identity_bounds['affected_quarters']),
        'MissingIdentityQTwoLower':f"{float(identity_q2['lower_hhi']):.3f}",
        'MissingIdentityQTwoUpper':f"{float(identity_q2['upper_hhi']):.3f}",
        'MissingIdentityQTwoAuthor':f"{float(identity_q2['author_hhi']):.3f}",
        'MissingIdentityQTwoIntegrator':f"{float(identity_q2['integrator_hhi']):.3f}",
        'MissingIdentityQTwoDistinctScenario':f"{float(identity_q2['all_missing_distinct_human_scenario_hhi']):.3f}",
        'ReviewQuarterAccounts':review_quarter['reviewer_accounts'],
        'ReviewQuarterPairs':review_quarter['reviewer_events'],
        'ReviewQuarterFractionHHI':f"{float(review_quarter['fractional_reviewer_hhi']):.3f}",
        'ReviewQuarterPairHHI':f"{float(review_quarter['reviewer_hhi']):.3f}",
        'JuanQuarterAuthoredPRs':sum(r['author']=='juanfont' for r in review_observations),
        'JuanQuarterReviewedPRs':sum('juanfont' in r['human_reviewers'].split(';') for r in review_observations),
        'GoQuarterPRs':go_q2['merged_prs'],
        'GoQuarterAuthorHHI':f"{float(go_q2['author_hhi']):.3f}",
        'GoQuarterReviewerHHI':f"{float(go_q2['fractional_reviewer_hhi']):.3f}",
        'GoQuarterIntegratorHHI':f"{float(go_q2['integrator_hhi']):.3f}",
        'FirstFormalReviewCount':reviews['first_formal_review_outcomes']['response'],
        'FirstFormalMergeNoReview':reviews['first_formal_review_outcomes']['merge_without_response'],
        'FirstFormalCloseNoReview':reviews['first_formal_review_outcomes']['nonmerge_close_without_response'],
        'FirstFormalOpenNoReview':reviews['first_formal_review_outcomes']['censor'],
        'ReopenedHumanPRs':census['human_reopened_prs'],
        'FirstClosedEventuallyMerged':census['first_closed_then_eventually_merged'],
        'InitialEpisodeMergeCount':census['model_dispositions']['merge'],
        'InitialEpisodeClosedCount':census['model_dispositions']['nonmerge_close'],
        'InitialEpisodeOpenCount':census['model_dispositions']['censor'],
        'ClockDisagreementPRs':clocks['clock_disagreement_prs'],
        'ClockMaxDelta':int(clocks['maximum_difference_seconds']),
        'CoxEarlyCensors':main_model['censored_before_first_event_excluded'],
        'CloseReopenTransitions':robustness['close_reopen_transitions'],
        'ShortCloseReopenTransitions':robustness['reopen_gaps_at_most_five_minutes'],
        'CoalescedModifiedPRs':len(robustness['modified_prs']),
        'CoalescedMergeCount':robustness['coalesced_dispositions']['merge'],
        'CoalescedConstantHR':f"{coalesced_constant['hazard_ratio']:.3f}",
        'CoalescedConstantLower':f"{coalesced_constant['lower_95']:.3f}",
        'CoalescedConstantUpper':f"{coalesced_constant['upper_95']:.3f}",
        'CoalescedLateHR':f"{coalesced_late['hazard_ratio']:.3f}",
        'CoalescedLateLower':f"{coalesced_late['lower_95']:.3f}",
        'CoalescedLateUpper':f"{coalesced_late['upper_95']:.3f}",
        'CoalescedLateMerges':coalesced_late['merge_events_in_period'],
        'InfluenceQuarterCount':robustness['completed_influence_fits'],
        'InfluenceConstantMin':f"{robustness['leave_quarter_out_ranges']['constant']['minimum_hazard_ratio']:.3f}",
        'InfluenceConstantMax':f"{robustness['leave_quarter_out_ranges']['constant']['maximum_hazard_ratio']:.3f}",
        'InfluenceLateMin':f"{robustness['leave_quarter_out_ranges']['late']['minimum_hazard_ratio']:.3f}",
        'InfluenceLateMax':f"{robustness['leave_quarter_out_ranges']['late']['maximum_hazard_ratio']:.3f}",
        'CoxObservationCount':main_model['observations'],
        'CoxMergeEventCount':main_model['merge_events'],
        'CoxQuarterClusters':main_model['quarter_clusters'],
        'CoxConcentrationHR':f"{main_hhi['hazard_ratio']:.3f}",
        'CoxConcentrationLower':f"{main_hhi['lower_95']:.3f}",
        'CoxConcentrationUpper':f"{main_hhi['upper_95']:.3f}",
        'PieceEarlyHR':f"{piece_early['hazard_ratio']:.3f}",
        'PieceEarlyLower':f"{piece_early['lower_95']:.3f}",
        'PieceEarlyUpper':f"{piece_early['upper_95']:.3f}",
        'PieceLateHR':f"{piece_late['hazard_ratio']:.3f}",
        'PieceLateLower':f"{piece_late['lower_95']:.3f}",
        'PieceLateUpper':f"{piece_late['upper_95']:.3f}",
        'PieceLateMergeEvents':piece_late['merge_events_in_period'],
        'PieceNoBacklogLateHR':f"{no_backlog_late['hazard_ratio']:.3f}",
        'PieceNoBacklogLateLower':f"{no_backlog_late['lower_95']:.3f}",
        'PieceNoBacklogLateUpper':f"{no_backlog_late['upper_95']:.3f}",
        'StratifiedConcentrationHR':f"{strat_hhi['hazard_ratio']:.3f}",
        'StratifiedConcentrationLower':f"{strat_hhi['lower_95']:.3f}",
        'StratifiedConcentrationUpper':f"{strat_hhi['upper_95']:.3f}",
        'PilotReopenedPRs':prs['pilot_reopened_prs'],
        'PilotDraftHistoryPRs':prs['pilot_with_draft_history'],
        'ValidatedCoreAliases':sum(r['validated'] for r in identity['mapping']),
        'QuarterRawHHI':f"{float(q['author_hhi']):.3f}",
        'QuarterLinkedHHI':f"{float(q['core_alias_linked_author_hhi']):.3f}",
        'QuarterAuthorClockHHI':f"{float(author_clock['author_clock_hhi']):.3f}",
        'OldTopAuthor':f"{previous['top1_percent']:.2f}",
        'NewTopAuthor':f"{current['top1_percent']:.2f}",
        'OldCommitCount':previous['events'],'NewCommitCount':current['events'],
        'OldAuthorCount':previous['entities'],'NewAuthorCount':current['entities'],
    }
    output=paper/'generated';output.mkdir(exist_ok=True)
    (output/'data_macros.tex').write_text('% Generated; edit scripts/data, not this file.\n'+
        '\n'.join('\\newcommand{\\'+name+'}{'+str(value)+'}' for name,value in values.items())+'\n')
    (output/'data_manifest.json').write_text(json.dumps(dict(source_sha256=sources,values=values,
        manuscript_status='Working draft: independent issue coding, mechanism validation and contribution assessment remain pending'),indent=2)+'\n')
    labels={'lag90_merger_hhi_per_01':'Prior 90-day merger HHI (+0.1)',
            'lag90_merges_log2':'Prior merge volume (log$_2$)',
            'backlog_log2':'Pending PRs at submission (log$_2$)',
            'prior_author_prs_log2':'Prior author PRs (log$_2$)',
            'prior_integrator_author':'Author previously integrated a PR',
            'calendar_years':'Calendar time (+one year)'}
    table=['\\begin{tabular}{lrrr}','\\toprule','Covariate & HR & Lower 95\\% & Upper 95\\% \\\\','\\midrule']
    for r in main_model['coefficients']:
        table.append(labels[r['variable']]+f" & {r['hazard_ratio']:.3f} & {r['lower_95']:.3f} & {r['upper_95']:.3f} \\\\")
    table.extend(['\\bottomrule','\\end{tabular}'])
    (output/'model_table.tex').write_text('\n'.join(table)+'\n')
    window_table=['\\begin{tabular}{llrrr}','\\toprule',
                  'Window & Specification & PRs & HR & 95\\% interval \\\\','\\midrule']
    names={'primary':'Primary', 'external_authors_sensitivity':'No prior integration',
           'role_experience_stratified_sensitivity':'Role/experience strata',
           'without_backlog_sensitivity':'Role strata, no backlog'}
    for days,model in sorted(window_models.items()):
        for name,label in names.items():
            coefficient=next(r for r in model[name]['coefficients'] if r['variable']==f'lag{days}_merger_hhi_per_01')
            window_table.append(f"{days} days & {label} & {model[name]['observations']} & {coefficient['hazard_ratio']:.3f} & "
                                f"{coefficient['lower_95']:.3f}--{coefficient['upper_95']:.3f} \\\\")
    window_table.extend(['\\bottomrule','\\end{tabular}'])
    (output/'window_table.tex').write_text('\n'.join(window_table)+'\n')
    age_table=['\\begin{tabular}{llrrr}','\\toprule',
               'Window & Backlog & Through day 30 & After day 30 & Late merges \\\\','\\midrule']
    for days,model in sorted(window_piecewise.items()):
        for name,label in [(None,'Reconstructed'),('without_backlog_sensitivity','Omitted')]:
            variant=model if name is None else model[name]
            early,late=variant['concentration_effects']
            def cell(effect):
                return f"{effect['hazard_ratio']:.3f} ({effect['lower_95']:.3f}--{effect['upper_95']:.3f})"
            age_table.append(f"{days} days & {label} & {cell(early)} & {cell(late)} & {late['merge_events_in_period']} \\\\")
    age_table.extend(['\\bottomrule','\\end{tabular}'])
    (output/'piecewise_window_table.tex').write_text('\n'.join(age_table)+'\n')
    scope_labels={'all_merged':'All merged', 'production_go_touched':'Go touched',
                  'production_with_only_go_files':'Go files only', 'documentation_touched':'Docs touched'}
    scope_table=['\\begin{tabular}{llrrrrr}','\\toprule',
                 'Quarter & Scope & Merged & Reviewed & Author & Review & Integrator \\\\','\\midrule']
    for r in scope_rows:
        if r['merge_quarter'] not in ['2026-Q2','2026-Q3'] or r['artifact_scope'] not in scope_labels:continue
        hhis=[f"{float(r[key]):.3f}" for key in ['author_hhi','fractional_reviewer_hhi','integrator_hhi']]
        scope_table.append(' & '.join([r['merge_quarter'],scope_labels[r['artifact_scope']],r['merged_prs'],
                              r['reviewed_merged_prs'],*hhis])+' \\\\')
    scope_table.extend(['\\bottomrule','\\end{tabular}'])
    (output/'file_scope_table.tex').write_text('\n'.join(scope_table)+'\n')
    robustness_table=['\\begin{tabular}{lrrr}','\\toprule',
                      'Episode definition & Constant HR (95\\%) & Late HR (95\\%) & Late merges \\\\','\\midrule']
    for label,constant,late in [('First terminal state',main_hhi,piece_late),
                               ('Five-minute coalescing',coalesced_constant,coalesced_late)]:
        def formatted(effect):
            return f"{effect['hazard_ratio']:.3f} ({effect['lower_95']:.3f}--{effect['upper_95']:.3f})"
        robustness_table.append(' & '.join([label,formatted(constant),formatted(late),str(late['merge_events_in_period'])])+' \\\\')
    robustness_table.extend(['\\bottomrule','\\end{tabular}'])
    (output/'robustness_table.tex').write_text('\n'.join(robustness_table)+'\n')
    identity_table=['\\begin{tabular}{lrrrcrr}','\\toprule',
                    'Quarter & PRs & Missing & Reviewer & Outer range & Author & Integrator \\\\','\\midrule']
    for r in affected_identity_rows:
        interval=f"{float(r['lower_hhi']):.3f}--{float(r['upper_hhi']):.3f}"
        values_row=[r['merge_quarter'],r['reviewed_prs'],r['eligible_missing_records'],
                    f"{float(r['observed_hhi']):.3f}",interval,
                    f"{float(r['author_hhi']):.3f}",f"{float(r['integrator_hhi']):.3f}"]
        identity_table.append(' & '.join(values_row)+' \\\\')
    identity_table.extend(['\\bottomrule','\\end{tabular}'])
    (output/'identity_bounds_table.tex').write_text('\n'.join(identity_table)+'\n')
    repair_table=['\\begin{tabular}{llccc}','\\toprule',
                  'Issue & Component oracle & Parent & Repair & Controls \\\\','\\midrule']
    repair_labels={2121:'Duplicate-name rename',2181:'Two /32 peer directions'}
    repair_plan=json.loads((paper/'repair_validation_plan.json').read_text())
    repair_cases={r['issue']:r for r in repair_run['cases']}
    for case in repair_plan['cases']:
        variants=repair_cases[case['issue']]['variants']
        def results(variant, names):
            return '/'.join(variants[variant]['test_outcomes'][name].capitalize() for name in names)
        controls='/'.join(results(v,case['control_tests']) for v in ['parent','fixed'])
        repair_table.append(' & '.join([str(case['issue']),repair_labels[case['issue']],
            results('parent',case['regression_tests']),results('fixed',case['regression_tests']),
            controls])+' \\\\')
    repair_table.extend(['\\bottomrule','\\end{tabular}'])
    (output/'repair_validation_table.tex').write_text('\n'.join(repair_table)+'\n')
    print('Generated',len(values),'traceable manuscript quantities.')


if __name__=='__main__':
    main()
