"""Local movie-library execution with an active Codex official-MCP connection."""
from __future__ import annotations

import argparse
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--no-asr', action='store_true')
    editing = parser.add_mutually_exclusive_group()
    editing.add_argument('--editing-v2', action='store_true',
                         help='Bind reference methods to new plans and audit their actual output; preserve locked budgets.')
    editing.add_argument('--audit-editing', action='store_true',
                         help='Measure the reference and existing renders locally; no model requests or new renders.')
    args = parser.parse_args(argv)
    if args.audit_editing:
        import json
        from .editing import audit_existing_editing
        report = audit_existing_editing(args.output.resolve(strict=True), args.reference.resolve(strict=True))
        print(json.dumps(report,ensure_ascii=False),flush=True)
        return 0
    from .pipeline import execute
    execute(args.reference.resolve(strict=True), args.library.resolve(strict=True), args.output,
            asr=not args.no_asr,editing_v2=args.editing_v2)
    return 0


if __name__ == '__main__':
    main()
