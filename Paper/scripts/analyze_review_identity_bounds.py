"""Bound fractional reviewer HHI under missing identities on a fixed PR subset.

The bounds relax per-PR assignment constraints. They are conservative outer
bounds, not sharp identified sets, confidence intervals or identity imputations.
Only collected, submitted records in observed open intervals are considered.
"""
import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

from analyze import concentration, parse_time, quarter, write_csv
from analyze_process import human
from analyze_review_census import paired_role_counts, review_observation
from collect_process import load_endpoint
from pr_history import in_observed_open_interval, reconstruct


ROOT = Path(__file__).resolve().parents[2]
PAPER = ROOT / "Paper"
HELPERS = ("analyze_review_identity_bounds.py", "analyze_review_census.py",
           "analyze.py", "analyze_process.py", "collect_process.py", "pr_history.py")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def unknown_review_observations(detail, reviews, cutoff, history, final_terminal):
    """Apply time/opportunity boundaries to submitted records without a login.

    A missing login does not establish a human, non-author or distinct reviewer.
    Even a partially preserved account is left unresolved here. Every retained
    record supplies at most one additional eligible identity in the relaxation.
    """
    created = parse_time(detail["created_at"])
    output = []
    for review in reviews:
        if review.get("state") == "PENDING" or not review.get("submitted_at"):
            continue
        user = review.get("user")
        if user and user.get("login"):
            continue
        timestamp = parse_time(review["submitted_at"])
        if timestamp < created:
            raise ValueError("Unattributed submitted review precedes PR creation")
        reason = "retained"
        if timestamp > cutoff:
            reason = "after_cutoff"
        elif timestamp > final_terminal:
            reason = "after_final_disposition"
        elif not in_observed_open_interval(history, timestamp):
            reason = "during_closed_interval"
        output.append(dict(review_id=review["id"], submitted_at=review["submitted_at"],
                           state=review.get("state"), eligibility=reason,
                           account_metadata_is_null=int(user is None),
                           author_association=review.get("author_association", "")))
    return output


def reviewer_hhi_outer_bounds(rows):
    """Relax assignments using known-credit floors and finite identity capacity.

    For PR i, known reviewers K_i retain credit at least 1/(|K_i|+m_i),
    where m_i is its count of eligible unattributed records. Summing these floors
    gives L_a. Total review credit remains N. At most M=sum(m_i) extra accounts
    can receive credit. Any feasible assignment is therefore within the simplex
    w >= L with M additional zero-floor entries and sum(w)=N.
    """
    if not rows:
        raise ValueError("A fixed reviewed-PR population must be nonempty")
    floors = defaultdict(list)
    observed = defaultdict(list)
    missing = []
    distinct_human_extra_squares = []
    for row in rows:
        actors = [actor.casefold() for actor in row["known_reviewers"]]
        if not actors or not all(actors) or len(actors) != len(set(actors)):
            raise ValueError("Each included PR needs unique known reviewer accounts")
        m = row["eligible_missing_records"]
        if type(m) is not int or m < 0:
            raise ValueError("Missing-review capacity must be a nonnegative integer")
        missing.append(m)
        distinct_human_extra_squares.append(m / (len(actors) + m) ** 2)
        for actor in actors:
            floors[actor].append(1 / (len(actors) + m))
            observed[actor].append(1 / len(actors))
    floor_values = [math.fsum(floors[a]) for a in sorted(floors)]
    observed_values = [math.fsum(observed[a]) for a in sorted(observed)]
    n, m = len(rows), sum(missing)
    floor_total = math.fsum(floor_values)
    residual = n - floor_total
    if residual < -1e-10 or abs(math.fsum(observed_values) - n) > 1e-10:
        raise ValueError("Fractional review credit does not conserve PR membership")
    residual = max(0.0, residual)
    observed_hhi = math.fsum(w * w for w in observed_values) / n ** 2
    distinct_human_hhi = (math.fsum(w * w for w in floor_values) +
                         math.fsum(distinct_human_extra_squares)) / n ** 2
    if m == 0:
        lower = upper = observed_hhi
        water_level = 0.0
    else:
        # The convex minimum raises low weights to a common level while
        # retaining larger floors. Bisection solves sum(max(L_j,t)) = N,
        # including M zero floors for the maximum additional account capacity.
        low, high = 0.0, float(n)
        for _ in range(100):
            level = (low + high) / 2
            total = math.fsum(max(w, level) for w in floor_values) + m * level
            if total <= n:
                low = level
            else:
                high = level
        water_level = low
        lower = (math.fsum(max(w, low) ** 2 for w in floor_values) +
                 m * low ** 2) / n ** 2
        # All residual mass on the largest floor maximizes squared weight.
        largest = max(floor_values)
        upper = (math.fsum(w * w for w in floor_values) +
                 2 * largest * residual + residual ** 2) / n ** 2
    if not (lower <= observed_hhi + 1e-12 <= upper + 2e-12):
        raise ValueError("Observed HHI lies outside the missing-identity relaxation")
    return dict(reviewed_prs=n, known_accounts=len(floor_values),
                eligible_missing_records=m, affected_prs=sum(v > 0 for v in missing),
                maximum_additional_accounts=m, observed_hhi=observed_hhi,
                lower_hhi=max(0.0, lower), upper_hhi=min(1.0, upper),
                all_missing_distinct_human_scenario_hhi=distinct_human_hhi,
                guaranteed_known_credit=floor_total, residual_credit=residual,
                water_level=water_level)


