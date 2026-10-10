"""Forward-only evidence feedback, using local queue replies and no model endpoint."""
from copy import deepcopy
import json
import shutil

import pytest

from omni_story.library import pipeline
from omni_story.library.contracts import PlanEvidenceError, plan_execution_diagnostics, validate_fine, validate_plan
from omni_story.library.media import sha256_file
from omni_story.library.pipeline import CodexMCP
from omni_story.library.state import LibraryState, LibraryStopped, write_json
from test_library_pipeline import task, _fake_bridge, _reply, _request
from test_library_execute import inputs, _bridge, _fixture_responses


REPAIR_MARKER = '\n上次输出未通过本地协议校验。只修复JSON字段、ID和时间域，不得补造画面证据。'
RANGE_ERROR = 'plan:range_not_supported_by_fine_observation'
CAPTION_ERROR = 'plan:caption_event_outside_selected_range'


@pytest.fixture
def evidence():
    catalog = {'sources': [{'source_id': 'movie', 'sha256': 'movie_sha', 'duration_s': 6000}]}
    observed = {'window_id': 'window_16', 'source_id': 'movie',
        'roles': [{'role_id': role, 'identity_confirmed': True, 'state': 'visible',
                   'identity_evidence': 'model recorded appearance'}
                  for role in ('po_panda', 'father', 'panda_villagers')],
        'events': [{'local_start_s': 31, 'local_end_s': 60, 'role_ids': ['po_panda', 'father'],
                    'observed_fact': 'model recorded first action'},
                   {'local_start_s': 60, 'local_end_s': 70, 'role_ids': ['panda_villagers'],
                    'observed_fact': 'model recorded later action'}],
        'usable_ranges': [{'local_in_s': 31, 'local_out_s': 60, 'event_indices': [0],
                           'role_ids': ['po_panda', 'father'], 'continuity_notes': 'first range'},
                          {'local_in_s': 60, 'local_out_s': 70, 'event_indices': [1],
                           'role_ids': ['panda_villagers'], 'continuity_notes': 'second range'}],
        'uncertainties': []}
    window = {'window_id': 'window_16', 'source_id': 'movie', 'source_sha256': 'movie_sha',
              'source_start_s': 4920, 'source_end_s': 5010, 'status': 'watched',
              'observation': observed}
    plan = {'reference_sha256': 'ref_sha', 'focus_role_id': 'po_panda',
        'focus_role_bindings': [{'window_id': 'window_16', 'role_id': 'po_panda',
                                 'identity_evidence': 'original local identity evidence'}],
        'slots': [{'slot_id': 'slot_1', 'intended_takeaway': 'model selected action',
                   'segment_ids': ['seg_16']}],
        'segments': [{'segment_id': 'seg_16', 'slot_id': 'slot_1', 'source_id': 'movie',
                      'window_id': 'window_16', 'source_in_s': 4978, 'source_out_s': 4990,
                      'role_ids': ['po_panda', 'panda_villagers'], 'speed': 1,
                      'look': 'none', 'framing': 'fit'}],
        'audio_mode': 'silent', 'source_gain_db': -12, 'reference_gain_db': 0,
        'width': 720, 'height': 1280, 'fps': 30, 'limitations': []}
    assert validate_fine(observed, window) is observed
    return catalog, {'window_16': window}, plan


def validate(value, evidence):
    catalog, windows, _ = evidence
    return validate_plan(value, catalog, windows, 'ref_sha', 22)


def rejection(evidence):
    with pytest.raises(PlanEvidenceError) as caught:
        validate(evidence[2], evidence)
    return caught.value


def supported_plan(evidence):
    result = deepcopy(evidence[2])
    result['segments'][0].update(source_out_s=4980, role_ids=['po_panda'])
    return result


