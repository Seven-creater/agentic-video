"""Offline forecast summaries grouped by annotated prefix information."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from statistics import mean


INFORMATION = ('explicit', 'implied', 'ambiguous', 'unlabeled')
ELEMENT_COUNTS = ('matched', 'partial', 'missing', 'contradicted', 'label_disputed')
VALIDITY = ('supported', 'plausible', 'insufficient', 'contradicted', 'missing')


def _mean(rows):
    return mean(row['metrics']['full_match_rate'] for row in rows) if rows else None


def summarize(manifest, report, *, include_implied=False) -> dict:
    cases = {case['id']: case for case in manifest['cases']}
    grouped = {name: [] for name in INFORMATION}
    eligible_cases, eligible_scored = [], []
    for row in report['rows']:
        if row['case_id'] not in cases:
            raise ValueError('prefix_subset_unknown_case')
        if row['task'] != 'forecast':
            continue
        case = cases[row['case_id']]
        labels = case.get('labels', {})
        information = labels.get('prefix_information', 'unlabeled')
        grouped[information].append(row)
        if information == 'explicit' or (include_implied and information == 'implied'):
            cut = row.get('prefix_cut_s')
            if cut is None:
                cut = labels.get('prefix_cut_s')
            eligible_cases.append(dict(case_id=row['case_id'], name=case['name'],
                prefix_cut_s=cut, status=row['status'],
                full_match_rate=row.get('metrics', {}).get('full_match_rate')))
            if row['status'] == 'scored_provisional':
                eligible_scored.append(row)
    groups = {}
    for information, rows in grouped.items():
        scored = [row for row in rows if row['status'] == 'scored_provisional']
        validity = Counter(row['metrics'].get('inference_validity') or 'missing' for row in scored)
        groups[information] = dict(planned=len(rows), scored=len(scored),
            statuses=dict(Counter(row['status'] for row in rows)),
            mean_full_match_rate=_mean(scored),
            element_counts={key: sum(row['metrics'].get('counts', {}).get(key, 0)
                for row in scored) for key in ELEMENT_COUNTS},
            inference_validity={key: validity[key] for key in VALIDITY})
    return dict(policy='prefix_subset_v1', include_implied=bool(include_implied), groups=groups,
        eligible_cases=eligible_cases, eligible_mean_full_match_rate=_mean(eligible_scored),
        note='Subset by annotated prefix information; labels are model-assisted unless confirmed.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('report', type=Path)
    parser.add_argument('--include-implied', action='store_true')
    args = parser.parse_args(argv)
    manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
    report = json.loads(args.report.read_text(encoding='utf-8'))
    print(json.dumps(summarize(manifest, report, include_implied=args.include_implied),
        ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
