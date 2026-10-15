"""Synthetic decision chains only: never annotate any actual study issue."""
import copy
import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prepare_mechanism_review import (HUMAN_FIELDS, IDENTITY_FIELDS, ROOT, digest,
    json_text, prepare, read_csv, source_record)
from validate_mechanism_review import (validate, validate_case, validate_rater,
    validate_witness, verify_preparation)


class MechanismReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plan = json.loads((ROOT / "Paper/mechanism_validation_plan.json").read_text())
        self.plan_hash = "synthetic-plan-hash"
        self.cutoff = "2026-01-10T00:00:00Z"
        items = [dict(id=i + 1, body=body, created_at=f"2026-01-0{i+1}T00:00:00Z",
                      updated_at=f"2026-01-0{i+1}T00:00:00Z")
                 for i, body in enumerate(("Synthetic symptom", "Synthetic explicit decision",
                                           "Synthetic treatment", "Synthetic outcome"))]
        self.path = self.root / "synthetic-packets.json"
        self.path.write_text(json_text(items))
        self.sources = []
        for i, item in enumerate(items):
            source = source_record(self.root, "synthetic:" + str(i),
                f"https://example.test/synthetic/{i}", self.path, f"/{i}/body", item,
                "2026-01-11T00:00:00Z", self.cutoff, "text")
            self.sources.append(source)
        self.catalog = dict(number=999, observation_cutoff_utc=self.cutoff,
                            sources=self.sources, contains_annotations=False)
        self.row = dict.fromkeys((*IDENTITY_FIELDS, *HUMAN_FIELDS), "")
        self.row.update(number="999", url="https://example.test/synthetic/issue",
            title="Synthetic issue; not a real case", stratum="synthetic", inclusion_probability="1.0",
            affected_component="server", decision_kind="server_retained", treatment_kind="server_change",
            supported_outcome="server_repair_supported", chain_assessment="supported_in_case",
            contrary_search="none_found_in_packet", historical_basis="within_cutoff_evidence",
            temporal_order="strictly_ordered", contrary_witnesses="[]",
            rationale="Synthetic evidence chain for structural tests only",
            contrary_search_notes="Synthetic packet inspected; no contrary evidence in this fixture",
            reviewed_at_utc="2026-01-12T00:00:00Z")
        for stage, source in zip(("evidence", "decision", "treatment", "outcome"), self.sources):
            self.row[stage + "_witnesses"] = json.dumps([self.witness(source)])
        self.meta = dict(reviewer_id="synthetic_human_declaration", human_review_performed=True,
            human_review_completed=True, independent_human_review_completed=False,
            other_rater_codes_consulted=False, assistant_suggestions_consulted=True,
            started_at_utc="2026-01-11T00:00:00Z", completed_at_utc="2026-01-13T00:00:00Z",
            plan_version=self.plan["version"], plan_sha256=self.plan_hash)

    def witness(self, source):
        i = self.sources.index(source) if source in getattr(self, "sources", []) else int(source["source_id"].split(":")[-1])
        return dict(source_id=source["source_id"], source_url=source["source_url"],
                    excerpt=json.loads(self.path.read_text())[i]["body"], time_field="created_at",
                    recorded_at_utc=source["recorded_times"]["created_at"])

    def case(self, row=None, catalog=None):
        return validate_case(row or self.row, catalog or self.catalog, self.root, self.plan)

    def rater(self, row=None, meta=None):
        return validate_rater([row or self.row], meta or self.meta, {999: self.catalog},
                              self.root, self.plan, self.plan_hash)

    def test_exact_witness_bindings_reject_fabricated_source_url_quote_or_time(self):
        sources = {s["source_id"]: dict(s, observation_cutoff_utc=self.cutoff) for s in self.sources}
        original = self.witness(self.sources[0])
        for key, value in (("source_id", "outside-packet"), ("source_url", "https://example.test/invented"),
                           ("excerpt", "Words never in the source"), ("recorded_at_utc", "2026-01-09T00:00:00Z")):
            with self.subTest(key=key):
                witness = dict(original, **{key: value})
                self.assertRaises(ValueError, validate_witness, witness, sources, self.root)
        self.assertFalse(self.case()["errors"])

    def test_source_edits_between_validation_calls_cannot_use_a_stale_cache(self):
        self.assertFalse(self.case()["errors"])
        self.path.write_text(self.path.read_text().replace("Synthetic symptom", "Changed synthetic symptom"))
        self.assertTrue(any("Changed or out-of-root" in error for error in self.case()["errors"]))

    def test_reversed_chronology_is_rejected_without_sorting_witnesses(self):
        row = dict(self.row, evidence_witnesses=self.row["outcome_witnesses"])
        self.assertTrue(any("Reversed" in e for e in self.case(row)["errors"]))

    def test_disclosed_reversed_sequence_is_retained_as_negative_evidence(self):
        row = dict(self.row, evidence_witnesses=self.row["outcome_witnesses"],
            temporal_order="observed_order_inconsistent", chain_assessment="contradicted_in_case",
            contrary_search="counterevidence_recorded", contrary_witnesses=self.row["outcome_witnesses"],
            rationale="Synthetic treatment/decision timing contradicts the proposed order")
        result = self.case(row)
        self.assertTrue(result["complete"])
        self.assertFalse(result["errors"])
        self.assertTrue(any("observed_reversed_stage_anchors" in a.get("flags", []) for a in result["ambiguities"]))
        for assessment in ("incomplete_chain", "unclear"):
            with self.subTest(assessment=assessment):
                self.assertFalse(self.case(dict(row, chain_assessment=assessment))["errors"])
        for changes in (dict(chain_assessment="supported_in_case"), dict(temporal_order="strictly_ordered"),
                        dict(temporal_order="coincident_or_ambiguous")):
            with self.subTest(changes=changes):
                self.assertTrue(any("Reversed" in e for e in self.case(dict(row, **changes))["errors"]))

    def test_same_second_is_ambiguity_and_never_strict_precedence(self):
        row = dict(self.row, decision_witnesses=self.row["evidence_witnesses"],
                   temporal_order="coincident_or_ambiguous")
        result = self.case(row)
        self.assertFalse(result["errors"])
        self.assertTrue(any("equal_timestamps_do_not_establish_strict_precedence" in a.get("flags", []) for a in result["ambiguities"]))
        row["temporal_order"] = "strictly_ordered"
        self.assertTrue(any("Strict chronology" in e for e in self.case(row)["errors"]))

    def test_post_cutoff_edits_are_not_within_cutoff_content(self):
        catalog = copy.deepcopy(self.catalog)
        catalog["sources"][0]["recorded_times"]["updated_at"] = "2026-01-11T00:00:00Z"
        catalog["sources"][0]["ambiguity_flags"].extend(["edited_after_cutoff", "edited_text_not_historical_revision"])
        row = dict(self.row, temporal_order="coincident_or_ambiguous")
        self.assertTrue(any("within-cutoff" in e for e in self.case(row, catalog)["errors"]))
        row["historical_basis"] = "post_cutoff_or_edited_context"
        self.assertFalse(self.case(row, catalog)["errors"])

    def test_missing_decision_is_retained_but_not_automatic_support(self):
        row = dict(self.row, decision_kind="no_explicit_decision", decision_witnesses="[]",
                   temporal_order="incomplete_or_unavailable", absence_notes="No explicit decision in synthetic packet")
        self.assertTrue(any("explicit decision" in e for e in self.case(row)["errors"]))
        row.update(chain_assessment="no_explicit_decision", contrary_search="unclear", contrary_search_notes="")
        self.assertTrue(self.case(row)["complete"])
        self.assertFalse(self.case(row)["errors"])

    def test_contradiction_and_interpreted_chains_require_real_search_evidence(self):
        row = dict(self.row, contrary_search_notes="")
        self.assertTrue(any("contrary-evidence search" in e for e in self.case(row)["errors"]))
        row = dict(self.row, chain_assessment="contradicted_in_case")
        self.assertTrue(any("contrary witness" in e for e in self.case(row)["errors"]))
        row["contrary_witnesses"] = self.row["outcome_witnesses"]
        self.assertFalse(self.case(row)["errors"])

    def test_filled_fields_from_undeclared_review_are_rejected(self):
        meta = dict(self.meta, human_review_performed=None, human_review_completed=None)
        self.assertTrue(any("declared actual human" in e for e in self.rater(meta=meta)["errors"]))
        meta = dict(self.meta, human_review_completed="true")
        self.assertTrue(any("boolean or null" in e for e in self.rater(meta=meta)["errors"]))

    def test_truthful_exposed_human_completion_is_structurally_valid_not_independent(self):
        result = self.rater()
        self.assertFalse(result["errors"])
        self.assertTrue(result["human_completion_declared"])
        self.assertFalse(result["independent_completion_declared"])
        meta = dict(self.meta, independent_human_review_completed=True)
        self.assertTrue(any("Independent review cannot" in e for e in self.rater(meta=meta)["errors"]))

    def test_empty_and_partial_forms_cannot_support_completion_declarations(self):
        empty = {**self.row, **dict.fromkeys(HUMAN_FIELDS, "")}
        result = self.rater(row=empty)
        self.assertEqual(result["structurally_complete_cases"], 0)
        self.assertTrue(any("Declared completion" in e for e in result["errors"]))
        meta = dict(self.meta, human_review_completed=None, completed_at_utc="")
        partial = dict(empty, rationale="Synthetic partial human pilot")
        result = self.rater(row=partial, meta=meta)
        self.assertFalse(result["errors"])
        self.assertEqual(result["annotated_cases"], 1)
        self.assertEqual(result["structurally_complete_cases"], 0)

    def test_completion_cannot_predate_review(self):
        meta = dict(self.meta, completed_at_utc="2026-01-11T00:00:00Z")
        self.assertTrue(any("Completion precedes" in e for e in self.rater(meta=meta)["errors"]))

    def synthetic_workspace(self):
        folder = self.root / "Paper/validation"
        folder.mkdir(parents=True)
        (self.root / "Paper/scripts").mkdir()
        (self.root / "Paper/mechanism_validation_plan.json").write_text(json_text(self.plan))
        for name in ("Paper/scripts/prepare_mechanism_review.py", "Paper/scripts/validate_mechanism_review.py", "Paper/validation/mechanism_instructions.txt"):
            (self.root / name).write_bytes((ROOT / name).read_bytes())
        for rater in (1, 2):
            with (folder / f"rater_{rater}.csv").open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=("number", "url", "title", "stratum", "inclusion_probability"))
                writer.writeheader()
                writer.writerow({key: self.row[key] for key in writer.fieldnames})
            (folder / f"rater_{rater}_metadata.json").write_text('{"synthetic_existing_declaration":"unchanged"}')
        (folder / "manifest.json").write_text('{"synthetic_existing_manifest":"unchanged"}')
        return folder

    def test_preparation_preserves_prior_forms_annotations_and_declarations(self):
        folder = self.synthetic_workspace()
        original = {p.name: p.read_bytes() for p in folder.glob("*")}
        catalogs = {999: self.catalog}
        with patch("prepare_mechanism_review.build_catalogs", return_value=(catalogs, {})):
            first = prepare(self.root)
            self.assertEqual(first["rater_1_annotated_cases"], 0)
            form = folder / "mechanism_rater_1.csv"
            rows = read_csv(form)
            rows[0]["rationale"] = "Synthetic preserved partial annotation"
            with form.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=rows[0]);writer.writeheader();writer.writerows(rows)
            metadata = folder / "mechanism_rater_1_metadata.json"
            saved_metadata = metadata.read_bytes()
            saved_form = form.read_bytes()
            second = prepare(self.root)
        self.assertEqual(second["rater_1_annotated_cases"], 1)
        self.assertEqual(saved_form, form.read_bytes())
        self.assertEqual(saved_metadata, metadata.read_bytes())
        self.assertTrue(all((folder / name).read_bytes() == value for name, value in original.items()))

    def test_single_rater_exposed_completion_and_status_hash_binding(self):
        folder = self.synthetic_workspace()
        catalogs = {999: self.catalog}
        with patch("prepare_mechanism_review.build_catalogs", return_value=(catalogs, {})):
            prepare(self.root)
        form = folder / "mechanism_rater_1.csv"
        rows = read_csv(form)
        rows[0].update({key: self.row[key] for key in HUMAN_FIELDS})
        with form.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=rows[0]);writer.writeheader();writer.writerows(rows)
        meta = dict(self.meta, plan_sha256=digest(self.root / "Paper/mechanism_validation_plan.json"))
        (folder / "mechanism_rater_1_metadata.json").write_text(json_text(meta))
        with patch("validate_mechanism_review.build_catalogs", return_value=(catalogs, {})):
            evidence = verify_preparation(self.root)
            report, code = validate(self.root, mode="single-rater")
        self.assertEqual(evidence["cases"], 1)
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "declared_reviews_structurally_complete")
        self.assertFalse(report["semantic_truth_verified"])
        self.assertFalse(report["agreement_estimated"])
        self.assertFalse(report["raters"]["1"]["independent_completion_declared"])
        self.assertEqual(report["source_sha256"]["Paper/validation/mechanism_rater_1.csv"], digest(form))
        self.assertNotIn("2", report["raters"])

    def test_plan_rebinding_is_allowed_only_before_any_actual_review(self):
        folder = self.synthetic_workspace()
        plan_path = self.root / "Paper/mechanism_validation_plan.json"
        with patch("prepare_mechanism_review.build_catalogs", return_value=({999: self.catalog}, {})):
            prepare(self.root)
            changed = dict(self.plan, question="Synthetic pre-review clarification")
            plan_path.write_text(json_text(changed))
            prepare(self.root)
            metadata = json.loads((folder / "mechanism_rater_1_metadata.json").read_text())
            self.assertEqual(metadata["plan_sha256"], digest(plan_path))
            self.assertIsNone(metadata["human_review_completed"])
            form = folder / "mechanism_rater_1.csv"
            rows = read_csv(form)
            rows[0]["rationale"] = "Synthetic human annotation must not be rebound silently"
            with form.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=rows[0]);writer.writeheader();writer.writerows(rows)
            saved = form.read_bytes()
            changed["question"] = "Another synthetic plan change after annotation"
            plan_path.write_text(json_text(changed))
            self.assertRaises(ValueError, prepare, self.root)
            self.assertEqual(saved, form.read_bytes())


if __name__ == "__main__":
    unittest.main()