def caption_plan(evidence, interval=(35, 38), indices=(1,)):
    catalog, windows, plan = evidence
    window = windows['window_16']
    window.update(source_start_s=790, source_end_s=880)
    observation = window['observation']
    observation['events'] = [
        {'local_start_s': 31, 'local_end_s': 60, 'role_ids': ['po_panda'],
         'observed_fact': 'model recorded broad action'},
        {'local_start_s': interval[0], 'local_end_s': interval[1], 'role_ids': ['po_panda'],
         'observed_fact': 'model recorded caption event'}]
    observation['usable_ranges'] = [{'local_in_s': 31, 'local_out_s': 60,
        'role_ids': ['po_panda'], 'event_indices': [0, 1], 'continuity_notes': 'original evidence'}]
    plan['segments'][0].update(source_in_s=832, source_out_s=839, role_ids=['po_panda'],
        caption={'text': 'Original model caption', 'start_s': 0, 'end_s': 7, 'position': 'bottom',
                 'font_size': 18, 'evidence': [{'window_id': 'window_16', 'event_indices': list(indices)}]})
    return plan


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def test_range_diagnostic_locates_original_segment_without_mutating_evidence(evidence):
    catalog, windows, plan = evidence
    base = supported_plan(evidence)['segments'][0]
    plan['segments'] = [{**base, 'segment_id': f'seg_{i}'} for i in range(1, 16)] + plan['segments']
    plan['slots'][0]['segment_ids'] = [segment['segment_id'] for segment in plan['segments']]
    original = deepcopy(evidence)
    error = rejection(evidence)
    assert isinstance(error, ValueError) and str(error) == RANGE_ERROR
    diagnostic = error.diagnostics
    assert diagnostic['segment_id'] == 'seg_16' and diagnostic['window_id'] == 'window_16'
    assert diagnostic['selected_source_interval_s'] == [4978, 4990]
    assert diagnostic['selected_local_interval_s'] == [58, 70]
    assert diagnostic['requested_role_ids'] == ['po_panda', 'panda_villagers']
    assert [item['source_interval_s'] for item in diagnostic['usable_ranges']] == [[4951, 4980], [4980, 4990]]
    assert [item['contains_selected_source_interval'] for item in diagnostic['usable_ranges']] == [False, False]
    assert [item['missing_requested_role_ids'] for item in diagnostic['usable_ranges']] == [['panda_villagers'], ['po_panda']]
    assert [item['evidence'] for item in diagnostic['usable_ranges']] == windows['window_16']['observation']['usable_ranges']
    assert evidence == original
    diagnostic['requested_role_ids'].append('changed diagnostic only')
    diagnostic['usable_ranges'][0]['evidence']['role_ids'].clear()
    assert evidence == original


def test_complete_execution_table_reports_unknown_ids_and_keeps_original_rejection(evidence):
    _, windows, plan = evidence
    plan['focus_role_bindings'][0]['window_id'] = 'w_16'
    unknown = {**plan['segments'][0], 'segment_id': 'unknown', 'window_id': 'w_16'}
    plan['segments'].append(unknown)
    before = deepcopy(evidence)
    with pytest.raises(ValueError, match='^plan:focus_binding_unknown_window$'):
        validate(plan, evidence)
    diagnostic = plan_execution_diagnostics(plan, windows)
    assert diagnostic['focus_bindings'][0]['known_window_id'] is False
    assert [row['known_window_id'] for row in diagnostic['selected_segments']] == [True, False]
    assert diagnostic['watched_window_table'] == [{
        'window_id': 'window_16', 'source_id': 'movie', 'source_start_s': 4920, 'source_end_s': 5010,
        'confirmed_role_ids': ['po_panda', 'father', 'panda_villagers'],
        'usable_ranges': [
            {'source_interval_s': [4951, 4980], 'local_interval_s': [31, 60], 'role_ids': ['po_panda', 'father']},
            {'source_interval_s': [4980, 4990], 'local_interval_s': [60, 70], 'role_ids': ['panda_villagers']}]}]
    assert evidence == before
    diagnostic['watched_window_table'][0]['usable_ranges'][0]['role_ids'].clear()
    assert evidence == before


