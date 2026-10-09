"""Use the sole remaining initial candidate after a known rejected model plan."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from omni_story.library import contracts, pipeline, server_capacity_recovery as recovery, server_jobs
from omni_story.library.media import sha256_file
from omni_story.library.opencode_provider import PROVIDER
from omni_story.library.state import LibraryState, LibraryStopped, json_sha, write_json
from test_server_capacity_recovery import append_http, original, read, response, snapshot, unchanged


KEY = 'server_remaining_candidate_feedback'
POLICY = 'opencode_remaining_initial_candidate_feedback_v1'
ERROR = 'plan:caption_event_outside_selected_range'


def seed_for(reference):
    digest = sha256_file(reference)
    return {'source_call_id': 'synthetic_cached_reference', 'reference_sha256': digest,
            'full_response': {'reference': {
                'reference_sha256': digest, 'theme': 'fixture visual state',
                'intended_takeaway': 'retain the observed state',
                'visible_evidence': [{'start_s': 0, 'end_s': 1, 'observed_fact': 'fixture state',
                                      'supports': 'synthetic observation only'}],
                'editing_methods': [{'method': 'continuous view', 'function': 'show the state',
                                     'visual_evidence': 'fixture geometry', 'start_s': 0, 'end_s': 1}],
                'uncertainties': ['Synthetic reference only.']}},
            'evidence_limit': 'Cached synthetic reference; no new reference observation.'}


@pytest.fixture
def rejected(original, tmp_path):
    state, planning, original_request, user = original
    library = tmp_path / 'library'
    library.mkdir()
    movie = library / 'movie.mp4'
    movie.write_bytes(b'synthetic source bytes, never decoded or submitted')
    reference = tmp_path / 'reference.mp4'
    reference.write_bytes(b'synthetic reference bytes, never decoded or submitted')

    def source(path, duration):
        info = path.stat()
        digest = sha256_file(path)
        return {'source_id': 'src_' + digest[:16], 'path': str(path), 'sha256': digest,
                'duration_s': duration, 'audio_stream_index': None, 'video_stream_index': 0,
                'size_bytes': info.st_size, 'mtime_ns': info.st_mtime_ns,
                'streams': [{'index': 0, 'codec_type': 'video'}], 'format': {'duration': str(duration)}}

    movie_source, reference_source = source(movie, 200.0), source(reference, 4.0)
    catalog = {'sources': [movie_source]}
    write_json(state.output / 'catalog/inventory.json', catalog)
    write_json(state.output / 'reference_catalog/inventory.json', {'sources': [reference_source]})
    config = {**state.input_lock['configuration'], 'span_s': 600, 'frames': 18, 'asr': False,
              'reference_seed_sha256': json_sha(seed_for(reference))}
    lock = {'reference_sha256': reference_source['sha256'],
            'library_sources': [{'source_id': movie_source['source_id'], 'sha256': movie_source['sha256']}],
            'configuration': config}
    state._reload()
    prior = deepcopy(state.data['calls'])
    state.data['input_lock'] = lock
    state.input_lock = deepcopy(lock)
    write_json(state.path, state.data)
    windows = []
    for index in range(20):
        start, end = 100.0 + index * 10, 110.0 + index * 10
        proxy = state.output / f'window_{index}.mp4'
        proxy.write_bytes(('synthetic observed window ' + str(index)).encode())
        window_id = 'window_' + json_sha({'source_sha256': movie_source['sha256'],
                                         'start': start, 'end': end})[:16]
        observation = {'window_id': window_id, 'source_id': movie_source['source_id'],
                       'roles': [{'role_id': 'subject', 'identity_confirmed': True,
                                  'state': 'synthetic stable subject', 'identity_evidence': 'fixture geometry'}],
                       'events': [{'local_start_s': 0.0, 'local_end_s': 4.0,
                                   'observed_fact': 'earlier visible state', 'role_ids': ['subject']},
                                  {'local_start_s': 7.0, 'local_end_s': 9.0,
                                   'observed_fact': 'later visible state', 'role_ids': ['subject']}],
                       'usable_ranges': [{'local_in_s': 0.0, 'local_out_s': 9.0, 'event_indices': [0, 1],
                                          'role_ids': ['subject'], 'continuity_notes': 'fixture continuity'}],
                       'uncertainties': ['Synthetic facts do not establish movie quality.']}
        window = {'window_id': window_id, 'source_id': movie_source['source_id'],
                  'source_sha256': movie_source['sha256'], 'source_start_s': start, 'source_end_s': end,
                  'source_offset_s': start, 'duration_s': 10.0, 'media_duration_s': 10.0,
                  'path': str(proxy), 'sha256': sha256_file(proxy), 'audio_stream_index': None,
                  'audio_present': False, 'status': 'watched', 'observation': observation}
        request = {'tool': 'analyze_video', 'arguments': {'video_source': str(proxy), 'prompt': f'old observation {index}'},
                   'media_sha256': window['sha256'], 'provider': PROVIDER['provider'],
                   'policy_version': state.data['policy_version'], 'observation_scope': {
                       'kind': 'continuous_window', 'source_sha256': movie_source['sha256'],
                       'source_start_s': start, 'source_end_s': end}}
        name = 'fine_' + window_id.removeprefix('window_') if index < 8 else 'old_' + f'{index:016x}'
        call, _ = state.begin_call(name, request)
        value = observation if index < 8 else {'old_accounting': index}
        state.complete_call(call, response(json.dumps(value), finish='stop'))
        write_json(state.output / 'calls' / call['id'] / 'parsed.json', value)
        if index < 8:
            windows.append(window)
    # All twenty prior synthetic records precede the already settled original pair.
    state._reload()
    state.data['calls'] = prior[:1] + state.data['calls'][3:] + prior[1:]
    write_json(state.path, state.data)
    assert state.usage()['requests'] == 23
    write_json(state.output / 'watched_windows.json', windows)
    job_path = state.output / '.omni-server/job.json'
    job = read(job_path)
    job['command'] = ['old-worker', '--reference', str(reference), '--library', str(library)]
    write_json(job_path, job)
    auth_path = recovery.register(state.output, user, sha256_file(user), registry_path=state.registry_path)
    first = state.output / '.omni-server/recoveries' / recovery.NAME
    write_json(first / 'job.json', {'job_id': 'first-CPU-control', 'state': 'failed', 'exit_code': 1,
                                  'supervisor_pid': 33333, 'child_pid': 44444})
    (first / 'job.log').write_bytes(b'LibraryStopped: library_file_set_changed\n')
    (first / 'run.lock').write_bytes(b'first-CPU-control\n')
    recovery.register_catalog_preflight_fix(state.output, registry_path=state.registry_path)
    failed_plan = {'reference_sha256': reference_source['sha256'], 'focus_role_id': 'subject',
                   'focus_role_bindings': [{'window_id': windows[0]['window_id'], 'role_id': 'subject',
                                            'identity_evidence': 'fixture observed subject'}],
                   'slots': [{'slot_id': 'slot_model', 'intended_takeaway': 'model-owned fixture claim',
                              'segment_ids': ['segment_model']}],
                   'segments': [{'segment_id': 'segment_model', 'slot_id': 'slot_model',
                                 'source_id': movie_source['source_id'], 'window_id': windows[0]['window_id'],
                                 'source_in_s': 101.0, 'source_out_s': 103.0, 'speed': 1.0,
                                 'look': 'none', 'framing': 'fit', 'role_ids': ['subject'],
                                 'caption': {'text': '原模型字幕不得由恢复代码改写', 'start_s': 0.0, 'end_s': 2.0,
                                             'position': 'bottom', 'font_size': 18,
                                             'evidence': [{'window_id': windows[0]['window_id'], 'event_indices': [1]}]}}],
                   'audio_mode': 'silent', 'source_gain_db': -12, 'reference_gain_db': 0,
                   'width': 160, 'height': 240, 'fps': 30, 'limitations': ['Synthetic model plan only.']}
    with pytest.raises(ValueError, match=ERROR):
        contracts.validate_plan(failed_plan, catalog, windows, reference_source['sha256'], 4.0,
                                reference_audio_stream_index=None)
    model_text = json.dumps(failed_plan, ensure_ascii=False)
    parent = None
    for index, name in enumerate([recovery.ALIAS, recovery.ALIAS + '_repair'], 24):
        request = deepcopy(original_request)
        request['arguments']['prompt'] += recovery.SUFFIX
        if parent:
            request['arguments']['prompt'] += (
                '\n上次输出未通过本地协议校验。只修复JSON字段、ID和时间域，不得补造画面证据。' +
                json.dumps({'validation_error': ERROR, 'previous_response': model_text}, ensure_ascii=False))
        call, _ = state.begin_call(name, request, repair_of=parent)
        state.complete_call(call, response(model_text, finish='stop'))
        write_json(state.output / 'calls' / call['id'] / 'protocol_failure.json',
                   {'error': ERROR, 'attempt': int(bool(parent)), 'model_text': model_text})
        append_http(state.output, call['id'], index, model_text, finish='stop', tokens=300)
        parent = call
    controller = state.output / '.omni-server/recoveries' / server_jobs.PREFLIGHT_RECOVERY_NAME
    write_json(controller / 'job.json', {'job_id': 'known-rejected-candidate-control', 'state': 'failed',
                                       'exit_code': 1, 'supervisor_pid': 55555, 'child_pid': 66666})
    (controller / 'job.log').write_bytes(b'ValueError: model_protocol_repair_exhausted:plan_0_capacity_v2\n')
    (controller / 'run.lock').write_bytes(b'known-rejected-candidate-control\n')
    write_json(state.output / 'failure_capacity_recovery_v1.json',
               {'error': 'model_protocol_repair_exhausted:plan_0_capacity_v2', 'type': 'ValueError',
                'usage': {'requests': 25}, 'no_automatic_paid_replay': True})
    state._reload()
    assert len(state.data['calls']) == state.usage()['requests'] == 25
    return state, failed_plan, windows, controller, auth_path, reference, library, catalog


def test_remaining_fixture_has_exact_known_caption_failure(rejected):
    state, failed_plan, windows, _, _, _, _, catalog = rejected
    assert len(windows) == 8
    with pytest.raises(ValueError, match=ERROR):
        contracts.validate_plan(failed_plan, catalog, windows, failed_plan['reference_sha256'], 4.0,
                                reference_audio_stream_index=None)
    assert all(call['status'] == 'received' for call in state.data['calls'])
    assert not list(state.output.glob('render_*'))


@pytest.mark.parametrize('case', ['unknown', 'wrong_history', 'wrong_budget', 'not_failed', 'live',
                                 'missing_failure', 'wrong_caption_error', 'non_json_reply',
                                 'existing_render', 'stage_one_already_paid', 'duplicate_alias',
                                 'bad_window_media', 'changed_window_observation'])
def test_remaining_registration_rejects_unsettled_or_consumed_scope_without_writing_proof(
        rejected, monkeypatch, case):
    state, _, windows, controller, _, _, _, _ = rejected
    if case in {'unknown', 'wrong_history', 'wrong_budget', 'duplicate_alias'}:
        data = read(state.path)
        if case == 'unknown':
            data['calls'][-1]['status'] = 'uncertain'
        elif case == 'wrong_history':
            data['calls'][0]['usage']['completion_tokens'] = 999
        elif case == 'wrong_budget':
            data['max_requests'] = 80
        else:
            data['calls'][-1]['name'] = recovery.ALIAS
        write_json(state.path, data)
    elif case == 'not_failed':
        job = read(controller / 'job.json')
        job.update(state='succeeded', exit_code=0)
        write_json(controller / 'job.json', job)
    elif case == 'live':
        monkeypatch.setattr(server_jobs, '_group_running', lambda pid: True)
    elif case == 'missing_failure':
        (state.output / 'failure_capacity_recovery_v1.json').unlink()
    elif case in {'wrong_caption_error', 'non_json_reply'}:
        call = state.data['calls'][-1]
        path = state.output / 'calls' / call['id'] / (
            'protocol_failure.json' if case == 'wrong_caption_error' else 'response.json')
        if case == 'wrong_caption_error':
            record = read(path)
            record['error'] = 'plan:unsupported_caption_field'
        else:
            record = response('not JSON', finish='stop')
        write_json(path, record)
        if case == 'non_json_reply':
            data = read(state.path)
            data['calls'][-1]['response_sha256'] = json_sha(record)
            write_json(state.path, data)
    elif case == 'existing_render':
        (state.output / 'render_0').mkdir()
    elif case == 'bad_window_media':
        Path(windows[1]['path']).write_bytes(b'changed observed proxy')
    elif case == 'changed_window_observation':
        current = read(state.output / 'watched_windows.json')
        current[1]['observation']['roles'][0]['state'] = 'a changed claimed state'
        write_json(state.output / 'watched_windows.json', current)
    else:
        call, _ = state.begin_call('plan_1', {'synthetic': 'remaining candidate is already consumed'})
        state.complete_call(call, response('{"already":"paid"}', finish='stop'))
    before = snapshot(state.output)
    with pytest.raises((LibraryStopped, ValueError, FileNotFoundError)):
        recovery.register_remaining_candidate(state.output, registry_path=state.registry_path)
    unchanged(state.output, before)
    assert not read(state.path)['artifacts'].get(KEY)
    assert not list((state.output / 'artifacts').glob(KEY + '_*.json'))


def test_remaining_registration_binds_model_plan_and_exact_diagnostics_without_rewriting_history(rejected):
    state, model, windows, controller, auth, _, _, _ = rejected
    before = snapshot(state.output)
    old = deepcopy(state.data)
    assert recovery.remaining_candidate(state.output) is None
    path = recovery.register_remaining_candidate(state.output, registry_path=state.registry_path)
    proof = read(path)
    assert recovery.remaining_candidate(state.output) == proof
    assert proof['policy'] == POLICY
    assert proof['consumed_candidate'] == 0 and proof['remaining_candidate'] == 1
    assert proof['new_candidate_rounds'] == 0 and proof['baseline_request_count'] == 25
    assert proof['feedback']['status'] == 'proposal_rejected_no_render'
    assert proof['feedback']['candidate'] == 0 and proof['feedback']['model_proposal'] == model
    assert proof['feedback']['validation_error'] == ERROR
    assert proof['feedback']['interval_diagnostics'] == [{
        'segment_id': 'segment_model', 'window_id': windows[0]['window_id'],
        'source_in_s': 101.0, 'source_out_s': 103.0, 'selected_local_interval_s': [1.0, 3.0],
        'cited_event_index': 1, 'cited_event_interval_s': [7.0, 9.0], 'error': ERROR}]
    assert Path(proof['baseline_state_path']).read_bytes() == before[Path('library_state.json')]
    assert Path(proof['watched_windows_path']).read_bytes() == before[Path('watched_windows.json')]
    assert proof['failed_controller_job_id'] == read(controller / 'job.json')['job_id']
    assert proof['original_capacity_authorization_sha256'] == sha256_file(auth)
    state._reload()
    assert state.data['calls'] == old['calls'] and state.usage()['requests'] == 25
    assert not list(state.output.glob('render_*'))
    unchanged(state.output, before, except_state=True)
    registered = snapshot(state.output)
    with pytest.raises(LibraryStopped, match='remaining_already_registered'):
        recovery.register_remaining_candidate(state.output, registry_path=state.registry_path)
    unchanged(state.output, registered)


@pytest.mark.parametrize('case', ['proof_digest', 'duplicate_proof', 'absolute_outside_proof',
                                 'absolute_outside_baseline', 'baseline', 'old_call_prefix',
                                 'failed_response', 'protocol_failure', 'controller', 'failure_report',
                                 'windows_snapshot', 'observed_media'])
def test_remaining_loader_rejects_changed_bound_evidence_and_paths(rejected, tmp_path, case):
    state, _, windows, controller, _, _, _, _ = rejected
    path = recovery.register_remaining_candidate(state.output, registry_path=state.registry_path)
    proof = read(path)
    data = read(state.path)
    if case in {'proof_digest', 'duplicate_proof', 'absolute_outside_proof', 'old_call_prefix'}:
        if case == 'proof_digest':
            data['artifacts'][KEY][0]['sha256'] = '0' * 64
        elif case == 'duplicate_proof':
            data['artifacts'][KEY].append(deepcopy(data['artifacts'][KEY][0]))
        elif case == 'old_call_prefix':
            data['calls'][-1]['usage']['completion_tokens'] = 999
        else:
            outside = tmp_path / 'outside-proof.json'
            outside.write_bytes(path.read_bytes())
            data['artifacts'][KEY][0]['path'] = str(outside)
        write_json(state.path, data)
    elif case == 'absolute_outside_baseline':
        outside = tmp_path / 'outside-baseline.json'
        outside.write_bytes(Path(proof['baseline_state_path']).read_bytes())
        proof['baseline_state_path'] = str(outside)
        write_json(path, proof)
        data['artifacts'][KEY][0]['sha256'] = json_sha(proof)
        write_json(state.path, data)
    else:
        target = {'baseline': Path(proof['baseline_state_path']),
                  'failed_response': state.output / 'calls' / proof['failed_plan_call_id'] / 'response.json',
                  'protocol_failure': state.output / 'calls' / proof['failed_plan_call_id'] / 'protocol_failure.json',
                  'controller': controller / 'job.json',
                  'failure_report': state.output / 'failure_capacity_recovery_v1.json',
                  'windows_snapshot': Path(proof['watched_windows_path']),
                  'observed_media': Path(windows[0]['path'])}[case]
        target.write_bytes(target.read_bytes() + b' ')
    before = snapshot(state.output)
    with pytest.raises(LibraryStopped, match='server_capacity_recovery_'):
        recovery.remaining_candidate(state.output)
    unchanged(state.output, before)


@pytest.mark.parametrize('fail_stage_one', [False, True])
def test_pipeline_skips_rejected_round_zero_and_selects_actual_round_one_without_touching_old_failure(
        rejected, monkeypatch, fail_stage_one):
    from omni_story.library import render
    state, failed_plan, windows, _, _, reference, library, _ = rejected
    proof_path = recovery.register_remaining_candidate(state.output, registry_path=state.registry_path)
    feedback = read(proof_path)['feedback']
    before = snapshot(state.output)
    prior_calls = deepcopy(read(state.path)['calls'])
    names, contexts, rendered_rounds = [], {}, []
    new_plan = deepcopy(failed_plan)
    new_plan['segments'][0].pop('caption')
    monkeypatch.setattr(pipeline, '_adaptive_coarse',
                        lambda *args, **kwargs: ([], [], str(state.output / 'planning.png')))
    monkeypatch.setattr(pipeline, '_status', lambda *args, **kwargs: None)

    class Model:
        def __init__(self, current):
            self.state = current

        def call(self, name, prompt, media, validator, *, image=False, scope=None):
            names.append(name)
            assert name in {'search_1', 'plan_1', 'blind_1', 'review_1'}, name
            if name in {'search_1', 'plan_1'}:
                context = json.JSONDecoder().raw_decode(prompt[prompt.index('{"reference":'):])[0]
                contexts[name] = context
                assert context['previous_review'] == feedback
            if name == 'plan_1' and fail_stage_one:
                raise ValueError('synthetic remaining candidate protocol failure')
            current = read(self.state.path)
            previous = next((call for call in current['calls'] if call['name'] == name), None)
            if previous:
                value = read(self.state.output / 'calls' / previous['id'] / 'parsed.json')
                validator(value)
                return value
            if name == 'search_1':
                window = windows[0]
                value = {'reason': 'synthetic selection of an already observed window', 'windows': [{
                    'source_id': window['source_id'], 'start_s': window['source_start_s'],
                    'end_s': window['source_end_s'], 'question': 'reuse known fixture evidence',
                    'role_ids': ['subject']}]}
            elif name == 'plan_1':
                value = new_plan
            elif name == 'blind_1':
                value = {'observed_story': 'a synthetic visible state', 'main_characters': ['fixture subject'],
                         'apparent_theme': 'a synthetic state',
                         'evidence': [{'start_s': 0, 'end_s': 1, 'observed_fact': 'fixture remains visible'}],
                         'confusions': ['This is not a real model quality assessment.']}
            else:
                value = {'reference_sha256': failed_plan['reference_sha256'], 'theme_status': 'partial',
                         'editing_status': 'partial', 'continuity_status': 'pass',
                         'evidence': ['synthetic plumbing only'], 'limitations': ['quality is unestablished'],
                         'revision_requests': []}
            validator(value)
            argument = 'image_source' if image else 'video_source'
            request = {'tool': 'analyze_image' if image else 'analyze_video',
                       'arguments': {argument: str(media), 'prompt': prompt}, 'media_sha256': sha256_file(media),
                       'provider': PROVIDER['provider'], 'policy_version': self.state.data['policy_version']}
            call, _ = self.state.begin_call(name, request)
            self.state.complete_call(call, response(json.dumps(value, ensure_ascii=False), finish='stop'))
            write_json(self.state.output / 'calls' / call['id'] / 'parsed.json', value)
            return value

    def render_video(sources, plan, directory, **kwargs):
        directory = Path(directory)
        rendered_rounds.append(directory.name)
        assert directory.name == 'render_1' and plan == new_plan
        target = directory / 'final.mp4'
        target.parent.mkdir()
        target.write_bytes(b'synthetic rendered artifact, never decoded')
        return {'rendered_path': str(target), 'sha256': sha256_file(target), 'measured_duration_s': 2.0,
                'provenance': [{**plan['segments'][0], 'output_in_s': 0.0, 'output_out_s': 2.0}]}

    def inventory(path, output):
        return {'sources': [{'source_id': 'synthetic_render', 'path': str(path), 'sha256': sha256_file(path),
                             'duration_s': 2.0, 'audio_stream_index': None}]}

    monkeypatch.setattr(render, 'render_library_video', render_video)
    monkeypatch.setattr(pipeline, 'inventory_sources', inventory)
    monkeypatch.setattr(pipeline, 'prepare_window', lambda source, *args, **kwargs: {'path': source['path']})

    def execute():
        return pipeline.execute(reference, library, state.output, asr=False, max_requests=None,
                                model_factory=Model, provider_config=state.input_lock['configuration'],
                                registry_path=state.registry_path, reference_seed=seed_for(reference),
                                failure_report_name='failure_remaining_candidate_v1.json')

    if fail_stage_one:
        with pytest.raises(ValueError, match='synthetic remaining candidate protocol failure'):
            execute()
        assert names == ['search_1', 'plan_1'] and not rendered_rounds
        assert read(state.output / 'failure_remaining_candidate_v1.json')['error'] == (
            'synthetic remaining candidate protocol failure')
    else:
        result = execute()
        assert names == ['search_1', 'plan_1', 'blind_1', 'review_1']
        assert rendered_rounds == ['render_1']
        assert result['selected_round'] == 1 and result['actual_fine_windows'] == 8
        assert result['selected_review_path'] == str(state.output / 'review_1.json')
        assert result['final_video'] == str(state.output / 'render_1/final.mp4')
        assert result['status'] == 'library_candidate_with_limitations'
    assert len(contexts['search_1']['already_watched']) == len(contexts['plan_1']['watched_windows']) == 8
    state._reload()
    assert state.data['calls'][:25] == prior_calls
    assert not (state.output / 'render_0').exists()
    unchanged(state.output, before, except_state=True)
