"""Check coursework parity and statistical boundary cases; run with unittest."""
import importlib.util
import json
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('analyze', Path(__file__).with_name('analyze.py'))
analyze = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analyze)


class StatisticalTests(unittest.TestCase):
    def test_empty_and_interpolated_percentiles(self):
        self.assertIsNone(analyze.percentile([], .5))
        self.assertEqual(analyze.percentile([1, 3, 9], .5), 3)
        self.assertEqual(analyze.percentile([1, 3, 9], .75), 6)
        self.assertEqual(analyze.percentile([1, 3], .5), 2)

    def test_concentration_extremes(self):
        single = analyze.concentration({'a': 100})
        equal = analyze.concentration({'a': 50, 'b': 50})
        self.assertEqual(single['hhi'], 1)
        self.assertEqual(equal['hhi'], .5)
        self.assertEqual(equal['effective_entities'], 2)

    def test_legacy_csv_single_digit_hour(self):
        self.assertEqual(analyze.parse_time('2021/10/18 9:16:08'),
                         analyze.parse_time('2021-10-18T09:16:08Z'))

    def test_golden_coursework_results(self):
        rows = json.loads((analyze.ROOT / 'Paper/results/v0.28.0/legacy_metrics.json').read_text())
        expected = [(121, 7, 313, .0376, .5825), (611, 32, 1322, .0238, .3114),
                    (1740, 96, 3455, .0164, .2419), (2005, 130, 4436, .0129, .2333),
                    (2803, 238, 9314, .0059, .2602)]
        for row, (commits, authors, touches, file_hhi, author_hhi) in zip(rows, expected):
            self.assertEqual(row['m2']['events'], commits)
            self.assertEqual(row['m2']['entities'], authors)
            self.assertEqual(row['m1']['events'], touches)
            self.assertEqual(round(row['m1']['hhi'], 4), file_hhi)
            self.assertEqual(round(row['m2']['hhi'], 4), author_hhi)
        self.assertEqual(len(rows), len(expected))
        self.assertEqual([r['m4b_cached_closed_issues']['samples'] for r in rows], [0, 5, 103, 74, 479])
        self.assertEqual(round(rows[2]['m3']['handoff_percent'],2),42.22)


if __name__ == '__main__':
    unittest.main()
