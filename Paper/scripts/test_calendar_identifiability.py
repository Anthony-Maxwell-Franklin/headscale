"""Focused algebraic, risk-set and model-identification guards."""
import unittest

import numpy as np
import pandas as pd

from audit_calendar_identifiability import (calendar_basis, fit_diagnostic,
                                             informative_rows, variance_decomposition)


class CalendarIdentifiabilityTests(unittest.TestCase):
    def test_variance_partition_distinguishes_between_and_within_cohort_variation(self):
        frame = pd.DataFrame(dict(creation_quarter=["a", "a", "b", "b"],
                                  lag90_merger_hhi=[.2, .4, .6, .8]))
        result = variance_decomposition(frame)
        self.assertAlmostEqual(result["total_sum_squares"], .2)
        self.assertAlmostEqual(result["within_quarter_sum_squares"], .04)
        self.assertAlmostEqual(result["between_quarter_fraction"], .8)
        constant = frame.assign(lag90_merger_hhi=.5)
        self.assertIsNone(variance_decomposition(constant)["within_quarter_fraction"])

    def test_calendar_basis_ignores_outcomes_and_is_four_centered_columns(self):
        frame = pd.DataFrame(dict(calendar_years=np.linspace(0, 5, 101), merge_event=np.arange(101) % 2))
        basis, metadata = calendar_basis(frame)
        changed, _ = calendar_basis(frame.assign(merge_event=1 - frame.merge_event))
        np.testing.assert_array_equal(basis, changed)
        np.testing.assert_allclose(basis.mean(axis=0), 0, atol=1e-12)
        self.assertEqual(basis.shape, (101, 4))
        self.assertEqual(metadata["knots_calendar_years"], [0, 1.25, 2.5, 3.75, 5])

    def test_risk_filter_uses_events_within_each_creation_cohort(self):
        frame = pd.DataFrame(dict(_stratum=["a", "a", "b", "b", "c"],
                                  _entry=[0, 0, 0, 3, 0], _exit=[1, 2, 2, 4, 100],
                                  _event=[1, 0, 1, 0, 0]))
        self.assertEqual(informative_rows(frame).tolist(), [True, True, True, False, False])

    def test_quarter_fixed_exposure_is_reported_as_nonidentified(self):
        rng = np.random.default_rng(20261015)
        n = 180
        quarters = np.repeat(["a", "b", "c"], n // 3)
        frame = pd.DataFrame(dict(creation_quarter=quarters,
                                  lag90_merger_hhi=np.repeat([.3, .5, .8], n // 3),
                                  lag90_merges=rng.poisson(20, n), backlog_at_submission=rng.poisson(30, n),
                                  prior_author_prs=rng.poisson(4, n), prior_integrator_author=np.zeros(n),
                                  calendar_years=np.arange(n) / n,
                                  duration_days=rng.exponential(2, n), merge_event=np.ones(n)))
        fitted = fit_diagnostic(frame, "primary", "quarter_baseline")
        self.assertEqual(fitted["status"], "failed_fit")
        self.assertIn("stratum", fitted["error"])
        self.assertNotIn("concentration_effects", fitted)

    def test_quarter_baseline_recovers_known_within_cohort_hazard(self):
        rng = np.random.default_rng(20261016)
        n = 1200
        cohort = np.repeat(np.arange(6), n // 6)
        hhi = np.asarray([.3, .4, .6, .7, .5, .65])[cohort] + rng.uniform(-.1, .1, n)
        baseline = np.asarray([-1, 2, -2, 1, 0, .7])[cohort]
        true_coefficient = .12
        durations = rng.exponential(np.exp(-true_coefficient * hhi * 10 - baseline))
        frame = pd.DataFrame(dict(creation_quarter=["q" + str(c) for c in cohort],
                                  lag90_merger_hhi=hhi, lag90_merges=rng.poisson(20, n),
                                  backlog_at_submission=rng.poisson(30, n), prior_author_prs=rng.poisson(4, n),
                                  prior_integrator_author=np.zeros(n), calendar_years=np.arange(n) / n,
                                  duration_days=durations, merge_event=np.ones(n)))
        fitted = fit_diagnostic(frame, "primary", "quarter_baseline")
        self.assertEqual(fitted["status"], "identified_fit")
        self.assertEqual(fitted["counts"]["informative_strata"], 6)
        self.assertAlmostEqual(fitted["concentration_effects"][0]["log_hazard_coefficient"],
                               true_coefficient, delta=.10)


if __name__ == "__main__":
    unittest.main()
