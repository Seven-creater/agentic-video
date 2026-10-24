"""Story-question leakage audit. A keyword match in synthesized text is not understanding."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


QUOTED = (re.compile('“([^“”]*)”'), re.compile('「([^「」]*)」'))
TRIM = ' \t\r\n，。、；：？！“”‘’「」《》（）…—·,.!?;:\'"()'
NAME_PREFIX = re.compile(r'^\d+_')


def name_token(name):
    return NAME_PREFIX.sub('', name if isinstance(name, str) else '')


def quotes(labels, *, min_quote_chars=4):
    """Original-video words kept inside quotes, trimmed and deduplicated in first-seen order."""
    texts = [e.get('observation') for e in labels.get('core_evidence') or [] if isinstance(e, dict)]
    texts.append(labels.get('theme_summary'))
    found = []
    for text in texts:
        if not isinstance(text, str):
            continue
        for fragment in (f for pattern in QUOTED for f in pattern.findall(text)):
            fragment = fragment.strip(TRIM)
            if len(fragment) >= min_quote_chars and fragment not in found:
                found.append(fragment)
    return found


def audit(manifest: dict, *, min_quote_chars=4) -> dict:
    rows = []
    for case in manifest.get('cases') or []:
        labels = case.get('labels') or {}
        paragraphs = labels.get('story_paragraphs') or []
        if not paragraphs or 'story_preparation_error' in labels:
            continue
        targets = labels.get('story_target_indices')
        if (any(not isinstance(p, str) for p in paragraphs) or not isinstance(targets, list) or not targets or
                any(type(i) is not int or not 0 <= i < len(paragraphs) for i in targets)):
            raise ValueError('story_leakage_story_answer_outside_text')
        token, quoted = name_token(case.get('name')), quotes(labels, min_quote_chars=min_quote_chars)
        hits = [dict(index=i, name_hit=bool(token and token in p), quote_hits=[q for q in quoted if q in p])
                for i, p in enumerate(paragraphs)]
        hits = [h for h in hits if h['name_hit'] or h['quote_hits']]
        leak = {h['index'] for h in hits}
        gold, distractors = set(targets), set(range(len(paragraphs))) - set(targets)
        rows.append(dict(case_id=case.get('id'), name=case.get('name'), name_token=token, quotes=quoted,
            paragraph_hits=hits,
            target_leak_rate=sum(i in leak for i in gold)/len(gold),
            distractor_leak_rate=sum(i in leak for i in distractors)/len(distractors) if distractors else None,
            keyword_separable=all(i in leak for i in gold) and not any(i in leak for i in distractors)))
    return dict(policy='story_leakage_audit_v1', rows=rows, summary=dict(cases=len(rows),
        cases_with_any_leak=sum(bool(row['paragraph_hits']) for row in rows),
        keyword_separable=sum(row['keyword_separable'] for row in rows)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--min-quote-chars', type=int, default=4)
    args = parser.parse_args(argv)
    manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
    print(json.dumps(audit(manifest, min_quote_chars=args.min_quote_chars),
                     ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
