"""S17 received replies and window-local identities through the production path.

Fixtures preserve glm_023/024/025 model_text and glm_023's request context from
sb_20_赵德汉藏钱_11010204/evaluations/theme1/evaluations/film1 (762d7f2).
"""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from omni_story.library import contracts, pipeline, protocol_feedback, replay_check
from omni_story.library.media import sha256_file
from omni_story.library.plan_normalisation import normalise_plan
from omni_story.library.state import LibraryStopped, write_json
from test_film_material_search import inputs
from test_plan_normalisation import evidence_plan
from test_protocol_feedback import reference
from test_v4_replay_regressions import received_call, watched_window


FIXTURES = Path(__file__).with_name('fixtures')


def read(name):
    return json.loads((FIXTURES / (name + '.json')).read_text())


def film_context():
    evidence = read('s17_context')
    context, config = evidence['context'], evidence['configuration']
    for window in context['watched_windows']:
        contracts.validate_fine(window['observation'], window)
        contracts.bind_film_window_entities(window, config['material_search'])
    return context, config


def production_check(context, config):
    state = dict(input_lock=dict(configuration=config,
        reference_sha256=context['reference']['reference_sha256']))
    return replay_check.validator(None, state, dict(name='plan_0'), dict(prompt=json.dumps(context)))


@pytest.mark.parametrize('attempt,reason,path,value,expected', [
    (0, 'plan/cast/cast_id:text_required', '$.cast', [dict(bindings=[])],
     'array of unique safe cast_id objects'),
    (1, 'audio_line_window_below_minimum', '$.audio_draft[8]', 2.0, 4.319999999999999),
    (2, 'visual_library:range_outside_content', '$.segments[0].source_in_s', 0.549886,
     [[1627.0, 1664.0], [1676.0, 1700.0]]),
])
def test_s17_real_replies_report_the_next_production_violation(attempt, reason, path, value, expected):
    context, config = film_context()
    triples = contracts.plan_field_contract(windows=context['watched_windows'])['cast']['bindable_triples']
    assert len(triples) == 13
    assert len({row['entity_id'] for row in triples}) == 13
    plan = read(f's17_attempt_{attempt}')
    original = deepcopy(plan)
    check = production_check(context, config)
    with pytest.raises(ValueError, match='^' + reason + '$') as caught:
        check(plan)
    violation = caught.value.violation
    assert (violation['path'], violation['value']) == (path, value)
    actual = violation['expected']
    if attempt == 0:
        assert actual['allowed'] == expected
        assert any(c['reason'] == 'cast_object_to_list' for c in plan['plan_normalisations'])
        assert 'cast_id' not in plan['cast'][0] and 'voice' not in plan['cast'][0]
    elif attempt == 1:
        assert actual == expected
    else:
        assert actual['time_domain'] == 'absolute_source_seconds'
        assert actual['content_range_s'] == [21.0, 4869.0]
        assert [[r['source_in_s'], r['source_out_s']] for r in actual['allowed_source_ranges']] == expected
    assert violation['valid_choices']['bindable_triples'] == triples
    assert [line['text'] for line in plan['audio_draft']] == [line['text'] for line in original['audio_draft']]
    for slot in plan['slots']:
        coverage = slot['coverage']
        assert isinstance(coverage['gaps'], list)
        assert all(isinstance(coverage[k], str) for k in ('visual_goal', 'audio_goal'))
    snapshot = deepcopy(plan)
    with pytest.raises(ValueError, match='^' + reason + '$'):
        check(plan)
    assert plan == snapshot


def test_film_ids_are_confirmed_only_window_scoped_ascii_and_stable_across_reload():
    first, _ = film_context()
    second, _ = film_context()
    assert first == second
    for window in first['watched_windows']:
        contracts.bind_film_window_entities(window, 'film')
        for role in window['observation']['roles']:
            assert role['entity_id'].isascii()
            assert role['entity_id'].startswith(window['source_sha256'] + ':film_')
            assert role['entity_id_origin'] == 'film_window_scoped'
        contracts.validate_fine(window['observation'], window)
    assert first == second
    window = deepcopy(first['watched_windows'][0])
    window['observation']['roles'] = [dict(role_id='unconfirmed', identity_confirmed=False),
        dict(role_id='not_a_bool', identity_confirmed=1),
        dict(role_id='existing', identity_confirmed=True, entity_id='received_entity')]
    original = deepcopy(window)
    contracts.bind_film_window_entities(window, 'film')
    assert window == original


@pytest.mark.parametrize('mode', [None, 'library', 'question_first'])
def test_other_modes_keep_window_bytes_unchanged(mode):
    windows = read('s17_context')['context']['watched_windows']
    before = json.dumps(windows, ensure_ascii=False)
    for window in windows:
        contracts.bind_film_window_entities(window, mode)
    assert json.dumps(windows, ensure_ascii=False) == before


def test_unaccepted_window_cannot_gain_entities():
    window = read('s17_context')['context']['watched_windows'][0]
    window['status'] = 'prepared'
    before = deepcopy(window)
    contracts.bind_film_window_entities(window, 'film')
    assert window == before