def test_complete_execution_table_exposes_all_disjoint_or_role_unsupported_selections(evidence):
    _, windows, plan = evidence
    second = {**plan['segments'][0], 'segment_id': 'another', 'source_in_s': 4970,
              'source_out_s': 4990, 'role_ids': ['po_panda']}
    good = {**supported_plan(evidence)['segments'][0], 'segment_id': 'supported'}
    plan['segments'].extend([second, good])
    plan['slots'][0]['segment_ids'] = [row['segment_id'] for row in plan['segments']]
    before = deepcopy(evidence)
    diagnostic = plan_execution_diagnostics(plan, list(windows.values()))
    assert [row['matching_single_usable_range_indices'] for row in diagnostic['selected_segments']] == [[], [], [0]]
    assert str(rejection(evidence)) == RANGE_ERROR
    assert evidence == before
    json.dumps(diagnostic, allow_nan=False)


def test_execution_diagnostic_distinguishes_range_roles_from_overlapping_event_roles(evidence):
    _, windows, _ = evidence
    plan = supported_plan(evidence)
    window = windows['window_16']
    window['observation']['usable_ranges'][0]['local_out_s'] = 70
    plan['segments'][0].update(source_in_s=4980, source_out_s=4989)
    before = deepcopy((plan, windows))
    with pytest.raises(ValueError, match='^plan:segment_role_missing_overlapping_event_evidence$'):
        validate(plan, evidence)
    selected = plan_execution_diagnostics(plan, windows)['selected_segments'][0]
    assert selected['matching_single_usable_range_indices'] == [0]
    assert selected['overlapping_event_role_ids'] == ['panda_villagers']
    assert selected['missing_overlapping_event_role_ids'] == ['po_panda']
    assert (plan, windows) == before


@pytest.mark.parametrize('bad', [None, [], {'segments': None, 'focus_role_bindings': None},
                                      {'segments': [None, {'window_id': [], 'source_in_s': 'bad'}]}])
def test_execution_table_remains_diagnostic_for_malformed_model_fields(evidence, bad):
    diagnostic = plan_execution_diagnostics(bad, evidence[1])
    assert diagnostic['watched_window_table'][0]['window_id'] == 'window_16'
    json.dumps(diagnostic, allow_nan=False)


@pytest.mark.parametrize('token', ['1e309', '-1e309', 'NaN'])
def test_execution_table_marks_nonfinite_model_values_without_changing_raw_or_verdict(evidence, tmp_path, token):
    original = '{"segments":[{"segment_id":"bad","source_in_s":' + token + ',"source_out_s":5,' \
        '"role_ids":[{"nested":[' + token + ']}]}],"focus_role_bindings":[{"role_id":{"nested":' + token + '}}]}'
    value = json.loads(original)
    diagnostic = plan_execution_diagnostics(value, evidence[1])
    marker = diagnostic['selected_segments'][0]['selected_source_interval_s'][0]
    assert marker['invalid_value'] == 'non_finite_number'
    assert diagnostic['selected_segments'][0]['role_ids'][0]['nested'][0] == marker
    assert diagnostic['focus_bindings'][0]['role_id']['nested'] == marker
    write_json(tmp_path / 'diagnostic.json', diagnostic)
    assert read(tmp_path / 'diagnostic.json') == diagnostic
    assert isinstance(value['segments'][0]['source_in_s'], float)
    assert json.dumps(value) == json.dumps(json.loads(original))
    plan = supported_plan(evidence)
    plan['segments'][0]['source_in_s'] = json.loads(token)
    with pytest.raises(ValueError) as strict_before:
        validate(plan, evidence)
    plan_execution_diagnostics(plan, evidence[1])
    with pytest.raises(ValueError) as strict_after:
        validate(plan, evidence)
    assert str(strict_before.value) == str(strict_after.value)


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='Real media tools required')
@pytest.mark.parametrize('clean', [False, True])
def test_actual_pipeline_adds_complete_table_only_to_clean_repair(inputs, clean):
    reference, library, output = inputs
    original_answer = _fixture_responses(reference, library)

    def answer(job):
        value = original_answer(job)
        if job['job_id'].split('_', 2)[2].startswith('plan_0'):
            value['focus_role_bindings'][0]['window_id'] = 'w_wrong_prefix'
            segment = value['segments'][0]
            segment['source_in_s'] = 1.0
            value['segments'].append({**segment, 'segment_id': 'second', 'source_in_s': 1.5, 'source_out_s': 4.0})
            value['slots'][0]['segment_ids'].append('second')
        return value

    with _bridge(output, answer) as jobs:
        with pytest.raises(ValueError, match='^model_protocol_repair_exhausted:plan_0$'):
            pipeline.execute(reference, library, output, span_s=3, frames=2, max_fine=1,
                max_requests=24, asr=False, model_factory=lambda state: CodexMCP(state, timeout_s=2),
                provider_config={'workflow': 'reference_rough_skill_v1'} if clean else None)
    repairs = [job for job in jobs if job['job_id'].endswith('_plan_0_repair')]
    assert len(repairs) == 1 and not (output / 'plan_0.json').exists()
    feedback = json.loads(repairs[0]['arguments']['prompt'].split(REPAIR_MARKER, 1)[1])
    assert feedback['validation_error'] == 'plan:focus_binding_unknown_window'
    if clean:
        table = feedback['validation_diagnostics']['execution_contract']
        assert [row['matching_single_usable_range_indices'] for row in table['selected_segments']] == [[], []]
        assert all(row['known_window_id'] for row in table['selected_segments'])
        assert table['focus_bindings'][0]['known_window_id'] is False
        assert table['watched_window_table'][0]['usable_ranges'][0]['source_interval_s'] == [1.5, 3.5]
    else:
        assert 'validation_diagnostics' not in feedback


