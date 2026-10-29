"""The received fine revision must be able to restore speech dropped by roughcut."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from omni_story.library import audio_measurement, audio_render, contracts, story_context, visual_audio
from omni_story.library.media import sha256_file
from test_unattended_outcomes import fine_fixture


FIXTURES = Path(__file__).parent / 'fixtures/e2e_visual_failure_20261029'


def evidence(name):
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


def received_plan():
    return received_reply('glm_171_chain_e2e_v1_fine_revise.json')


def received_reply(name):
    return contracts.parse_model_json(evidence(name)['result']['content'][0]['text'])


def recovery_case(tmp_path, *, renamed=False):
    plan = received_plan()
    if renamed:
        # Same received geometry and durations, unrelated identities and wording.
        for index, segment in enumerate(plan['segments']):
            old = segment['segment_id']
            segment['segment_id'] = f'shot_{index}'
            for line in plan['audio_draft']:
                line['segment_ids'] = [segment['segment_id'] if ident == old else ident
                                       for ident in line['segment_ids']]
            for slot in plan['slots']:
                slot['segment_ids'] = [segment['segment_id'] if ident == old else ident
                                      for ident in slot['segment_ids']]
        for index, line in enumerate(plan['audio_draft']):
            line.update(id=f'utterance_{index}', text=f'另一参考第{index}句。')
    outcome = evidence('run_outcome.json')
    durations = {row['id']: row['actual_end_s'] - row['start_s']
                 for row in outcome['audio_measurement']['line_windows']}
    # The first waveform's measured duration is recorded in glm_171's reason/limitations.
    durations = [2.08, *(durations[f'line_{index}'] for index in (2, 3, 4))]
    reference = dict(reference_sha256='b' * 64, theme='参考目标', intended_takeaway='参考表达')
    rows, cursor = [], 0.
    for segment in plan['segments']:
        end = cursor + segment['source_out_s'] - segment['source_in_s']
        rows.append(dict(id=segment['segment_id'], start_s=cursor, end_s=end, purpose=segment['reason']))
        cursor = end
    parent = dict(reference=reference, video_sha256='a' * 64, duration_s=cursor,
        segments=rows, audio_draft=deepcopy(plan['audio_draft']), received_tts=[],
        partial=True, partial_reasons=outcome['partial_reasons'])
    first = plan['audio_draft'][0]['id']
    if renamed:
        parent['partial_reasons'] = ['audio_timing_unresolved', f'dropped_line:{first}:tts_exceeds_window']
    parent['timing_problems'] = [dict(line_id=first, reason='tts_exceeds_window',
                                    actual_duration_s=durations[0], available_s=2.)]
    parent['dropped_lines'] = [dict(id=first, reason='tts_exceeds_window',
                                   timing_problem=deepcopy(parent['timing_problems'][0]))]
    for line, segment, duration in zip(parent['audio_draft'], rows, durations):
        line.update(start_s=segment['start_s'], end_s=segment['end_s'])
        path = tmp_path / (line['id'] + '.wav')
        path.write_bytes(b'pinned received waveform')
        parent['received_tts'].append(dict(id=line['id'], text=line['text'], voice=line['voice'],
            actual_duration_s=duration, audio_path=str(path), audio_sha256=sha256_file(path)))
    return reference, parent, plan


def build(reference, parent, plan):
    from omni_story.library.line_windows import segment_output_timeline
    timeline = segment_output_timeline(plan['segments'])
    render = dict(sha256='c' * 64, duration_s=list(timeline.values())[-1]['end_s'], provenance=[
        dict(segment_id=ident, output_in_s=row['start_s'], output_out_s=row['end_s'])
        for ident, row in timeline.items()])
    return story_context.build_fine_context(reference, parent, plan, render)


@pytest.mark.parametrize('renamed', [False, True])
def test_received_revision_restores_fitted_line_without_tts_and_measures_it(tmp_path, monkeypatch, renamed):
    reference, parent, plan = recovery_case(tmp_path, renamed=renamed)
    snapshot = deepcopy((parent, plan))
    context = build(reference, parent, plan)
    assert context.get('dropped_lines', []) == []
    assert not context.get('partial') and not context.get('partial_reasons')
    assert context.get('timing_problems', []) == []
    assert (parent, plan) == snapshot
    performed = story_context.synthesize_draft(context, tmp_path / 'tts',
        tts=lambda *a, **kw: pytest.fail('Reuse the received waveforms; never resend TTS'))
    assert not performed['timing_problems']
    spoken = story_context.mixable_draft(performed)
    assert [line['id'] for line in spoken] == [line['id'] for line in plan['audio_draft']]
    import numpy as np
    monkeypatch.setattr(audio_render, '_samples', lambda path: np.full((9 * 48000, 2), .1))
    measurement = audio_measurement.measure_mix('mix', 'music', 'voice', 'mux',
        [dict(line, actual_end_s=line['end_s']) for line in spoken], music_expected=False)
    assert measurement['passed']
    assert [row['id'] for row in measurement['line_windows']] == [line['id'] for line in spoken]

    # Recovery is based on the exact received text/voice, never a duration guess.
    for field, changed in [('text', '另一句'), ('voice', 'Ethan')]:
        unmatched = deepcopy(parent)
        unmatched['received_tts'][0][field] = changed
        assert build(reference, unmatched, plan)['dropped_lines'] == parent['dropped_lines']
    for invalid in (float('nan'), float('inf'), True):
        unmatched = deepcopy(parent)
        unmatched['received_tts'][0]['actual_duration_s'] = invalid
        assert build(reference, unmatched, plan)['dropped_lines'] == parent['dropped_lines']
    anchored = deepcopy(parent)
    anchored['dropped_lines'][0]['reason'] = 'start_anchor_overlap'
    assert build(reference, anchored, plan)['dropped_lines'] == anchored['dropped_lines']
    too_short = deepcopy(plan)
    too_short['segments'][0]['freeze_tail_s'] = 0
    too_short['audio_draft'][0]['end_s'] = 2
    # An exhausted preflight warning may reach the measured backstop; it may not revive speech.
    too_short['line_window_warnings'] = [dict(code='audio_line_window_below_minimum')]
    for line in too_short['audio_draft'][1:]:
        line['start_s'] -= .2
        line['end_s'] -= .2
    assert build(reference, parent, too_short)['dropped_lines'] == parent['dropped_lines']

    # Other partial evidence survives a successful timing recovery.
    limited = deepcopy(parent)
    limited['partial_reasons'].append('fine_revision_exhausted')
    recovered = build(reference, limited, plan)
    assert recovered['partial'] and recovered['partial_reasons'] == ['fine_revision_exhausted']
    limited['dropped_lines'].append(dict(id=plan['audio_draft'][1]['id'], reason='other_exclusion'))
    recovered = build(reference, limited, plan)
    assert recovered['partial'] and recovered['dropped_lines'] == limited['dropped_lines'][1:]
    # A recovered window is still subject to the real scheduler's overlap check.
    overlap = deepcopy(plan)
    overlap['audio_draft'][1]['segment_ids'] = [overlap['segments'][0]['segment_id'],
                                              overlap['segments'][1]['segment_id']]
    overlap['audio_draft'][1]['start_anchor'] = dict(segment_id=overlap['segments'][0]['segment_id'], offset_s=0)
    scheduled = story_context.synthesize_draft(build(reference, parent, overlap), tmp_path / 'tts',
        tts=lambda *a, **kw: pytest.fail('No resend'))
    assert any(row['reason'] == 'start_anchor_overlap' for row in scheduled['timing_problems'])


@pytest.mark.parametrize('remaining_partial', [False, True])
def test_finish_uses_current_context_instead_of_reapplying_rough_timing_flags(
        tmp_path, monkeypatch, remaining_partial):
    reference, parent, plan = recovery_case(tmp_path)
    value, selected, review = fine_fixture(tmp_path / 'run', monkeypatch)
    value.story_context = parent
    # Isolate the second bug: even a corrected build result was overwritten in finish().
    corrected = deepcopy(parent)
    corrected.update(dropped_lines=[], timing_problems=[], partial=False, partial_reasons=[])
    if remaining_partial:
        corrected.update(partial=True, partial_reasons=['fine_revision_exhausted'])
    value.fine_context = lambda *a: deepcopy(corrected)
    review['target'] = received_reply('glm_173_chain_e2e_v1_fine_review_r.json')
    seen = []

    def execute(*args, **options):
        current = options['story_context']
        assert current.get('partial_reasons') == corrected['partial_reasons']
        assert current.get('partial') is remaining_partial
        seen.append(current)
        return dict(status='delivered', final_video=selected['rendered_path'], final_sha256=selected['sha256'],
            content_delivery_ready=True, technical_delivery_verified=True, audio_measurement=dict(passed=True),
            partial=current['partial'], partial_reasons=current['partial_reasons'])

    monkeypatch.setattr(visual_audio, 'execute', execute)
    result = value.finish(selected, review)
    assert len(seen) == 1
    assert result['partial'] is remaining_partial
    assert result['partial_reasons'] == corrected['partial_reasons']
    assert result['joint_quality_gate'] is not remaining_partial
