"""Recover mechanical JSON defects without losing received plans or inventing identity."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from omni_story.library import contracts, pipeline, replay_check
from test_protocol_feedback import harness, validate_fields


FIXTURES = Path(__file__).parent / 'fixtures'


def read_fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


def reply_text(name):
    return '\n'.join(row['text'] for row in read_fixture(name)['result']['content']
                     if row.get('type') == 'text')


def test_real_attempt_one_recovers_complete_plan_and_records_only_stray_quote():
    text = reply_text('s8_glm_224_plan_0_repair.json')
    assert len(text) == 8177
    assert text.startswith('```json\n') and text.endswith('}"\n```')
    expected = json.loads(text.removeprefix('```json\n').removesuffix('"\n```'))
    notes = []
    assert contracts.parse_model_json(text, normalisations=notes) == expected
    assert notes == [dict(kind='stray_trailing_quote', count=1, removed_text='"')]
    assert len(expected['segments']) > 0 and len(expected['audio_draft']) > 0


@pytest.mark.parametrize('text', [
    {'a': 1}, '{"a":1}', ' \n {"a":1} \t',
    '```json\n{"a":1}\n```', '说明\n```\n{"a":1}\n```\n结束',
])
def test_strict_valid_replies_do_not_record_normalisation(text):
    notes = []
    result = contracts.parse_model_json(text, normalisations=notes)
    assert result == {'a': 1}
    assert notes == []
    if isinstance(text, dict):
        assert result is text


def test_trailing_comma_pass_preserves_strings_and_escapes():
    expected = dict(text='a,] quote: " comma: ,} slash: \\', values=[1, dict(text='\\",]')])
    valid = json.dumps(expected, ensure_ascii=False)
    notes = []
    assert contracts.parse_model_json(valid, notes) == expected
    assert notes == []
    assert contracts.parse_model_json(valid[:-1] + ', \n\t}', notes) == expected
    assert notes == [dict(kind='trailing_comma', count=1)]


@pytest.mark.parametrize('suffix', ['"', "'", '`', '\n"\t\' `'])
def test_only_whitespace_and_stray_quotes_can_follow_outer_object(suffix):
    notes = []
    assert contracts.parse_model_json('{"a":1}' + suffix, notes) == {'a': 1}
    assert notes == [dict(kind='stray_trailing_quote',
                          count=sum(char in "\"'`" for char in suffix),
                          removed_text=suffix)]


def test_comma_and_quote_passes_record_successful_changes_in_order():
    notes = []
    assert contracts.parse_model_json('{"a":[{"b":1, }, \n],\t}"', notes) == {'a': [{'b': 1}]}
    assert notes == [dict(kind='trailing_comma', count=3),
                     dict(kind='stray_trailing_quote', count=1, removed_text='"')]


@pytest.mark.parametrize('fixture,offset', [
    ('s12_glm_002_reference_repair.json', 2136),
    ('s12_glm_003_reference_repair2.json', 2089),
])
def test_real_early_outer_close_preserves_complete_reference_and_strategy(fixture, offset):
    reply = read_fixture(fixture)
    text = reply['model_text']
    notes = []
    result = contracts.parse_model_json(text, notes)
    assert text[offset] == '}'
    assert result == json.loads(text[:offset] + text[offset + 1:])
    assert notes == [dict(kind='early_outer_close', offset=offset)]
    assert list(result) == ['viewer_takeaway', 'reference_sha256', 'theme',
        'intended_takeaway', 'visible_evidence', 'editing_methods', 'strategy',
        'uncertainties', 'value_judgment', 'cultural_context']
    assert result['uncertainties'] and result['value_judgment']['evidence']

    # The redacted evidence has no measured media duration. 37.9 is only the
    # replies' largest annotated end; validation here is conditional on that bound.
    ref = dict(sha256='e5c58df93f8f1e88f8bb9ee8e75f0031633fc53cd817e6a82cc5b256d4368c85',
               duration_s=37.9)
    assert contracts.validate_reference(deepcopy(result), ref['sha256'], ref['duration_s'],
                                        require_value_fields=True)
    validate = replay_check.nested_validator(pipeline.execute, 'validate_current_reference',
        creative_audio=True, ref=ref, config={})
    validate(deepcopy(result))
    assert contracts.validate_creative_reference_strategy(result, music_bed_configured=False) == {
        'adaptation_mode': 'reinterpret', 'speech_mode': 'mixed', 'timing_driver': 'action'}


@pytest.mark.parametrize('count', [1, 2, 3])
def test_early_outer_close_repeats_at_most_three_times_and_records_original_offsets(count):
    text = '{"a":{"nested":1}}' + ''.join(f', "k{i}":{i}}}' for i in range(count))
    offsets = [index for index, char in enumerate(text[:-1])
               if char == '}' and text[index + 1:].startswith(',')]
    notes = []
    assert contracts.parse_model_json(text, notes) == {
        'a': {'nested': 1}, **{f'k{i}': i for i in range(count)}}
    assert notes == [dict(kind='early_outer_close', offset=offset) for offset in offsets]


def test_early_outer_close_keeps_existing_fence_and_comma_rules():
    notes = []
    assert contracts.parse_model_json('说明\n```json\n {"a":1,}, "b":2}\n```\n结束', notes) == {
        'a': 1, 'b': 2}
    assert notes == [dict(kind='trailing_comma', count=1),
                     dict(kind='early_outer_close', offset=7)]


def test_early_outer_close_checks_only_top_level_duplicates_and_decodes_json_keys():
    notes = []
    assert contracts.parse_model_json('{"a":{"x":1,"x":2}}, "\\u0062":2}', notes) == {
        'a': {'x': 2}, 'b': 2}
    assert notes == [dict(kind='early_outer_close', offset=18)]


@pytest.mark.parametrize('text', [
    '{"a":1}, "a":2}', '{"a":1}, "\\u0061":2}',
    '{"a":1,"a":2}, "b":3}', '{"a":1}, "b":2}, "a":3}',
    '{"a":1}}{"a":1}', '{"a":1}, 2:3}', '{"a":1}, "b" 2}',
    '{"a":1}, "b":}', '{"a":1}, "b":2} trailing',
    '{"a":1}, "b":2}"',
    '{"a":1}, "b":2}, "c":3}, "d":4}, "e":5}',
    'prose {"a":1}, "b":2}', '[{"a":1}, "b":2}',
])
def test_early_outer_close_rejects_ambiguous_or_incomplete_reattachment(text):
    notes = []
    with pytest.raises(json.JSONDecodeError) as original:
        json.loads(text)
    with pytest.raises(json.JSONDecodeError) as caught:
        contracts.parse_model_json(text, notes)
    assert str(caught.value) == str(original.value)
    assert caught.value.doc == original.value.doc
    assert notes == []


@pytest.mark.parametrize('text', [
    '{"a":1}"x', '{"a":1}{"b":2}', '{"a":1}说明', '{"a":1}}',
    '{"a":1}{', '{"a":1,}"x', '{"outer":{"a":1,}', '{"a":[1,,]}',
    '{,}', '{"a":[,]}', "{'a':1,}", 'prose {"a":1}"',
    '[{"a":1,}]', '[{"a":1,}', '[{"a":1}]"',
])
def test_rejected_syntax_keeps_original_strict_error_and_records_nothing(text):
    notes = []
    with pytest.raises(json.JSONDecodeError) as original:
        json.loads(text)
    with pytest.raises(json.JSONDecodeError) as caught:
        contracts.parse_model_json(text, notes)
    assert str(caught.value) == str(original.value)
    assert caught.value.doc == original.value.doc
    assert notes == []


@pytest.mark.parametrize('text', [
    '```json\n{"a":1}"\n```\n```json\n{"b":2}\n```',
    '```json {"a":1}"```', '```python\n{"a":1}"\n```',
    '```json\n{"a":1}"',
])
def test_existing_fence_rules_still_reject_invalid_wrappers(text):
    notes = []
    with pytest.raises(ValueError, match='model_response:single_json_object_required'):
        contracts.parse_model_json(text, notes)
    assert notes == []


@pytest.mark.parametrize('time_normalization', [False, True])
@pytest.mark.parametrize('text,expected_notes', [
    ('{"a":1}', []),
    ('{"a":1,}', [dict(kind='trailing_comma', count=1)]),
    ('{"a":1}"', [dict(kind='stray_trailing_quote', count=1, removed_text='"')]),
])
def test_model_call_records_normalisation_separately_in_both_branches(
        tmp_path, monkeypatch, time_normalization, text, expected_notes):
    mcp, media, sent = harness(tmp_path, monkeypatch, [text])
    mcp.state.normalize_observation_times = time_normalization

    def validate(value):
        assert value == {'a': 1}

    assert mcp.call('plan_0', 'prompt', media, validate) == {'a': 1}
    call = mcp.state.data['calls'][0]
    folder = mcp.output / 'calls' / call['id']
    artifact = folder / 'json_normalisation.json'
    assert artifact.exists() == bool(expected_notes)
    if expected_notes:
        assert json.loads(artifact.read_text()) == expected_notes
    assert json.loads((folder / 'parsed.json').read_text()) == {'a': 1}
    assert json.loads((folder / 'response.json').read_text())['result']['content'][0]['text'] == text
    assert mcp.call('plan_0', 'prompt', media, validate) == {'a': 1}
    assert len(sent) == 1
    if expected_notes:
        assert json.loads(artifact.read_text()) == expected_notes


@pytest.mark.parametrize('time_normalization', [False, True])
def test_model_call_records_early_outer_close(time_normalization, tmp_path, monkeypatch):
    text = '{"a":1}, "b":2}'
    mcp, media, sent = harness(tmp_path, monkeypatch, [text])
    mcp.state.normalize_observation_times = time_normalization

    def validate(value):
        assert value == {'a': 1, 'b': 2}

    assert mcp.call('reference', 'prompt', media, validate) == {'a': 1, 'b': 2}
    folder = mcp.output / 'calls' / mcp.state.data['calls'][0]['id']
    assert json.loads((folder / 'json_normalisation.json').read_text()) == [
        dict(kind='early_outer_close', offset=6)]
    assert json.loads((folder / 'response.json').read_text())['result']['content'][0]['text'] == text
    assert len(sent) == 1


@pytest.mark.parametrize('time_normalization', [False, True])
def test_model_call_records_json_normalisation_even_if_validation_requires_repair(
        tmp_path, monkeypatch, time_normalization):
    mcp, media, sent = harness(tmp_path, monkeypatch,
        ['{"first":true}"', dict(first=True, second=True)])
    mcp.state.normalize_observation_times = time_normalization
    assert mcp.call('stage', 'prompt', media, validate_fields) == dict(first=True, second=True)
    assert len(sent) == 2
    first, second = mcp.state.data['calls']
    first_folder = mcp.output / 'calls' / first['id']
    assert json.loads((first_folder / 'json_normalisation.json').read_text()) == [
        dict(kind='stray_trailing_quote', count=1, removed_text='"')]
    assert json.loads((first_folder / 'protocol_failure.json').read_text())['error'] == 'second:text_required'
    assert not (mcp.output / 'calls' / second['id'] / 'json_normalisation.json').exists()


def test_real_attempt_two_already_drops_unused_unbound_cast_then_rejects_short_line_window():
    context = read_fixture('s8_plan_evidence.json')
    windows = context['watched_windows']
    plan = contracts.parse_model_json(reply_text('s8_glm_225_plan_0_repair2.json'))
    original = deepcopy(plan)
    character = plan['cast'][2]
    assert character['cast_id'] == 'tigress_snow'
    assert not any(line.get('cast_id') == character['cast_id'] for line in plan['audio_draft'])
    observed = {(w['window_id'], r['role_id']): r for w in windows for r in w['observation']['roles']}
    assert not any(r.get('entity_id') == character['entity_id'] for r in observed.values())
    # Both declared appearances are visible, but have no confirmed entity to copy.
    for binding in character['bindings']:
        role = observed[binding['window_id'], binding['role_id']]
        assert role['identity_confirmed'] is True
        assert not role.get('entity_id')
    reference = context['reference']
    strategy = contracts.reference_strategy(reference, creative_audio=True)
    validate = replay_check.nested_validator(pipeline.execute, 'validate_current_plan',
        context=context, creative_audio=True, editing_reference=None, editing_v2=False,
        maximum_segments=32, progress_driven=True,
        ref=dict(sha256=reference['reference_sha256'], duration_s=context['reference_duration_s'],
                 audio_stream_index=context['reference_audio_stream_index']),
        reference_reading=reference, semantic_audit=False, semantic_pipeline=None,
        sources=context['catalog'], strategy=strategy,
        visual_only=strategy['adaptation_mode'] == 'reinterpret', windows=windows)
    with pytest.raises(ValueError, match='^audio_line_window_below_minimum$') as caught:
        validate(plan)
    contracts.validate_task_cast(plan['cast'], {w['window_id']: w for w in windows})
    assert [c['cast_id'] for c in plan['cast']] == ['po_main', 'tigress_villain']
    removed = [change for change in plan['plan_normalisations']
               if change['reason'] == 'drop_unreferenced_unbound_cast']
    assert len(removed) == 1
    assert removed[0]['path'] == '$.cast[2]'
    assert removed[0]['before']['cast_id'] == character['cast_id']
    assert removed[0]['before']['bindings'] == [] and removed[0]['after'] is None
    assert [line['text'] for line in plan['audio_draft']] == [line['text'] for line in original['audio_draft']]
    assert plan['segments'] == original['segments']
    assert (len(plan['segments']), len(plan['audio_draft']), len(plan['slots'])) == (11, 21, 7)
    violation = caught.value.violation
    assert violation['path'] == '$.audio_draft[5]'
    assert plan['audio_draft'][5]['id'] == 'ad_05'
    assert plan['audio_draft'][5]['text'] == '狐狸还是快递盒？'
    assert violation['value'] == pytest.approx(1.2)
    assert violation['expected'] == pytest.approx(1.26)
