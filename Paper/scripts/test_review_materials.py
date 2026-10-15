import unittest

from analyze import parse_time
from build_review_materials import commit_events, link, merge_index, render_case


class ReviewMaterialsTests(unittest.TestCase):
    def test_exact_merge_commit_linkage_respects_observation_cutoff(self):
        cutoff = parse_time("2026-10-13T13:45:25Z")
        sha = "1" * 40
        other = "1" * 39 + "2"
        index = merge_index([
            dict(number=1, merged_at="2026-10-13T13:45:25Z", merge_commit_sha=sha),
            dict(number=2, merged_at="2026-10-13T13:45:26Z", merge_commit_sha=sha),
            dict(number=3, merged_at=None, merge_commit_sha=sha),
            dict(number=4, merged_at="2026-10-12T00:00:00Z", merge_commit_sha=other),
        ], cutoff)
        self.assertEqual([row["number"] for row in index[sha]], [1])
        self.assertEqual([row["number"] for row in index[other]], [4])
        self.assertNotIn(sha[:12], index)
        self.assertRaises(ValueError, merge_index,
                          [dict(number=1, merged_at=None), dict(number=1, merged_at=None)], cutoff)

    def test_reference_events_are_not_repair_labels_and_future_events_are_excluded(self):
        cutoff = parse_time("2026-10-13T13:45:25Z")
        event = dict(id=10, event="referenced", commit_id="a" * 40,
                     created_at="2026-10-13T13:45:25Z")
        future = dict(event, id=11, created_at="2026-10-13T13:45:26Z")
        rows = commit_events([event, future, dict(id=12, event="closed")], cutoff)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event"], "referenced")
        self.assertNotIn("repair", rows[0])
        self.assertRaises(ValueError, commit_events, [dict(event, commit_id="a" * 12)], cutoff)

    def test_cached_text_is_escaped_and_annotation_fields_are_excluded(self):
        attack = '<script>alert("unsafe")</script>'
        record = dict(number=1, title=attack, body=attack,
                      created_at="2026-10-12T00:00:00Z", updated_at="2026-10-12T00:00:00Z",
                      html_url="https://github.com/juanfont/headscale/issues/1", state="closed",
                      responsibility="PRIVATE_RATER_LABEL", outcome="PRIVATE_RATER_OUTCOME")
        rendered = render_case(record, [], [], [], {}, "2026-10-13T13:45:25Z")
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertNotIn("PRIVATE_RATER_LABEL", rendered)
        self.assertNotIn("PRIVATE_RATER_OUTCOME", rendered)
        self.assertNotIn("href=", link("javascript:alert(1)", attack))
        self.assertNotIn("href=", link("https://github.com@evil.example/path", "label"))
        self.assertIn('href="https://github.com/juanfont/headscale/issues/1"',
                      link(record["html_url"], "source"))


if __name__ == "__main__":
    unittest.main()