def test_contained_range_with_missing_role_is_still_rejected(evidence):
    evidence[2]['segments'][0].update(source_out_s=4980)
    error = rejection(evidence)
    assert str(error) == RANGE_ERROR
    assert error.diagnostics['usable_ranges'][0]['contains_selected_source_interval'] is True
    assert error.diagnostics['usable_ranges'][0]['missing_requested_role_ids'] == ['panda_villagers']


@pytest.mark.parametrize('start,end,roles,accepted', [
    (4978, 4980, ['po_panda'], True),
    (4980, 4990, ['panda_villagers'], True),
    (4978, 4990, ['po_panda'], False),
    (4978, 4990, ['panda_villagers'], False),
    (4950.999, 4980.001, ['po_panda'], True),
    (4950.9989, 4980, ['po_panda'], False),
    (4978, 4980.0011, ['po_panda'], False),
])
def test_original_single_usable_range_and_tolerance_rules(evidence, start, end, roles, accepted):
    plan = evidence[2]
    plan['segments'][0].update(source_in_s=start, source_out_s=end, role_ids=roles)
    plan['focus_role_bindings'][0]['role_id'] = roles[0]
    if accepted:
        assert validate(plan, evidence) is plan
    else:
        assert str(rejection(evidence)) == RANGE_ERROR


@pytest.mark.parametrize('interval,accepted', [((35, 38), False), ((38, 42), False),
                                            ((49, 52), False), ((48.999, 52), True)])
def test_caption_overlap_still_excludes_touching_endpoints(evidence, interval, accepted):
    plan = caption_plan(evidence, interval, indices=(0, 1))
    original = deepcopy(evidence)
    if accepted:
        assert validate(plan, evidence) is plan
    else:
        error = rejection(evidence)
        assert isinstance(error, ValueError) and str(error) == CAPTION_ERROR
        assert error.diagnostics['selected_local_interval_s'] == [42, 49]
        assert error.diagnostics['caption_evidence'] == {'window_id': 'window_16', 'event_indices': [0, 1]}
        cited = error.diagnostics['cited_events']
        assert [item['overlaps_selected_interval'] for item in cited] == [True, False]
        assert cited[1]['source_interval_s'] == [790 + interval[0], 790 + interval[1]]
        assert cited[1]['evidence'] == evidence[1]['window_16']['observation']['events'][1]
    assert evidence == original


def test_diagnostics_project_only_validated_json_fields(evidence):
    caption_plan(evidence)
    observation = evidence[1]['window_16']['observation']
    observation['events'][1]['unused_extension'] = object()
    observation['usable_ranges'][0]['unused_extension'] = float('nan')
    evidence[2]['segments'][0]['caption']['evidence'][0]['unused_extension'] = object()
    diagnostic = rejection(evidence).diagnostics
    json.dumps(diagnostic, allow_nan=False)
    assert all('unused_extension' not in item['evidence'] for item in diagnostic['cited_events'])
    assert 'unused_extension' not in diagnostic['usable_ranges'][0]['evidence']
    assert 'unused_extension' not in diagnostic['caption_evidence']


