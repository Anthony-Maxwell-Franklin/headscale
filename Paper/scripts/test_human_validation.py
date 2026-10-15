import csv
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from analyze_human_validation import nominal_agreement,hypergeom_count_interval,weighted_category_estimates,validate_rater,main


class HumanValidationTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec('statsmodels'),'Locked research environment required')
    def test_agreement_matches_independent_statsmodels_implementation(self):
        import numpy as np
        from statsmodels.stats.inter_rater import cohens_kappa
        left=['a','a','a','b','b','c','c','c'];right=['a','b','a','b','c','c','a','c']
        table=np.zeros((3,3))
        for a,b in zip(left,right):table['abc'.index(a),'abc'.index(b)]+=1
        self.assertAlmostEqual(nominal_agreement(left,right)['kappa'],cohens_kappa(table).kappa)
        self.assertIsNone(nominal_agreement(['a']*3,['a']*3)['kappa'])

    @unittest.skipUnless(importlib.util.find_spec('scipy'),'Locked research environment required')
    def test_exact_bounds_match_scipy_and_do_not_claim_zero_for_unseen_categories(self):
        from scipy.stats import hypergeom
        N,n,x,tail=10,4,0,.025
        expected_lower=min(K for K in range(x,N-n+x+1) if hypergeom.sf(x-1,N,K,n)>=tail)
        expected_upper=max(K for K in range(x,N-n+x+1) if hypergeom.cdf(x,N,K,n)>=tail)
        self.assertEqual(hypergeom_count_interval(N,n,x,tail),(expected_lower,expected_upper))
        self.assertGreater(expected_upper,0)
        self.assertEqual(hypergeom_count_interval(10,10,3,.025),(3,3))

    def test_weighted_estimates_recover_stratified_population_not_sample_frequency(self):
        rows=[dict(stratum='oversampled',inclusion_probability=.5,outcome='yes'),
              dict(stratum='oversampled',inclusion_probability=.5,outcome='no'),
              dict(stratum='census',inclusion_probability=1.,outcome='yes'),
              dict(stratum='census',inclusion_probability=1.,outcome='yes')]
        estimates=weighted_category_estimates(rows,'outcome',['yes','no'],6)
        self.assertAlmostEqual(estimates[0]['population_proportion'],4/6)
        self.assertNotAlmostEqual(estimates[0]['population_proportion'],3/4)
        self.assertAlmostEqual(sum(r['population_proportion'] for r in estimates),1)
        self.assertRaises(ValueError,weighted_category_estimates,rows,'outcome',['yes','no'],7)

    def test_filled_labels_do_not_substitute_for_declared_human_origin(self):
        sample={1:dict(stratum='s',inclusion_probability=1.)}
        rows={1:dict(stratum='s',inclusion_probability=1.,responsibility='server',outcome='open',
                     administrative_closure='no',evidence_url='https://example.test',evidence_notes='Synthetic fixture')}
        allowed=dict(responsibility=['server'],outcome=['open'],administrative_closure=['no'])
        errors,complete=validate_rater(rows,{},sample,allowed,2)
        self.assertEqual(complete,1)
        self.assertTrue(any('Human review completion' in e for e in errors))
        self.assertTrue(any('Independence' in e for e in errors))

    def test_adjudication_requires_current_original_labels_and_preserves_inputs(self):
        # Synthetic declarations and labels live only in a temporary test folder.
        # They are not annotations of the actual study or independent human work.
        root=Path(__file__).resolve().parents[2]
        with (root/'Paper/results/research-v0.29.5/issue_validation_sample.csv').open() as handle:
            sample=list(csv.DictReader(handle))
        book=json.loads((root/'Paper/data/issue_coding_codebook.json').read_text())
        with tempfile.TemporaryDirectory() as folder:
            directory=Path(folder);output=directory/'output'
            original=[]
            for number in [1,2]:
                rows=[]
                for i,case in enumerate(sample):
                    row={k:case[k] for k in ['number','stratum','inclusion_probability']}
                    row.update(responsibility=book['defect_eligibility'][i%2],outcome=book['closure_outcome'][i%2],
                               administrative_closure='yes' if i%2 else 'no',evidence_url='https://example.test/synthetic',
                               evidence_notes='Synthetic test fixture; not a human review')
                    if number==2 and i==0:row['outcome']=book['closure_outcome'][2]
                    rows.append(row)
                path=directory/f'rater_{number}.csv'
                with path.open('w',newline='') as handle:
                    writer=csv.DictWriter(handle,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
                original.append(path.read_bytes())
                metadata=dict(reviewer_id=f'synthetic_fixture_{number}',independent_human_review_completed=True,
                              other_rater_codes_consulted=False,assistant_suggestions_consulted=False,
                              completed_at_utc='2026-10-14T00:00:00Z',codebook_version=book['version'])
                (directory/f'rater_{number}_metadata.json').write_text(json.dumps(metadata))
            argv=['analysis','--input',str(directory),'--output',str(output)]
            with patch('sys.argv',argv),redirect_stdout(io.StringIO()):self.assertEqual(main(),2)
            status=json.loads((output/'validation_status.json').read_text())
            run=Path(status['analysis_directory'])
            self.assertEqual(status['status'],'awaiting_adjudication')
            self.assertFalse((run/'weighted_category_estimates.csv').exists())
            with (run/'adjudication_template.csv').open() as handle:adjudication=list(csv.DictReader(handle))
            self.assertEqual(len(adjudication),1)
            case=adjudication[0]
            case.update(responsibility=case['rater_1_responsibility'],outcome=case['rater_1_outcome'],
                        administrative_closure=case['rater_1_administrative_closure'],
                        adjudicator_id='synthetic_fixture',evidence_url='https://example.test/synthetic',
                        evidence_notes='Synthetic test adjudication')
            with (directory/'adjudication.csv').open('w',newline='') as handle:
                writer=csv.DictWriter(handle,fieldnames=list(case));writer.writeheader();writer.writerow(case)
            with patch('sys.argv',argv),redirect_stdout(io.StringIO()):self.assertEqual(main(),0)
            status=json.loads((output/'validation_status.json').read_text())
            self.assertEqual(status['status'],'declared_human_coding_analysed')
            self.assertTrue((Path(status['analysis_directory'])/'weighted_category_estimates.csv').exists())
            self.assertEqual([p.read_bytes() for p in [directory/'rater_1.csv',directory/'rater_2.csv']],original)
            case['rater_1_outcome']='outdated-label'
            with (directory/'adjudication.csv').open('w',newline='') as handle:
                writer=csv.DictWriter(handle,fieldnames=list(case));writer.writeheader();writer.writerow(case)
            with patch('sys.argv',argv),redirect_stdout(io.StringIO()):self.assertEqual(main(),2)
            stale=json.loads((output/'validation_status.json').read_text())
            self.assertEqual(stale['status'],'awaiting_adjudication')
            self.assertNotEqual(stale['analysis_directory'],status['analysis_directory'])


if __name__=='__main__':unittest.main()
