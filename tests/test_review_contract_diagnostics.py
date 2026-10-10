"""Review format diagnostics and sole-repair feedback, without model endpoints."""
from copy import deepcopy
import json

import pytest

from omni_story.library.contracts import ReviewContractError, review_output_contract, validate_review
from omni_story.library.pipeline import CodexMCP
from test_library_pipeline import task, _fake_bridge, _reply


REPAIR_MARKER = '\n上次输出未通过本地协议校验。只修复JSON字段、ID和时间域，不得补造画面证据。'


def valid_review():
    return {'reference_sha256': 'test_ref', 'theme_status': 'pass', 'editing_status': 'partial',
        'continuity_status': 'fail', 'evidence': ['original time evidence and judgement'],
        'limitations': ['original negative finding'], 'revision_requests': ['original revision request']}


def received_object_shape():
    """The four evidence-object shapes returned identically by actual calls021/022."""
    value = valid_review()
    value['evidence'] = [{'start_s': start, 'end_s': end, 'observed_fact': 'original model observation'}
        for start, end in ((8, 10), (26, 41), (134, 146), (159, 179))]
    return value


def rejection(value):
    with pytest.raises(ReviewContractError) as caught:
        validate_review(value, 'test_ref')
    return caught.value


def test_received_selected_review_shapes_remain_rejected_with_specific_type_feedback():
    value = received_object_shape()
    before = deepcopy(value)
    error = rejection(value)
    assert str(error) == 'review/evidence:text_required'
    diagnostic = error.diagnostics
    assert diagnostic['error_path'] == '$.evidence[0]'
    assert diagnostic['expected'] == 'non-empty string'
    assert diagnostic['actual'] == {'type': 'object', 'keys': ['start_s', 'end_s', 'observed_fact']}
    fields = diagnostic['review_list_fields']
    assert [field['path'] for field in fields] == ['$.evidence', '$.limitations', '$.revision_requests']
    assert len(fields[0]['items']) == 4 and all(not item['valid'] for item in fields[0]['items'])
    assert all(field['items'][0]['valid'] for field in fields[1:])
    assert diagnostic['output_contract'] == review_output_contract()
    json.dumps(diagnostic, allow_nan=False)
    assert value == before
    diagnostic['actual']['keys'].clear()
    diagnostic['output_contract']['format_example']['evidence'].clear()
    assert value == before


def test_valid_strings_and_negative_review_are_returned_unchanged():
    value = valid_review()
    before = deepcopy(value)
    assert validate_review(value, 'test_ref') is value
    assert value == before
    value.update(limitations=[], revision_requests=[])
    assert validate_review(value, 'test_ref') is value


@pytest.mark.parametrize('field', ['evidence', 'limitations', 'revision_requests'])
@pytest.mark.parametrize('bad', [None, 'wrong list type', {}, ('tuple',)])
def test_wrong_list_types_keep_original_rejection(field, bad):
    value = valid_review()
    value[field] = bad
    error = rejection(value)
    assert str(error) == 'review/' + field + ':list_required'
    assert error.diagnostics['error_path'] == '$.' + field
    assert error.diagnostics['expected'].endswith('array of non-empty strings')
    json.dumps(error.diagnostics, allow_nan=False)


def test_empty_evidence_is_not_a_repair_and_missing_lists_are_not_defaulted():
    value = valid_review()
    value['evidence'] = []
    error = rejection(value)
    assert str(error) == 'review/evidence:list_required'
    assert error.diagnostics['expected'] == 'non-empty array of non-empty strings'
    assert error.diagnostics['actual'] == {'type': 'array', 'length': 0}
    for field in ('evidence', 'limitations', 'revision_requests'):
        value = valid_review()
        del value[field]
        error = rejection(value)
        assert str(error) == 'review/' + field + ':list_required'
        assert next(row for row in error.diagnostics['review_list_fields']
                    if row['path'] == '$.' + field)['present'] is False
        assert field not in value


@pytest.mark.parametrize('field', ['evidence', 'limitations', 'revision_requests'])
@pytest.mark.parametrize('bad', ['', ' \t\n', None, 3, True, ['nested'], {'observed_fact': 'not a string'}])
def test_mixed_elements_report_exact_index_without_coercion(field, bad):
    value = valid_review()
    value[field] = ['preserved original string', bad]
    before = deepcopy(value)
    error = rejection(value)
    assert str(error) == 'review/' + field + ':text_required'
    assert error.diagnostics['error_path'] == '$.' + field + '[1]'
    assert error.diagnostics['expected'] == 'non-empty string'
    assert value == before


