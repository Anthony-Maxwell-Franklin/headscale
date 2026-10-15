"""Check native test execution and semantic failures, not just a success flag."""
import hashlib
import json
from pathlib import Path

from validate_repair_cases import git, validate_selection


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_test_events(events, case, variant, exit_code):
    """Reject build failures, missing/skipped tests and unrelated failure causes."""
    wanted = "fail" if variant == "parent" else "pass"
    required = {name: wanted for name in case["regression_tests"]}
    required.update({name: "pass" for name in case["control_tests"]})
    for name, action in required.items():
        runs = [e for e in events if e.get("Test") == name and e.get("Action") == "run"]
        ends = [e for e in events if e.get("Test") == name and e.get("Action") in ("pass", "fail", "skip")]
        if len(runs) != 1 or len(ends) != 1 or ends[0]["Action"] != action:
            raise ValueError("Required native test was absent, repeated, skipped or had the wrong outcome: " + name)
        if variant == "parent" and name in case["regression_tests"]:
            output = "".join(e.get("Output", "") for e in events if e.get("Test") == name)
            diagnostics = {
                2121: ["duplicate-name rename was accepted",
                       "duplicate given name persisted: laptop",
                       'node 2 name = "laptop", want "server"'],
                2181: ["FilterNodesByACL() unexpected result (-want +got)"],
            }[case["issue"]]
            if not all(message in output for message in diagnostics):
                raise ValueError("Parent failure is not the specified semantic regression: " + name)
    if exit_code != (1 if variant == "parent" else 0):
        raise ValueError("Native test command has an unexpected exit code")
    return {name: action for name, action in required.items()}


def verify_repair_validation(root):
    paper = root / "Paper"
    latest_path = paper / "results/repair-validation/latest.json"
    latest = json.loads(latest_path.read_text())
    directory = root / latest["run_directory"]
    if not directory.resolve().is_relative_to((paper / "results/repair-validation").resolve()):
        raise ValueError("Repair run is outside the declared result directory")
    summary_path = directory / "summary.json"
    if digest(summary_path) != latest["summary_sha256"]:
        raise ValueError("Repair run summary changed")
    report = json.loads(summary_path.read_text())
    if (not latest["complete"] or not report["complete"] or report["human_labels_generated"] or
            report["population_repair_prevalence_estimated"] or report["original_deployment_replayed"]):
        raise ValueError("Repair run has incomplete or overstated scope")
    plan_path = paper / "repair_validation_plan.json"
    plan = json.loads(plan_path.read_text())
    if report["go_toolchain"] != plan["go_toolchain"]:
        raise ValueError("Reported Go toolchain differs from the execution plan")
    if (directory / "plan.json").read_bytes() != plan_path.read_bytes():
        raise ValueError("Repair execution plan differs from the recorded plan")
    validate_selection(plan)
    expected_provenance = dict(
        script_sha256=digest(paper / "scripts/validate_repair_cases.py"),
        plan_sha256=digest(plan_path),
        researcher_fixture_sha256=digest(paper / "repair_fixtures/research_2121_test.go"),
        closure_inputs_sha256=digest(paper / "results/issue-validation/closure_mechanisms.csv"),
        packets_sha256=digest(paper / "results/review-materials/packets.json"))
    if report["provenance"] != expected_provenance:
        raise ValueError("Repair run source or selection evidence changed")
    cases = {case["issue"]: case for case in report["cases"]}
    if len(cases) != len(report["cases"]) or set(cases) != {c["issue"] for c in plan["cases"]}:
        raise ValueError("Repair run omitted or duplicated a selected case")
    for case in plan["cases"]:
        recorded = cases[case["issue"]]
        if not recorded["demonstrated"] or recorded["pr"] != case["pr"]:
            raise ValueError("Selected repair case is not demonstrated")
        fixture = (paper / case["fixture"]).read_bytes() if case.get("fixture") else git(
            "show", case["fixed_commit"] + ":" + case["fixture_git_path"])
        for variant in ("parent", "fixed"):
            result = recorded["variants"][variant]
            path = directory / str(case["issue"]) / (variant + ".json")
            if json.loads(path.read_text()) != result:
                raise ValueError("Native variant result differs from run summary")
            stdout = directory / str(case["issue"]) / (variant + ".jsonl")
            stderr = directory / str(case["issue"]) / (variant + ".stderr.txt")
            if digest(stdout) != result["stdout_sha256"] or digest(stderr) != result["stderr_sha256"]:
                raise ValueError("Native execution logs changed")
            events = [json.loads(line) for line in stdout.read_text().splitlines() if line.strip()]
            outcomes = check_test_events(events, case, variant, result["exit_code"])
            if outcomes != result["test_outcomes"] or not result["expected_behavior_demonstrated"]:
                raise ValueError("Native execution evidence contradicts the result")
            commit = case["parent_commit"] if variant == "parent" else case["fixed_commit"]
            expected = dict(
                commit=commit,
                source_archive_sha256=hashlib.sha256(git("archive", "--format=tar", commit)).hexdigest(),
                fixture_sha256=hashlib.sha256(fixture).hexdigest(),
                dependency_sha256={name: hashlib.sha256(git("show", commit + ":" + name)).hexdigest()
                                   for name in ("go.mod", "go.sum")})
            if result["source_provenance"] != expected:
                raise ValueError("Official source, dependency or held-constant oracle differs")
            command = ["go", "test", "-mod=readonly", "-p=2", "-count=1", "-timeout=120s",
                       "-json", "-run", case["run_pattern"], case["package"]]
            if result["command"] != command:
                raise ValueError("Native command differs from the selected test specification")
    return dict(cases=len(cases),
                regression_checks=sum(len(c["regression_tests"]) for c in plan["cases"]),
                control_checks=sum(len(c["control_tests"]) for c in plan["cases"]),
                go_toolchain=report["go_toolchain"], run_directory=latest["run_directory"],
                summary_sha256=digest(summary_path), semantic_failures_verified=True,
                original_deployment_replayed=False, human_labels_generated=False)


if __name__ == "__main__":
    print(json.dumps(verify_repair_validation(Path(__file__).resolve().parents[2]), indent=2))
