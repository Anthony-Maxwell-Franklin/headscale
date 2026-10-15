import unittest

from analyze import parse_time
from pr_history import reconstruct,pending_before,in_observed_open_interval


class HistoryTests(unittest.TestCase):
    def test_close_reopen_gap_is_not_pending_and_future_reopen_is_not_used(self):
        detail=dict(created_at='2026-01-01T00:00:00Z',closed_at='2026-01-04T00:00:00Z',
                    merged_at='2026-01-04T00:00:00Z')
        events=[dict(id=1,event='closed',created_at='2026-01-02T00:00:00Z'),
                dict(id=2,event='reopened',created_at='2026-01-03T00:00:00Z'),
                dict(id=3,event='merged',created_at='2026-01-04T00:00:00Z'),
                dict(id=4,event='closed',created_at='2026-01-04T00:00:00Z')]
        cutoff=parse_time('2026-01-05T00:00:00Z')
        history=reconstruct(detail,events,cutoff)
        self.assertEqual(history['anomalies'],[])
        self.assertEqual(history['first_outcome'],'nonmerge_close')
        self.assertFalse(pending_before(history,parse_time('2026-01-02T12:00:00Z')))
        self.assertFalse(in_observed_open_interval(history,parse_time('2026-01-02T12:00:00Z')))
        self.assertTrue(pending_before(history,parse_time('2026-01-03T12:00:00Z')))
        truncated=reconstruct(detail,events,parse_time('2026-01-02T18:00:00Z'))
        self.assertEqual(truncated['reopened_events'],0)
        self.assertFalse(truncated['open_at_cutoff'])
        self.assertTrue(pending_before(history,parse_time('2026-01-02T00:00:00Z')))
        self.assertFalse(pending_before(history,parse_time('2026-01-02T00:00:01Z')))

    def test_merge_close_tie_is_one_merge_and_missing_merge_can_use_detail(self):
        detail=dict(created_at='2026-01-01T00:00:00Z',merged_at='2026-01-02T00:00:00Z',
                    closed_at='2026-01-02T00:00:00Z')
        timeline=[dict(id=1,event='closed',created_at=detail['closed_at'])]
        history=reconstruct(detail,timeline,parse_time('2026-01-03T00:00:00Z'))
        self.assertEqual(history['first_outcome'],'merge')
        self.assertEqual(len(history['open_intervals']),1)
        self.assertEqual(history['anomalies'],[])

    def test_missing_historical_close_is_an_audit_failure(self):
        detail=dict(created_at='2026-01-01T00:00:00Z',merged_at=None,
                    closed_at='2026-01-04T00:00:00Z')
        timeline=[dict(id=1,event='reopened',created_at='2026-01-03T00:00:00Z')]
        history=reconstruct(detail,timeline,parse_time('2026-01-05T00:00:00Z'))
        self.assertIn('reopened_without_observed_close',history['anomalies'])
        self.assertIn('last_detail_close_missing_from_timeline',history['anomalies'])

    def test_one_second_notification_lag_is_retained_as_clock_uncertainty(self):
        detail=dict(created_at='2026-01-01T00:00:00Z',merged_at='2026-01-02T00:00:00Z',
                    closed_at='2026-01-02T00:00:01Z')
        timeline=[dict(id=1,event='merged',created_at='2026-01-02T00:00:01Z'),
                  dict(id=2,event='closed',created_at='2026-01-02T00:00:01Z')]
        history=reconstruct(detail,timeline,parse_time('2026-01-03T00:00:00Z'))
        self.assertEqual(history['anomalies'],[])
        self.assertEqual(history['metadata_clock_differences'][0]['timeline_minus_detail_seconds'],1)
        self.assertEqual(history['first_terminal_ts'],parse_time(detail['merged_at']))
        closed_detail=dict(detail,merged_at=None,closed_at='2026-01-02T00:00:00Z')
        close_event=[dict(id=3,event='closed',created_at='2026-01-02T00:00:02Z')]
        closed_history=reconstruct(closed_detail,close_event,parse_time('2026-01-03T00:00:00Z'))
        self.assertEqual(closed_history['anomalies'],[])
        self.assertEqual(closed_history['metadata_clock_differences'][0]['timeline_minus_detail_seconds'],2)
        self.assertEqual(closed_history['first_terminal_ts'],parse_time(close_event[0]['created_at']))


if __name__=='__main__':
    unittest.main()
