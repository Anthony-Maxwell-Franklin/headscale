import unittest

from analyze import parse_time
from analyze_review_census import review_observation,paired_role_counts
from pr_history import reconstruct


class ReviewCensusTests(unittest.TestCase):
    def test_fractional_review_activity_conserves_the_matched_pr_population(self):
        rows=[dict(author='writer-a',merged_by='Integrator',human_reviewers='reviewer-a;reviewer-b'),
              dict(author='writer-b',merged_by='Integrator',human_reviewers='reviewer-a')]
        counts=paired_role_counts(rows)
        self.assertEqual(sum(counts['author'].values()),2)
        self.assertEqual(sum(counts['integrator'].values()),2)
        self.assertEqual(sum(counts['fractional_reviewer'].values()),2)
        self.assertEqual(counts['fractional_reviewer'],dict({'reviewer-a':1.5,'reviewer-b':.5}))
        self.assertEqual(sum(counts['reviewer'].values()),3)

    def test_review_activity_and_first_episode_have_distinct_boundaries(self):
        detail=dict(user=dict(login='Author',type='User'),created_at='2026-01-01T00:00:00Z',
                    closed_at='2026-01-04T00:00:00Z',merged_at='2026-01-04T00:00:00Z')
        timeline=[dict(id=1,event='closed',created_at='2026-01-02T00:00:00Z'),
                  dict(id=2,event='reopened',created_at='2026-01-03T00:00:00Z'),
                  dict(id=3,event='merged',created_at='2026-01-04T00:00:00Z')]
        cutoff=parse_time('2026-01-06T00:00:00Z')
        def review(identifier,actor,time,state='COMMENTED',account_type='User'):
            return dict(id=identifier,user=dict(login=actor,type=account_type),submitted_at=time,state=state)
        reviews=[review(1,'Reviewer','2026-01-01T01:00:00Z'),
                 review(2,'Reviewer','2026-01-01T02:00:00Z','DISMISSED'),
                 review(3,'closed-period','2026-01-02T12:00:00Z'),
                 review(4,'after-reopening','2026-01-03T12:00:00Z'),
                 review(5,'author','2026-01-01T03:00:00Z'),
                 review(6,'bot[bot]','2026-01-01T04:00:00Z',account_type='Bot'),
                 review(7,'pending',None,state='PENDING'),
                 dict(id=8,user=None,submitted_at='2026-01-01T05:00:00Z',state='COMMENTED'),
                 review(9,'after-merge','2026-01-05T00:00:00Z')]
        result=review_observation(detail,reviews,cutoff,reconstruct(detail,timeline,cutoff))
        self.assertEqual(len(result['initial_events']),2)
        self.assertEqual(len(result['all_open_events']),3)
        self.assertEqual(len({r[1] for r in result['all_open_events']}),2)
        self.assertEqual(result['excluded'],dict(during_closed_interval=1,author_self_review=1,
                                                bot_actor=1,pending_or_unsubmitted=1,unknown_actor=1,
                                                after_final_disposition=1))
        self.assertAlmostEqual(result['duration_days'],1/24)
        self.assertRaises(ValueError,review_observation,detail,reviews+[reviews[0]],cutoff)

    def test_unsubmitted_review_does_not_extend_opportunity_past_merge(self):
        detail=dict(user=dict(login='author'),created_at='2026-01-01T00:00:00Z',
                    merged_at='2026-01-02T00:00:00Z',closed_at='2026-01-02T00:00:00Z')
        reviews=[dict(id=1,user=dict(login='reviewer',type='User'),state='PENDING')]
        result=review_observation(detail,reviews,parse_time('2026-04-01T00:00:00Z'))
        self.assertEqual(result['outcome'],'merge_without_response')
        self.assertEqual(result['duration_days'],1.)


if __name__=='__main__':
    unittest.main()
