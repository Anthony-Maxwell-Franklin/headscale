"""Export the initial-episode disposition curve used by the working manuscript."""
import csv
import json
from pathlib import Path

from analyze import write_csv
from analyze_process import competing_disposition


def main():
    directory=Path(__file__).resolve().parents[2]/'Paper/results/pr-census'
    coverage=json.loads((directory/'coverage.json').read_text())
    summary=json.loads((directory/'summary.json').read_text())
    if not coverage.get('lifecycle_validated') or summary['model_episode_scope']!='first disposition':
        raise ValueError('Validated initial episodes required')
    with (directory/'human_pr_metadata_features.csv').open() as handle:
        rows=list(csv.DictReader(handle))
    curve=competing_disposition([(float(r['duration_days']),r['outcome']) for r in rows])
    write_csv(directory/'human_first_disposition_cif.csv',curve)
    print('Exported initial-episode disposition curve for',len(rows),'human-account PRs')


if __name__=='__main__':main()
