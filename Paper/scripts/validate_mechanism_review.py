"""Check declared mechanism-review structure, never semantic truth or causality."""
import argparse
import hashlib
import json
from pathlib import Path

from prepare_mechanism_review import (ROOT, STAGES, CATEGORIES, HUMAN_FIELDS, IDENTITY_FIELDS,
    build_catalogs, digest, json_text, parse_time, read_csv, resolve_text)


def validate_witness(witness, sources, root, text_cache=None):
    """Bind a quote to a source already included in this issue's pinned packet."""
    required = {"source_id", "source_url", "excerpt", "time_field", "recorded_at_utc"}
    if not isinstance(witness, dict) or set(witness) != required or not all(isinstance(x, str) for x in witness.values()):
        raise ValueError("Witness must contain exactly the five string-valued source/quote/time fields")
    if witness["source_id"] not in sources:
        raise ValueError("Witness source is not included in this issue packet")
    source = sources[witness["source_id"]]
    if witness["source_url"] != source["source_url"]:
        raise ValueError("Witness URL differs from the pinned source")
    text = resolve_text(root, source, text_cache)
    if hashlib.sha256(text.encode()).hexdigest() != source["text_sha256"]:
        raise ValueError("Resolved source text differs from its pinned hash")
    if not witness["excerpt"].strip() or witness["excerpt"] not in text:
        raise ValueError("Witness excerpt is absent from the pinned source text")
    field, stamp = witness["time_field"], witness["recorded_at_utc"]
    if source["recorded_times"]:
        if not field or source["recorded_times"].get(field) != stamp:
            raise ValueError("Witness time does not match its source's recorded timestamp")
    elif field or stamp:
        raise ValueError("Untimed source cannot be assigned an invented event timestamp")
    time = parse_time(stamp) if stamp else None
    flags = list(source["ambiguity_flags"])
    updated = source["recorded_times"].get("updated_at")
    if updated and time and parse_time(updated) > time:
        flags.append("quoted_text_may_postdate_anchor")
    if time and time > parse_time(source["observation_cutoff_utc"]):
        flags.append("anchor_after_cutoff")
    return time, flags


