"""Prepare blank decision-chain forms and neutral references to existing packets."""
import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STAGES = ("evidence", "decision", "treatment", "outcome")
CATEGORIES = ("affected_component", "decision_kind", "treatment_kind", "supported_outcome",
              "chain_assessment", "contrary_search", "historical_basis", "temporal_order")
HUMAN_FIELDS = (*CATEGORIES, *(stage + "_witnesses" for stage in STAGES),
                "contrary_witnesses", "rationale", "absence_notes", "contrary_search_notes", "reviewed_at_utc")
IDENTITY_FIELDS = ("number", "url", "title", "stratum", "inclusion_probability",
                   "packet_file", "source_catalog_file")
TIME_FIELDS = ("created_at", "updated_at", "submitted_at", "merged_at", "closed_at")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def json_text(value):
    return json.dumps(value, indent=2, ensure_ascii=False)


def read_csv(path):
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError("Missing or duplicate worksheet columns")
        rows = list(reader)
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError("Malformed worksheet row")
    if len({row["number"] for row in rows}) != len(rows):
        raise ValueError("Duplicate issue in worksheet")
    return rows


def json_pointer(value, pointer):
    for part in pointer.split("/")[1:]:
        part = part.replace("~1", "/").replace("~0", "~")
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def resolve_text(root, source, cache=None):
    path = root / source["source_file"]
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Out-of-root packet source")
    # Callers may supply a cache for one validation only. A subsequent invocation
    # starts empty, so edits between validations cannot reuse stale source text.
    if cache is None:
        cache = {}
    if path not in cache:
        content = path.read_bytes()
        cache[path] = dict(sha256=hashlib.sha256(content).hexdigest(), text=content.decode(), parsed=None)
    cached = cache[path]
    if cached["sha256"] != source["source_sha256"]:
        raise ValueError("Changed or out-of-root packet source")
    if source["representation"] == "patch":
        return cached["text"]
    if cached["parsed"] is None:
        cached["parsed"] = json.loads(cached["text"])
    item = json_pointer(cached["parsed"], source["json_pointer"])
    return json_text(item) if source["representation"] == "json" else (item or "")


def parse_time(value):
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Timestamp requires a timezone")
    return stamp.astimezone(timezone.utc)


def source_record(root, source_id, source_url, path, pointer, item, fetched, cutoff, representation="json", source_sha256=None):
    times = {name: item[name] for name in TIME_FIELDS if item.get(name)} if isinstance(item, dict) else {}
    flags = []
    if not times:
        flags.append("recorded_event_time_unavailable")
    if times.get("created_at") and parse_time(times["created_at"]) > parse_time(cutoff):
        flags.append("created_after_cutoff")
    if times.get("submitted_at") and parse_time(times["submitted_at"]) > parse_time(cutoff):
        flags.append("submitted_after_cutoff")
    if times.get("updated_at") and parse_time(times["updated_at"]) > parse_time(cutoff):
        flags.append("edited_after_cutoff")
    if times.get("updated_at") and times.get("created_at") and parse_time(times["updated_at"]) > parse_time(times["created_at"]):
        flags.append("edited_text_not_historical_revision")
    if fetched and parse_time(fetched) > parse_time(cutoff):
        flags.append("retrieved_after_cutoff")
    if not fetched:
        flags.append("retrieval_time_unavailable")
    record = dict(source_id=source_id, source_url=source_url,
                  source_file=str(path.relative_to(root)), source_sha256=source_sha256 or digest(path),
                  json_pointer=pointer, representation=representation, recorded_times=times,
                  retrieved_at_utc=fetched, ambiguity_flags=flags)
    text = path.read_text() if representation == "patch" else (json_text(item) if representation == "json" else (item.get("body") or ""))
    record["text_sha256"] = hashlib.sha256(text.encode()).hexdigest()
    return record


