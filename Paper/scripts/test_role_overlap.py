import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from analyze import parse_time
from analyze_role_overlap import (
    CATEGORIES, aggregate, checked_endpoint, compare_membership,
    participation_category, role_observation,
)
from pr_history import reconstruct


class RoleOverlapTests(unittest.TestCase):
    def setUp(self):
        self.cutoff = parse_time("2026-04-10T00:00:00Z")
        self.head = "a" * 40
        self.detail = dict(
            number=10, html_url="https://github.com/example/project/pull/10",
            user=dict(login="Author", type="User"),
            merged_by=dict(login="Author", type="User"),
            created_at="2026-04-01T00:00:00Z",
            merged_at="2026-04-04T00:00:00Z",
            closed_at="2026-04-04T00:00:00Z", head=dict(sha=self.head),
        )
        self.timeline = [
            dict(id=1, event="closed", created_at="2026-04-02T00:00:00Z"),
            dict(id=2, event="reopened", created_at="2026-04-03T00:00:00Z"),
            dict(id=3, event="merged", created_at="2026-04-04T00:00:00Z"),
        ]

    def review(self, identifier, actor="Reviewer", submitted="2026-04-01T01:00:00Z",
               state="APPROVED", commit=None, kind="User"):
        return dict(id=identifier, user=dict(login=actor, type=kind) if actor is not None else None,
                    state=state, submitted_at=submitted,
                    commit_id=self.head if commit is None else commit)

    def observe(self, reviews, detail=None):
        detail = detail or self.detail
        return role_observation(detail, reviews, self.cutoff,
                                reconstruct(detail, self.timeline, self.cutoff))

    def test_categories_partition_account_roles_without_inventing_a_third_person(self):
        cases = [
            ("Author", "AUTHOR", {"Reviewer"}, CATEGORIES[0]),
            ("Author", "Author", set(), CATEGORIES[1]),
            ("Author", "Integrator", {"INTEGRATOR"}, CATEGORIES[2]),
            ("Author", "Integrator", {"Reviewer"}, CATEGORIES[3]),
            ("Author", "Integrator", set(), CATEGORIES[4]),
        ]
        for author, integrator, reviewers, expected in cases:
            self.assertEqual(participation_category(author, integrator, reviewers), expected)
        self.assertRaises(ValueError, participation_category, "author", "merger", {"AUTHOR"})
        row, _ = self.observe([self.review(1)])
        self.assertEqual(row["visible_union_account_count"], 2)
        self.assertEqual(row["has_review_by_account_other_than_author_and_integrator"], 1)
        self.assertEqual(row["has_at_least_three_visible_accounts"], 0)

    def test_reopening_gaps_and_post_merge_records_do_not_supply_review_or_head_matches(self):
        reviews = [
            self.review(1, commit="b" * 40),
            self.review(2, actor="ClosedGap", submitted="2026-04-02T12:00:00Z"),
            self.review(3, actor="AfterMerge", submitted="2026-04-05T00:00:00Z"),
            self.review(4, actor="AfterReopening", submitted="2026-04-03T12:00:00Z", state="COMMENTED"),
            self.review(5, actor="Author"),
            self.review(6, actor="robot[bot]", kind="Bot"),
            self.review(7, actor="Pending", submitted=None, state="PENDING"),
        ]
        row, audit = self.observe(reviews)
        self.assertEqual(row["attributed_reviewer_accounts"], "afterreopening;reviewer")
        self.assertEqual(row["collected_approved_records"], 1)
        self.assertEqual(row["collected_approved_matching_head_records"], 0)
        self.assertEqual(row["visible_union_account_count"], 3)
        reasons = {r["review_id"]: r["eligibility"] for r in audit}
        self.assertEqual(reasons[2], "during_closed_interval")
        self.assertEqual(reasons[3], "after_final_disposition")
        self.assertEqual(reasons[5], "author_self_review")
        self.assertEqual(reasons[6], "bot_actor")
        self.assertEqual(reasons[7], "pending_or_unsubmitted")

    def test_unknown_actors_remain_unassigned_and_are_time_bounded(self):
        reviews = [
            self.review(1, actor=None),
            self.review(2, actor=None, submitted="2026-04-02T12:00:00Z"),
            self.review(3, actor=None, submitted="2026-04-05T00:00:00Z"),
        ]
        row, audit = self.observe(reviews)
        self.assertEqual(row["raw_unattributed_records"], 3)
        self.assertEqual(row["temporally_eligible_unattributed_records"], 1)
        self.assertEqual(row["participation_category"], CATEGORIES[1])
        self.assertEqual(row["visible_union_account_count"], 1)
        self.assertEqual(row["has_collected_approved_record"], 0)
        self.assertEqual(audit[0]["eligibility"], "eligible_time_unattributed_actor")

    def test_collected_approval_is_a_record_diagnostic_not_a_latest_decision(self):
        reviews = [self.review(1),
                   self.review(2, submitted="2026-04-01T02:00:00Z", state="CHANGES_REQUESTED"),
                   self.review(3, actor="Dismissed", state="DISMISSED")]
        row, audit = self.observe(reviews)
        self.assertEqual(row["attributed_eligible_review_records"], 3)
        self.assertEqual(row["collected_approved_records"], 1)
        self.assertEqual(row["collected_approved_matching_head_records"], 1)
        self.assertEqual(row["collected_approved_account_count"], 1)
        self.assertEqual(len(audit), 3)
        missing = copy.deepcopy(self.detail)
        missing["head"] = {}
        row, _ = self.observe([self.review(4, commit="")], missing)
        self.assertEqual(row["has_collected_approved_record"], 1)
        self.assertEqual(row["has_collected_head_matching_approved_record"], 0)

    def test_membership_checks_reject_swapped_populations_and_role_assignments(self):
        row, _ = self.observe([self.review(1)])
        reference = dict(number="10", eventual_merge="1", author="author", merged_by="author",
                         merge_quarter="2026-Q2", human_reviewers="reviewer",
                         submitted_review_records="1", unknown_actor_records="0")
        metadata = dict(number="10", final_observed_outcome="merge", author="author",
                        merged_by="author", self_merged="1")
        compare_membership([row], [reference], [metadata])
        changed = dict(reference, number="11")
        self.assertRaises(ValueError, compare_membership, [row], [changed], [metadata])
        changed = dict(reference, human_reviewers="other")
        self.assertRaises(ValueError, compare_membership, [row], [changed], [metadata])
        self.assertRaises(ValueError, compare_membership, [row, row], [reference], [metadata])
        summary = aggregate([row])
        self.assertEqual(sum(summary[c] for c in CATEGORIES), 1)
        self.assertEqual(summary["self_merges_with_collected_head_matching_approved_record"], 1)

    def test_raw_endpoint_hashes_cover_actual_bytes_and_reject_tampering(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            endpoint = root / "reviews"
            endpoint.mkdir()
            raw = b'[{"id": 1}]'
            (endpoint / "page_0001.json").write_bytes(raw)
            status = dict(complete=True, next_url=None,
                          pages=[dict(file="page_0001.json", sha256=hashlib.sha256(raw).hexdigest())])
            (endpoint / "coverage.json").write_text(json.dumps(status))
            sources = {}
            data, _ = checked_endpoint(endpoint, sources, root)
            self.assertEqual(data, [dict(id=1)])
            self.assertEqual(len(sources), 2)
            (endpoint / "page_0001.json").write_bytes(b'[{"id": 2}]')
            self.assertRaises(ValueError, checked_endpoint, endpoint, {}, root)
            status["complete"] = False
            (endpoint / "coverage.json").write_text(json.dumps(status))
            self.assertRaises(ValueError, checked_endpoint, endpoint, {}, root)


if __name__ == "__main__":
    unittest.main()