def validate_case(row, catalog, root, plan, text_cache=None):
    errors, ambiguities, anchors, witnesses = [], [], {}, {}
    annotated = any(row.get(key, "").strip() for key in HUMAN_FIELDS)
    if not annotated:
        return dict(annotated=False, complete=False, errors=[], ambiguities=[])
    if text_cache is None:
        text_cache = {}
    for field in CATEGORIES:
        if row.get(field, "") and row[field] not in plan["categories"][field]:
            errors.append("Invalid category: " + field)
    sources = {s["source_id"]: dict(s, observation_cutoff_utc=catalog["observation_cutoff_utc"]) for s in catalog["sources"]}
    temporal_uncertainty = False
    historical_conflict = False
    for stage in (*STAGES, "contrary"):
        field = stage + "_witnesses"
        raw = row.get(field, "").strip()
        witnesses[stage] = []
        if not raw:
            continue
        try:
            items = json.loads(raw)
            if not isinstance(items, list):
                raise ValueError("Witness field must be a JSON array")
            for index, item in enumerate(items):
                time, flags = validate_witness(item, sources, root, text_cache)
                witnesses[stage].append(item)
                if index == 0 and stage in STAGES:
                    anchors[stage] = time
                    temporal_uncertainty |= time is None or "quoted_text_may_postdate_anchor" in flags
                if flags:
                    ambiguities.append(dict(stage=stage, source_id=item["source_id"], flags=flags))
                historical_conflict |= any(f in flags for f in ("created_after_cutoff", "submitted_after_cutoff", "edited_after_cutoff", "committed_after_cutoff", "anchor_after_cutoff"))
        except (ValueError, TypeError, KeyError) as exc:
            errors.append(field + ": " + str(exc))
    selected = [(stage, anchors[stage]) for stage in STAGES if stage in anchors and anchors[stage] is not None]
    reversed_order = False
    for (left, earlier), (right, later) in zip(selected, selected[1:]):
        if earlier > later:
            reversed_order = True
            ambiguities.append(dict(stages=[left, right],
                flags=["observed_reversed_stage_anchors"],
                recorded_times=[earlier.isoformat(), later.isoformat()]))
        elif earlier == later:
            temporal_uncertainty = True
            ambiguities.append(dict(stages=[left, right], flags=["equal_timestamps_do_not_establish_strict_precedence"]))
    if len(anchors) < len(STAGES) or any(value is None for value in anchors.values()):
        temporal_uncertainty = True
        ambiguities.append(dict(flags=["incomplete_or_untimed_chain"]))
    if row.get("temporal_order") == "strictly_ordered" and temporal_uncertainty:
        errors.append("Strict chronology is unsupported by coincident, edited, missing or untimed anchors")
    if reversed_order and (row.get("temporal_order") != "observed_order_inconsistent" or
            row.get("chain_assessment") not in ("contradicted_in_case", "incomplete_chain", "unclear")):
        errors.append("Reversed chronological anchors must be disclosed as observed_order_inconsistent negative/unclear evidence, never a supported chain")
    if not reversed_order and row.get("temporal_order") == "observed_order_inconsistent":
        errors.append("Observed-order-inconsistent category needs an actual reversed stage-anchor sequence")
    if row.get("historical_basis") == "within_cutoff_evidence" and historical_conflict:
        errors.append("Post-cutoff or post-cutoff-edited evidence cannot establish within-cutoff content")
    interpreted = row.get("chain_assessment") in ("supported_in_case", "contradicted_in_case")
    if interpreted and (not row.get("contrary_search_notes", "").strip() or row.get("contrary_search") not in ("counterevidence_recorded", "none_found_in_packet")):
        errors.append("Interpreted chain needs an actual documented contrary-evidence search")
    if row.get("chain_assessment") == "supported_in_case":
        if row.get("decision_kind") in ("no_explicit_decision", "unclear", "") or any(not witnesses[stage] for stage in STAGES):
            errors.append("Supported chain needs an explicit decision and all four witness stages")
    if row.get("chain_assessment") == "no_explicit_decision" and row.get("decision_kind") != "no_explicit_decision":
        errors.append("No-decision assessment requires the no-explicit-decision category")
    if row.get("chain_assessment") == "contradicted_in_case" or row.get("contrary_search") == "counterevidence_recorded":
        if not witnesses["contrary"]:
            errors.append("Contradiction/counterevidence needs an actual contrary witness")
    if row.get("reviewed_at_utc"):
        try:
            parse_time(row["reviewed_at_utc"])
        except (ValueError, TypeError):
            errors.append("Invalid case review timestamp")
    required = (*CATEGORIES, *(s + "_witnesses" for s in (*STAGES, "contrary")), "rationale", "reviewed_at_utc")
    complete = all(row.get(key, "").strip() for key in required)
    if complete and any(not witnesses[stage] for stage in STAGES) and not row.get("absence_notes", "").strip():
        errors.append("Missing chain stages need an explicit absence/uncertainty note")
    return dict(annotated=True, complete=complete and not errors, errors=errors, ambiguities=ambiguities)


