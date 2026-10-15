"""Build neutral offline evidence packets for the fixed human-coding sample.

Raw observations and exact commit matches are evidence pointers, never inferred
repair labels. Existing reviewer annotations are not rendered or modified.
"""
import argparse
import csv
import hashlib
import html
import json
import re
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

from analyze import parse_time, write_csv
from collect_process import load_endpoint


ROOT = Path(__file__).resolve().parents[2]
SHA = re.compile(r"[0-9a-f]{40}")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def page(title, content):
    return (
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{html.escape(title)}</title>'
        '<style>body{max-width:1000px;margin:2em auto;padding:0 1em;'
        'font:16px/1.5 system-ui}pre{white-space:pre-wrap;overflow-wrap:anywhere;'
        'background:#f4f5f6;padding:1em}table{border-collapse:collapse;width:100%}'
        'td,th{padding:.4em;border:1px solid #ddd;text-align:left}'
        'article{border-top:1px solid #ddd;margin-top:1em}a{color:#164b92}'
        '.notice{padding:1em;background:#fff3d4}details{margin:.6em 0}</style>'
        f'<body><h1>{html.escape(title)}</h1>{content}</body></html>'
    )


def link(url, label):
    parsed = urlparse(url or "")
    escaped = html.escape(str(label))
    if parsed.scheme != "https" or parsed.hostname not in {"github.com", "api.github.com"} or parsed.username:
        return escaped
    return f'<a href="{html.escape(url, quote=True)}">{escaped}</a>'


def commit_events(timeline, cutoff):
    rows = []
    for event in timeline:
        sha = event.get("commit_id")
        if not sha:
            continue
        if not SHA.fullmatch(sha):
            raise ValueError("Unexpected commit identifier in pinned evidence")
        if parse_time(event["created_at"]) > cutoff:
            continue
        rows.append(dict(event=event["event"], event_id=event["id"],
                         created_at=event["created_at"], commit_sha=sha,
                         commit_api_url=event.get("commit_url"),
                         event_api_url=event.get("url")))
    return rows


def merge_index(details, cutoff):
    index = defaultdict(list)
    seen = set()
    for pr in details:
        number = pr["number"]
        if number in seen:
            raise ValueError("Duplicate PR identifier in merge-commit index")
        seen.add(number)
        if not pr.get("merged_at") or parse_time(pr["merged_at"]) > cutoff:
            continue
        sha = pr.get("merge_commit_sha")
        if sha:
            if not SHA.fullmatch(sha):
                raise ValueError("Unexpected merged PR commit identifier")
            index[sha].append(pr)
    return index


def evidence_notice(cutoff):
    return (
        '<p class="notice">Cached observations for independent human review. '
        'No assistant classifications or either reviewer\'s labels are included. '
        'A reference or exact PR merge-commit match is not proof of repair. '
        'A missing local commit is not evidence that no repair occurred.</p>'
        f'<p>Observation cutoff: {html.escape(cutoff)}. '
        'Bodies are retrieved text, not historical revisions; edited/deleted '
        'content may be incomplete. Opening an online link may show newer data.</p>'
    )