def test_unrelated_validation_error_keeps_plain_value_error(evidence):
    plan = supported_plan(evidence)
    plan['segments'][0]['source_out_s'] = 5011
    with pytest.raises(ValueError) as caught:
        validate(plan, evidence)
    assert str(caught.value) == 'plan:segment_outside_watched_window'
    assert not hasattr(caught.value, 'diagnostics')


def test_new_sole_repair_prompt_and_failure_include_diagnostics(task, evidence):
    state, media = task
    client = CodexMCP(state, timeout_s=2)
    expected = rejection(evidence).diagnostics
    good = supported_plan(evidence)
    with _fake_bridge(state.output, [_reply(json.dumps(evidence[2])), _reply(json.dumps(good))]) as submitted:
        assert client.call('plan_1', 'original forward prompt', media, lambda value: validate(value, evidence)) == good
        assert len(submitted) == 2
    failure = read(state.output / 'calls' / state.data['calls'][0]['id'] / 'protocol_failure.json')
    assert failure['error'] == RANGE_ERROR and failure['validation_diagnostics'] == expected
    repair_prompt = submitted[1]['arguments']['prompt']
    assert repair_prompt.startswith('original forward prompt' + REPAIR_MARKER)
    feedback = json.loads(repair_prompt.split(REPAIR_MARKER, 1)[1])
    assert feedback == {'validation_error': RANGE_ERROR, 'previous_response': json.dumps(evidence[2]),
                        'validation_diagnostics': expected}
    assert state.data['calls'][1]['repair_of'] == state.data['calls'][0]['id']
    assert state.usage()['requests'] == 2


@pytest.mark.parametrize('repair_valid', [True, False])
def test_cached_different_diagnostics_preserve_paid_files_and_saved_repair(task, evidence, repair_valid):
    state, media = task
    client = CodexMCP(state, timeout_s=2)
    replies = [_reply(json.dumps(evidence[2])), _reply(json.dumps(supported_plan(evidence) if repair_valid else evidence[2]))]
    with _fake_bridge(state.output, replies):
        if repair_valid:
            client.call('plan_1', 'original prompt', media, lambda value: validate(value, evidence))
        else:
            with pytest.raises(ValueError, match='^model_protocol_repair_exhausted:plan_1$'):
                client.call('plan_1', 'original prompt', media, lambda value: validate(value, evidence))
    protected = {path: path.read_bytes() for path in (state.output / 'calls').glob('*/*') if path.is_file()}
    queue_bytes = {path: path.read_bytes() for path in client.queue.glob('*') if path.is_file()}

    def changed_validator(value):
        try:
            validate(value, evidence)
        except PlanEvidenceError as error:
            error.diagnostics['forward_annotation'] = 'new local diagnostic only'
            raise

    resumed = LibraryState(state.output, {'reference_sha256': 'test_ref'}, max_requests=8)
    cached = CodexMCP(resumed, timeout_s=0.1)
    if repair_valid:
        assert cached.call('plan_1', 'changed forward prompt', media, changed_validator) == supported_plan(evidence)
    else:
        with pytest.raises(ValueError, match='^model_protocol_repair_exhausted:plan_1$') as caught:
            cached.call('plan_1', 'changed forward prompt', media, changed_validator)
        assert caught.value.diagnostics['forward_annotation'] == 'new local diagnostic only'
    assert resumed.usage()['requests'] == 2
    assert all(path.read_bytes() == data for path, data in protected.items())
    assert {path: path.read_bytes() for path in client.queue.glob('*') if path.is_file()} == queue_bytes
    diagnostics = list((state.output / 'artifacts/cached_reply_validation').glob('*.json'))
    assert len(diagnostics) == (1 if repair_valid else 2)
    assert all(read(path)['validation_diagnostics']['forward_annotation'] == 'new local diagnostic only'
               for path in diagnostics)