def validate_rater(rows, metadata, catalogs, root, plan, plan_hash):
    errors, details = [], []
    text_cache = {}
    for field in ("human_review_performed", "human_review_completed", "independent_human_review_completed", "other_rater_codes_consulted", "assistant_suggestions_consulted"):
        if metadata.get(field) is not None and type(metadata[field]) is not bool:
            errors.append("Reviewer declaration must be boolean or null: " + field)
    for row in rows:
        detail = validate_case(row, catalogs[int(row["number"])], root, plan, text_cache)
        details.append(dict(number=int(row["number"]), **detail))
        errors.extend(f"Issue {row['number']}: {error}" for error in detail["errors"])
    annotated = sum(item["annotated"] for item in details)
    completed = sum(item["complete"] for item in details)
    if metadata.get("plan_version") != plan["version"] or metadata.get("plan_sha256") != plan_hash:
        errors.append("Reviewer declaration has a different plan")
    if annotated or metadata.get("human_review_completed") is True or metadata.get("independent_human_review_completed") is True:
        if metadata.get("human_review_performed") is not True or not str(metadata.get("reviewer_id") or "").strip():
            errors.append("Filled fields require a declared actual human review and reviewer identifier")
        if not isinstance(metadata.get("other_rater_codes_consulted"), bool) or not isinstance(metadata.get("assistant_suggestions_consulted"), bool):
            errors.append("Reviewer must declare consultation of other codes and assistant suggestions")
        try:
            started = parse_time(metadata.get("started_at_utc") or "")
            for row in rows:
                if row.get("reviewed_at_utc") and parse_time(row["reviewed_at_utc"]) < started:
                    errors.append("Case review predates the declared review start")
        except (ValueError, TypeError):
            errors.append("Missing or invalid human-review start time")
    declared = metadata.get("human_review_completed") is True
    independent = metadata.get("independent_human_review_completed") is True
    if independent and not declared:
        errors.append("Independent completion requires an actual human-completion declaration")
    if independent and (metadata.get("other_rater_codes_consulted") is not False or metadata.get("assistant_suggestions_consulted") is not False):
        errors.append("Independent review cannot be declared after consulting other codes or assistant suggestions")
    if declared:
        if type(metadata.get("independent_human_review_completed")) is not bool:
            errors.append("Completed human review must explicitly declare its independent/non-independent status")
        if completed != len(rows):
            errors.append("Declared completion is unsupported by complete case worksheets")
        try:
            completion = parse_time(metadata.get("completed_at_utc") or "")
            if completion < parse_time(metadata.get("started_at_utc") or "") or any(row.get("reviewed_at_utc") and parse_time(row["reviewed_at_utc"]) > completion for row in rows):
                errors.append("Completion precedes a declared review timestamp")
        except (ValueError, TypeError):
            errors.append("Missing or invalid completion timestamp")
    elif metadata.get("completed_at_utc"):
        errors.append("Completion timestamp exists without a completion declaration")
    return dict(annotated_cases=annotated, structurally_complete_cases=completed,
                human_completion_declared=declared, independent_completion_declared=independent,
                assistant_suggestions_consulted=metadata.get("assistant_suggestions_consulted"),
                other_rater_codes_consulted=metadata.get("other_rater_codes_consulted"), errors=errors, cases=details)


def preparation_inputs(root):
    """Read-only preparation checks; human annotation content may legitimately evolve."""
    folder = root / "Paper/validation"
    plan_path = root / "Paper/mechanism_validation_plan.json"
    plan = json.loads(plan_path.read_text())
    manifest = json.loads((folder / "mechanism_preparation_manifest.json").read_text())
    catalogs, source_inputs = build_catalogs(root)
    if manifest["plan_sha256"] != digest(plan_path) or manifest["source_inputs_sha256"] != source_inputs:
        raise ValueError("Pinned plan or packet inputs changed")
    for name, value in manifest["preparation_code_sha256"].items():
        if digest(root / name) != value:
            raise ValueError("Mechanism preparation code/instructions changed")
    identity, worksheets = {}, {}
    for number, catalog in catalogs.items():
        path = root / f"Paper/results/mechanism-validation/sources/{number}.json"
        if manifest["source_catalog_sha256"].get(str(path.relative_to(root))) != digest(path) or json.loads(path.read_text()) != catalog:
            raise ValueError("Source index differs from the existing pinned packet")
    for rater in (1, 2):
        path = folder / f"mechanism_rater_{rater}.csv"
        rows = read_csv(path)
        if not rows or set(rows[0]) != {*IDENTITY_FIELDS, *HUMAN_FIELDS} or {int(r["number"]) for r in rows} != set(catalogs):
            raise ValueError("Worksheet columns/membership changed")
        reference = read_csv(folder / f"rater_{rater}.csv")
        if [r["number"] for r in rows] != [r["number"] for r in reference]:
            raise ValueError("Worksheet no longer follows its existing independent rater order")
        if any(any(row[key] != old[key] for key in ("number", "url", "title", "stratum", "inclusion_probability")) for row, old in zip(rows, reference)):
            raise ValueError("Worksheet differs from existing case identity/sampling information")
        identity[str(rater)] = [{key: row[key] for key in IDENTITY_FIELDS} for row in rows]
        worksheets[str(rater)] = rows
    if hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest() != manifest["identity_order_sha256"]:
        raise ValueError("Pinned worksheet identity/sampling information changed")
    return plan, manifest, catalogs, worksheets


