"""Frozen-denominator template metrics. Scores are not engineering delivery."""
from __future__ import annotations

import math
import copy
from statistics import mean


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def compile_packet_judgment(value, mapping):
    """Convert the transmitted video's clock; never guess a missing time base."""
    if value.get('time_base') != 'packet_seconds':
        raise ValueError('template_judge_explicit_packet_time_base_required')
    by_id = {row['asset_id']: row for row in mapping}
    if len(by_id) != len(mapping):
        raise ValueError('template_judge_packet_duplicate_asset')
    compiled = copy.deepcopy(value)
    compiled['time_base'] = 'source_seconds'
    for element in compiled.get('elements', {}).values():
        for evidence in element.get('evidence', []):
            bound = by_id.get(evidence.get('asset_id'))
            start, end = evidence.get('start_s'), evidence.get('end_s')
            if (bound is None or not number(start) or not number(end) or
                    not bound['packet_start_s'] <= start < end <= bound['packet_end_s']):
                raise ValueError('template_judge_evidence_outside_packet_asset')
            evidence['start_s'] = start - bound['packet_start_s']
            # The encoded segment can end with a fraction of AAC/frame padding.
            # Intervals entirely in padding are rejected, not invented as media.
            evidence['end_s'] = min(end - bound['packet_start_s'], bound['source_duration_s'])
            if not 0 <= evidence['start_s'] < evidence['end_s']:
                raise ValueError('template_judge_evidence_only_in_transport_padding')
    return compiled


def validate_judgment(template, value, *, claims=()):
    by_id = {a['asset_id']: a for a in template['assets'] if a['asset_id'] in template['query']}
    elements = value.get('elements', {})
    expected = {e['id'] for e in template['rubric']['elements']}
    if not isinstance(elements, dict) or set(elements) != expected:
        raise ValueError('template_judge_requires_all_frozen_elements')
    for item in elements.values():
        if type(item.get('score')) is not int or item['score'] not in {0, 1, 2}:
            raise ValueError('template_judge_score_out_of_range')
        evidence = item.get('evidence')
        if not isinstance(evidence, list) or (item['score'] > 0 and not evidence):
            raise ValueError('template_judge_actual_evidence_required')
        for e in evidence:
            if (e.get('asset_id') not in by_id or not number(e.get('start_s')) or
                    not number(e.get('end_s')) or
                    not 0 <= e['start_s'] < e['end_s'] <= by_id[e['asset_id']]['duration_s'] or
                    not isinstance(e.get('observation'), str) or not e['observation'].strip()):
                raise ValueError('template_judge_evidence_outside_asset')
    order = value.get('order', {})
    edges = {a + '>' + b for a, b in template['rubric']['order_edges']}
    if set(order) != edges or any(type(v) is not bool for v in order.values()):
        raise ValueError('template_judge_requires_all_order_edges')
    outcomes = value.get('claims', {})
    if set(outcomes) != set(claims) or any(v not in {'supported', 'unsupported', 'unknown'} for v in outcomes.values()):
        raise ValueError('template_judge_claim_identity_changed')
    if not isinstance(value.get('limitations'), list):
        raise ValueError('template_judge_limitations_required')
    return value


def score(template, judgment):
    weights = {e['id']: 2 if e['required'] else 1 for e in template['rubric']['elements']}
    total = sum(weights.values())
    # No result is a zero template score; missing events also lose their edges.
    if judgment is None:
        return dict(template_score=0.0, order_accuracy=0.0 if template['rubric']['order_edges'] else None,
                    unsupported_rate=None, claim_verification_coverage=0.0, evaluated=False)
    scores = {k: v['score'] for k, v in judgment['elements'].items()}
    order = [correct and scores[a] > 0 and scores[b] > 0
             for (a, b), correct in ((edge, judgment['order'][edge[0] + '>' + edge[1]])
                                     for edge in template['rubric']['order_edges'])]
    claims = list(judgment['claims'].values())
    verified = [v for v in claims if v != 'unknown']
    return dict(template_score=100 * sum(weights[k] * scores[k] for k in weights) / (2 * total),
        order_accuracy=mean(order) if order else None,
        unsupported_rate=verified.count('unsupported') / len(verified) if verified else None,
        claim_verification_coverage=len(verified) / len(claims) if claims else 0.0,
        evaluated=True)


def retrieval_metrics(ranked_ids, relevant_ids, *, k=5):
    if len(set(ranked_ids)) != len(ranked_ids) or not relevant_ids or k < 1:
        raise ValueError('template_retrieval_unique_ranking_and_nonempty_gold_required')
    relevant = set(relevant_ids)
    first = next((i + 1 for i, key in enumerate(ranked_ids) if key in relevant), None)
    return dict(recall_at_k=len(set(ranked_ids[:k]) & relevant) / len(relevant),
                reciprocal_rank=1 / first if first else 0.0)


def summarize(manifest, records):
    templates = {t['template_id']: t for t in manifest['templates']}
    planned = {(t, m) for t in templates for m in manifest['methods']}
    by_key = {}
    for row in records:
        key = row['template_id'], row['method']
        if key not in planned or key in by_key or row.get('protocol_sha256') != manifest['protocol_sha256']:
            raise ValueError('template_report_unplanned_duplicate_or_changed_record')
        by_key[key] = row
    rows = []
    for key in sorted(planned):
        row = by_key.get(key, {})
        judgment = row.get('judgment')
        if judgment is not None:
            validate_judgment(templates[key[0]], judgment, claims=row.get('claim_ids', []))
        rows.append(dict(template_id=key[0], method=key[1], status=row.get('status', 'pending'),
                         **score(templates[key[0]], judgment)))
    groups = []
    for method in manifest['methods']:
        chosen = [r for r in rows if r['method'] == method]
        groups.append(dict(method=method, planned=len(chosen),
            evaluated=sum(r['evaluated'] for r in chosen),
            evaluation_coverage=sum(r['evaluated'] for r in chosen) / len(chosen),
            mean_template_score_all_planned=mean(r['template_score'] for r in chosen),
            mean_order_accuracy_all_planned=mean(r['order_accuracy'] for r in chosen
                if r['order_accuracy'] is not None) if any(r['order_accuracy'] is not None for r in chosen) else None,
            mean_unsupported_rate_verified=mean(r['unsupported_rate'] for r in chosen
                if r['unsupported_rate'] is not None) if any(r['unsupported_rate'] is not None for r in chosen) else None,
            mean_claim_verification_coverage_all_planned=mean(r['claim_verification_coverage'] for r in chosen),
            statuses={s: sum(r['status'] == s for r in chosen) for s in sorted({r['status'] for r in chosen})}))
    return dict(schema_version='template_evaluation_report_v1', protocol_sha256=manifest['protocol_sha256'],
        experiment_id=manifest['experiment_id'], groups=groups, rows=rows,
        synthetic_smoke_only=manifest.get('synthetic_smoke_only') is True,
        note='Understanding scores only. No generated-video engineering pass or cash cost is inferred.')