def render_case(record, comments, timeline, refs, commits, cutoff):
    number = record["number"]
    content = evidence_notice(cutoff)
    content += '<p><a href="../instructions.txt">Coding instructions</a> · '
    content += '<a href="../codebook.json">Codebook</a></p>'
    content += "<p>" + link(record["html_url"], f"Original issue #{number}") + "</p>"
    content += "<p>" + html.escape(
        f'Created: {record["created_at"]}; updated: {record["updated_at"]}; '
        f'observed state: {record["state"]}; reason: {record.get("state_reason")}'
    ) + "</p>"
    content += "<h2>Report body</h2><pre>" + html.escape(record.get("body") or "") + "</pre>"
    content += "<h2>Comments, in creation order</h2>"
    for comment in sorted(comments, key=lambda row: (row["created_at"], row["id"])):
        author = (comment.get("user") or {}).get("login", "unattributed")
        content += "<article><h3>" + link(comment["html_url"], author) + "</h3>"
        content += "<p>" + html.escape(
            f'Created: {comment["created_at"]}; updated: {comment["updated_at"]}'
        ) + "</p><pre>" + html.escape(comment.get("body") or "") + "</pre></article>"
    content += "<h2>All cached timeline observations</h2>"
    content += "<p>Events retain the cached API order. Use recorded timestamps "
    content += "where present; missing timestamps are not inferred.</p>"
    for event in timeline:
        actor = (event.get("actor") or {}).get("login", "unattributed")
        summary = f'{event.get("created_at", "timestamp unavailable")} · {event.get("event", "unknown")} · {actor}'
        content += "<details><summary>" + html.escape(summary) + "</summary><pre>"
        content += html.escape(json.dumps(event, indent=2, ensure_ascii=False)) + "</pre></details>"
    content += "<h2>Commit and PR evidence pointers</h2>"
    if not refs:
        content += "<p>No timestamped commit pointer occurs in this cached timeline.</p>"
    for ref in refs:
        sha = ref["commit_sha"]
        content += "<article><h3>" + link(ref["commit_api_url"], sha) + "</h3>"
        content += "<p>" + link(ref["event_api_url"], ref["event"])
        content += " · " + html.escape(ref["created_at"]) + "</p>"
        available = commits[sha]["local_object_available"]
        if available:
            content += f'<p><a href="../commits/{sha}.patch">Full local Git patch</a>; '
            content += html.escape(commits[sha]["subject"])
            content += f'; in release ancestry: {commits[sha]["in_release_ancestry"]}.</p>'
        else:
            content += "<p>Commit object unavailable in the pinned local Git repository.</p>"
        for pr in ref["matching_prs"]:
            content += "<p>Exact merged-commit SHA match: " + link(pr["html_url"], f'PR #{pr["number"]}: {pr["title"]}')
            content += f' · <a href="../prs/{pr["number"]}.json">Cached PR detail, reviews and timeline</a>.</p>'
        content += "</article>"
    return page(f'Issue #{number}: {record["title"]}', content)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "Paper/results/review-materials")
    args = parser.parse_args()
    out = args.output
    for name in ["issues", "commits", "prs"]:
        (out / name).mkdir(parents=True, exist_ok=True)
    sources = {}

    def remember(path):
        sources[str(path.relative_to(ROOT))] = digest(path)

    def endpoint(path):
        data = load_endpoint(path)
        coverage = path / "coverage.json"
        remember(coverage)
        for raw in json.loads(coverage.read_text())["pages"]:
            remember(path / raw["file"])
        return data

    sample_path = ROOT / "Paper/results/research-v0.29.5/issue_validation_sample.csv"
    remember(sample_path)
    with sample_path.open() as handle:
        sample = list(csv.DictReader(handle))
    numbers = [int(row["number"]) for row in sample]
    if len(set(numbers)) != len(numbers):
        raise ValueError("Duplicate sampled issue identifiers")
    issue_source = ROOT / "Paper/data/github-2026-10-13"
    remember(issue_source / "issues_and_prs.json")
    remember(issue_source / "manifest.json")
    issue_status = json.loads((issue_source / "manifest.json").read_text())
    if not issue_status["complete"]:
        raise ValueError("Issue snapshot pagination is incomplete")
    raw_records = []
    for raw_page in issue_status["pages"]:
        source = issue_source / raw_page["file"]
        if digest(source) != raw_page["sha256"]:
            raise ValueError("Issue snapshot page checksum mismatch")
        remember(source)
        raw_records.extend(json.loads(source.read_text()))
    consolidated = json.loads((issue_source / "issues_and_prs.json").read_text())
    if consolidated != raw_records:
        raise ValueError("Consolidated issue records differ from raw snapshot pages")
    records = {row["number"]: row for row in consolidated}
    cutoff_iso = issue_status["finished_at_utc"]
    cutoff = parse_time(cutoff_iso)
    evidence = ROOT / "Paper/data/issue-evidence-2026-10-13"
    remember(evidence / "manifest.json")
    if not json.loads((evidence / "manifest.json").read_text())["complete"]:
        raise ValueError("Issue evidence collection is incomplete")
    pr_source = ROOT / "Paper/data/pr-census-2026-10-13"
    details = []
    for pr in endpoint(pr_source / "pr_list"):
        detail = endpoint(pr_source / "pulls" / str(pr["number"]) / "detail")
        if detail["number"] != pr["number"]:
            raise ValueError("PR detail membership mismatch")
        details.append(detail)
    index = merge_index(details, cutoff)
    repo = ROOT / "Paper/data/headscale.git"
    release_manifest = ROOT / "Paper/results/v0.29.5/manifest.json"
    remember(release_manifest)
    release = json.loads(release_manifest.read_text())["anchors"][-1]["sha"]
    if not SHA.fullmatch(release):
        raise ValueError("Unexpected release commit identifier")
    commits = {}
    link_rows = []
    packets = []
    pr_contexts = {}
    for number in numbers:
        record = records[number]
        if "pull_request" in record:
            raise ValueError("Issue sample contains a PR")
        directory = evidence / "issues" / str(number)
        timeline = endpoint(directory / "timeline")
        comments = endpoint(directory / "comments")
        if any(parse_time(c["created_at"]) > cutoff or parse_time(c["updated_at"]) > cutoff for c in comments):
            raise ValueError("Post-cutoff comment content requires separate review materials")
        if any(e.get("created_at") and parse_time(e["created_at"]) > cutoff for e in timeline):
            raise ValueError("Post-cutoff timeline event requires separate review materials")
        refs = commit_events(timeline, cutoff)
        for ref in refs:
            sha = ref["commit_sha"]
            if sha not in commits:
                available = subprocess.run(
                    ["git", "--git-dir=" + str(repo), "cat-file", "-e", sha + "^{commit}"],
                    capture_output=True, check=False
                ).returncode == 0
                commit = dict(local_object_available=available, subject="", in_release_ancestry=None)
                if available:
                    commit["subject"] = subprocess.check_output(
                        ["git", "--git-dir=" + str(repo), "show", "-s", "--format=%s", sha], text=True
                    ).strip()
                    ancestry = subprocess.run(
                        ["git", "--git-dir=" + str(repo), "merge-base", "--is-ancestor", sha, release],
                        capture_output=True, check=False
                    ).returncode
                    if ancestry not in (0, 1):
                        raise ValueError("Cannot verify referenced commit's release ancestry")
                    commit["in_release_ancestry"] = ancestry == 0
                    patch = subprocess.check_output([
                        "git", "--git-dir=" + str(repo), "show", "--no-ext-diff",
                        "--no-textconv", "--format=fuller", "--stat", "--patch", sha
                    ])
                    (out / "commits" / (sha + ".patch")).write_bytes(patch)
                commits[sha] = commit
            ref["matching_prs"] = [dict(number=pr["number"], title=pr["title"], html_url=pr["html_url"],
                                       merged_at=pr["merged_at"]) for pr in index.get(sha, [])]
            link_rows.append(dict(issue_number=number, **{k:v for k,v in ref.items() if k != "matching_prs"},
                                  local_object_available=commits[sha]["local_object_available"],
                                  matching_pr_numbers=";".join(str(pr["number"]) for pr in ref["matching_prs"])))
            for pr in index.get(sha, []):
                if pr["number"] not in pr_contexts:
                    pr_directory = pr_source / "pulls" / str(pr["number"])
                    pr_contexts[pr["number"]] = dict(detail=pr, reviews=endpoint(pr_directory / "reviews"),
                        timeline=endpoint(pr_directory / "timeline"), observation_cutoff_utc=cutoff_iso,
                        warning="Raw later-retrieved PR context; check each timestamp and edit time. No repair classification inferred.")
                    (out / "prs" / f'{pr["number"]}.json').write_text(
                        json.dumps(pr_contexts[pr["number"]], indent=2, ensure_ascii=False) + "\n"
                    )
        packets.append(dict(number=number, issue=record, comments=comments, timeline=timeline, commit_pointers=refs))
        (out / "issues" / f"{number}.html").write_text(render_case(record, comments, timeline, refs, commits, cutoff_iso))
    (out / "packets.json").write_text(json.dumps(packets, indent=2, ensure_ascii=False) + "\n")
    (out / "commit_context.json").write_text(json.dumps(commits, indent=2, ensure_ascii=False) + "\n")
    write_csv(out / "commit_pr_links.csv", link_rows)
    for name, destination in [("instructions.txt", "instructions.txt"), ("../data/issue_coding_codebook.json", "codebook.json")]:
        source = ROOT / "Paper/validation" / name
        source = source.resolve()
        remember(source)
        shutil.copyfile(source, out / destination)
    for rater in [1, 2]:
        form = ROOT / "Paper/validation" / f"rater_{rater}.csv"
        with form.open() as handle:
            order = [int(row["number"]) for row in csv.DictReader(handle)]
        if len(order) != len(numbers) or set(order) != set(numbers):
            raise ValueError("Reviewer form membership differs from pinned sample")
        rows = "".join(f'<li><a href="issues/{n}.html">Issue #{n}: {html.escape(records[n]["title"])}</a></li>' for n in order)
        content = evidence_notice(cutoff_iso) + '<p><a href="instructions.txt">Instructions</a> · <a href="codebook.json">Codebook</a></p><ol>' + rows + "</ol>"
        (out / f"rater_{rater}.html").write_text(page(f"Reviewer {rater}: independently ordered evidence", content))
        sources[f"rater_{rater}_case_order"] = hashlib.sha256(json.dumps(order).encode()).hexdigest()
    summary = dict(cases=len(numbers), cutoff_utc=cutoff_iso, commit_pointer_events=len(link_rows),
                   cases_with_commit_pointers=len({r["issue_number"] for r in link_rows}),
                   distinct_commit_pointers=len(commits),
                   locally_available_commits=sum(c["local_object_available"] for c in commits.values()),
                   cases_with_exact_merge_commit_match=len({r["issue_number"] for r in link_rows if r["matching_pr_numbers"]}),
                   distinct_matched_prs=len(pr_contexts), cross_referenced_events=sum(
                       e.get("event") == "cross-referenced" for packet in packets for e in packet["timeline"]),
                   annotations_included=False, repair_labels_inferred=False,
                   interpretation="Evidence pointers for a stratified sample; no population prevalence or independently verified repair claimed")
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    remember(Path(__file__).resolve())
    outputs = {str(path.relative_to(out)): digest(path) for path in out.rglob("*") if path.is_file() and path.name != "manifest.json"}
    manifest = dict(summary=summary, source_sha256=sources, output_sha256=outputs,
                    pinned_release_commit=release,
                    limitations=["Exact commit matches do not prove that a PR repaired the sampled issue.",
                                 "Missing local commit objects can include fork-only or rewritten history.",
                                 "PR context is later-retrieved raw evidence, not historical state reconstruction.",
                                 "Reviewer labels and assistant case suggestions are excluded."])
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