def test_historical_known_failure_cannot_gain_new_repair_or_overwrite_old_failure(task, evidence):
    state, media = task
    call, folder = state.begin_call('original', _request(media, 'fixed prompt'))
    state.complete_call(call, _reply(json.dumps(evidence[2])))
    write_json(folder / 'protocol_failure.json', {'error': 'old generic failure only'})
    protected = {path: path.read_bytes() for path in folder.iterdir() if path.is_file()}
    client = CodexMCP(state)
    with pytest.raises(LibraryStopped, match='^historical_format_failure_no_new_repair:'):
        client.call('later', 'fixed prompt', media, lambda value: validate(value, evidence))
    assert state.usage()['requests'] == 1 and not list(client.queue.glob('*.request.json'))
    assert all(path.read_bytes() == data for path, data in protected.items())
    diagnostics = list((state.output / 'artifacts/cached_reply_validation').glob('*.json'))
    assert len(diagnostics) == 1 and read(diagnostics[0])['validation_diagnostics'] == rejection(evidence).diagnostics


def test_final_exhaustion_keeps_original_string_and_last_diagnostic(task, evidence):
    state, media = task
    client = CodexMCP(state, timeout_s=2)
    original = json.dumps(evidence[2])
    repaired = supported_plan(evidence)
    repaired['segments'][0]['role_ids'].append('panda_villagers')
    with _fake_bridge(state.output, [_reply(original), _reply(json.dumps(repaired))]) as submitted:
        with pytest.raises(ValueError) as caught:
            client.call('plan_1', 'prompt', media, lambda value: validate(value, evidence))
        assert len(submitted) == 2
    assert str(caught.value) == 'model_protocol_repair_exhausted:plan_1'
    assert isinstance(caught.value.__cause__, PlanEvidenceError)
    assert caught.value.diagnostics['selected_source_interval_s'] == [4978, 4980]
    assert caught.value.diagnostics['usable_ranges'][0]['contains_selected_source_interval'] is True
    assert state.usage()['requests'] == 2


def test_ordinary_errors_keep_original_repair_feedback_keys(task):
    state, media = task

    def validator(value):
        if not value['valid']:
            raise ValueError('ordinary_error')

    with _fake_bridge(state.output, [_reply('{"valid":false}'), _reply('{"valid":true}')]) as submitted:
        assert CodexMCP(state, timeout_s=2).call('plan_1', 'prompt', media, validator) == {'valid': True}
    feedback = json.loads(submitted[1]['arguments']['prompt'].split(REPAIR_MARKER, 1)[1])
    assert feedback == {'validation_error': 'ordinary_error', 'previous_response': '{"valid":false}'}


def test_new_pipeline_failure_report_includes_diagnostics_and_preserves_old_failure(tmp_path, monkeypatch, evidence):
    media = tmp_path / 'synthetic.mp4'
    media.write_bytes(b'No decoder or model is used by this test.')
    source = {'source_id': 'synthetic', 'path': str(media), 'sha256': sha256_file(media),
              'duration_s': 2.0, 'audio_stream_index': None}
    monkeypatch.setattr(pipeline, '_catalog', lambda *args: {'sources': [source]})
    output = tmp_path / 'run'
    output.mkdir()
    old_failure = b'{"error":"old rejected plan"}\n'
    (output / 'failure.json').write_bytes(old_failure)
    inner = rejection(evidence)
    exhausted = ValueError('model_protocol_repair_exhausted:plan_1')
    exhausted.diagnostics = inner.diagnostics

    class Model:
        def call(self, *args, **kwargs):
            raise exhausted

    with pytest.raises(ValueError, match='^model_protocol_repair_exhausted:plan_1$'):
        pipeline.execute(media, media, output, asr=False, model_factory=lambda state: Model(),
                         failure_report_name='failure_remaining_candidate_v1.json')
    assert (output / 'failure.json').read_bytes() == old_failure
    report = read(output / 'failure_remaining_candidate_v1.json')
    assert report['validation_diagnostics'] == inner.diagnostics
    assert report['error'] == str(exhausted) and report['type'] == 'ValueError'
    assert report['usage']['requests'] == 0 and report['no_automatic_paid_replay'] is True
