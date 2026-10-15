"""Execute bounded before/after component oracles without human-label imputation."""
import argparse
import csv
import hashlib
import io
import json
import os
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PAPER = ROOT / "Paper"
REPO = PAPER / "data/headscale.git"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*arguments):
    return subprocess.check_output(["git", "--git-dir=" + str(REPO), *arguments])


def validate_selection(plan):
    with (PAPER / "results/issue-validation/closure_mechanisms.csv").open() as handle:
        closing = {int(r["number"]): r["last_closure_commit"] for r in csv.DictReader(handle)
                   if r["last_closure_commit"]}
    packets = {p["number"]: p for p in json.loads(
        (PAPER / "results/review-materials/packets.json").read_text())}
    selected = {}
    for number, commit in closing.items():
        refs = [r for r in packets[number]["commit_pointers"] if r["commit_sha"] == commit]
        matched = sorted({p["number"] for r in refs for p in r["matching_prs"]})
        available = subprocess.run(
            ["git", "--git-dir=" + str(REPO), "cat-file", "-e", commit + "^{commit}"],
            capture_output=True).returncode == 0
        if matched and available:
            if len(matched) != 1:
                raise ValueError("Ambiguous exact closing-commit PR match")
            selected[number] = (commit, matched[0])
    specified = {c["issue"]: (c["fixed_commit"], c["pr"]) for c in plan["cases"]}
    if selected != specified:
        raise ValueError("The complete availability-based case selection changed")
    for case in plan["cases"]:
        actual_parent = git("rev-parse", case["fixed_commit"] + "^").decode().strip()
        if actual_parent != case["parent_commit"]:
            raise ValueError("Specified comparison is not the official first parent")
        for filename in ("go.mod", "go.sum"):
            if git("show", case["fixed_commit"] + ":" + filename) != git(
                    "show", case["parent_commit"] + ":" + filename):
                raise ValueError("Dependency manifest differs within a repair pair")


def prepare_source(case, variant, fixture, sources):
    commit = case["parent_commit"] if variant == "parent" else case["fixed_commit"]
    archive = git("archive", "--format=tar", commit)
    directory = sources / str(case["issue"]) / variant
    expected = {}
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        for member in source.getmembers():
            if member.isfile():
                expected[member.name] = hashlib.sha256(source.extractfile(member).read()).hexdigest()
        if not directory.exists():
            directory.mkdir(parents=True)
            source.extractall(directory, filter="data")
    target = directory / case["fixture_destination"]
    fixture_hash = hashlib.sha256(fixture).hexdigest()
    if target.exists() and digest(target) not in {
            expected.get(case["fixture_destination"]), fixture_hash}:
        raise ValueError("Existing source fixture has unrecorded edits")
    target.write_bytes(fixture)
    expected[case["fixture_destination"]] = fixture_hash
    for name, value in expected.items():
        if digest(directory / name) != value:
            raise ValueError("Isolated source differs from official commit/oracle: " + name)
    return directory, expected, dict(commit=commit,
                                    source_archive_sha256=hashlib.sha256(archive).hexdigest(),
                                    fixture_sha256=fixture_hash,
                                    dependency_sha256={name: expected[name] for name in ("go.mod", "go.sum")})