def read_rows(path, key):
    with path.open() as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != len({row[key] for row in rows}):
        raise ValueError("Duplicate observations in " + str(path))
    return {row[key]: row for row in rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=PAPER / "data/pr-census-2026-10-13")
    parser.add_argument("--review-results", type=Path, default=PAPER / "results/review-census")
    parser.add_argument("--output", type=Path, default=PAPER / "results/review-identity-sensitivity")
    args = parser.parse_args()
    review_summary_path = args.review_results / "summary.json"
    summary = json.loads(review_summary_path.read_text())
    coverage_path = args.review_results / "coverage.json"
    if (not summary["complete"] or not summary["lifecycle_reconstructed"] or
            not json.loads(coverage_path.read_text())["complete"]):
        raise ValueError("Complete lifecycle-aware review census is required")
    if summary["provenance"]["script_sha256"] != digest(PAPER / "scripts/analyze_review_census.py"):
        raise ValueError("Review analysis differs from its recorded source")
    raw_sources = summary["provenance"]["source_coverage_sha256"]

    def checked_endpoint(directory):
        name = str((directory / "coverage.json").relative_to(ROOT))
        if raw_sources.get(name) != digest(directory / "coverage.json"):
            raise ValueError("Raw endpoint coverage differs from review census: " + name)
        return load_endpoint(directory)

    prs = checked_endpoint(args.input / "pr_list")
    if len(prs) != len({pr["number"] for pr in prs}):
        raise ValueError("Duplicate PR list membership")
    if len(prs) != summary["total_prs"]:
        raise ValueError("Raw PR population differs from review census")
    cutoff = max(parse_time(page["fetched_at_utc"]) for page in
                 json.loads((args.input / "pr_list/coverage.json").read_text())["pages"])
    observations_path = args.review_results / "human_pr_formal_review_observations.csv"
    quarters_path = args.review_results / "quarterly_paired_review_roles.csv"
    observations = read_rows(observations_path, "number")
    reference_quarters = read_rows(quarters_path, "merge_quarter")
    groups = defaultdict(list)
    per_pr, unknown_audit = [], []
    matched_human_numbers = set()
    for pr in prs:
        number = pr["number"]
        directory = args.input / "pulls" / str(number)
        detail = checked_endpoint(directory / "detail")
        reviews = checked_endpoint(directory / "reviews")
        timeline = checked_endpoint(directory / "timeline")
        if detail["number"] != number:
            raise ValueError("PR detail and population membership differ")
        history = reconstruct(detail, timeline, cutoff)
        if history["anomalies"]:
            raise ValueError("Unresolved lifecycle anomalies for PR " + str(number))
        result = review_observation(detail, reviews, cutoff, history)
        unknown = unknown_review_observations(detail, reviews, cutoff, history,
                                              result["final_terminal"])
        if len(unknown) != result["excluded"].get("unknown_actor", 0):
            raise ValueError("Missing-identity audit differs from review-census count")
        actors = sorted({event[1] for event in result["all_open_events"]})
        is_human = human(detail["user"])
        merged = parse_time(detail["merged_at"]) if detail.get("merged_at") else None
        is_merged = merged is not None and merged <= cutoff
        q = quarter(merged) if is_merged else ""
        fixed_subset = bool(is_human and is_merged and actors)
        for record in unknown:
            unknown_audit.append(dict(number=number, **record, human_pr=int(is_human),
                                      merged_by_cutoff=int(is_merged), merge_quarter=q,
                                      known_reviewers=len(actors),
                                      in_fixed_reviewed_subset=int(fixed_subset)))
        if not is_human:
            continue
        row = observations.get(str(number))
        if row is None:
            raise ValueError("Human PR is absent from the review observation export")
        matched_human_numbers.add(str(number))
        if (row["human_reviewers"] != ";".join(actors) or
                int(row["eventual_merge"]) != int(is_merged) or
                row["merge_quarter"] != q or
                row["author"] != detail["user"]["login"].casefold()):
            raise ValueError("Known role membership differs for PR " + str(number))
        if not is_merged:
            continue
        merger = (detail.get("merged_by") or {}).get("login", "")
        if not merger or merger.casefold() != row["merged_by"].casefold():
            raise ValueError("Matched merged PR needs its recorded integrator")
        missing = sum(record["eligibility"] == "retained" for record in unknown)
        null_missing = sum(record["eligibility"] == "retained" and
                           record["account_metadata_is_null"] for record in unknown)
        item = dict(number=number, merge_quarter=q, author=row["author"],
                    merged_by=merger.casefold(), human_reviewers=row["human_reviewers"],
                    known_reviewers=actors, eligible_missing_records=missing,
                    eligible_missing_null_account_records=null_missing)
        groups[q].append(item)
        per_pr.append({key: value for key, value in item.items() if key != "known_reviewers"})
    if matched_human_numbers != set(observations) or len(observations) != summary["human_prs"]:
        raise ValueError("Human population differs from the complete review export")
    if len(unknown_audit) != summary["excluded_review_records"]["unknown_actor"]:
        raise ValueError("Global unattributed-record audit is incomplete")
    if set(groups) != set(reference_quarters):
        raise ValueError("Merged-quarter population differs from review census")
    output_quarters = []
    for q, group in sorted(groups.items()):
        reviewed = [row for row in group if row["known_reviewers"]]
        reference = reference_quarters[q]
        if (len(group) != int(reference["human_merged_prs"]) or
                len(reviewed) != int(reference["reviewed_human_merged_prs"])):
            raise ValueError("Quarter membership differs: " + q)
        item = dict(merge_quarter=q, human_merged_prs=len(group),
                    reviewed_prs=len(reviewed), known_accounts=0,
                    eligible_missing_records=0, affected_prs=0,
                    maximum_additional_accounts=0, observed_hhi=None,
                    lower_hhi=None, upper_hhi=None, guaranteed_known_credit=None,
                    residual_credit=None, water_level=None, author_hhi=None,
                    integrator_hhi=None, integrator_above_upper=None,
                    reviewer_author_order_unresolved=None,
                    all_missing_distinct_human_scenario_hhi=None,
                    all_eligible_missing_account_metadata_null=None,
                    known_only_and_distinct_human_scenarios_cross_author_hhi=None)
        if reviewed:
            item.update(reviewer_hhi_outer_bounds(reviewed))
            if abs(item["observed_hhi"] - float(reference["fractional_reviewer_hhi"])) > 1e-10:
                raise ValueError("Known-reviewer HHI differs: " + q)
            roles = paired_role_counts(reviewed)
            for role in ("author", "integrator"):
                value = concentration(roles[role])["hhi"]
                if abs(value - float(reference[role + "_hhi"])) > 1e-10:
                    raise ValueError("Matched " + role + " HHI differs: " + q)
                item[role + "_hhi"] = value
            item["integrator_above_upper"] = int(item["integrator_hhi"] > item["upper_hhi"])
            item["reviewer_author_order_unresolved"] = int(
                item["lower_hhi"] <= item["author_hhi"] <= item["upper_hhi"])
            item["all_eligible_missing_account_metadata_null"] = int(all(
                r["eligible_missing_records"] == r["eligible_missing_null_account_records"]
                for r in reviewed))
            item["known_only_and_distinct_human_scenarios_cross_author_hhi"] = int(
                item["all_eligible_missing_account_metadata_null"] and
                (item["observed_hhi"] - item["author_hhi"]) *
                (item["all_missing_distinct_human_scenario_hhi"] - item["author_hhi"]) < 0)
        output_quarters.append(item)
    affected = [row for row in output_quarters if row["eligible_missing_records"]]
    aggregate = dict(
        complete=True, fixed_population="Human-authored merged PRs with at least one attributed eligible review",
        total_prs=len(prs), human_merged_prs=len(per_pr),
        fixed_reviewed_prs=sum(row["reviewed_prs"] for row in output_quarters),
        global_unattributed_submitted_records=len(unknown_audit),
        eligible_missing_records_in_fixed_subset=sum(row["eligible_missing_records"] for row in affected),
        affected_fixed_subset_prs=sum(row["affected_prs"] for row in affected),
        affected_quarters=[row["merge_quarter"] for row in affected],
        affected_quarters_with_integrator_above_upper=[
            row["merge_quarter"] for row in affected if row["integrator_above_upper"]],
        affected_quarters_with_unresolved_reviewer_author_order=[
            row["merge_quarter"] for row in affected if row["reviewer_author_order_unresolved"]],
        quarters_with_opposite_reviewer_author_order_in_two_identity_scenarios=[
            row["merge_quarter"] for row in affected
            if row["known_only_and_distinct_human_scenarios_cross_author_hhi"]],
        unknown_record_audit_counts=dict(Counter(
            f'{row["eligibility"]}:fixed_subset={row["in_fixed_reviewed_subset"]}'
            for row in unknown_audit)),
        method="Conservative outer bounds from credit floors, finite account capacity and simplex relaxation",
        sharp_bounds=False, confidence_intervals=False, identities_imputed=False,
        identity_scenarios=[
            "Known-only: missing actors supply no additional eligible human identities.",
            "All-distinct-human: each eligible missing record adds a different non-author human account; accounts are distinct across PRs.",
            "These are conditional what-if assignments, not estimates of actual identities or their likelihood.",
        ],
        cutoff_utc=max(page["fetched_at_utc"] for page in
                       json.loads((args.input / "pr_list/coverage.json").read_text())["pages"]),
        limitations=[
            "The population is fixed to PRs with an attributed review; it does not expand to unknown-only or unreviewed PRs.",
            "Unknown records can refer to authors, bots, repeated reviewers, known accounts or additional accounts.",
            "Per-PR incidence and author-exclusion constraints are relaxed; endpoints need not be attainable assignments.",
            "The all-distinct-human scenario assumes every eligible missing record adds an otherwise unknown eligible account; no actual account is assigned.",
            "Observed open intervals and final-disposition boundaries use collected lifecycle events at second precision.",
            "Deleted records, missing informal reviews, known-account aliases and unobserved identity changes are outside these bounds.",
            "No sampling uncertainty, effort, knowledge ownership or bus factor is estimated.",
        ],
        provenance=dict(
            scripts_sha256={name: digest(PAPER / "scripts" / name) for name in HELPERS},
            source_sha256={str(path.relative_to(ROOT)): digest(path) for path in
                           (review_summary_path, coverage_path, observations_path, quarters_path,
                            PAPER / "uv.lock")}))
    if aggregate["fixed_reviewed_prs"] != summary["human_merged_with_observed_submitted_review"]:
        raise ValueError("Fixed reviewed population size differs")
    args.output.mkdir(parents=True, exist_ok=True)
    outputs = {
        "quarterly_hhi_outer_bounds.csv": output_quarters,
        "merged_pr_identity_inputs.csv": per_pr,
        "unattributed_record_audit.csv": unknown_audit,
    }
    for name, rows in outputs.items():
        write_csv(args.output / name, rows)
    aggregate["output_sha256"] = {name: digest(args.output / name) for name in outputs}
    (args.output / "summary.json").write_text(json.dumps(aggregate, indent=2) + "\n")
    print(json.dumps({key: value for key, value in aggregate.items()
                      if key not in {"provenance", "output_sha256", "limitations"}}, indent=2))


if __name__ == "__main__":
    main()
