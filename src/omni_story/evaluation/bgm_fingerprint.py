"""Local BGM template matching over chromaprint fingerprints; no model calls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

METHOD = 'chromaprint_local_v1'
MIN_OVERLAP = 20


def similarity(a, b, max_shift=120):
    """Best bit-agreement rate of two raw chromaprint sequences within ±max_shift
    frames; alignments shorter than MIN_OVERLAP frames do not count."""
    left = np.asarray(a, dtype=np.int64) & 0xFFFFFFFF
    right = np.asarray(b, dtype=np.int64) & 0xFFFFFFFF
    best = 0.0
    for shift in range(-max_shift, max_shift + 1):
        start, end = max(0, shift), min(left.size, right.size + shift)
        if end - start < MIN_OVERLAP:
            continue
        xor = (left[start:end] ^ right[start - shift:end - shift]).astype(np.uint32)
        ones = int(np.unpackbits(xor.view(np.uint8).reshape(-1, 4), axis=1).sum())
        best = max(best, 1.0 - ones / (32 * (end - start)))
    return best


def _load_rows(screen_rows_path):
    """Server screening rows indexed by video key; blank lines are ignored."""
    rows = {}
    for line in Path(screen_rows_path).read_text(encoding='utf-8').splitlines():
        if line.strip():
            row = json.loads(line)
            rows[row['video']] = row
    return rows


def _key(path):
    return '/'.join(Path(path).parts[-2:])


def _fingerprint(rows, path):
    if not path:
        return None
    row = rows.get(_key(path))
    if row is None or row.get('error') or not row.get('fp_bgm'):
        return None
    return row['fp_bgm']


def evaluate(manifest, screen_rows_path, *, threshold=0.65, min_group=3):
    """Match each case's query fingerprint against every case's reference
    fingerprint; the reference itself never substitutes for the query. Gates run
    in the order their inputs exist: usable audio, then a signature BGM shared
    by min_group videos of the template directory, then a confirmed mechanism."""
    rows = _load_rows(screen_rows_path)
    cases = []
    for case in manifest.get('cases') or []:
        reference = case.get('reference') or {}
        query = case.get('query') or {}
        cases.append((case, _fingerprint(rows, reference.get('path')),
                      _fingerprint(rows, query.get('path')),
                      _key(query.get('path') or '').rpartition('/')[0]))
    library = {case.get('id'): fp for case, fp, _, _ in cases if fp is not None}
    result = {}
    for case, ref_fp, query_fp, directory in cases:
        row = dict(case_id=case.get('id'), template=case.get('name'))
        result[row['case_id']] = row
        if query_fp is None or ref_fp is None:
            row['status'] = 'ineligible_audio'
            continue
        group = sum(similarity(query_fp, other['fp_bgm']) >= threshold
                    for video, other in rows.items()
                    if video.rpartition('/')[0] == directory
                    and other.get('fp_bgm') and not other.get('error'))
        if group < min_group:
            row['status'] = 'not_applicable_no_signature_bgm'
        elif (case.get('labels') or {}).get('same_mechanism') is not True:
            row['status'] = 'not_applicable_cross_video_unverified'
        else:
            scored = {case_id: similarity(query_fp, fp) for case_id, fp in library.items()}
            candidates = sorted(cid for cid, rate in scored.items() if rate >= threshold)
            row.update(status='scored_provisional', method=METHOD, metrics=dict(
                music_template_hit=int(row['case_id'] in candidates),
                unique_template_hit=int(candidates == [row['case_id']]),
                candidate_count=len(candidates), best_similarity=max(scored.values()),
                own_reference_similarity=scored[row['case_id']]))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('screen_rows', type=Path)
    args = parser.parse_args(argv)
    manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
    print(json.dumps(evaluate(manifest, args.screen_rows),
                     ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
