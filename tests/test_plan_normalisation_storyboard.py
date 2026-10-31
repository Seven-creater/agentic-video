"""Replay received S7 storyboard plans; keep creative failures model-owned."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from omni_story.library import contracts, pipeline, replay_check
from omni_story.library.line_windows import segment_output_timeline
from omni_story.library.plan_normalisation import normalise_plan


FIXTURES = Path(__file__).parent / 'fixtures'


def read(name):
    return json.loads((FIXTURES / ('s7_' + name + '.json')).read_text())


def validator(context):
    reference = context['reference']
    return replay_check.nested_validator(pipeline.execute, 'validate_current_plan',
        context=context, creative_audio=True, editing_reference=None, editing_v2=False,
        maximum_segments=32, progress_driven=True,
        ref=dict(sha256=reference['reference_sha256'], duration_s=context['reference_duration_s'],
                 audio_stream_index=context['reference_audio_stream_index']),
        reference_reading=reference, semantic_audit=False, semantic_pipeline=None,
        sources=context['catalog'], strategy=contracts.reference_strategy(reference),
        visual_only=True, windows=context['watched_windows'])


def test_s7_real_replies_remove_bookkeeping_failures_and_preserve_creative_conflict():
    context = read('context')
    windows = {w['window_id']: w for w in context['watched_windows']}
    first = read('glm_193_plan_0')
    goose = first['cast'][1]
    assert goose['cast_id'] == 'employee_goose' and goose['voice'] == 'Cherry'
    assert [r['id'] for r in first['audio_draft'] if r['voice'] == 'Cherry'] == ['line_1', 'line_6']
    assert all(r.get('cast_id') != goose['cast_id'] for r in first['audio_draft'])
    assert not any(t['entity_id'] == goose['entity_id'] for t in
                   contracts.plan_field_contract(windows=windows)['cast']['bindable_triples'])
    normalise_plan(first, windows, film_cast=context['film_cast'], speech_mode='dialogue')
    assert [c['cast_id'] for c in first['cast']] == ['boss_panda']
    assert any(c['reason'] == 'drop_unreferenced_unbound_cast' for c in first['plan_normalisations'])
    contracts.validate_task_cast(first['cast'], windows)

    for name in ['glm_194_plan_0_repair', 'glm_195_plan_0_repair2']:
        plan = read(name)
        original = deepcopy(plan)
        # The full production closure performs normalization before validation.
        with pytest.raises(ValueError, match='^plan:repeated_source_footage$') as caught:
            validator(context)(plan)
        assert caught.value.violation['expected']['conflicts'][0]['overlap_s'] == 2
        assert caught.value.violation['expected']['conflicts'][0]['segment_ids'] == ['seg_7', 'seg_3']
        if name == 'glm_194_plan_0_repair':
            pieces = plan['segments'][5:7]
            assert [(s['source_in_s'], s['source_out_s']) for s in pieces] == [(2859, 2861), (2861, 2862)]
            assert plan['slots'][4]['segment_ids'] == [s['segment_id'] for s in pieces]
            assert plan['audio_draft'][5]['segment_ids'] == [s['segment_id'] for s in pieces]
            assert any(c['reason'] == 'split_contiguous_usable_ranges' for c in plan['plan_normalisations'])
            for s in pieces:
                assert contracts.matching_plan_source_ranges(s, contracts.plan_source_range_contract(windows[s['window_id']]))
                contracts.validate_plan_segment_roles(s, windows[s['window_id']], '$.segments')
        else:
            assert plan['segments'] == original['segments']
        assert [r['text'] for r in plan['audio_draft']] == [r['text'] for r in original['audio_draft']]
        before = deepcopy(plan)
        normalise_plan(plan, windows, film_cast=context['film_cast'], speech_mode='dialogue', fps=plan['fps'])
        assert plan == before


def split_case(bounds=((0, 2), (2, 4), (4, 6))):
    window = dict(window_id='w', source_id='source', source_sha256='a' * 64,
        source_start_s=100, source_end_s=106, status='watched', observation=dict(
            window_id='w', source_id='source', uncertainties=[],
            roles=[dict(role_id='A', identity_confirmed=True, entity_id='a' * 64 + ':actor',
                        state='站立', identity_evidence='可见角色')],
            events=[dict(local_start_s=0, local_end_s=6, role_ids=['A'], observed_fact='角色站立')],
            usable_ranges=[dict(local_in_s=a, local_out_s=b, role_ids=['A'], event_indices=[0],
                                continuity_notes='持续画面') for a, b in bounds]))
    segment = dict(segment_id='s', slot_id='slot', source_id='source', window_id='w',
        source_in_s=100, source_out_s=106, role_ids=['A'], speed=2, freeze_tail_s=1)
    return dict(segments=[segment], slots=[dict(slot_id='slot', segment_ids=['s'])],
        editing_bindings=[dict(method_id='m', segment_ids=['s'])],
        audio_relation=[dict(segment_id='s', relation='narration')],
        audio_draft=[dict(id='line', text='你好。', voice='Ethan', mode='narration', segment_ids=['s'],
            identity_anchor_segment_id='s', start_anchor=dict(segment_id='s', offset_s=2.5),
            audio_relation=[dict(segment_id='s', relation='narration')])]), window


@pytest.mark.parametrize('bounds', [((0, 2), (2, 4), (4, 6)), ((3, 6), (0, 3)),
    ((0, 2), (2.0005, 6)), ((0, 3), (2, 6))])
def test_split_updates_every_contract_reference_preserves_timing_and_is_idempotent(bounds):
    plan, window = split_case(bounds)
    plan['segments'][0]['caption'] = dict(text='字幕', start_s=.5, end_s=3.5,
        evidence=[dict(window_id='w', event_indices=[0])])
    # A preexisting suffix must never be overwritten by a derived ID.
    plan['segments'].append(dict(plan['segments'][0], segment_id='s_2', source_in_s=100, source_out_s=101))
    plan['slots'][0]['segment_ids'].append('s_2')
    normalise_plan(plan, [window], fps=30)
    pieces = plan['segments'][:-1]
    assert len(pieces) >= 2
    ids = [s['segment_id'] for s in pieces]
    assert len({s['segment_id'] for s in plan['segments']}) == len(plan['segments'])
    assert plan['slots'][0]['segment_ids'] == ids + ['s_2']
    assert plan['editing_bindings'][0]['segment_ids'] == ids
    line = plan['audio_draft'][0]
    assert line['segment_ids'] == ids
    for relations in [line['audio_relation'], plan['audio_relation']]:
        assert [r['segment_id'] for r in relations] == ids
        assert all(r['relation'] == 'narration' for r in relations)
    assert line['identity_anchor_segment_id'] == ids[0]
    timeline = segment_output_timeline(pieces, fps=30)
    anchor = line['start_anchor']
    assert timeline[anchor['segment_id']]['start_s'] + anchor['offset_s'] == pytest.approx(2.5)
    assert sum(s.get('freeze_tail_s', 0) for s in pieces) == 1
    assert all(not s.get('freeze_tail_s', 0) for s in pieces[:-1])
    assert sum((s['source_out_s'] - s['source_in_s']) / s['speed'] for s in pieces) == 3
    captions = [(timeline[s['segment_id']]['start_s'] + s['caption']['start_s'],
                 timeline[s['segment_id']]['start_s'] + s['caption']['end_s'])
                for s in pieces if s.get('caption')]
    assert captions[0][0] == .5 and captions[-1][1] == 3.5
    assert all(a[1] == b[0] for a, b in zip(captions, captions[1:]))
    for s in pieces:
        assert contracts.matching_plan_source_ranges(s, contracts.plan_source_range_contract(window))
        contracts.validate_plan_segment_roles(s, window, '$.segments')
    contracts.validate_audio_draft(plan['audio_draft'], plan['segments'], check_line_windows=False, fps=30)
    before = deepcopy(plan)
    normalise_plan(plan, [window], fps=30)
    assert plan == before


def caption_case(bounds, caption):
    plan, window = split_case(bounds)
    segment = plan['segments'][0]
    segment.update(speed=1, freeze_tail_s=0, look='none', framing='fit',
        source_out_s=100 + bounds[-1][1],
        caption=dict(caption, position='bottom', font_size=20,
                     evidence=[dict(window_id='w', event_indices=[0])]))
    plan['slots'][0]['intended_takeaway'] = '一段画面'
    plan.update(fps=30, reference_sha256='ref', audio_mode='silent', width=64, height=64, limitations=[])
    plan.pop('audio_draft')
    plan.pop('audio_relation')
    plan.pop('editing_bindings')
    return plan, window, dict(sources=[dict(source_id='source', duration_s=106, sha256='a' * 64)])


def piece_quantized_duration(segment, fps=30):
    span = (segment['source_out_s'] - segment['source_in_s']) / segment.get('speed', 1)
    return (round(span * fps) + round(segment.get('freeze_tail_s', 0) * fps)) / fps


def test_split_clips_captions_to_each_piece_frame_quantized_duration():
    # 4.13 s quantizes up as one segment (124 frames) but the last 2.069 s piece
    # quantizes down (62 frames), so an unclamped caption end would exceed it.
    plan, window, catalog = caption_case(((0, 2.061), (2.061, 4.13)), dict(text='字幕', start_s=.5, end_s=4.13))
    normalise_plan(plan, [window], fps=30)
    pieces = plan['segments']
    assert [(s['source_in_s'], s['source_out_s']) for s in pieces] == [(100, 102.061), (102.061, 104.13)]
    for segment in pieces:
        caption = segment['caption']
        assert caption['end_s'] <= piece_quantized_duration(segment) + 1e-9
        assert round(caption['end_s'] * 30) > round(caption['start_s'] * 30)
    contracts.validate_plan(plan, catalog, [window], 'ref', allow_unfocused=True, max_duration_s=None)


def test_split_drops_captions_that_quantize_to_no_output_frame():
    # The 0.01 s remainder on the second piece is shorter than one frame at 30 fps.
    plan, window, catalog = caption_case(((0, 1), (1, 2)), dict(text='字幕', start_s=.98, end_s=1.01))
    normalise_plan(plan, [window], fps=30)
    pieces = plan['segments']
    assert [(s['source_in_s'], s['source_out_s']) for s in pieces] == [(100, 101), (101, 102)]
    assert pieces[0]['caption']['end_s'] == 1
    assert 'caption' not in pieces[1]
    contracts.validate_plan(plan, catalog, [window], 'ref', allow_unfocused=True, max_duration_s=None)


def test_split_never_exceeds_the_render_segment_cap():
    plan, window = split_case()
    capped = deepcopy(plan)
    normalise_plan(capped, [window], max_segments=2)  # 1 segment would become 3.
    assert capped['segments'] == plan['segments']
    assert not any(c['reason'] == 'split_contiguous_usable_ranges'
                   for c in capped.get('plan_normalisations', []))
    normalise_plan(capped, [window], max_segments=3)
    assert len(capped['segments']) == 3
    assert capped['slots'][0]['segment_ids'] == [s['segment_id'] for s in capped['segments']]
    before = deepcopy(capped)
    normalise_plan(capped, [window], max_segments=3)
    assert capped == before


def renamed_window(window, window_id, *, role_id=None, entity_id=None, qfs=True):
    clone = deepcopy(window)
    clone['window_id'] = window_id
    observation = clone['observation']
    observation['window_id'] = window_id
    if role_id is not None:
        for event in observation['events'] + observation['usable_ranges']:
            event['role_ids'] = [role_id if role == 'A' else role for role in event['role_ids']]
        observation['roles'][0]['role_id'] = role_id
    if entity_id is not None:
        observation['roles'][0]['entity_id'] = entity_id
    clone['qfs_identity'] = ([] if not qfs else
        [dict(entity_id=observation['roles'][0]['entity_id'], canonical_name='甲')])
    return clone


def test_binding_derivation_is_stable_across_normalisation_passes():
    # The cast has no name/entity match; its declared unused window and its
    # cast_id line both point at role A, while the same confirmed entity also
    # appears in the EDL window. Every derivation path must agree on one
    # binding list, or a second pass would rewrite it and append records.
    plan, window = split_case(((0, 6),))
    declared_window = renamed_window(window, 'declared_window')
    used_window = renamed_window(window, 'used_window')
    plan['segments'][0]['window_id'] = used_window['window_id']
    plan['audio_draft'] = [dict(id='line', text='你好。', voice='Ethan', mode='dialogue', cast_id='actor',
        speaker=dict(window_id='declared_window', role_id='A'), segment_ids=['s'])]
    plan['cast'] = [dict(cast_id='actor', entity_id='approximate', voice='Ethan',
                         bindings=[dict(window_id='declared_window', role_id='A')])]
    windows = [declared_window, used_window]
    normalise_plan(plan, windows)
    bindings = sorted((b['window_id'], b['role_id']) for b in plan['cast'][0]['bindings'])
    assert bindings == [('declared_window', 'A'), ('used_window', 'A')]
    contracts.validate_task_cast(plan['cast'], windows)
    contracts.validate_dialogue_speakers(plan['audio_draft'], plan['segments'],
        cast=plan['cast'], windows={w['window_id']: w for w in windows})
    before = deepcopy(plan)
    normalise_plan(plan, windows)
    assert plan == before


def test_bindings_cover_every_used_appearance_of_the_confirmed_entity():
    # Role names come from qfs_identity, so a window without that entry hides an
    # entity-equal role from the first pass; the binding list must not grow later.
    plan, window = split_case(((0, 6),))
    named_window = renamed_window(window, 'named_window')
    silent_window = renamed_window(window, 'silent_window', role_id='C', qfs=False)
    plan['segments'] = [
        dict(plan['segments'][0], segment_id='s1', window_id='named_window', source_in_s=100, source_out_s=106),
        dict(plan['segments'][0], segment_id='s2', window_id='silent_window', source_in_s=100, source_out_s=106)]
    plan['slots'][0]['segment_ids'] = ['s1', 's2']
    plan['cast'] = [dict(cast_id='actor', canonical_name='甲', entity_id='approximate', voice='Ethan', bindings=[])]
    windows = [named_window, silent_window]
    normalise_plan(plan, windows)
    bindings = sorted((b['window_id'], b['role_id']) for b in plan['cast'][0]['bindings'])
    assert bindings == [('named_window', 'A'), ('silent_window', 'C')]
    contracts.validate_task_cast(plan['cast'], windows)
    before = deepcopy(plan)
    normalise_plan(plan, windows)
    assert plan == before


def test_split_requires_full_coverage_and_each_pieces_confirmed_overlapping_role(monkeypatch):
    good, window = split_case()
    normalise_plan(good, [window])
    assert len(good['segments']) == 3
    alternate, window = split_case(((0, 3), (0, 2), (2, 6)))
    window['observation']['events'][0]['local_end_s'] = 2.5
    normalise_plan(alternate, [window])
    assert [(s['source_in_s'], s['source_out_s']) for s in alternate['segments']] == [(100, 102), (102, 106)]
    for failure in ['gap', 'range_role', 'event_role', 'unconfirmed', 'unknown_window']:
        plan, window = split_case()
        if failure == 'gap':
            window['observation']['usable_ranges'][1]['local_in_s'] = 2.01
        elif failure == 'range_role':
            window['observation']['usable_ranges'][1]['role_ids'] = []
        elif failure == 'event_role':
            window['observation']['events'][0]['local_end_s'] = 2
        elif failure == 'unconfirmed':
            window['observation']['roles'][0]['identity_confirmed'] = False
        else:
            plan['segments'][0]['window_id'] = 'unknown'
        before = deepcopy(plan['segments'])
        normalise_plan(plan, [window])
        assert plan['segments'] == before
        assert not any(c['reason'] == 'split_contiguous_usable_ranges' for c in plan.get('plan_normalisations', []))
    plan, window = split_case()
    before = deepcopy(plan['segments'])
    normalise_plan(plan, [window], catalog=dict(sources=[dict(source_id='source', duration_s=106,
        content_range=dict(content_start_s=100, content_end_s=105))]))
    assert plan['segments'] == before
    # Normalization must consult the validator helper, including future rules.
    shared_validator = contracts.validate_plan_segment_roles
    def reject_second_piece(segment, window, path, **kwargs):
        if segment['source_in_s'] == 102:
            raise ValueError('extra_role_rule')
        return shared_validator(segment, window, path, **kwargs)
    monkeypatch.setattr(contracts, 'validate_plan_segment_roles', reject_second_piece)
    plan, window = split_case()
    normalise_plan(plan, [window])
    assert len(plan['segments']) == 1


@pytest.mark.parametrize('link', ['cast_id', 'voice', 'binding'])
def test_empty_cast_binds_only_one_confirmed_same_role_entity_and_keeps_referenced_ambiguity(link):
    plan, window = split_case(((0, 6),))
    character = dict(cast_id='actor', entity_id='approximate', voice='Ethan', bindings=[])
    plan['cast'] = [character]
    line = plan['audio_draft'][0]
    line.update(mode='dialogue', speaker=dict(window_id='w', role_id='A'))
    if link == 'cast_id':
        line['cast_id'] = 'actor'
    elif link == 'binding':
        line['voice'] = 'Cherry'  # The explicit appearance supplies the identity.
        character['bindings'] = [dict(window_id='w', role_id='A')]
    normalise_plan(plan, [window])
    assert character['entity_id'] == window['observation']['roles'][0]['entity_id']
    assert character['bindings'] == [dict(window_id='w', role_id='A')]
    contracts.validate_task_cast(plan['cast'], [window])
    before = deepcopy(plan)
    normalise_plan(plan, [window])
    assert plan == before

    # A uniquely confirmed identity may be bound even if its watched appearance
    # is not in the EDL; an unused cast should be removed only after this lookup.
    unused, identity_window = split_case(((0, 6),))
    unused['segments'][0]['window_id'] = 'another_selection'
    unused['audio_draft'] = []
    entity = identity_window['observation']['roles'][0]['entity_id']
    unused['cast'] = [dict(cast_id='unused', entity_id=entity, bindings=[])]
    normalise_plan(unused, [identity_window])
    assert unused['cast'][0]['bindings'] == [dict(window_id='w', role_id='A')]

    # Equal voice alone cannot choose between two speaker entities.
    ambiguous, window = split_case(((0, 6),))
    ambiguous['cast'] = [dict(cast_id='actor', entity_id='approximate', voice='Ethan', bindings=[])]
    line = ambiguous['audio_draft'][0]
    line.update(cast_id='actor', speaker=dict(window_id='w', role_id='A'))
    window['observation']['roles'].append(dict(window['observation']['roles'][0], role_id='B', entity_id='a' * 64 + ':other'))
    ambiguous['audio_draft'].append(dict(line, id='other', speaker=dict(window_id='w', role_id='B')))
    normalise_plan(ambiguous, [window])
    assert ambiguous['cast'][0]['bindings'] == []
    with pytest.raises(ValueError, match='plan/cast/bindings:list_required'):
        contracts.validate_task_cast(ambiguous['cast'])

    # Dropping a cast referenced only as visual focus must not make that focus
    # drift to the one remaining character on the next normalization pass.
    plan['focus_role_id'] = 'missing'
    plan['cast'].append(dict(cast_id='missing', entity_id='missing_identity', bindings=[]))
    normalise_plan(plan, [window])
    assert plan['focus_role_bindings'] == []
    before = deepcopy(plan)
    normalise_plan(plan, [window])
    assert plan == before
