"""List (and with --apply delete) raw MCP request journals of finished runs.

mcp_http*.jsonl keeps every request body, including base64 video; one retest writes
hundreds of MB and the server disk filled on 2026-11-11. Each call's request/response
stays in calls/; a pruned run can no longer be resumed or used as a revision parent.
Only runs with run_outcome.json and no change for --days are touched. Dry run by default.

    python scripts/prune_request_logs.py ROOT [ROOT ...] [--days 1] [--apply]
"""
import argparse
import time
from pathlib import Path


def candidates(roots, days):
    limit = time.time() - days * 86400
    for root in roots:
        for journal in sorted(Path(root).rglob('mcp_http*.jsonl')):
            run = journal.parent
            if (run / 'run_outcome.json').is_file() and journal.stat().st_mtime < limit:
                yield journal


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('roots', nargs='+')
    parser.add_argument('--days', type=float, default=1.0)
    parser.add_argument('--apply', action='store_true', help='delete; without it only list')
    args = parser.parse_args(argv)
    total = count = 0
    for journal in candidates(args.roots, args.days):
        size = journal.stat().st_size
        total += size
        count += 1
        print(f'{size/1048576:9.1f} MB  {journal}')
        if args.apply:
            journal.unlink()
    print(f'{"deleted" if args.apply else "would delete"} {count} files, {total/1048576:.0f} MB')


if __name__ == '__main__':
    main()