def test_new_film_window_is_persisted_with_entities_before_plan_prompt(inputs, monkeypatch):
    reading = reference()
    reading['reference_sha256'] = sha256_file(inputs.ref)
    reading['strategy'] = dict(adaptation_mode='reinterpret', speech_mode='dialogue', timing_driver='speech')
    seed = dict(full_response=dict(reference=reading), evidence_limit='synthetic received reference')
    config = dict(creative_audio=True, workflow='reference_rough_skill_v1', material_search='film')
    monkeypatch.setattr(pipeline, '_adaptive_coarse', lambda *args, **kwargs: ([], [], str(inputs.ref)))
    def prepare(source, start, end, cache, **kwargs):
        return dict(path=str(inputs.ref), sha256=sha256_file(inputs.ref), source_id=source['source_id'],
            source_sha256=source['sha256'], source_start_s=start, source_end_s=end,
            source_offset_s=start, duration_s=end-start, source_visual_policy='silent_subtitle_masked',
            source_subtitle_masks=kwargs.get('subtitle_masks', []))
    monkeypatch.setattr(pipeline, 'prepare_window', prepare)
    class ReachedPlan(Exception):
        pass
    class Model:
        def __init__(self, state):
            self.state, self.exclusions = state, []
        def call(self, name, prompt, media_path, validator, **kwargs):
            if name == 'search_0':
                value = dict(reason='confirmed action', windows=[dict(source_id=inputs.source['source_id'],
                    start_s=3, end_s=4, question='check speaker', role_ids=[])])
            elif name.startswith('fine_'):
                window = dict(zip(validator.__code__.co_freevars,
                    (cell.cell_contents for cell in validator.__closure__)))['window']
                value = dict(window_id=window['window_id'], source_id=window['source_id'],
                    roles=[dict(role_id='A', identity_confirmed=True, state='blue coat', identity_evidence='visible')],
                    events=[dict(local_start_s=0, local_end_s=1, observed_fact='turns head', role_ids=['A'])],
                    usable_ranges=[dict(local_in_s=0, local_out_s=1, role_ids=['A'], event_indices=[0],
                        continuity_notes='continuous')], uncertainties=[])
            elif name == 'plan_0':
                context = replay_check.prompt_context(dict(prompt=prompt), 'reference', 'watched_windows')
                windows = context['watched_windows']
                role = windows[0]['observation']['roles'][0]
                assert role['entity_id_origin'] == 'film_window_scoped'
                assert contracts.plan_field_contract(windows=windows)['cast']['bindable_triples'][0]['entity_id'] == role['entity_id']
                saved = json.loads((inputs.output / 'watched_windows.json').read_text())
                assert saved[0]['observation']['roles'][0] == role
                raise ReachedPlan()
            else:
                pytest.fail('unexpected synthetic call: ' + name)
            validator(value)
            return value
    with pytest.raises(ReachedPlan):
        pipeline.execute(inputs.ref, inputs.library, inputs.output, asr=False, max_fine=None,
            model_factory=Model, provider_config=config, reference_seed=seed)