def run_variant(case, variant, source, expected, provenance, output, environment):
    events_path = output / (variant + ".jsonl")
    stderr_path = output / (variant + ".stderr.txt")
    command = ["go", "test", "-mod=readonly", "-p=2", "-count=1", "-timeout=120s",
               "-json", "-run", case["run_pattern"], case["package"]]
    print(f"Running issue {case['issue']} {variant}: {' '.join(command)}", flush=True)
    with events_path.open("w") as events, stderr_path.open("w") as errors:
        result = subprocess.run(command, cwd=source, env=environment,
                                stdout=events, stderr=errors, timeout=1800)
    parsed = []
    for line in events_path.read_text().splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            parsed.append(item)
    terminal = {e["Test"]: e["Action"] for e in parsed
                if e.get("Test") and e.get("Action") in ("pass", "fail", "skip")}
    for name, value in expected.items():
        if digest(source / name) != value:
            raise ValueError("Go execution changed a tracked source: " + name)
    needed = case["regression_tests"] + case["control_tests"]
    executions = {name: terminal.get(name, "not_executed") for name in needed}
    wanted = ("fail" if variant == "parent" else "pass")
    demonstrated = (all(executions[t] == wanted for t in case["regression_tests"]) and
                    all(executions[t] == "pass" for t in case["control_tests"]) and
                    result.returncode == (1 if variant == "parent" else 0))
    record = dict(variant=variant, command=command, exit_code=result.returncode,
                  test_outcomes=executions, expected_behavior_demonstrated=demonstrated,
                  source_provenance=provenance,
                  stdout_sha256=digest(events_path), stderr_sha256=digest(stderr_path))
    (output / (variant + ".json")).write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(dict(issue=case["issue"], **record), indent=2), flush=True)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-downloads", action="store_true",
                        help="Allow official Go toolchain/module downloads; otherwise require cached dependencies")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    plan_path = PAPER / "repair_validation_plan.json"
    plan = json.loads(plan_path.read_text())
    validate_selection(plan)
    sources = PAPER / "reproductions/repair-cases"
    prepared = []
    for case in plan["cases"]:
        fixture = (PAPER / case["fixture"]).read_bytes() if case.get("fixture") else git(
            "show", case["fixed_commit"] + ":" + case["fixture_git_path"])
        variants = {variant: prepare_source(case, variant, fixture, sources)
                    for variant in ("parent", "fixed")}
        prepared.append((case, variants))
    if args.prepare_only:
        print(json.dumps(dict(status="Prepared; no component test has been executed",
                              cases=[c["issue"] for c, _ in prepared],
                              plan_sha256=digest(plan_path)), indent=2))
        return
    environment = os.environ.copy()
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        environment.pop(name, None)
    for key, folder in {"GOCACHE": "go-build", "GOMODCACHE": "go-modules",
                        "GOTMPDIR": "go-tmp", "TMPDIR": "go-tmp"}.items():
        directory = ROOT / ".cache" / folder
        directory.mkdir(parents=True, exist_ok=True)
        environment[key] = str(directory)
    environment.update(GOTOOLCHAIN=plan["go_toolchain"], GOENV="off", GOFLAGS="",
                       GOWORK="off", GOMAXPROCS="2", CGO_ENABLED="0", TZ="UTC",
                       GOPROXY="https://proxy.golang.org", GOSUMDB="sum.golang.org")
    if not args.allow_downloads:
        environment["GOPROXY"] = "off"
    now = datetime.now(timezone.utc)
    run = PAPER / "results/repair-validation" / now.strftime("%Y%m%dT%H%M%S%fZ")
    run.mkdir(parents=True)
    (run / "plan.json").write_bytes(plan_path.read_bytes())
    outcomes = []
    for case, variants in prepared:
        folder = run / str(case["issue"])
        folder.mkdir()
        records = {variant: run_variant(case, variant, *variants[variant],
                                       folder, environment)
                   for variant in ("parent", "fixed")}
        outcomes.append(dict(issue=case["issue"], pr=case["pr"],
                             demonstrated=all(r["expected_behavior_demonstrated"] for r in records.values()),
                             claim_boundary=case["claim_boundary"], variants=records))
    report = dict(complete=all(c["demonstrated"] for c in outcomes),
                  started_at_utc=now.isoformat(), finished_at_utc=datetime.now(timezone.utc).isoformat(),
                  go_toolchain=plan["go_toolchain"], downloads_allowed=args.allow_downloads,
                  cases=outcomes, source_available_selection=True,
                  human_labels_generated=False, population_repair_prevalence_estimated=False,
                  original_deployment_replayed=False,
                  provenance=dict(script_sha256=digest(Path(__file__)),
                                  plan_sha256=digest(plan_path),
                                  researcher_fixture_sha256=digest(PAPER / "repair_fixtures/research_2121_test.go"),
                                  closure_inputs_sha256=digest(PAPER / "results/issue-validation/closure_mechanisms.csv"),
                                  packets_sha256=digest(PAPER / "results/review-materials/packets.json")))
    (run / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    (PAPER / "results/repair-validation/latest.json").write_text(json.dumps(
        dict(run_directory=str(run.relative_to(ROOT)), summary_sha256=digest(run / "summary.json"),
             complete=report["complete"]), indent=2) + "\n")
    print(json.dumps(dict(complete=report["complete"], run_directory=str(run.relative_to(ROOT)),
                         demonstrated_cases=[c["issue"] for c in outcomes if c["demonstrated"]]), indent=2))
    if not report["complete"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
