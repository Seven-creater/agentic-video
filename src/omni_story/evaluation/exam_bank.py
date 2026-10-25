"""Offline exam-bank construction: one shuffled mixed story and BGM query eligibility."""
from __future__ import annotations

import random


def build_mixed_story(manifest, *, seed):
    """Shuffle every prepared story into one shared exam text; answers live outside it."""
    entries = []
    targets = {}
    for case in manifest['cases']:
        labels = case['labels']
        if 'story_preparation_error' in labels:
            continue
        targets[case['id']] = set(labels.get('story_target_indices') or [])
        entries.extend((case['id'], index, text)
                       for index, text in enumerate(labels.get('story_paragraphs', [])))
    random.Random(seed).shuffle(entries)
    paragraphs, source_map, gold = [], {}, {}
    # Paragraph rows carry no case, template or target identity; source_map is the answer key.
    for position, (case_id, index, text) in enumerate(entries):
        paragraph_id = f'p{position:03d}'
        paragraphs.append(dict(id=paragraph_id, text=text))
        source_map[paragraph_id] = dict(case_id=case_id, index=index)
        if index in targets[case_id]:
            gold.setdefault(case_id, []).append(paragraph_id)
    excluded = [dict(case_id=case['id'], reason='no_target_paragraphs')
                for case in manifest['cases']
                if 'story_preparation_error' not in case['labels'] and not gold.get(case['id'])]
    return dict(policy='mixed_story_v1', seed=seed, paragraphs=paragraphs, gold=gold,
                source_map=source_map, excluded=excluded)


def story_levels(selected_ids, story_ids, source_map, case_id, targets):
    """Two layers: did it find the right story, and how its picks split by source."""
    picks = [source_map[i] for i in selected_ids]
    basis = [source_map[i] for i in (story_ids or selected_ids)]
    counts = {}
    for row in basis:
        counts[row['case_id']] = counts.get(row['case_id'], 0) + 1
    chosen = max(counts, key=lambda c: (counts[c], c == case_id)) if counts else None
    own = [r for r in picks if r['case_id'] == case_id]
    return dict(story_hit=float(chosen == case_id), chosen_story_case_id=chosen,
        story_basis='story_paragraph_ids' if story_ids else 'paragraph_ids',
        picked_target=sum(r['index'] in targets for r in own),
        picked_same_story_non_target=sum(r['index'] not in targets for r in own),
        picked_other_story=len(picks) - len(own),
        same_story_share=len(own) / len(picks) if picks else None)


def story_set_metrics(selected_ids, gold_ids):
    selected, gold = set(selected_ids), set(gold_ids)
    matched = len(selected & gold)
    precision = matched / len(selected) if selected else None
    recall = matched / len(gold) if gold else None
    f1 = None
    if precision is not None and recall is not None:
        f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
    return dict(precision=precision, recall=recall, f1=f1, exact=selected == gold)


def bgm_eligibility(rows, *, min_music_rms_db=-45.0, max_speech_words=0, min_duration_s=2.0,
                    min_queries_per_template=2):
    """Reject unusable BGM queries at design time, before any paid run sees them."""
    queries = []
    counts = {}
    for row in rows:
        reasons = []
        if row['duration_s'] < min_duration_s:
            reasons.append('duration_below_min')
        if row['music_rms_db'] < min_music_rms_db:
            reasons.append('music_rms_below_min')
        if row['speech_words'] > max_speech_words:
            reasons.append('speech_words_above_max')
        counts[row['case_id']] = counts.get(row['case_id'], 0) + (not reasons)
        queries.append(dict(case_id=row['case_id'], query_id=row['query_id'],
                            eligible=not reasons, reasons=reasons))
    templates = {case_id: dict(eligible_queries=count,
                               status='included' if count >= min_queries_per_template
                               else 'excluded_at_design')
                 for case_id, count in counts.items()}
    return dict(policy='bgm_eligibility_v1', queries=queries, templates=templates)
