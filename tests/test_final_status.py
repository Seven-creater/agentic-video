"""S19 received verdicts, with local fake media/services; no model calls."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from omni_story.library import audio_render, clean_chain, final_visual_review, story_finecut, visual_audio
from omni_story.library.replay_check import nested_validator
from omni_story.library.run_outcome import write_run_outcome
from test_clean_chain import harness
from test_unattended_outcomes import fine_fixture


FIXTURES = Path(__file__).parent / 'fixtures'


@pytest.fixture
def evidence():
    return json.loads((FIXTURES / 's19_final_status.json').read_text())


def test_finish_uses_actual_av_verdict_after_fine_revision(tmp_path, monkeypatch, evidence):
    value, selected, _ = fine_fixture(tmp_path, monkeypatch)
    recorded = evidence['finecut']
    audio = deepcopy(recorded['creative_audio'])
    audio.update(final_video=selected['rendered_path'], final_sha256=selected['sha256'])
    value.fine_context = lambda *args: deepcopy(recorded['story_context'])
    monkeypatch.setattr(visual_audio, 'execute', lambda *args, **kwargs: audio)
    review = deepcopy(recorded['review'])
    result = value.finish(selected, review)
    assert review == recorded['review']  # Preserve the pre-audio partial/revise evidence.
    assert result['status'] == 'completed'
    assert result['joint_quality_gate'] is result['joint_quality_gate_passed'] is True
    saved = value.base / 'result.json'
    original = saved.read_bytes()
    monkeypatch.setattr(visual_audio, 'execute', lambda *a, **k: pytest.fail('cached audio restarted'))
    assert value.run() == result
    assert saved.read_bytes() == original


@pytest.fixture
def final_chain(harness, monkeypatch, evidence):
    def run(problem=None, *, deferred=True):
        case = harness()
        if deferred:
            case.update(rough_status='rough_planned', deferred_review=True)
        case['review']['missing_core_meaning'] = []
        case['arguments']['provider_config'] = {'creative_audio': True}
        # The rough's reference and preview are irrelevant to these received final verdicts.
        monkeypatch.setattr(clean_chain, 'build_rough_context', lambda *a, **k: dict(
            reference={}, segments=[], audio_draft=[], received_tts=[],
            timing_problems=['Controlled test skips rough preview.']))
        monkeypatch.setattr(clean_chain, 'synthesize_draft', lambda story, *a, **k: story)
        fine = deepcopy(evidence['finecut'])
        audio = fine['creative_audio']
        if problem is not None:
            # A stale positive summary must not overrule a later actual-media failure either.
            fine.update(status='completed', joint_quality_gate=True)
        if problem == 'av_fails':
            audio['review']['picture_audio_coherence'] = 'contradictory'
        elif problem == 'av_unverifiable':
            audio['review']['picture_audio_coherence'] = 'unverifiable'
        elif problem == 'av_revision':
            audio['review']['repair_at'] = 'audio'
        elif problem == 'av_selection':
            audio['review'].update(repair_at='selection', missing_core_meaning=['核心含义缺失'])
        elif problem == 'av_theme':
            audio['review']['theme_alignment'] = 'different'
        elif problem == 'audio_partial':
            audio['status'] = 'partial'
        elif problem == 'audio_partial_flag':
            audio['partial'] = True
            fine.update(status='completed_partial', partial=True)
        elif problem == 'joint_failed':
            audio['joint_quality_gate'] = False
        elif problem == 'not_ready':
            audio['content_delivery_ready'] = False
        elif problem == 'not_decoded':
            audio['technical_delivery_verified'] = False
        elif problem == 'measurement_failed':
            audio['audio_measurement']['passed'] = False

        def finecut(state, mcp, context, parent, ref, output):
            movie = output / 'delivery/story_finecut.mp4'
            movie.parent.mkdir(parents=True)
            movie.write_bytes(b'controlled delivered media')
            fine.update(final_video=str(movie), final_sha256=clean_chain.sha256_file(movie))
            audio.update(final_video=str(movie), final_sha256=fine['final_sha256'])
            return fine

        def subtitle(video, lines, output):
            assert Path(video).name == 'story_finecut.mp4'
            output.write_bytes(b'controlled subtitled media')
            paths = {name: str(output.with_suffix('.' + name)) for name in
                     ('receipt_path', 'subtitle_srt_path', 'subtitle_ass_path')}
            for path in paths.values():
                Path(path).write_bytes(b'controlled subtitle receipt')
            return dict(path=str(output), sha256=clean_chain.sha256_file(output), **paths)

        def visual(video, folder, mcp, **kwargs):
            verdict = deepcopy(evidence['final_visual_review'])
            if problem == 'visual_fails':
                verdict['status'] = 'needs_revision'
            receipt = folder / 'review.json'
            receipt.parent.mkdir(parents=True)
            receipt.write_text(json.dumps(verdict))
            return dict(verdict, receipt_path=str(receipt), reviewed_proxy=str(video),
                        final_sha256=clean_chain.sha256_file(video), evidence_sha256={})

        monkeypatch.setattr(clean_chain, 'execute_finecut', finecut)
        monkeypatch.setattr(audio_render, 'subtitle_final_video', subtitle)
        monkeypatch.setattr(final_visual_review, 'review_final_layout', visual)
        result = clean_chain.execute(**case['arguments'])
        assert (case['arguments']['output'] / 'result.json').read_bytes() == case['original_result_bytes']
        assert result['finecut'] == fine  # Aggregation must not rewrite received fine results.
        return case, result
    return run


@pytest.mark.parametrize('deferred', [True, False], ids=['deferred_rough', 'partial_rough'])
def test_latest_actual_media_pass_delivers_final_subtitled(final_chain, deferred):
    case, result = final_chain(deferred=deferred)
    assert result['status'] == 'completed'
    assert result['joint_quality_gate'] is True
    outcome = write_run_outcome(case['arguments']['output'], result)
    assert outcome['outcome'] == 'delivered'
    assert Path(outcome['final_video']).name == 'final_subtitled.mp4'


@pytest.mark.parametrize('problem', ['av_fails', 'av_unverifiable', 'av_revision', 'av_selection',
    'av_theme', 'visual_fails', 'audio_partial', 'audio_partial_flag', 'joint_failed',
    'not_ready', 'not_decoded', 'measurement_failed'])
def test_latest_failure_or_incomplete_audio_still_stops(final_chain, problem):
    case, result = final_chain(problem)
    assert result['joint_quality_gate'] is False
    assert result['status'] == ('needs_visual_revision' if problem == 'visual_fails' else
                                'needs_material_reselection' if problem == 'av_selection' else
                                'needs_audio_revision')
    outcome = write_run_outcome(case['arguments']['output'], result)
    assert outcome['outcome'] == 'stopped'
    assert outcome['final_video'] is None


def test_recorded_chain_resume_preserves_original_bytes(final_chain, monkeypatch, evidence):
    case, result = final_chain()
    output = case['arguments']['output']
    # Reproduce a settled old chain: caching must return its verdict, never repair history.
    result.update(status=evidence['chain']['status'], joint_quality_gate=False)
    saved = output / 'chain_result.json'
    saved.write_text(json.dumps(result, ensure_ascii=False, indent=4) + '\n')
    originals = {path: path.read_bytes() for path in output.rglob('*.json')}
    monkeypatch.setattr(clean_chain.pipeline, 'execute', lambda *a, **k: pytest.fail('recorded run restarted'))
    assert clean_chain.execute(**case['arguments']) == result
    assert {path: path.read_bytes() for path in originals} == originals


def test_recorded_fine_protocol_errors_remain_errors():
    value = story_finecut.StoryFinecut.__new__(story_finecut.StoryFinecut)
    value.story_context = {}
    value.normalize_times = lambda: True  # The recorded creative run normalized review intervals.
    validate = nested_validator(story_finecut.StoryFinecut.review, 'target_check',
                                self=value, result=dict(duration_s=45.5))
    replies = json.loads((FIXTURES / 's19_fine_reviews.json').read_text())
    for row in replies:
        original = deepcopy(row['reply'])
        if row['error']:
            with pytest.raises(ValueError, match='story_finecut:picture_audio_coherence_invalid'):
                validate(deepcopy(original))
            assert original['picture_audio_coherence'].startswith('coherent_with_issues：')
        else:
            validate(deepcopy(original))
            assert original['picture_audio_coherence'] == 'coherent'
        assert row['reply'] == original
