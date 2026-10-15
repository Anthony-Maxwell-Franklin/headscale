"""Independent assignment and optimisation checks for the identity relaxation."""
import itertools
import math
import random
import unittest

import numpy as np
from scipy.optimize import minimize

from analyze import parse_time
from analyze_review_census import review_observation
from analyze_review_identity_bounds import reviewer_hhi_outer_bounds, unknown_review_observations
from pr_history import reconstruct


class ReviewIdentityBoundTests(unittest.TestCase):
    def test_all_small_feasible_assignments_are_contained(self):
        rows = [
            dict(known_reviewers=["a", "b"], eligible_missing_records=2),
            dict(known_reviewers=["b"], eligible_missing_records=1),
        ]
        bounds = reviewer_hhi_outer_bounds(rows)
        # Missing records may be ineligible, repeat an existing identity, or
        # introduce an identity shared across PRs. No independence is assumed.
        choices = (None, "a", "b", "new1", "new2", "new3")
        realized = []
        for assignment in itertools.product(choices, repeat=3):
            sets = [
                {"a", "b"} | {a for a in assignment[:2] if a is not None},
                {"b"} | {a for a in assignment[2:] if a is not None},
            ]
            credit = {}
            for reviewers in sets:
                for reviewer in reviewers:
                    credit[reviewer] = credit.get(reviewer, 0.0) + 1 / len(reviewers)
            self.assertAlmostEqual(sum(credit.values()), 2.0)
            hhi = sum((weight / 2) ** 2 for weight in credit.values())
            realized.append(hhi)
            self.assertGreaterEqual(hhi + 1e-12, bounds["lower_hhi"])
            self.assertLessEqual(hhi - 1e-12, bounds["upper_hhi"])
        self.assertLessEqual(bounds["lower_hhi"], min(realized))
        self.assertGreaterEqual(bounds["upper_hhi"], max(realized))
        self.assertTrue(any(abs(hhi - bounds["all_missing_distinct_human_scenario_hhi"]) < 1e-12
                            for hhi in realized))
        # This example explicitly demonstrates that the relaxed extrema need
        # not be feasible assignments; they must not be described as sharp.
        self.assertLess(bounds["lower_hhi"], min(realized) - 1e-6)
        self.assertGreater(bounds["upper_hhi"], max(realized) + 1e-6)

    def test_lower_bound_matches_independent_constrained_optimization(self):
        rng = random.Random(20261014)
        for _ in range(8):
            rows = [dict(known_reviewers=rng.sample(["a", "b", "c"], rng.randint(1, 3)),
                         eligible_missing_records=rng.randint(0, 3))
                    for _ in range(rng.randint(2, 7))]
            bounds = reviewer_hhi_outer_bounds(rows)
            floors = {}
            for row in rows:
                for reviewer in row["known_reviewers"]:
                    floors[reviewer] = floors.get(reviewer, 0.0) + (
                        1 / (len(row["known_reviewers"]) + row["eligible_missing_records"]))
            lower = np.array(list(floors.values()) +
                             [0.0] * sum(r["eligible_missing_records"] for r in rows))
            n = len(rows)
            initial = lower + (n - sum(lower)) / len(lower)
            fit = minimize(lambda x: np.dot(x, x) / n ** 2, initial,
                           jac=lambda x: 2 * x / n ** 2, method="SLSQP",
                           bounds=[(float(value), None) for value in lower],
                           constraints={"type": "eq", "fun": lambda x: sum(x) - n,
                                        "jac": lambda x: np.ones_like(x)},
                           options={"ftol": 1e-12, "maxiter": 1000})
            self.assertTrue(fit.success, fit.message)
            self.assertAlmostEqual(bounds["lower_hhi"], fit.fun, places=8)

    def test_no_missing_identities_collapse_and_capacity_expands_bounds(self):
        base = [dict(known_reviewers=["a", "b"], eligible_missing_records=0),
                dict(known_reviewers=["b"], eligible_missing_records=0)]
        known = reviewer_hhi_outer_bounds(base)
        self.assertAlmostEqual(known["lower_hhi"], 0.625)
        self.assertEqual(known["lower_hhi"], known["upper_hhi"])
        previous = known
        for capacity in range(1, 8):
            rows = [dict(known_reviewers=["a", "b"], eligible_missing_records=capacity),
                    dict(known_reviewers=["b"], eligible_missing_records=0)]
            current = reviewer_hhi_outer_bounds(rows)
            self.assertLessEqual(current["lower_hhi"], previous["lower_hhi"] + 1e-12)
            self.assertGreaterEqual(current["upper_hhi"], previous["upper_hhi"] - 1e-12)
            self.assertLessEqual(current["lower_hhi"], known["observed_hhi"])
            self.assertGreaterEqual(current["upper_hhi"], known["observed_hhi"])
            previous = current
        for invalid in (-1, 0.5, True):
            self.assertRaises(ValueError, reviewer_hhi_outer_bounds,
                              [dict(known_reviewers=["a"], eligible_missing_records=invalid)])
        self.assertRaises(ValueError, reviewer_hhi_outer_bounds,
                          [dict(known_reviewers=["A", "a"], eligible_missing_records=0)])

    def test_unknown_identity_does_not_bypass_temporal_or_closed_boundaries(self):
        detail = dict(user=dict(login="author", type="User"),
                      created_at="2026-01-01T00:00:00Z",
                      merged_at="2026-01-04T00:00:00Z",
                      closed_at="2026-01-04T00:00:00Z")
        timeline = [dict(id=1, event="closed", created_at="2026-01-02T00:00:00Z"),
                    dict(id=2, event="reopened", created_at="2026-01-03T00:00:00Z"),
                    dict(id=3, event="merged", created_at="2026-01-04T00:00:00Z")]
        cutoff = parse_time("2026-01-05T00:00:00Z")
        history = reconstruct(detail, timeline, cutoff)
        times = ["2026-01-01T00:01:00Z", "2026-01-02T12:00:00Z",
                 "2026-01-03T00:00:00Z", "2026-01-04T00:00:00Z",
                 "2026-01-04T12:00:00Z", "2026-01-06T00:00:00Z"]
        reviews = [dict(id=10 + i, user=None, state="COMMENTED", submitted_at=t)
                   for i, t in enumerate(times)]
        reviews.extend([
            dict(id=20, user=None, state="PENDING", submitted_at=times[0]),
            dict(id=21, user=dict(login="author"), state="COMMENTED", submitted_at=times[0]),
            dict(id=22, user=dict(login="bot", type="Bot"), state="COMMENTED", submitted_at=times[0]),
        ])
        result = review_observation(detail, reviews, cutoff, history)
        audit = unknown_review_observations(detail, reviews, cutoff, history,
                                            result["final_terminal"])
        self.assertEqual([r["eligibility"] for r in audit],
                         ["retained", "during_closed_interval", "retained", "retained",
                          "after_final_disposition", "after_cutoff"])
        self.assertEqual(len(audit), 6)
        self.assertEqual(sum(r["eligibility"] == "retained" for r in audit), 3)
        before = dict(id=30, user=None, state="COMMENTED", submitted_at="2025-12-31T23:59:59Z")
        self.assertRaises(ValueError, unknown_review_observations, detail, [before],
                          cutoff, history, result["final_terminal"])


if __name__ == "__main__":
    unittest.main()
