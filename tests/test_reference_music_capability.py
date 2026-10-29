"""Real reference reply regressions; no model requests or audio synthesis."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from omni_story.library import contracts, pipeline, protocol_feedback, replay_check
from omni_story.library import question_first, visual_story_trial
from omni_story.library.state import write_json


FIXTURE = Path(__file__).parent / 'fixtures/reference_music_capability/response.json'


@pytest.fixture
def reading():
    reply = json.loads(FIXTURE.read_text())
    return json.loads(reply['result']['content'][0]['text'])


def reference_validator(reading, config):
    return replay_check.nested_validator(pipeline.execute, 'validate_current_reference',
        ref=dict(sha256=reading['reference_sha256'], duration_s=27),
        creative_audio=config.get('creative_audio') is True, config=config)


def assert_capability_error(error, reading):
    assert str(error) == 'creative_reference_music_requires_explicit_bed_with_new_speech'
    violation = error.violation
    assert violation['path'] == '$.strategy.timing_driver'
    assert violation['value'] == 'music'
    assert violation['expected']['allowed'] == ['speech', 'action']
    assert violation['expected']['speech_mode'] == reading['strategy']['speech_mode']
    assert violation['expected']['music_bed_configured'] is False
    assert 'speech_form/visual_form' in violation['repair_hint']
    feedback = protocol_feedback.diagnose('reference', reading,
        dict(stage='reference', duration_s=27, validation_error=error))
    assert feedback[0]['path'] == violation['path']
    assert feedback[0]['expected'] == violation['expected']
    json.dumps(feedback, ensure_ascii=False, allow_nan=False)


def test_received_reply_requires_executable_strategy_and_keeps_supported_matrix(reading):
    original = deepcopy(reading)
    with pytest.raises(ValueError) as caught:
        reference_validator(reading, dict(creative_audio=True))(reading)
    assert_capability_error(caught.value, reading)
    assert reading == original
    # Extend the enum contract by configuration, never by reference content.
    for bed in (None, '/explicit/bed.wav'):
        config = dict(creative_audio=True, audio_music_bed=bed)
        for speech in ('narration', 'dialogue', 'mixed', 'none'):
            for timing in ('speech', 'action', 'music'):
                value = deepcopy(reading)
                value['strategy'].update(speech_mode=speech, timing_driver=timing)
                before = deepcopy(value)
                if bed is None and speech != 'none' and timing == 'music':
                    with pytest.raises(ValueError) as caught:
                        reference_validator(value, config)(value)
                    assert_capability_error(caught.value, value)
                else:
                    reference_validator(value, config)(value)
                assert value == before
    # Descriptive/legacy readers and library mode do not inherit delivery limits.
    contracts.validate_reference(reading, reading['reference_sha256'], 27)
    reference_validator(reading, {})(reading)
    golden = json.loads((FIXTURE.parent.parent / 'question_first/heihu_reference_reading_1013f88.json').read_text())
    reference_validator(golden, dict(creative_audio=True))(golden)


class ReachedQuestions(Exception):
    pass


def run_reference(tmp_path, monkeypatch, reading, config, *, seed=False):
    reference = tmp_path / 'reference.mp4'
    reference.write_bytes(b'local reference stand-in; observations come from the received reply')
    source = dict(source_id='ref', path=str(reference), sha256=reading['reference_sha256'],
        duration_s=27, audio_stream_index=None)
    monkeypatch.setattr(pipeline, '_catalog', lambda *a: dict(sources=[deepcopy(source)]))
    monkeypatch.setattr(visual_story_trial, 'proxy', lambda *a, **k: dict(path=str(reference)))
    monkeypatch.setattr(question_first, 'configure', lambda *a: [dict(film=1)])
    seen = []
    def questions(self, value):
        seen.append('qfs_questions')
        raise ReachedQuestions()
    monkeypatch.setattr(question_first.QuestionFirstRunner, 'run', questions)
    def call(name, prompt, media, validator, **kwargs):
        seen.append(dict(name=name, prompt=prompt))
        value = deepcopy(reading)
        validator(value)
        return value
    options = dict(provider_config=dict(workflow='reference_rough_skill_v1', creative_audio=True,
        material_search='question_first', **config), model_factory=lambda state: SimpleNamespace(call=call),
        asr=False, max_fine=None, max_requests=80)
    if seed:
        options['reference_seed'] = dict(full_response=dict(reference=deepcopy(reading)),
            evidence_limit='Received evidence fixture; no new observation.')
    return lambda: pipeline.execute(reference, tmp_path / 'library', tmp_path / 'run', **options), seen


@pytest.mark.parametrize('source', ['fresh', 'seed', 'override'])
def test_pipeline_rejects_before_music_dispatch_and_honors_explicit_correction(tmp_path, monkeypatch, reading, source):
    config = {}
    requested = deepcopy(reading)
    if source == 'override':
        config['reference_strategy'] = deepcopy(reading['strategy'])
        reading['strategy']['timing_driver'] = 'action'
    run, seen = run_reference(tmp_path, monkeypatch, reading, config, seed=source == 'seed')
    with pytest.raises(ValueError) as caught:
        run()
    assert_capability_error(caught.value, requested)
    assert 'qfs_questions' not in seen
    assert not (tmp_path / 'run/reference_reading.json').exists()
    assert len(seen) == (0 if source == 'seed' else 1)
    if source == 'override':
        # A user override is the executed strategy, even when the raw model says music.
        corrected = deepcopy(requested['strategy'])
        corrected['timing_driver'] = 'action'
        other = tmp_path / 'corrected'
        other.mkdir()
        run, seen = run_reference(other, monkeypatch, requested, dict(reference_strategy=corrected))
        with pytest.raises(ReachedQuestions):
            run()
        saved = json.loads((other / 'run/reference_reading.json').read_text())
        assert saved['strategy'] == corrected and saved['speech_form'] == requested['speech_form']
        assert [row['name'] for row in seen if isinstance(row, dict)] == ['reference']


@pytest.mark.parametrize('bed_configured', [False, True])
def test_prompt_and_validator_share_runtime_capability_contract(tmp_path, monkeypatch, reading, bed_configured):
    config = {}
    if bed_configured:
        bed = tmp_path / 'explicit.wav'
        bed.write_bytes(b'explicit configured bed; this test stops before rendering')
        config['audio_music_bed'] = str(bed)
    else:
        reading['strategy']['timing_driver'] = 'action'
    run, seen = run_reference(tmp_path, monkeypatch, reading, config)
    with pytest.raises(ReachedQuestions):
        run()
    prompt = seen[0]['prompt']
    assert 'music_bed_configured' in prompt
    contract = contracts.creative_reference_strategy_contract(music_bed_configured=bed_configured)
    assert json.dumps(contract, ensure_ascii=False) in prompt
    assert contract['timing_drivers_by_speech_mode']['mixed'] == (
        ['speech', 'music', 'action'] if bed_configured else ['speech', 'action'])
    assert contract['timing_drivers_by_speech_mode']['none'] == ['speech', 'music', 'action']
    saved = json.loads((tmp_path / 'run/reference_reading.json').read_text())
    assert saved['strategy'] == reading['strategy']
    assert ('audio_delivery' in saved) is bed_configured
    assert [row['name'] for row in seen if isinstance(row, dict)] == ['reference']
    assert seen[-1] == 'qfs_questions'


def test_replay_uses_the_recorded_music_bed_configuration(tmp_path, reading):
    write_json(tmp_path / 'reference_catalog/inventory.json', dict(sources=[dict(
        sha256=reading['reference_sha256'], duration_s=27)]))
    config = dict(creative_audio=True)
    state = dict(input_lock=dict(configuration=config))
    call = dict(name='reference')
    with pytest.raises(ValueError) as caught:
        replay_check.validator(tmp_path, state, call, {})(deepcopy(reading))
    assert_capability_error(caught.value, reading)
    config['audio_music_bed'] = '/recorded/explicit.wav'
    replay_check.validator(tmp_path, state, call, {})(deepcopy(reading))