def build_catalogs(root):
    """Resolve only neutral, already packet-included sources, never annotations."""
    base = root / "Paper/results/review-materials"
    manifest_path = base / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    source_hashes = manifest["source_sha256"]
    output_hashes = manifest["output_sha256"]
    inputs = {str(manifest_path.relative_to(root)): digest(manifest_path)}

    def pinned(path, output=False):
        relative = str(path.relative_to(root))
        hashes = output_hashes if output else source_hashes
        # The packet manifest records output names relative to its own directory.
        key = str(path.relative_to(base)) if output and relative not in hashes else relative
        if key not in hashes or digest(path) != hashes[key]:
            raise ValueError("Packet source is missing or differs from its manifest: " + relative)
        inputs[relative] = digest(path)

    packets_path = base / "packets.json"
    pinned(packets_path, output=True)
    packets = json.loads(packets_path.read_text())
    cutoff = manifest["summary"]["cutoff_utc"]
    retrieval = {}

    def map_pages(directory, coverage="coverage.json"):
        path = directory / coverage
        pinned(path)
        status = json.loads(path.read_text())
        if status.get("complete") is not True:
            raise ValueError("Incomplete source pagination")
        for page in status["pages"]:
            raw = directory / page["file"]
            pinned(raw)
            if digest(raw) != page["sha256"]:
                raise ValueError("Source-page checksum mismatch")
            data = json.loads(raw.read_text())
            for row in data if isinstance(data, list) else [data]:
                if row.get("id") is not None:
                    retrieval[(str(directory.relative_to(root)), str(row["id"]))] = page.get("fetched_at_utc", "")

    issue_dir = root / "Paper/data/github-2026-10-13"
    map_pages(issue_dir, "manifest.json")
    catalogs = {}
    for position, packet in enumerate(packets):
        number = packet["number"]
        if number in catalogs:
            raise ValueError("Duplicate packet issue")
        issue = packet["issue"]
        fallback = issue["html_url"]
        fetched = retrieval.get((str(issue_dir.relative_to(root)), str(issue["id"])), "")
        sources = [source_record(root, "issue:" + str(issue["id"]), fallback, packets_path,
                                 f"/{position}/issue/body", issue, fetched, cutoff, "text", inputs[str(packets_path.relative_to(root))])]
        for channel in ("comments", "timeline"):
            directory = root / "Paper/data/issue-evidence-2026-10-13/issues" / str(number) / channel
            map_pages(directory)
            for index, item in enumerate(packet[channel]):
                identifier = str(item.get("id") or ("sha256-" + hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()))
                url = item.get("html_url") or item.get("url") or fallback
                plain = channel == "comments" and bool(item.get("body"))
                pointer = f"/{position}/{channel}/{index}" + ("/body" if plain else "")
                fetched = retrieval.get((str(directory.relative_to(root)), str(item.get("id"))), "")
                sources.append(source_record(root, f"issue:{number}:{channel}:{identifier}", url,
                                             packets_path, pointer, item, fetched, cutoff, "text" if plain else "json", inputs[str(packets_path.relative_to(root))]))
        seen_commits, seen_prs = set(), set()
        for ref in packet["commit_pointers"]:
            sha = ref["commit_sha"]
            patch = base / "commits" / (sha + ".patch")
            if sha not in seen_commits and patch.exists():
                pinned(patch, output=True)
                source = source_record(root, "commit:" + sha, ref["commit_api_url"], patch, "", {}, "", cutoff, "patch", inputs[str(patch.relative_to(root))])
                for line in patch.read_text().splitlines():
                    if line.startswith("CommitDate:"):
                        stamp = parsedate_to_datetime(line.split(":", 1)[1].strip()).isoformat()
                        source["recorded_times"] = {"committed_at": stamp}
                        source["ambiguity_flags"].remove("recorded_event_time_unavailable")
                        if parse_time(stamp) > parse_time(cutoff):
                            source["ambiguity_flags"].append("committed_after_cutoff")
                        break
                sources.append(source)
                seen_commits.add(sha)
            for pr in ref["matching_prs"]:
                pr_number = pr["number"]
                if pr_number in seen_prs:
                    continue
                seen_prs.add(pr_number)
                path = base / "prs" / f"{pr_number}.json"
                pinned(path, output=True)
                context = json.loads(path.read_text())
                detail_dir = root / "Paper/data/pr-census-2026-10-13/pulls" / str(pr_number)
                for key, channel in (("detail", "detail"), ("reviews", "reviews"), ("timeline", "timeline")):
                    directory = detail_dir / channel
                    map_pages(directory)
                    items = [context[key]] if key == "detail" else context[key]
                    for index, item in enumerate(items):
                        identifier = str(item.get("id") or ("sha256-" + hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()))
                        plain = bool(item.get("body"))
                        pointer = "/" + key + ("" if key == "detail" else "/" + str(index)) + ("/body" if plain else "")
                        fetched = retrieval.get((str(directory.relative_to(root)), str(item.get("id"))), "")
                        sources.append(source_record(root, f"pr:{pr_number}:{key}:{identifier}",
                            item.get("html_url") or item.get("url") or pr["html_url"], path, pointer,
                            item, fetched, cutoff, "text" if plain else "json", inputs[str(path.relative_to(root))]))
        if len({s["source_id"] for s in sources}) != len(sources):
            raise ValueError("Duplicate source ID inside issue packet")
        catalogs[number] = dict(number=number, observation_cutoff_utc=cutoff,
                                sources=sources, contains_annotations=False,
                                warning="Sources and timestamps are observations, not responsibility decisions or repair labels")
    return catalogs, inputs


