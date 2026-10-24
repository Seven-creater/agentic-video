import json

import pytest

from omni_story.evaluation.reference_exam import REPAIR_PROMPT, repair_once, validate_judgment_shape


def item(n, relation='matched', **extra):
    row = dict(gold_id=f'g{n:03d}', gold_validity='valid', relation=relation, candidate_ids=['u000'] if relation != 'missing' else [],
        reason='r', evidence=[dict(start_s=0, end_s=1, observation='o')] if relation != 'missing' else [])
    row.update(extra)
    return row


def good(count=2, **extra):
    return dict(items=[item(i) for i in range(count)], limitations=[], **extra)


def test_well_formed_judgment_has_no_defects():
    assert validate_judgment_shape(good(), count=2) == []
    assert validate_judgment_shape(dict(items=[item(0, 'missing')]), count=1) == []


@pytest.mark.parametrize('raw,expected', [
    ([], ['judgment_not_object']),
    (dict(), ['items_missing']),
    (dict(items=[item(0)]), ['gold_ids_mismatch']),
    (dict(items=[item(0), item(0)]), ['gold_ids_mismatch']),
])
def test_item_set_defects(raw, expected):
    assert validate_judgment_shape(raw, count=2) == expected


def test_missing_keys_and_empty_evidence_are_reported_but_missing_relation_may_have_none():
    broken = item(1)
    del broken['evidence']
    empty = item(0, evidence=[])
    result = validate_judgment_shape(dict(items=[empty, broken]), count=2)
    assert result == ['evidence_empty:g000', 'evidence_missing:g001']
    assert validate_judgment_shape(dict(items=[item(0, 'missing')]), count=1) == []


def test_prefix_inference_is_required_only_on_request():
    assert validate_judgment_shape(good(), count=2) == []
    assert validate_judgment_shape(good(), count=2, require_inference=True) == ['inference_validity_missing']
    assert validate_judgment_shape(good(inference_validity='maybe'), count=2, require_inference=True) == ['inference_validity_invalid']
    assert validate_judgment_shape(good(inference_validity='plausible'), count=2, require_inference=True) == []


def ask_returning(text, calls):
    def ask(prompt):
        calls.append(prompt)
        return text
    return ask


def test_valid_reply_never_calls_ask():
    calls = []
    repaired, after, record = repair_once(json.dumps(good()), [], ask=ask_returning('x', calls), count=2)
    assert repaired == good() and after == [] and calls == [] and record['calls'] == 0


def test_successful_repair_keeps_original_and_records_hashes():
    calls = []
    original = json.dumps(good())
    fixed = good(inference_validity='supported')
    repaired, after, record = repair_once(original, ['inference_validity_missing'],
        ask=ask_returning('```json\n' + json.dumps(fixed) + '\n```', calls), count=2, require_inference=True)
    assert repaired == fixed and after == []
    assert len(calls) == 1 and 'inference_validity_missing' in calls[0] and original in calls[0]
    assert record['calls'] == 1 and record['original_sha256'] != record['repaired_sha256']
    assert record['defects_before'] == ['inference_validity_missing'] and record['defects_after'] == []


def test_failed_repair_is_not_retried():
    calls = []
    repaired, after, record = repair_once(json.dumps(good()), ['inference_validity_missing'],
        ask=ask_returning(json.dumps(good()), calls), count=2, require_inference=True)
    assert repaired is None and after == ['inference_validity_missing'] and len(calls) == 1
    repaired, after, _ = repair_once('{}', ['items_missing'], ask=ask_returning('not json', calls), count=2)
    assert repaired is None and after == ['repair_reply_not_json'] and len(calls) == 2


def test_truncated_empty_body_is_not_a_format_defect():
    calls = []
    repaired, after, record = repair_once('  ', ['items_missing'], ask=ask_returning('x', calls), count=2, stop_reason='max_tokens')
    assert repaired is None and after == ['empty_body_truncated'] and calls == [] and record['calls'] == 0


def test_prompt_forbids_changing_judgments():
    assert '不改变任何已有判断' in REPAIR_PROMPT