@pytest.mark.parametrize('normalize', [False, True])
@pytest.mark.parametrize('saved_ids', [False, True])
def test_received_film_window_rederives_ids_from_original_reply(tmp_path, normalize, saved_ids):
    window, raw = watched_window(tmp_path, 'window_a')
    raw['roles'] = [dict(role_id='A', identity_confirmed=True, state='blue coat', identity_evidence='visible'),
                    dict(role_id='B', identity_confirmed=False, state='crowd', identity_evidence='uncertain')]
    window['observation'] = deepcopy(raw)
    call = received_call(tmp_path, 'accepted', raw, window['path'])
    failed = [received_call(tmp_path, f'failed_{index}', dict(raw, roles=roles), window['path'], failed=True)
        for index, roles in enumerate([[None], [dict(identity_confirmed=True)]])]
    if saved_ids:
        contracts.bind_film_window_entities(window, 'film')
    write_json(tmp_path / 'watched_windows.json', [window])
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    state = SimpleNamespace(output=tmp_path, data=dict(calls=[call, *failed]),
        input_lock=dict(configuration=dict(material_search='film')))
    first = pipeline._received_windows(state, visual_only=True, normalize=normalize)
    second = pipeline._received_windows(state, visual_only=True, normalize=normalize)
    assert first == second
    roles = first[0]['observation']['roles']
    assert roles[0]['entity_id_origin'] == 'film_window_scoped'
    assert 'entity_id' not in roles[1]
    assert pipeline._window_context(first[0])['observation']['roles'] == roles
    assert pipeline.fine_identity_context(first[0], first, material_search='film')['already_watched_roles'] == [roles[1] | dict(
        source_id=window['source_id'], source_sha256=window['source_sha256'])]
    assert len(pipeline.fine_identity_context(first[0], first, material_search='library')['already_watched_roles']) == 2
    assert {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before
    # Saving/reloading a derived window must still authenticate against the raw reply.
    write_json(tmp_path / 'watched_windows.json', first)
    assert pipeline._received_windows(state, visual_only=True, normalize=normalize) == first
    first[0]['observation']['roles'][0]['entity_id'] += '_tampered'
    write_json(tmp_path / 'watched_windows.json', first)
    with pytest.raises(LibraryStopped, match='watched_window_not_bound_to_received_fine'):
        pipeline._received_windows(state, visual_only=True, normalize=normalize)


def test_window_entities_allow_separate_cast_with_same_voice_but_reject_merged_binding():
    context, _ = film_context()
    windows = context['watched_windows'][:2]
    characters = []
    for index, window in enumerate(windows):
        role = next(r for r in window['observation']['roles'] if r['role_id'] == 'candidate_B')
        characters.append(dict(cast_id=f'cast_{index}', entity_id=role['entity_id'], voice='Ethan',
            bindings=[dict(window_id=window['window_id'], role_id=role['role_id'])]))
    assert len(contracts.validate_task_cast(characters, windows)) == 2
    merged = deepcopy(characters[:1])
    merged[0]['bindings'] += characters[1]['bindings']
    with pytest.raises(ValueError, match='plan:cast_entity_not_supported_by_observation'):
        contracts.validate_task_cast(merged, windows)


def test_cast_object_uses_existing_bookkeeping_and_validators():
    plan, catalog, window = evidence_plan()
    window.pop('qfs_identity')
    window['observation']['roles'][0].pop('entity_id')
    contracts.bind_film_window_entities(window, 'film')
    binding = dict(window_id=window['window_id'], role_id='A')
    plan['cast'] = dict(cast_id='story_role', voice='Ethan', bindings=[binding])
    plan['focus_role_bindings'] = [binding]
    normalise_plan(plan, [window], speech_mode='mixed', fps=plan['fps'])
    assert plan['cast'][0]['entity_id'] == window['observation']['roles'][0]['entity_id']
    assert plan['cast'][0]['cast_id'] == 'story_role' and plan['cast'][0]['voice'] == 'Ethan'
    assert any(c['path'] == '$.cast' and c['reason'] == 'cast_object_to_list' for c in plan['plan_normalisations'])
    contracts.validate_plan(plan, catalog, [window], plan['reference_sha256'], require_audio_draft=True)
    contracts.validate_dialogue_speakers(plan['audio_draft'], plan['segments'], cast=plan['cast'], windows=[window])
    before = deepcopy(plan)
    normalise_plan(plan, [window], speech_mode='mixed', fps=plan['fps'])
    assert plan == before


@pytest.mark.parametrize('gaps,expected', [('missing shot', ['missing shot']), ('', []), (' ', [' '])])
def test_coverage_only_changes_requested_shapes_with_audit(gaps, expected):
    plan = dict(slots=[dict(coverage=dict(gaps=gaps, visual_goal=['one', 'two'], audio_goal=['speech']))])
    normalise_plan(plan)
    assert plan['slots'][0]['coverage'] == dict(gaps=expected, visual_goal='one；two', audio_goal='speech')
    changes = plan['plan_normalisations']
    assert len(changes) == 3 and all(c['reason'] == 'coverage_shape' for c in changes)
    assert {c['path'] for c in changes} == {f'$.slots[0].coverage.{key}' for key in ('gaps', 'visual_goal', 'audio_goal')}
    before = deepcopy(plan)
    normalise_plan(plan)
    assert plan == before


@pytest.mark.parametrize('value', [None, 1, {}, [], ['', 'text'], [' '], ['text', 2], [['text']]])
def test_other_coverage_shapes_are_left_to_validation(value):
    plan = dict(slots=[dict(coverage=dict(gaps=value, visual_goal=value, audio_goal=value))])
    before = deepcopy(plan)
    normalise_plan(plan)
    assert plan == before


def test_coverage_audit_preserves_actual_slot_index_with_malformed_neighbor():
    plan = dict(slots=[None, dict(coverage=dict(gaps='missing'))])
    normalise_plan(plan)
    assert plan['slots'] == [None, dict(coverage=dict(gaps=['missing']))]
    assert plan['plan_normalisations'][0]['path'] == '$.slots[1].coverage.gaps'


def test_no_bindable_entities_feedback_is_explicit_and_does_not_change_reply():
    plan = read('s17_attempt_0')
    windows = read('s17_context')['context']['watched_windows']
    before = deepcopy(plan)
    feedback = protocol_feedback.diagnose('plan_0', plan, dict(stage='plan', windows=windows))
    hints = [r['hint'] for r in feedback if r['path'] in {'$.cast', '$.audio_draft'}]
    assert hints and all('没有可绑定的已确认身份' in hint for hint in hints)
    assert all('旁白' in hint and '已确认说话者的窗口' in hint for hint in hints)
    assert plan == before
    context, _ = film_context()
    feedback = protocol_feedback.diagnose('plan_0', plan, dict(stage='plan', windows=context['watched_windows']))
    assert not any('没有可绑定的已确认身份' in r['hint'] for r in feedback)
