#!/usr/bin/env python3
"""Audit observed account overlap and collected-revision review linkage.

Role participation is not effort, knowledge or policy enforcement. Approval
states and head SHAs are collected values, not immutable merge-time facts.
"""
import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from analyze import parse_time, quarter, write_csv
from analyze_process import human
from analyze_review_census import review_observation
from collect_process import load_endpoint
from pr_history import in_observed_open_interval, reconstruct


ROOT = Path(__file__).resolve().parents[2]
CATEGORIES = (
    "self_merge_with_other_account_review",
    "self_merge_without_attributed_other_account_review",
    "other_merger_with_merger_only_review",
    "other_merger_with_third_account_review",
    "other_merger_without_attributed_other_account_review",
)
HELPERS = (
    "analyze_role_overlap.py", "analyze.py", "analyze_process.py",
    "analyze_review_census.py", "collect_process.py", "github_auth.py",
    "pr_history.py", "research_metrics.py", "test_role_overlap.py",
)
LIMITATIONS = (
    "Observed accounts are not independently verified people, effort, knowledge holders or bus-factor estimates.",
    "Submitted-review participation does not establish review quality, gate enforcement or maintenance intent.",
    "A record labelled APPROVED in the collected snapshot is not proof of historical approval validity at merge; later or dismissed decisions may alter interpretation.",
    "The collected detail.head.sha is not independently established as the historical merge-time revision.",
    "A missing exact review-commit/head-SHA match does not establish unreviewed changes or a bypass.",
    "Unattributed submitted records may change observed account categories or union counts; human status and distinctness are not assigned.",
    "Deleted or informal reviews, account aliases and non-atomic collection remain outside this audit.",
    "These exploratory census descriptions do not supply human outcome coding, repair prevalence, causal effects or a novelty/acceptance claim.",
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def remember(path, sources, root=ROOT):
    key = str(path.resolve().relative_to(root.resolve()))
    value = digest(path)
    if key in sources and sources[key] != value:
        raise ValueError("Input changed during analysis: " + key)
    sources[key] = value


def checked_endpoint(directory, sources, root=ROOT):
    """Validate pagination and record both coverage and actual page bytes."""
    status_path = directory / "coverage.json"
    remember(status_path, sources, root)
    status = json.loads(status_path.read_text())
    if status.get("complete") is not True or status.get("next_url"):
        raise ValueError("Incomplete raw endpoint: " + str(directory))
    names = [page["file"] for page in status["pages"]]
    if not names or len(names) != len(set(names)):
        raise ValueError("Missing or duplicated raw endpoint pages")
    for page in status["pages"]:
        name = page["file"]
        if Path(name).name != name or not name.startswith("page_"):
            raise ValueError("Unexpected cached page name")
        path = directory / name
        remember(path, sources, root)
        if digest(path) != page["sha256"]:
            raise ValueError("Cached endpoint checksum mismatch: " + str(path))
    return load_endpoint(directory), status


def participation_category(author, integrator, reviewers):
    """Partition account participation; authors are excluded from reviewers."""
    author = author.casefold()
    integrator = integrator.casefold()
    reviewers = {actor.casefold() for actor in reviewers}
    if not author or not integrator or "" in reviewers or author in reviewers:
        raise ValueError("Role accounts must be nonempty and reviewers non-author")
    if author == integrator:
        return CATEGORIES[0] if reviewers else CATEGORIES[1]
    if not reviewers:
        return CATEGORIES[4]
    return CATEGORIES[2] if reviewers == {integrator} else CATEGORIES[3]


def role_observation(detail, reviews, cutoff, history):
    """Return PR/account diagnostics plus every raw record's eligibility audit."""
    if history["anomalies"]:
        raise ValueError("Lifecycle anomalies preclude role interpretation")
    merged = parse_time(detail["merged_at"]) if detail.get("merged_at") else None
    if merged is None or merged > cutoff or not human(detail["user"]):
        raise ValueError("Role audit requires a human-account merged PR by cutoff")
    author = detail["user"]["login"].casefold()
    integrator = (detail.get("merged_by") or {}).get("login", "").casefold()
    if not integrator:
        raise ValueError("Merged PR lacks an attributable integrator")
    result = review_observation(detail, reviews, cutoff, history)
    allowed = {event[2] for event in result["all_open_events"]}
    reviewers = {event[1] for event in result["all_open_events"]}
    head = (detail.get("head") or {}).get("sha") or ""
    if head and not re.fullmatch(r"[0-9a-fA-F]{40}", head):
        raise ValueError("Collected head SHA is not a full commit identifier")
    head = head.casefold()
    created = parse_time(detail["created_at"])
    audit = []
    for review in reviews:
        user = review.get("user") or {}
        actor = (user.get("login") or "").casefold()
        state = review.get("state", "")
        submitted = review.get("submitted_at")
        unknown = not actor
        reason = "eligible_attributed_other_account"
        if state == "PENDING" or not submitted:
            reason = "pending_or_unsubmitted"
        elif actor and not human(user):
            reason = "bot_actor"
        elif actor == author:
            reason = "author_self_review"
        else:
            timestamp = parse_time(submitted)
            if timestamp < created:
                raise ValueError("Submitted review precedes PR creation")
            if timestamp > cutoff:
                reason = "after_cutoff"
            elif timestamp > result["final_terminal"]:
                reason = "after_final_disposition"
            elif not in_observed_open_interval(history, timestamp):
                reason = "during_closed_interval"
            elif unknown:
                reason = "eligible_time_unattributed_actor"
        eligible = reason == "eligible_attributed_other_account"
        if eligible != (review["id"] in allowed):
            raise ValueError("Review eligibility differs from completed role census")
        commit = (review.get("commit_id") or "").casefold()
        audit.append(dict(
            number=detail["number"], review_id=review["id"],
            actor_account=actor, actor_type=user.get("type", ""),
            collected_state=state, submitted_at=submitted or "",
            review_commit_id=commit, collected_head_sha=head,
            review_commit_matches_collected_head=int(bool(head and commit == head)),
            eligibility=reason, attributed_eligible=int(eligible),
            temporally_eligible_unattributed=int(reason == "eligible_time_unattributed_actor"),
            collected_approved_eligible=int(eligible and state == "APPROVED"),
            collected_approved_matching_head_eligible=int(
                eligible and state == "APPROVED" and bool(head) and commit == head),
            url=review.get("html_url", ""),
        ))
    approved = [r for r in audit if r["collected_approved_eligible"]]
    head_approved = [r for r in audit if r["collected_approved_matching_head_eligible"]]
    approved_accounts = {r["actor_account"] for r in approved}
    head_approved_accounts = {r["actor_account"] for r in head_approved}
    thirds = reviewers - {author, integrator}
    union = reviewers | {author, integrator}
    row = dict(
        number=detail["number"], url=detail.get("html_url", ""),
        author=author, integrator=integrator, merged_at=detail["merged_at"],
        merge_quarter=quarter(merged), self_merge=int(author == integrator),
        participation_category=participation_category(author, integrator, reviewers),
        attributed_reviewer_accounts=";".join(sorted(reviewers)),
        attributed_reviewer_account_count=len(reviewers),
        merger_among_reviewers=int(integrator in reviewers),
        reviewer_accounts_other_than_author_and_integrator=";".join(sorted(thirds)),
        reviewer_account_count_other_than_author_and_integrator=len(thirds),
        visible_union_accounts=";".join(sorted(union)),
        visible_union_account_count=len(union),
        attributed_eligible_review_records=len(allowed),
        temporally_eligible_unattributed_records=sum(r["temporally_eligible_unattributed"] for r in audit),
        raw_unattributed_records=result["excluded"].get("unknown_actor", 0),
        collected_approved_records=len(approved),
        collected_approved_accounts=";".join(sorted(approved_accounts)),
        collected_approved_account_count=len(approved_accounts),
        collected_approved_matching_head_records=len(head_approved),
        collected_approved_matching_head_accounts=";".join(sorted(head_approved_accounts)),
        collected_approved_matching_head_account_count=len(head_approved_accounts),
        has_attributed_other_account_review=int(bool(reviewers)),
        has_collected_approved_record=int(bool(approved)),
        has_collected_head_matching_approved_record=int(bool(head_approved)),
        has_review_by_account_other_than_author_and_integrator=int(bool(thirds)),
        has_approved_record_by_account_other_than_author_and_integrator=int(bool(approved_accounts - {author, integrator})),
        has_at_least_three_visible_accounts=int(len(union) >= 3),
        collected_head_sha=head,
    )
    return row, audit


def aggregate(rows):
    categories = Counter(r["participation_category"] for r in rows)
    if set(categories) - set(CATEGORIES) or sum(categories.values()) != len(rows):
        raise ValueError("Participation categories do not partition merged PRs")
    union_counts = Counter(r["visible_union_account_count"] for r in rows)
    result = dict(merged_human_prs=len(rows),
                  attributed_reviewed_prs=sum(r["has_attributed_other_account_review"] for r in rows),
                  self_merged_prs=sum(r["self_merge"] for r in rows),
                  prs_with_collected_approved_record=sum(r["has_collected_approved_record"] for r in rows),
                  prs_with_collected_head_matching_approved_record=sum(r["has_collected_head_matching_approved_record"] for r in rows),
                  self_merges_with_attributed_review=sum(r["self_merge"] * r["has_attributed_other_account_review"] for r in rows),
                  self_merges_with_collected_approved_record=sum(r["self_merge"] * r["has_collected_approved_record"] for r in rows),
                  self_merges_with_collected_head_matching_approved_record=sum(r["self_merge"] * r["has_collected_head_matching_approved_record"] for r in rows),
                  prs_with_review_by_account_other_than_author_and_integrator=sum(r["has_review_by_account_other_than_author_and_integrator"] for r in rows),
                  prs_with_approved_record_by_account_other_than_author_and_integrator=sum(r["has_approved_record_by_account_other_than_author_and_integrator"] for r in rows),
                  prs_with_at_least_three_visible_accounts=sum(r["has_at_least_three_visible_accounts"] for r in rows),
                  prs_with_temporally_eligible_unattributed_records=sum(r["temporally_eligible_unattributed_records"] > 0 for r in rows),
                  temporally_eligible_unattributed_records=sum(r["temporally_eligible_unattributed_records"] for r in rows))
    result.update({category: categories[category] for category in CATEGORIES})
    result["visible_union_account_counts"] = dict(sorted(union_counts.items()))
    return result


def compare_membership(rows, review_rows, metadata_rows):
    """Refuse plausible totals whose PR membership or account assignments differ."""
    def unique(items):
        result = {int(r["number"]): r for r in items}
        if len(result) != len(items):
            raise ValueError("Duplicate PR membership")
        return result
    actual = unique(rows)
    review = unique([r for r in review_rows if r["eventual_merge"] == "1"])
    metadata = unique([r for r in metadata_rows if r["final_observed_outcome"] == "merge"])
    if actual.keys() != review.keys() or actual.keys() != metadata.keys():
        raise ValueError("Merged-PR membership differs from existing censuses")
    for number, row in actual.items():
        reference = review[number]
        pairs = (
            (row["author"], reference["author"].casefold()),
            (row["integrator"], reference["merged_by"].casefold()),
            (row["merge_quarter"], reference["merge_quarter"]),
            (row["attributed_reviewer_accounts"], reference["human_reviewers"]),
            (row["attributed_eligible_review_records"], int(reference["submitted_review_records"])),
            (row["raw_unattributed_records"], int(reference["unknown_actor_records"])),
            (row["author"], metadata[number]["author"].casefold()),
            (row["integrator"], metadata[number]["merged_by"].casefold()),
            (row["self_merge"], int(metadata[number]["self_merged"])),
        )
        if any(left != right for left, right in pairs):
            raise ValueError("Role assignments differ for PR " + str(number))


def verify_export(root=ROOT, output=None):
    """Read-only integrity and raw-record replay for the exported census.

    Return the summary after checking every source/output hash, rerunning
    eligibility and account diagnostics, and comparing all quarter aggregates.
    This verifies the declared operationalization, not semantic review quality.
    """
    root = Path(root).resolve()
    output = Path(output) if output is not None else root / "Paper/results/role-overlap"
    summary = json.loads((output / "summary.json").read_text())
    manifest_path = output / "source_manifest.json"
    if summary.get("complete") is not True or digest(manifest_path) != summary["source_manifest_sha256"]:
        raise ValueError("Incomplete or altered role-overlap source manifest")
    sources = json.loads(manifest_path.read_text())["input_sha256"]
    if len(sources) != summary["source_files"]:
        raise ValueError("Role-overlap source-file count differs")
    for name, expected in sources.items():
        path = (root / name).resolve()
        path.relative_to(root)
        if digest(path) != expected:
            raise ValueError("Role-overlap input changed: " + name)
    helper_hashes = {str((Path("Paper/scripts") / name)): digest(root / "Paper/scripts" / name)
                     for name in HELPERS}
    if summary["script_and_helper_sha256"] != helper_hashes:
        raise ValueError("Role-overlap helper provenance is incomplete or changed")
    plan_path = output / "plan.json"
    plan = json.loads(plan_path.read_text())
    if digest(plan_path) != summary["plan_sha256"] or tuple(plan["categories"]) != CATEGORIES:
        raise ValueError("Role-overlap analysis plan changed")
    if parse_time(plan["created_at_utc"]) > parse_time(summary["started_at_utc"]):
        raise ValueError("Role-overlap plan postdates census execution")
    csv_names = ("pr_role_overlap.csv", "review_record_audit.csv", "quarterly_role_overlap.csv")
    expected_output_keys = {str((output / name).relative_to(root)) for name in csv_names}
    if set(summary["output_sha256"]) != expected_output_keys:
        raise ValueError("Role-overlap output provenance is incomplete")
    tables = {}
    for name in csv_names:
        path = output / name
        if digest(path) != summary["output_sha256"][str(path.relative_to(root))]:
            raise ValueError("Role-overlap output changed: " + name)
        with path.open(newline="") as handle:
            tables[name] = list(csv.DictReader(handle))
    exported = {int(r["number"]): r for r in tables["pr_role_overlap.csv"]}
    if len(exported) != len(tables["pr_role_overlap.csv"]):
        raise ValueError("Duplicate exported PR membership")
    exported_reviews = {(int(r["number"]), int(r["review_id"])): r
                        for r in tables["review_record_audit.csv"]}
    if len(exported_reviews) != len(tables["review_record_audit.csv"]):
        raise ValueError("Duplicate exported review records")
    raw_sources = {}
    input_path = root / plan["input"]
    prs, status = checked_endpoint(input_path / "pr_list", raw_sources, root)
    cutoff = max(parse_time(page["fetched_at_utc"]) for page in status["pages"])
    if cutoff != parse_time(summary["cutoff_timestamp_utc"]):
        raise ValueError("Role-overlap cutoff differs from pinned list")
    replayed, replayed_review_keys = [], set()
    for pr in prs:
        folder = input_path / "pulls" / str(pr["number"])
        detail, _ = checked_endpoint(folder / "detail", raw_sources, root)
        if detail["number"] != pr["number"]:
            raise ValueError("Raw detail membership differs during verification")
        merged = parse_time(detail["merged_at"]) if detail.get("merged_at") else None
        if not human(detail["user"]) or merged is None or merged > cutoff:
            continue
        reviews, _ = checked_endpoint(folder / "reviews", raw_sources, root)
        timeline, _ = checked_endpoint(folder / "timeline", raw_sources, root)
        row, audit = role_observation(detail, reviews, cutoff, reconstruct(detail, timeline, cutoff))
        reference = exported.get(pr["number"])
        if reference is None or any(str(value) != reference.get(key) for key, value in row.items()):
            raise ValueError("Role-overlap PR audit differs from raw sources")
        replayed.append(row)
        for record in audit:
            key = (record["number"], record["review_id"])
            reference = exported_reviews.get(key)
            if reference is None or any(str(value) != reference.get(field) for field, value in record.items()):
                raise ValueError("Role-overlap review audit differs from raw sources")
            replayed_review_keys.add(key)
    if set(exported) != {r["number"] for r in replayed} or set(exported_reviews) != replayed_review_keys:
        raise ValueError("Exported PR/review population differs from raw replay")
    required_paths = [plan_path, root / "Paper/pyproject.toml", root / "Paper/uv.lock", root / "run-research.sh"]
    required_paths.extend(root / name for name in helper_hashes)
    for folder, names in ((plan["review_census"], ("summary.json", "coverage.json", "human_pr_formal_review_observations.csv")),
                          (plan["merger_census"], ("summary.json", "human_pr_metadata_features.csv"))):
        required_paths.extend(root / folder / name for name in names)
    required = {str(path.relative_to(root)): digest(path) for path in required_paths}
    required.update(raw_sources)
    if any(sources.get(name) != value for name, value in required.items()):
        raise ValueError("Role-overlap raw/helper/lock provenance omits required inputs")
    with (root / plan["review_census"] / "human_pr_formal_review_observations.csv").open(newline="") as handle:
        reference_reviews = list(csv.DictReader(handle))
    with (root / plan["merger_census"] / "human_pr_metadata_features.csv").open(newline="") as handle:
        reference_metadata = list(csv.DictReader(handle))
    compare_membership(replayed, reference_reviews, reference_metadata)
    normalized_aggregate = json.loads(json.dumps(aggregate(replayed)))
    if summary["all_merged"] != normalized_aggregate or summary["raw_review_records_audited"] != len(exported_reviews):
        raise ValueError("Role-overlap summary differs from raw replay")
    groups = defaultdict(list)
    for row in replayed:
        groups[row["merge_quarter"]].append(row)
    quarters = {r["merge_quarter"]: r for r in tables["quarterly_role_overlap.csv"]}
    if len(quarters) != len(tables["quarterly_role_overlap.csv"]) or set(quarters) != set(groups) or summary["quarters"] != len(groups):
        raise ValueError("Role-overlap quarter membership differs")
    for q, group in groups.items():
        expected = aggregate(group)
        observed = quarters[q]
        for key, value in expected.items():
            same = (json.loads(observed[key]) == json.loads(json.dumps(value))
                    if isinstance(value, dict) else int(observed[key]) == value)
            if not same:
                raise ValueError("Role-overlap quarter aggregate differs: " + q)
        year, index = int(q[:4]), int(q[-1])
        end = datetime(year + (index == 4), 1 if index == 4 else index * 3 + 1, 1, tzinfo=timezone.utc)
        if int(observed["calendar_quarter_completed_by_cutoff"]) != int(end.timestamp() <= cutoff):
            raise ValueError("Role-overlap quarter boundary flag differs")
    if summary.get("limitations") != list(LIMITATIONS):
        raise ValueError("Role-overlap interpretation limits changed")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "Paper/data/pr-census-2026-10-13")
    parser.add_argument("--reviews", type=Path, default=ROOT / "Paper/results/review-census")
    parser.add_argument("--metadata", type=Path, default=ROOT / "Paper/results/pr-census")
    parser.add_argument("--output", type=Path, default=ROOT / "Paper/results/role-overlap")
    args = parser.parse_args()
    started = datetime.now(timezone.utc).isoformat()
    sources = {}
    plan_path = args.output / "plan.json"
    remember(plan_path, sources)
    plan = json.loads(plan_path.read_text())
    if tuple(plan["categories"]) != CATEGORIES or parse_time(plan["created_at_utc"]) > parse_time(started):
        raise ValueError("Expected a recorded pre-execution analysis plan")
    for actual, key in ((args.input, "input"), (args.reviews, "review_census"), (args.metadata, "merger_census")):
        if actual.resolve() != (ROOT / plan[key]).resolve():
            raise ValueError("Analysis input differs from the recorded plan: " + key)
    for path in [args.reviews / "summary.json", args.reviews / "coverage.json",
                 args.reviews / "human_pr_formal_review_observations.csv",
                 args.metadata / "summary.json", args.metadata / "human_pr_metadata_features.csv",
                 ROOT / "Paper/pyproject.toml", ROOT / "Paper/uv.lock", ROOT / "run-research.sh"]:
        remember(path, sources)
    helpers = {str((ROOT / "Paper/scripts" / name).relative_to(ROOT)):
               digest(ROOT / "Paper/scripts" / name) for name in HELPERS}
    sources.update(helpers)
    review_summary = json.loads((args.reviews / "summary.json").read_text())
    review_coverage = json.loads((args.reviews / "coverage.json").read_text())
    metadata_summary = json.loads((args.metadata / "summary.json").read_text())
    if not (review_summary["complete"] and review_summary["lifecycle_reconstructed"]
            and review_coverage["complete"] and review_coverage["timelines_required"]
            and metadata_summary["metadata_complete"]):
        raise ValueError("Complete lifecycle and metadata censuses required")
    with (args.reviews / "human_pr_formal_review_observations.csv").open(newline="") as handle:
        review_rows = list(csv.DictReader(handle))
    with (args.metadata / "human_pr_metadata_features.csv").open(newline="") as handle:
        metadata_rows = list(csv.DictReader(handle))
    prs, list_status = checked_endpoint(args.input / "pr_list", sources)
    cutoff = max(parse_time(page["fetched_at_utc"]) for page in list_status["pages"])
    numbers = [pr["number"] for pr in prs]
    if len(numbers) != len(set(numbers)) or len(numbers) != review_summary["total_prs"]:
        raise ValueError("PR-list population differs from completed census")
    rows = []
    records = []
    for number in sorted(numbers):
        folder = args.input / "pulls" / str(number)
        detail, detail_status = checked_endpoint(folder / "detail", sources)
        if detail["number"] != number:
            raise ValueError("Raw detail membership differs from PR list")
        merged = parse_time(detail["merged_at"]) if detail.get("merged_at") else None
        if not human(detail["user"]) or merged is None or merged > cutoff:
            continue
        reviews, review_status = checked_endpoint(folder / "reviews", sources)
        timeline, _ = checked_endpoint(folder / "timeline", sources)
        row, audit = role_observation(detail, reviews, cutoff, reconstruct(detail, timeline, cutoff))
        row.update(detail_source=str((folder / "detail").relative_to(ROOT)),
                   reviews_source=str((folder / "reviews").relative_to(ROOT)),
                   timeline_source=str((folder / "timeline").relative_to(ROOT)),
                   detail_fetched_at_utc=";".join(p["fetched_at_utc"] for p in detail_status["pages"]),
                   reviews_fetched_at_utc=";".join(p["fetched_at_utc"] for p in review_status["pages"]))
        rows.append(row)
        records.extend(audit)
    compare_membership(rows, review_rows, metadata_rows)
    if len(rows) != review_summary["human_merged_prs"] or len(rows) != metadata_summary["merge_events"]:
        raise ValueError("Merged count differs from completed censuses")
    groups = defaultdict(list)
    for row in rows:
        groups[row["merge_quarter"]].append(row)
    quarterly = []
    for q, group in sorted(groups.items()):
        year, index = int(q[:4]), int(q[-1])
        end = datetime(year + (index == 4), 1 if index == 4 else index * 3 + 1, 1, tzinfo=timezone.utc)
        item = dict(merge_quarter=q, calendar_quarter_completed_by_cutoff=int(end.timestamp() <= cutoff))
        item.update(aggregate(group))
        item["visible_union_account_counts"] = json.dumps(item["visible_union_account_counts"], sort_keys=True)
        quarterly.append(item)
    # Detect concurrent source edits rather than publishing internally mixed provenance.
    for name, expected in sources.items():
        if digest(ROOT / name) != expected:
            raise ValueError("Source changed during analysis: " + name)
    args.output.mkdir(parents=True, exist_ok=True)
    outputs = [args.output / "pr_role_overlap.csv", args.output / "review_record_audit.csv",
               args.output / "quarterly_role_overlap.csv"]
    for path, values in zip(outputs, (rows, records, quarterly)):
        write_csv(path, values)
    manifest_path = args.output / "source_manifest.json"
    manifest_path.write_text(json.dumps(dict(schema_version=1, input_sha256=dict(sorted(sources.items()))), indent=2) + "\n")
    summary = dict(schema_version=1, complete=True, started_at_utc=started,
                   completed_at_utc=datetime.now(timezone.utc).isoformat(),
                   cutoff_timestamp_utc=datetime.fromtimestamp(cutoff, timezone.utc).isoformat(),
                   plan_sha256=digest(plan_path), source_manifest_sha256=digest(manifest_path),
                   source_files=len(sources), script_and_helper_sha256=helpers,
                   output_sha256={str(path.relative_to(ROOT)): digest(path) for path in outputs},
                   population_membership_verified_against=[str(args.reviews.relative_to(ROOT)), str(args.metadata.relative_to(ROOT))],
                   raw_review_records_audited=len(records), quarters=len(quarterly),
                   all_merged=aggregate(rows), limitations=list(LIMITATIONS))
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({key: value for key, value in summary.items()
                      if key not in ("script_and_helper_sha256", "output_sha256")}, indent=2))


if __name__ == "__main__":
    main()
