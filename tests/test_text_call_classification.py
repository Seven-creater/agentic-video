"""S27 transport receipts and read-only historical admission; all agents are fake."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
from types import MethodType, SimpleNamespace

import pytest

from omni_story.library import pipeline, prompts, server_cli as cli
from omni_story.library.media import sha256_file
from omni_story.library.opencode_provider import TEXT_MAX_OUTPUT_TOKENS
from omni_story.library.state import LibraryState, LibraryStopped, json_sha, write_json
from test_picture_aware import aware, ReachedTTS
from test_resume_from import failed, read
from test_text_call_transport import model
from test_v4_replay_regressions import file_bytes


EVIDENCE = read(Path(__file__).parent / 'fixtures/s27_text_outcomes.json')


def emit(kwargs, events):
    kwargs['stdout'].write(('\n'.join(json.dumps(event) for event in events) + '\n').encode())
    kwargs['stdout'].flush()


def media_request(media):
    return dict(tool='analyze_video', arguments=dict(video_source=str(media), prompt='Fine stage'),
        media_sha256=sha256_file(media), observation_scope=dict(kind='continuous_window',
            source_sha256=sha256_file(media), source_start_s=0, source_end_s=4))


@pytest.mark.parametrize('reason', ['length', 'stop', 'error', 'other'])
@pytest.mark.parametrize('exit_kind', ['success', 'timeout', 'crash', 'oserror'])
def test_completed_step_is_received_even_without_text(model, monkeypatch, reason, exit_kind):
    events = deepcopy(EVIDENCE['completed_without_text'])
    events[-1]['part']['reason'] = reason
    def run(argv, **kwargs):
        emit(kwargs, events)
        if exit_kind == 'timeout':
            raise subprocess.TimeoutExpired(argv, 1)
        if exit_kind == 'crash':
            raise subprocess.CalledProcessError(1, argv)
        if exit_kind == 'oserror':
            raise OSError('post-receipt failure')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(subprocess, 'run', run)
    request = dict(tool='text_completion', arguments=dict(prompt='Only JSON'))
    call, reply = model._submit_text('silent_review_1', request)
    assert model.state.data['calls'][0]['status'] == 'received'
    assert reply['result']['content'] == [dict(type='text', text='')]
    assert reply['finish_reason'] == reason
    assert reply['usage'] == dict(prompt_tokens=8874, completion_tokens=12,
                                 completion_tokens_details=dict(reasoning_tokens=8180))
    assert not (model.output / 'calls' / call['id'] / 'failure.json').exists()


def test_empty_received_reply_uses_format_repair_and_replays_without_sending(model, monkeypatch):
    sends = []
    def run(argv, **kwargs):
        config = json.loads(kwargs['env']['OPENCODE_CONFIG_CONTENT'])
        assert config['provider']['zhipuai-coding-plan']['models']['glm-5.3-flash']['limit']['output'] == TEXT_MAX_OUTPUT_TOKENS
        sends.append(argv)
        events = EVIDENCE['completed_without_text'] if len(sends) == 1 else [
            dict(type='text', part=dict(text='{"ok":true}')),
            dict(type='step_finish', part=dict(reason='stop'))]
        emit(kwargs, events)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(subprocess, 'run', run)
    assert model.call_text('silent_review_1', 'Only JSON', lambda value: None) == {'ok': True}
    assert model.call_text('silent_review_1', 'Ignored on replay', lambda value: None) == {'ok': True}
    assert len(sends) == 2
    original, repair = model.state.data['calls']
    assert repair['repair_of'] == original['id'] and original['status'] == repair['status'] == 'received'
    folder = model.output / 'calls' / original['id']
    failure = read(folder / 'protocol_failure.json')
    assert failure['output_limit'] == dict(finish_reason='length', completion_tokens=12,
        reasoning_tokens=8180, content_characters=0)
    assert read(folder / 'text_generation.json')['max_output_tokens'] == 32768
    for call in model.state.data['calls']:
        request = read(model.output / 'calls' / call['id'] / 'request.json')
        assert request['tool'] == 'text_completion'
        assert 'media_sha256' not in request and 'observation_scope' not in request
        assert set(request['arguments']) == {'prompt'}


@pytest.mark.parametrize('exit_kind', ['success', 'timeout', 'crash'])
def test_started_without_finished_step_stays_uncertain_and_never_replays(model, monkeypatch, exit_kind):
    sends = []
    def run(argv, **kwargs):
        sends.append(argv)
        emit(kwargs, [*EVIDENCE['completed_without_text'][:1],
                      dict(type='text', part=dict(text='{"partial":true}'))])
        if exit_kind == 'timeout':
            raise subprocess.TimeoutExpired(argv, 1)
        if exit_kind == 'crash':
            raise subprocess.CalledProcessError(1, argv)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(subprocess, 'run', run)
    with pytest.raises(LibraryStopped, match='text_check_outcome_unknown_no_replay:'):
        model.call_text('silent_review_0', 'Only JSON', lambda value: None)
    assert model.state.data['calls'][0]['status'] == 'uncertain'
    with pytest.raises(LibraryStopped, match='recorded_request_not_received_no_replay:'):
        model.call_text('silent_review_0', 'Only JSON', lambda value: None)
    assert len(sends) == 1


def test_picture_first_empty_receipts_keep_strict_gate(model, monkeypatch):
    from test_picture_first import visual_script
    monkeypatch.setattr(pipeline, '_silent_blind_reading', lambda *a: {})
    def run(argv, **kwargs):
        emit(kwargs, EVIDENCE['completed_without_text'])
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(subprocess, 'run', run)
    with pytest.raises(ValueError, match='model_protocol_repair_exhausted:silent_check_0'):
        pipeline._silent_story_check(model.state, model, dict(sha256='render'), visual_script(), 0, model.output)
    assert all(call['status'] == 'received' for call in model.state.data['calls'])
    assert not (model.output / 'silent_review.json').exists()


def test_advisory_empty_replies_exhaust_repairs_and_run_reaches_tts(aware, monkeypatch):
    sends = []
    call_text = pipeline.CodexMCP.call_text
    def production_text(self, *args):
        self.queue = self.output / 'mcp_queue'
        self.queue.mkdir(exist_ok=True)
        self.executable, self.package_root, self.timeout_s = '/fake/opencode', self.output, 1
        self._submit_text = MethodType(pipeline.CodexMCP._submit_text, self)
        return call_text(self, *args)
    def run(argv, **kwargs):
        sends.append(argv)
        emit(kwargs, EVIDENCE['completed_without_text'])
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(pipeline.CodexMCP, 'call_text', production_text)
    monkeypatch.setattr(subprocess, 'run', run)
    with pytest.raises(ReachedTTS):
        aware.chain()
    review = read(aware.output / 'silent_review.json')
    assert review['review_unavailable']['reason'] == 'model_protocol_repair_exhausted:silent_review_0'
    assert not review['revision_suggested'] and review['score'] is None
    assert aware.events[-1] == 'TTS' and 'plan_1' not in aware.sent and len(sends) == 3
    saved = read(aware.output / 'library_state.json')
    assert all(call['status'] == 'received' for call in saved['calls'])
    state = LibraryState(aware.output, saved['input_lock'], max_requests=None)
    media = next(aware.output.rglob('actual.mp4'))
    state.begin_call('fine_stage', media_request(media))


@pytest.mark.parametrize('stale_media_binding', [False, True])
def test_uncertain_text_cannot_poison_media_but_cannot_be_resent(tmp_path, stale_media_binding):
    state = LibraryState(tmp_path / 'task', dict(configuration={}))
    state.enable_independent_continuation()
    media = tmp_path / 'rough.mp4'
    media.write_bytes(b'rough render bytes')
    text = dict(tool='text_completion', arguments=dict(prompt='JSON only'))
    if stale_media_binding:
        text.update(media_sha256=sha256_file(media), observation_scope=media_request(media)['observation_scope'])
    call, _ = state.begin_call('silent_review_1', text)
    state.fail_call(call, 'timeout', uncertain=True)
    with pytest.raises(LibraryStopped, match='request_already_recorded|independent_request_requires_bound_media_scope'):
        state.begin_call('silent_review_1', text)
    fine, _ = state.begin_call('fine_stage', media_request(media))
    state.complete_call(fine, {})
    assert state.data['calls'][0]['status'] == 'uncertain'


def test_unknown_media_and_submitted_calls_keep_their_guards(tmp_path):
    media = tmp_path / 'rough.mp4'
    media.write_bytes(b'rough render bytes')
    state = LibraryState(tmp_path / 'task', dict(configuration={}))
    state.enable_independent_continuation()
    request = media_request(media)
    call, _ = state.begin_call('blind_1', request)
    with pytest.raises(LibraryStopped, match='request_outcome_unknown_no_replay'):
        state.begin_call('fine_stage', dict(request, arguments=dict(request['arguments'], prompt='new')))
    state.fail_call(call, 'timeout', uncertain=True)
    with pytest.raises(LibraryStopped, match='unknown_media_must_not_be_resubmitted'):
        state.begin_call('fine_stage', dict(request, arguments=dict(request['arguments'], prompt='new')))


def old_text_record(case, kind):
    prompt = prompts.silent_review_prompt({}, dict(theme='旧主旨'), [], render_sha256='render', legacy=True)
    request = dict(tool='text_completion', arguments=dict(prompt=prompt))
    call = dict(id='glm_007_silent_review_0', name='silent_review_0', status='uncertain',
                request_sha256=json_sha(request))
    folder = case.task / 'calls' / call['id']
    write_json(folder / 'request.json', request)
    events = EVIDENCE['completed_without_text'] if kind == 'completed' else []
    if kind == 'started':
        events = EVIDENCE['completed_without_text'][:1]
    if kind == 'malformed':
        (folder / 'agent_events.jsonl').write_text('not an event\n')
    elif kind != 'missing':
        (folder / 'agent_events.jsonl').write_text(''.join(json.dumps(event) + '\n' for event in events))
    failure = EVIDENCE['completed_failure'] if kind in {'completed', 'empty_other'} else EVIDENCE['spawn_failure']
    write_json(folder / 'failure.json', failure)
    case.state['calls'].append(call)
    case.state['request_count'] += 1
    case.state['input_lock']['configuration']['creative_style'] = 'picture_aware'
    write_json(case.task / 'library_state.json', case.state)
    return call, prompt


@pytest.mark.parametrize('kind,status', [('spawn', 'failed_known'), ('completed', 'received_invalid')])
def test_old_records_admitted_read_only_recorded_and_never_resent(failed, monkeypatch, kind, status):
    call, _ = old_text_record(failed, kind)
    before = file_bytes(failed.task)
    baseline = cli.parent_baseline(failed.task, failed.task / 'evaluations/next')
    resume = cli.resume_config(failed.task, failed.ref)
    assert baseline['request_count'] == len(failed.state['calls']) and baseline['unknown_inputs'] == []
    for record in (baseline, resume['resume_from']):
        receipt = record['text_call_reinterpretations'][0]
        assert receipt['effective_status'] == status and receipt['previous_status'] == 'uncertain'
        assert receipt['never_resend'] and receipt['call_id'] == call['id']
        assert all(sha256_file(row['path']) == row['sha256'] for row in receipt['evidence'])
    baseline_state = LibraryState(failed.root / 'baseline_child', dict(configuration=dict(parent_baseline=baseline)))
    baseline_model = pipeline.CodexMCP.__new__(pipeline.CodexMCP)
    baseline_model.state, baseline_model.output = baseline_state, baseline_state.output
    request = read(failed.task / 'calls' / call['id'] / 'request.json')
    with pytest.raises(LibraryStopped, match='text_check_historical_' + status + '_no_replay:'):
        baseline_model._submit_text('silent_review_0', request)
    assert baseline_state.data['request_count'] == 0
    child = LibraryState(failed.root / 'child', dict(configuration=resume))
    plan = dict(segments=[dict(segment_id='seg', window_id='w', source_in_s=100, source_out_s=104)],
        audio_draft=[dict(id='line', text='两人商量。', mode='narration', picture_relation='additive', segment_ids=['seg'])])
    window = dict(window_id='w', source_id='film', source_start_s=100, source_end_s=110, observation=dict(events=[]))
    monkeypatch.setattr(pipeline, '_silent_blind_reading', lambda *a: {})
    glm = SimpleNamespace(call_text=lambda *a: pytest.fail('old text call must never be sent'))
    review = pipeline._picture_aware_review(child, glm, dict(sha256='render'), plan, dict(theme='主旨'),
                                           [window], 0, failed.root)
    assert status + '_no_replay:' in review['review_unavailable']['reason']
    assert review['review_unavailable']['call_id'] == call['id'] and not review['revision_suggested']
    receipt = child.data['artifacts']['text_call_reinterpretation'][0]
    assert read(receipt['path'])['effective_status'] == status
    assert child.data['request_count'] == 0 and file_bytes(failed.task) == before


def test_resume_reinterpretation_pins_original_event_bytes(failed):
    call, prompt = old_text_record(failed, 'completed')
    resume = cli.resume_config(failed.task, failed.ref)
    child = LibraryState(failed.root / 'child', dict(configuration=resume))
    log = failed.task / 'calls' / call['id'] / 'agent_events.jsonl'
    log.write_text(log.read_text() + '\n')
    with pytest.raises(LibraryStopped, match='resume_from_silent_text_evidence_changed'):
        pipeline._resume_silent_reply(child, 'silent_review_0', prompt, lambda value: None)
    assert child.data['request_count'] == 0


@pytest.mark.parametrize('kind', ['started', 'missing', 'malformed', 'empty_other'])
def test_old_records_without_proof_still_block(failed, kind):
    call, prompt = old_text_record(failed, kind)
    before = file_bytes(failed.task)
    with pytest.raises(LibraryStopped, match='parent_text_check_outcome_unknown_no_replay:' + call['id']):
        cli.parent_baseline(failed.task, failed.task / 'evaluations/next')
    resume = cli.resume_config(failed.task, failed.ref)
    assert 'text_call_reinterpretations' not in resume['resume_from']
    child = LibraryState(failed.root / 'child', dict(configuration=resume))
    with pytest.raises(LibraryStopped, match='resume_from_silent_outcome_unknown_no_replay:'):
        pipeline._resume_silent_reply(child, 'silent_review_0', prompt, lambda value: None)
    assert child.data['request_count'] == 0 and file_bytes(failed.task) == before


@pytest.mark.parametrize('kind', ['spawn', 'completed'])
def test_picture_first_keeps_strict_gate_for_old_text_failure(failed, kind):
    old_text_record(failed, kind)
    resume = cli.resume_config(failed.task, failed.ref)
    resume['creative_style'] = 'picture_first'
    child = LibraryState(failed.root / 'child', dict(configuration=resume))
    prompt = read(failed.task / 'calls/glm_007_silent_review_0/request.json')['arguments']['prompt']
    with pytest.raises(LibraryStopped, match='resume_from_silent_(?:failed_known|received_invalid)_no_replay:'):
        pipeline._resume_silent_reply(child, 'silent_review_0', prompt, lambda value: None)
    assert not (child.output / 'silent_review.json').exists() and child.data['request_count'] == 0
