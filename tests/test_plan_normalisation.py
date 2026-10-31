"""Real received plans plus evidence-bound mechanical repair, shared by both stages."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from omni_story.library import contracts, pipeline, protocol_feedback, replay_check, story_finecut
from test_mask_and_line_window import short_plan
from test_replay_check import speaker_fixture

FIXTURE = Path(__file__).parent / 'fixtures/plan_normalisation_20261031'


def normalisation():
    from omni_story.library import plan_normalisation
    return plan_normalisation


def read(name):
    return json.loads((FIXTURE / name).read_text())


def real_validator(context, creative=True):
    reference = context['reference']
    return replay_check.nested_validator(pipeline.execute, 'validate_current_plan',
        context={}, creative_audio=creative, editing_reference=None, editing_v2=False,
        maximum_segments=32, progress_driven=True,
        ref=dict(sha256=reference['reference_sha256'], duration_s=context['reference_duration_s'], audio_stream_index=0),
        reference_reading=reference, semantic_audit=False, semantic_pipeline=None,
        sources=context['catalog'], strategy=contracts.reference_strategy(reference, creative_audio=creative),
        visual_only=creative, windows=context['windows'])


@pytest.mark.parametrize('name', ['glm_182_plan_0.json', 'glm_183_plan_0_repair.json', 'glm_184_plan_0_repair2.json'])
def test_real_replies_normalised_before_unchanged_validator_with_valid_choices(name):
    context, plan = read('observations.json'), read(name)
    original = deepcopy(plan)
    error = None
    try:
        real_validator(context)(plan)
    except ValueError as caught:
        error = caught
    # Remaining real evidence errors must not be mistaken for repairable IDs.
    if name.startswith('glm_182'):
        assert error is None
    else:
        assert error is not None
        choices = error.violation['valid_choices']
        assert choices['bindable_triples']
        assert 'speaker_visible_segments' in choices
        feedback = protocol_feedback.diagnose('plan_0', plan, dict(context,
            stage='plan', validation_error=error))
        assert feedback[0]['valid_choices'] == choices
        assert feedback[0]['expected'] == error.violation['expected']
    assert plan['plan_normalisations']
    for change in plan['plan_normalisations']:
        assert set(change) == {'path', 'before', 'after', 'reason'}
        assert change['before'] != change['after']
    windows = {w['window_id']: w for w in context['windows']}
    contracts.validate_task_cast(plan.get('cast'), windows)
    if name.startswith('glm_183'):
        assert str(error) == 'plan/focus_role_bindings:list_required'
        assert plan['focus_role_bindings'] == []
        assert plan['focus_role_id'] == original['focus_role_id']
        assert plan['segments'] == original['segments']  # Never replace invented footage.
        assert choices['valid_window_ids'] == sorted(windows)
        assert choices['unknown_segment_window_ids'] == ['ref_movie']
        assert all(set(row) == {'window_id', 'role_id', 'entity_id', 'name'}
                   for row in choices['bindable_rows'])
        assert {'阿宝', '平先生'} <= {row['name'] for row in choices['bindable_rows']}
    else:
        contracts.validate_focus_role_bindings(plan['focus_role_bindings'], windows, plan['focus_role_id'])
        for before, after in zip(original['segments'], plan['segments']):
            assert {k: v for k, v in after.items() if k != 'source_id'} == {
                k: v for k, v in before.items() if k != 'source_id'}
            assert after['source_id'] == windows[after['window_id']]['source_id']
        assert all(b['window_id'] in {s['window_id'] for s in plan['segments']}
                   for c in plan['cast'] for b in c['bindings'])
    if name.startswith('glm_184'):
        assert str(error) == 'plan:repeated_source_footage'
        expected = error.violation['expected']
        assert expected['conflicts'] == [dict(segment_ids=['seg_3', 'seg_6'],
            source_id=plan['segments'][2]['source_id'], overlap_s=4)]
        assert expected['free_alternative_ranges']
        assert choices['speaker_visible_segments']['line_1'] == ['seg_2', 'seg_5']
        # Repeated footage is the only rejection, including later dialogue checks.
        contracts.validate_reference_dialogue_form(plan['audio_draft'], 'dialogue')
        contracts.validate_dialogue_speakers(plan['audio_draft'], plan['segments'],
            cast=plan['cast'], windows=context['windows'])
    if name.startswith('glm_182'):
        assert any(change['path'] == '$.segments[7].source_id' for change in plan['plan_normalisations'])
        before = deepcopy(plan)
        real_validator(context)(plan)
        assert plan == before
    assert [line['text'] for line in plan['audio_draft']] == [line['text'] for line in original['audio_draft']]


@pytest.mark.parametrize('focus', ['cast_boss', 'Po'])
def test_real_182_focus_can_be_rebuilt_from_cast_id_or_canonical_alias(focus):
    context, plan = read('observations.json'), read('glm_182_plan_0.json')
    plan['focus_role_id'] = focus
    plan['focus_role_bindings'] = []
    real_validator(context)(plan)
    assert len(plan['focus_role_bindings']) == 5
    assert all(b['role_id'] in {'po_panda', 'A'} for b in plan['focus_role_bindings'])


def test_window_source_fields_are_derived_and_unknown_windows_are_left_to_validator():
    plan, catalog, window = evidence_plan()
    segment = plan['segments'][0]
    segment.update(source_id='approximate', source_sha256='wrong')
    normalisation().normalise_plan(plan, {window['window_id']: window})
    assert segment['source_id'] == window['source_id']
    assert segment['source_sha256'] == window['source_sha256']
    changes = {c['path']: c for c in plan['plan_normalisations']}
    assert changes['$.segments[0].source_id']['before'] == 'approximate'
    assert changes['$.segments[0].source_sha256']['before'] == 'wrong'
    contracts.validate_plan(plan, catalog, [window], plan['reference_sha256'], require_audio_draft=True)
    before = deepcopy(plan)
    normalisation().normalise_plan(plan, [window])
    assert plan == before
    segment.update(window_id='invented', source_id='still_wrong')
    normalisation().normalise_plan(plan, [window])
    assert segment['source_id'] == 'still_wrong'


@pytest.mark.parametrize('identity', ['cast_id', 'alias', 'entity_id'])
def test_focus_matches_character_even_without_prior_focus_bindings(identity):
    plan, catalog, window = evidence_plan()
    other = dict(window['observation']['roles'][0], role_id='other',
                 entity_id=window['source_sha256'] + ':other', name='另一人')
    window['observation']['roles'].append(other)
    plan['cast'].append(dict(cast_id='other_cast', canonical_name='另一人',
                            entity_id=other['entity_id'], bindings=[]))
    plan['focus_role_bindings'] = []
    plan['focus_role_id'] = {'cast_id': 'story_role', 'alias': '影片别名',
                             'entity_id': window['observation']['roles'][0]['entity_id']}[identity]
    if identity != 'cast_id':
        plan.pop('cast')  # Match observed entity / pinned cast.json alias directly.
    film_cast = dict(characters=[dict(name='规范人物', aliases=['Alias', '影片别名'])])
    normalisation().normalise_plan(plan, [window], film_cast=film_cast)
    assert plan['focus_role_bindings'] == [dict(window_id=window['window_id'], role_id='A',
        identity_evidence=window['observation']['roles'][0]['identity_evidence'])]
    contracts.validate_plan(plan, catalog, [window], plan['reference_sha256'], require_audio_draft=True)


def test_focus_cannot_drift_to_another_character_when_selected_window_has_one_other_cast():
    plan, _, window = evidence_plan()
    plan['focus_role_id'] = 'missing_character'
    plan['cast'].append(dict(cast_id='missing_character', canonical_name='另一人',
                            entity_id=window['source_sha256'] + ':missing', bindings=[]))
    normalisation().normalise_plan(plan, [window])
    assert plan['focus_role_id'] == 'missing_character'
    assert plan['focus_role_bindings'] == []
    with pytest.raises(ValueError, match='plan/focus_role_bindings:list_required'):
        contracts.validate_focus_role_bindings([], {window['window_id']: window}, plan['focus_role_id'])


def test_distinct_local_focus_entities_keep_declared_bindings_without_extrapolation():
    plan, _, window = evidence_plan()
    other = deepcopy(window)
    other['window_id'] = 'other_window'
    other['observation']['window_id'] = other['window_id']
    other['observation']['roles'][0]['entity_id'] = window['source_sha256'] + ':other'
    other['qfs_identity'] = []
    third = deepcopy(other)
    third['window_id'] = 'unbound_window'
    third['observation']['window_id'] = third['window_id']
    third['observation']['roles'][0]['entity_id'] = window['source_sha256'] + ':third'
    plan['segments'].extend(dict(plan['segments'][0], segment_id=w['window_id'], window_id=w['window_id'])
                            for w in [other, third])
    plan['focus_role_bindings'] = [dict(window_id=w['window_id'], role_id='A') for w in [window, other]]
    normalisation().normalise_plan(plan, [window, other, third])
    assert {b['window_id'] for b in plan['focus_role_bindings']} == {window['window_id'], other['window_id']}
    contracts.validate_focus_role_bindings(plan['focus_role_bindings'],
        {w['window_id']: w for w in [window, other, third]}, plan['focus_role_id'])


def test_historical_ok_focus_with_distinct_local_entities_does_not_regress():
    plan = read('legacy_focus_plan.json')
    declared = {(b['window_id'], b['role_id']) for b in plan['focus_role_bindings']}
    real_validator(read('legacy_focus_observations.json'))(plan)
    assert {(b['window_id'], b['role_id']) for b in plan['focus_role_bindings']} == declared


def test_missing_observation_identity_evidence_is_reported_without_fabricating_it():
    plan, _, window = evidence_plan()
    role = window['observation']['roles'][0]
    role.pop('identity_evidence')
    normalisation().normalise_plan(plan, [window])
    assert plan['focus_role_bindings'] == []
    assert 'identity_evidence' not in role
    with pytest.raises(ValueError, match='fine/identity_evidence') as caught:
        contracts.validate_fine(window['observation'], window)
    normalisation().add_valid_choices(caught.value, plan, [window])
    assert caught.value.violation['valid_choices']['bindable_rows'] == []
    assert caught.value.violation['valid_choices']['focus_binding_blockers'] == [dict(
        window_id=window['window_id'], role_id='A', reason='observation_identity_evidence_required')]


def evidence_plan():
    plan, catalog, window = short_plan()
    role = dict(role_id='A', identity_confirmed=True, identity_evidence='本窗口外形明确', state='站立')
    window['observation']['roles'] = [role]
    for event in window['observation']['events']:
        event['role_ids'] = ['A']
    for usable in window['observation']['usable_ranges']:
        usable['role_ids'] = ['A']
    plan['segments'][0]['role_ids'] = ['A']
    role['entity_id'] = window['source_sha256'] + ':observed_entity'
    window['qfs_identity'] = [dict(entity_id=window['source_sha256'] + ':observed_entity', canonical_name='规范人物', aliases=['Alias'])]
    plan['segments'][0]['source_out_s'] = 20
    plan['focus_role_id'] = 'focus'
    plan['cast'] = [dict(cast_id='story_role', canonical_name='Alias', entity_id='approximate', voice='Ethan',
                        bindings=[dict(window_id='unknown', role_id='unknown')])]
    plan['focus_role_bindings'] = [dict(window_id='unknown', role_id='unknown')]
    plan['audio_draft'] = [dict(id='line', text='你好。', voice='Ethan', mode='dialogue',
        speaker=dict(window_id=window['window_id'], role_id=role['role_id']), segment_ids=[plan['segments'][0]['segment_id']])]
    return plan, catalog, window


def test_alias_binding_copies_observed_entity_and_focus_evidence_then_passes_original_validator():
    plan, catalog, window = evidence_plan()
    normalisation().normalise_plan(plan, [window], speech_mode='mixed', fps=plan['fps'])
    role = window['observation']['roles'][0]
    assert plan['cast'][0]['entity_id'] == role['entity_id']
    assert plan['focus_role_bindings'] == [dict(window_id=window['window_id'], role_id=role['role_id'],
                                               identity_evidence=role['identity_evidence'])]
    contracts.validate_plan(plan, catalog, [window], plan['reference_sha256'], require_audio_draft=True)
    before = deepcopy(plan)
    normalisation().normalise_plan(plan, [window], speech_mode='mixed', fps=plan['fps'])
    assert plan == before  # Re-validation cannot append spurious audit records.


def test_unconfirmed_identity_without_alternative_remains_rejected_with_triples():
    plan, catalog, window = evidence_plan()
    window['observation']['roles'][0]['identity_confirmed'] = False
    normalisation().normalise_plan(plan, [window])
    assert plan['cast'] == []
    with pytest.raises(ValueError) as caught:
        contracts.validate_plan(plan, catalog, [window], plan['reference_sha256'])
    normalisation().add_valid_choices(caught.value, plan, [window])
    assert caught.value.violation['expected']['bindable_triples'] == []


def dialogue_plan(lengths=(4, 4, 4)):
    segments, cursor = [], 0
    for index, length in enumerate(lengths):
        segments.append(dict(segment_id=f's{index}', window_id='w', source_id='source',
            source_in_s=cursor, source_out_s=cursor + length, role_ids=['A'] if index == 1 else ['B'], speed=1))
        cursor += length
    return dict(segments=segments, audio_draft=[dict(id='line', text='你好。', voice='Ethan', mode='dialogue',
        speaker=dict(window_id='w', role_id='B'), segment_ids=['s1'], start_s=4, end_s=5,
        audio_relation=[dict(segment_id='s1', relation='on_screen')])])


def test_dialogue_nearest_tie_uses_earlier_fitting_segment_and_records_all_changes():
    plan = dialogue_plan()
    normalisation().normalise_plan(plan, speech_mode='mixed')
    line = plan['audio_draft'][0]
    assert line['segment_ids'] == ['s0']
    assert 'start_s' not in line and 'end_s' not in line
    assert line['audio_relation'] == [dict(segment_id='s0', relation='on_screen')]
    contracts.validate_audio_draft(plan['audio_draft'], plan['segments'])
    contracts.validate_dialogue_speakers(plan['audio_draft'], plan['segments'])
    assert len(plan['plan_normalisations']) == 4


def test_dialogue_skips_short_nearest_candidate_then_moves_to_fitting_one():
    plan = dialogue_plan((.2, 4, 2))
    normalisation().normalise_plan(plan, speech_mode='mixed')
    assert plan['audio_draft'][0]['segment_ids'] == ['s2']


def test_short_visible_windows_use_existing_earlier_anchor():
    plan = dialogue_plan((.2, 4, .2))
    plan['audio_draft'][0].update(start_s=.3, end_s=1.3)
    normalisation().normalise_plan(plan, speech_mode='mixed')
    assert plan['audio_draft'][0]['identity_anchor_segment_id'] == 's0'
    assert plan['audio_draft'][0]['segment_ids'] == ['s1']
    contracts.validate_audio_draft(plan['audio_draft'], plan['segments'])
    contracts.validate_dialogue_speakers(plan['audio_draft'], plan['segments'])


def test_no_earlier_anchor_and_no_fitting_window_becomes_narration_when_allowed():
    plan = dialogue_plan((4, 4, .2))
    plan['segments'][0]['role_ids'] = ['A']
    normalisation().normalise_plan(plan, speech_mode='mixed')
    assert plan['audio_draft'][0]['mode'] == 'narration'
    assert plan['audio_draft'][0]['speaker'] is None
    contracts.validate_audio_draft(plan['audio_draft'], plan['segments'])


def test_never_visible_speaker_and_narration_forbidden_still_fails():
    plan = dialogue_plan()
    for s in plan['segments']:
        s['role_ids'] = ['A']
    normalisation().normalise_plan(plan, allow_narration=False)
    with pytest.raises(ValueError, match='dialogue_speaker_not_in_selected_segments') as caught:
        contracts.validate_dialogue_speakers(plan['audio_draft'], plan['segments'])
    normalisation().add_valid_choices(caught.value, plan)
    assert caught.value.violation['valid_choices']['speaker_visible_segments'] == {'line': []}


def test_library_mode_does_not_normalise_real_model_fields():
    context, plan = read('observations.json'), read('glm_182_plan_0.json')
    before = deepcopy(plan)
    with pytest.raises(ValueError, match='cast_entity_not_supported_by_observation'):
        real_validator(context, creative=False)(plan)
    assert plan == before


def test_same_module_repairs_fine_dialogue_using_only_surviving_events():
    flow, plan = speaker_fixture()
    base = plan['segments'][0]
    plan['segments'] = [dict(base, source_in_s=5, source_out_s=9),
                        dict(base, segment_id='visible', source_in_s=1, source_out_s=5)]
    plan['audio_draft'][0]['speaker'] = deepcopy(flow.story_context['audio_draft'][0]['speaker'])
    flow.plan_check(plan)
    assert plan['audio_draft'][0]['segment_ids'] == ['visible']
    assert plan['plan_normalisations']


def test_repeated_source_check_threshold_and_free_alternatives():
    plan, catalog, window = short_plan()
    first = plan['segments'][0]
    first.update(source_in_s=11, source_out_s=13)
    plan['segments'].append(dict(first, segment_id='repeat', source_in_s=12, source_out_s=14))
    with pytest.raises(ValueError, match='repeated_source_footage') as caught:
        normalisation().validate_repeated_footage(plan, [window], catalog)
    expected = caught.value.violation['expected']
    assert expected['conflicts'][0]['segment_ids'] == [first['segment_id'], 'repeat']
    assert expected['conflicts'][0]['overlap_s'] == 1
    assert expected['free_alternative_ranges']
    assert all(r['source_out_s'] <= 11 or r['source_in_s'] >= 14 for r in expected['free_alternative_ranges'])
    plan['segments'][1]['source_in_s'] = 12.5
    normalisation().validate_repeated_footage(plan, [window], catalog)  # Exactly .5 allowed.
    plan['segments'][1]['source_in_s'] = 12.49
    with pytest.raises(ValueError):
        normalisation().validate_repeated_footage(plan, [window], catalog)
    plan['segments'][1]['source_id'] = 'other_source'
    normalisation().validate_repeated_footage(plan, [window], catalog)


def test_real_xueshan_plan_requires_parent_lineage_to_find_repeated_movie_shots():
    plan = read('xueshan_fine_plan_0.json')
    # The supplied real fine plan only addresses chain_parent, with disjoint ranges.
    normalisation().validate_repeated_footage(plan)
    # Explicit synthetic mapping of the two repeat intervals in the incident.
    # These are NOT claimed to be a recovered original server provenance receipt.
    intervals = [(6, 7, 100, 101), (19, 20, 100, 101), (8, 10, 102, 104), (21, 22, 102, 103)]
    parent = dict(segments=[dict(start_s=0, end_s=25.5, source_ranges=[dict(source_id='movie',
        start_s=a, end_s=b, source_in_s=c, source_out_s=d) for a, b, c, d in intervals])])
    with pytest.raises(ValueError, match='repeated_source_footage') as caught:
        normalisation().validate_repeated_footage(plan, parent=parent)
    expected = caught.value.violation['expected']
    assert ['clip_3', 'clip_6'] in [r['segment_ids'] for r in expected['conflicts']]
    assert expected['free_alternative_ranges']
    assert all(r['source_out_s'] <= 6 or r['source_in_s'] >= 7 for r in expected['free_alternative_ranges'])


def test_normalised_plan_artifact_replays_without_rewriting_raw_paid_reply(tmp_path, monkeypatch):
    from test_protocol_feedback import harness
    plan, catalog, window = evidence_plan()
    context = dict(windows=[window], catalog=catalog, reference_duration_s=30,
        reference=dict(reference_sha256=plan['reference_sha256'], strategy=dict(speech_mode='mixed')))
    raw = deepcopy(plan)
    check = real_validator(context)
    mcp, media, sent = harness(tmp_path, monkeypatch, [raw], provider='official_vision_mcp_in_opencode')
    accepted = mcp.call('plan_0', 'prompt', media, check)
    assert accepted['plan_normalisations']
    assert len(sent) == 1
    parsed = list((mcp.output / 'calls').glob('*/parsed.json'))
    assert len(parsed) == 1 and json.loads(parsed[0].read_text()) == plan
    assert mcp.call('plan_0', 'prompt', media, check) == accepted
    assert len(sent) == 1


def test_source_lineage_survives_rough_context_and_fine_subrange_mapping():
    from omni_story.library.story_context import build_rough_context
    from omni_story.library.render import compile_library_plan
    plan, catalog, window = evidence_plan()
    normalisation().normalise_plan(plan, [window], speech_mode='mixed', fps=plan['fps'])
    catalog['sources'][0]['path'] = 'synthetic-movie.mp4'
    compiled = compile_library_plan(catalog, plan, fps=plan['fps'])
    context = build_rough_context(dict(reference_sha256=plan['reference_sha256'], theme='主题', intended_takeaway='主题'),
        plan, dict(sha256='a' * 64, duration_s=compiled['duration_s'], provenance=compiled['segments']), windows=[window])
    assert context['plan_normalisations'] == plan['plan_normalisations']
    mapped = normalisation().source_ranges(dict(source_id='chain_parent', source_in_s=.5, source_out_s=1.5), context)
    assert mapped[0]['source_in_s'] == pytest.approx(17.55)
    assert mapped[0]['source_out_s'] == pytest.approx(18.55)


def test_film_cast_snapshot_alias_is_exact_and_never_confirms_a_role():
    plan, _, window = evidence_plan()
    window['qfs_identity'][0]['aliases'] = []
    window['qfs_identity'][0]['canonical_name'] = '规范人物'
    plan['cast'][0]['canonical_name'] = '别名'
    cast = dict(characters=[dict(name='规范人物', aliases=['别名'])])
    normalisation().normalise_plan(plan, [window], film_cast=cast)
    assert plan['cast'][0]['entity_id'] == window['observation']['roles'][0]['entity_id']
    window['observation']['roles'][0]['identity_confirmed'] = False
    normalisation().normalise_plan(plan, [window], film_cast=cast)
    assert plan['cast'] == []


def test_fine_production_rejects_direct_parent_overlap_even_without_original_lineage():
    flow, plan = speaker_fixture()
    plan['segments'].append(dict(plan['segments'][0], segment_id='repeated', source_in_s=2, source_out_s=4))
    with pytest.raises(ValueError, match='repeated_source_footage') as caught:
        flow.plan_check(plan)
    assert caught.value.violation['expected']['conflicts'][0]['segment_ids'] == ['clip', 'repeated']