def verify_preparation(root=ROOT):
    plan, manifest, catalogs, worksheets = preparation_inputs(root)
    return dict(cases=len(catalogs), source_catalogs_verified=len(catalogs),
                source_records=sum(len(c["sources"]) for c in catalogs.values()),
                source_inputs_verified=True, existing_rater_orders_preserved=True,
                human_annotation_fields_checked_for_truth=False, semantic_truth_verified=False,
                plan_version=plan["version"], plan_sha256=manifest["plan_sha256"],
                preparation_manifest_sha256=digest(root / "Paper/validation/mechanism_preparation_manifest.json"))


def validate(root=ROOT, mode="two-rater"):
    if mode not in ("two-rater", "single-rater"):
        raise ValueError("Unknown review mode")
    plan, manifest, catalogs, worksheets = preparation_inputs(root)
    folder = root / "Paper/validation"
    raters, metadatas, input_hashes = {}, [], {}
    for rater in (1, 2):
        for name in (f"mechanism_rater_{rater}.csv", f"mechanism_rater_{rater}_metadata.json"):
            path = folder / name
            input_hashes[str(path.relative_to(root))] = digest(path)
        if rater == 2 and mode == "single-rater":
            continue
        metadata = json.loads((folder / f"mechanism_rater_{rater}_metadata.json").read_text())
        metadatas.append(metadata)
        raters[str(rater)] = validate_rater(worksheets[str(rater)], metadata, catalogs, root, plan, manifest["plan_sha256"])
    errors = [f"Rater {rater}: {error}" for rater, result in raters.items() for error in result["errors"]]
    ids = [str(meta.get("reviewer_id") or "").strip().casefold() for meta in metadatas]
    if len(ids) == 2 and all(ids) and ids[0] == ids[1]:
        errors.append("The two independently ordered worksheets need distinct declared reviewer IDs")
    complete = not errors and all(result["human_completion_declared"] for result in raters.values())
    status = ("invalid_mechanism_review" if errors else "declared_reviews_structurally_complete" if complete else
              "declared_human_review_in_progress" if any(result["annotated_cases"] for result in raters.values()) else
              "awaiting_actual_human_mechanism_review")
    result = dict(status=status, mode=mode, cases=len(catalogs), raters=raters, errors=errors,
        declaration_scope="Reviewer identity, actual review and independence are self-reported, not authenticated",
        validation_scope="Source membership, quote matching and recorded chronology only; no semantic adjudication",
        semantic_truth_verified=False, mechanism_validated=False, agreement_estimated=False,
        population_repair_prevalence_estimated=False, causal_effect_estimated=False,
        source_sha256=input_hashes, validator_script_sha256=digest(Path(__file__)),
        plan_sha256=manifest["plan_sha256"], preparation_manifest_sha256=digest(folder / "mechanism_preparation_manifest.json"))
    output = root / "Paper/results/mechanism-validation/validation_status.json"
    output.write_text(json_text(result) + "\n")
    return result, 0 if complete else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("two-rater", "single-rater"), default="two-rater")
    args = parser.parse_args()
    report, code = validate(mode=args.mode)
    print(json.dumps(dict(status=report["status"], cases=report["cases"],
        annotated_cases={r: value["annotated_cases"] for r, value in report["raters"].items()},
        errors=report["errors"], semantic_truth_verified=False, mechanism_validated=False), indent=2))
    raise SystemExit(code)
