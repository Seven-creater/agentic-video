"""Reserve old production calls externally, without altering its frozen code."""
import argparse
import json

from .budget import reserve_request


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--key',required=True)
    p.add_argument('--evidence',required=True)
    p.add_argument('--timing',choices=['pre_submission','late_reconciliation'],default='pre_submission')
    args=p.parse_args()
    row=reserve_request(reservation_key=args.key,audit=dict(evidence=args.evidence,timing=args.timing))
    if row is None:
        raise RuntimeError('reference_subject_budget_gate_not_configured')
    print(json.dumps(dict(sequence=row['sequence'],reservation_key=args.key)))


if __name__=='__main__':main()