def test_all_review_lists_are_described_even_when_first_field_fails():
    value = received_object_shape()
    value.update(limitations=[{'negative': 'retain it'}], revision_requests=None)
    diagnostic = rejection(value).diagnostics
    assert diagnostic['review_list_fields'][1]['items'][0]['actual'] == {
        'type': 'object', 'keys': ['negative']}
    assert diagnostic['review_list_fields'][2]['actual'] == {'type': 'null'}
    instruction = diagnostic['output_contract']['repair_instruction']
    assert '保留原时间证据' in instruction and '负评' in instruction and '不补造事实' in instruction


@pytest.mark.parametrize('field,bad,error', [
    ('reference_sha256', 'different', 'review:reference_sha_changed'),
    ('theme_status', 'success', 'review:theme_status'),
    ('editing_status', '', 'review:editing_status'),
    ('continuity_status', 'unknown', 'review:continuity_status')])
def test_sha_and_status_rejections_are_not_bypassed_or_changed(field, bad, error):
    value = received_object_shape()
    value[field] = bad
    with pytest.raises(ValueError) as caught:
        validate_review(value, 'test_ref')
    assert str(caught.value) == error
    assert not isinstance(caught.value, ReviewContractError)


@pytest.mark.parametrize('bad', [None, [], 'not an object'])
def test_review_object_contract_still_rejects(bad):
    with pytest.raises(ValueError, match='^review:object_required$'):
        validate_review(bad, 'test_ref')


def test_diagnostics_are_json_safe_without_copying_nonfinite_or_nested_values():
    value = valid_review()
    value['evidence'] = [float('nan'), float('inf'), 10 ** 400,
        {float('inf'): {'nested': float('nan')}, 'observed_fact': object()}, object()]
    diagnostic = rejection(value).diagnostics
    encoded = json.dumps(diagnostic, allow_nan=False)
    assert json.loads(encoded) == diagnostic
    items = diagnostic['review_list_fields'][0]['items']
    assert items[0]['actual'] == {'type': 'number', 'finite': False}
    assert items[2]['actual'] == {'type': 'number', 'finite': True}
    assert items[3]['actual']['keys'] == ['<float key>', 'observed_fact']


def test_output_contract_is_fresh_and_contains_only_format_examples():
    contract = review_output_contract()
    before = deepcopy(contract)
    assert contract['list_fields']['evidence']['min_items'] == 1
    assert contract['list_fields']['limitations']['min_items'] == 0
    assert contract['list_fields']['revision_requests']['min_items'] == 0
    assert all(isinstance(item, str) and '<' in item for examples in
               contract['format_example'].values() for item in examples)
    assert not any(char.isdigit() for examples in contract['format_example'].values()
                   for item in examples for char in item)
    contract['format_example']['evidence'].clear()
    contract['status_fields']['theme_status']['allowed'].clear()
    contract['list_fields']['evidence']['min_items'] = 0
    assert review_output_contract() == before
    json.dumps(before, allow_nan=False)


@pytest.mark.parametrize('repair_valid', [True, False])
def test_mcp_sole_repair_receives_type_diagnostics_and_exhaustion_still_stops(task, repair_valid):
    state, media = task
    original = received_object_shape()
    expected = rejection(original).diagnostics
    repaired = valid_review() if repair_valid else original
    replies = [_reply(json.dumps(original)), _reply(json.dumps(repaired))]
    client = CodexMCP(state, timeout_s=2)
    with _fake_bridge(state.output, replies) as submitted:
        if repair_valid:
            assert client.call('selected_review_v2_1', 'original review prompt', media,
                              lambda value: validate_review(value, 'test_ref')) == repaired
        else:
            with pytest.raises(ValueError, match='^model_protocol_repair_exhausted:selected_review_v2_1$') as caught:
                client.call('selected_review_v2_1', 'original review prompt', media,
                            lambda value: validate_review(value, 'test_ref'))
            assert caught.value.diagnostics == expected
        assert len(submitted) == 2
    feedback = json.loads(submitted[1]['arguments']['prompt'].split(REPAIR_MARKER, 1)[1])
    assert feedback == {'validation_error': 'review/evidence:text_required',
        'previous_response': json.dumps(original), 'validation_diagnostics': expected}
    assert state.usage()['requests'] == 2
    assert state.data['calls'][1]['repair_of'] == state.data['calls'][0]['id']
    for index, call in enumerate(state.data['calls']):
        folder = state.output / 'calls' / call['id']
        if index == 0 or not repair_valid:
            failure = json.loads((folder / 'protocol_failure.json').read_text(encoding='utf8'))
            assert failure['attempt'] == index and failure['validation_diagnostics'] == expected
            assert not (folder / 'parsed.json').exists()