def prepare(root=ROOT):
    plan_path = root / "Paper/mechanism_validation_plan.json"
    plan = json.loads(plan_path.read_text())
    folder = root / "Paper/validation"
    destination = root / "Paper/results/mechanism-validation"
    catalogs, inputs = build_catalogs(root)
    existing_paths = [folder / name for name in ("rater_1.csv", "rater_2.csv", "rater_1_metadata.json", "rater_2_metadata.json", "manifest.json")]
    original = {str(path.relative_to(root)): digest(path) for path in existing_paths}
    prepared, forms = {}, {}
    for rater in (1, 2):
        source = read_csv(folder / f"rater_{rater}.csv")
        if {int(row["number"]) for row in source} != set(catalogs):
            raise ValueError("Rater membership differs from fixed packet sample")
        rows = []
        for old in source:
            n = int(old["number"])
            row = {key: old[key] for key in ("number", "url", "title", "stratum", "inclusion_probability")}
            row.update(packet_file=f"Paper/results/review-materials/issues/{n}.html",
                       source_catalog_file=f"Paper/results/mechanism-validation/sources/{n}.json")
            row.update(dict.fromkeys(HUMAN_FIELDS, ""))
            rows.append(row)
        path = folder / f"mechanism_rater_{rater}.csv"
        if path.exists():
            previous = read_csv(path)
            if len(previous) != len(rows) or any(any(a.get(k) != b[k] for k in IDENTITY_FIELDS) for a, b in zip(previous, rows)):
                raise ValueError("Existing mechanism form identity/order changed; refusing to overwrite")
            if set(previous[0]) != set(rows[0]):
                raise ValueError("Existing mechanism worksheet columns changed")
            for row, prior in zip(rows, previous):
                row.update({key: prior[key] for key in HUMAN_FIELDS})
        forms[rater] = rows
        prepared[f"rater_{rater}_annotated_cases"] = sum(any(row[key].strip() for key in HUMAN_FIELDS) for row in rows)
    relative_sources = {f"Paper/results/mechanism-validation/sources/{n}.json": hashlib.sha256((json_text(c) + "\n").encode()).hexdigest() for n, c in catalogs.items()}
    identity = {str(r): [{k: row[k] for k in IDENTITY_FIELDS} for row in rows] for r, rows in forms.items()}
    scripts = {name: digest(root / name) for name in (
        "Paper/scripts/prepare_mechanism_review.py", "Paper/scripts/validate_mechanism_review.py",
        "Paper/validation/mechanism_instructions.txt")}
    pinned = dict(plan_sha256=digest(plan_path), preparation_code_sha256=scripts, source_inputs_sha256=inputs,
                  source_catalog_sha256=relative_sources,
                  identity_order_sha256=hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest())
    manifest_path = folder / "mechanism_preparation_manifest.json"
    declaration_fields = ("reviewer_id", "human_review_performed", "human_review_completed",
        "independent_human_review_completed", "other_rater_codes_consulted",
        "assistant_suggestions_consulted", "started_at_utc", "completed_at_utc")
    pristine_review = not any(prepared.values())
    for rater in (1, 2):
        metadata_path = folder / f"mechanism_rater_{rater}_metadata.json"
        if metadata_path.exists():
            declaration = json.loads(metadata_path.read_text())
            pristine_review &= all(declaration.get(key) in (None, "") for key in declaration_fields)
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text())
        if any(previous.get(k) != value for k, value in pinned.items() if k not in ("plan_sha256", "preparation_code_sha256", "source_catalog_sha256")):
            raise ValueError("Pinned mechanism plan, packet sources or worksheet identity changed")
        revised = any(previous.get(key) != pinned[key] for key in ("plan_sha256", "preparation_code_sha256", "source_catalog_sha256"))
        if revised and not pristine_review:
            raise ValueError("Preparation projection changed after review began; preserve its previous version and provenance")
        first_hashes = previous["existing_human_forms_sha256_at_first_preparation"]
    else:
        first_hashes = original
    (destination / "sources").mkdir(parents=True, exist_ok=True)
    for number, catalog in catalogs.items():
        (destination / "sources" / f"{number}.json").write_text(json_text(catalog) + "\n")
    for rater, rows in forms.items():
        path = folder / f"mechanism_rater_{rater}.csv"
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=[*IDENTITY_FIELDS, *HUMAN_FIELDS])
            writer.writeheader()
            writer.writerows(rows)
        path = folder / f"mechanism_rater_{rater}_metadata.json"
        if not path.exists():
            path.write_text(json_text(dict(reviewer_id="", human_review_performed=None,
                human_review_completed=None, independent_human_review_completed=None, other_rater_codes_consulted=None,
                assistant_suggestions_consulted=None, started_at_utc="", completed_at_utc="",
                plan_version=plan["version"], plan_sha256=digest(plan_path))) + "\n")
        else:
            metadata = json.loads(path.read_text())
            if metadata.get("plan_sha256") != digest(plan_path) or metadata.get("plan_version") != plan["version"]:
                if not pristine_review:
                    raise ValueError("Cannot silently rebind an existing human declaration to a different plan")
                metadata.update(plan_version=plan["version"], plan_sha256=digest(plan_path))
                path.write_text(json_text(metadata) + "\n")
    after = {str(path.relative_to(root)): digest(path) for path in existing_paths}
    if original != after:
        raise ValueError("Existing human forms changed during preparation")
    result = dict(status="prepared_awaiting_actual_human_review", cases=len(catalogs), **prepared,
                  **pinned, existing_human_forms_sha256_at_first_preparation=first_hashes,
                  existing_human_forms_unchanged_this_preparation=True,
                  annotations_generated=False, completion_declarations_generated=False,
                  semantic_truth_verified=False, population_or_causal_estimates_generated=False)
    manifest_path.write_text(json_text(result) + "\n")
    return result


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    result = prepare()
    print(json.dumps({k: result[k] for k in ("status", "cases", "rater_1_annotated_cases", "rater_2_annotated_cases", "existing_human_forms_unchanged_this_preparation")}, indent=2))
