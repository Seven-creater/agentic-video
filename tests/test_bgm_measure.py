"""Offline BGM query measurement against a fake ffmpeg; no binary or media needed."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from omni_story.evaluation import bgm_measure
from omni_story.evaluation.exam_bank import bgm_eligibility


LOUD_STDERR = ('[Parsed_volumedetect_0 @ 0x1] n_samples: 144000\n'
               '[Parsed_volumedetect_0 @ 0x1] mean_volume: -23.4 dB\n'
               '[Parsed_volumedetect_0 @ 0x1] max_volume: -12.1 dB\n')
NO_AUDIO_STDERR = "Stream map '0:a:0' matches no streams.\n"


def install_ffmpeg(monkeypatch, runs, stderr_for):
    def child(command, **kwargs):
        stderr = stderr_for.get(command[command.index('-i') + 1], NO_AUDIO_STDERR)
        runs.append((list(command), kwargs))
        return SimpleNamespace(returncode=int('mean_volume' not in stderr), stderr=stderr)
    monkeypatch.setattr(bgm_measure.subprocess, 'run', child)
    return child


@pytest.mark.parametrize('line,expected', [
    ('mean_volume: -23.4 dB', -23.4),
    ('mean_volume: -0.5 dB', -0.5),
    ('mean_volume: 3.25 dB', 3.25),
    ('mean_volume: +1.5 dB', 1.5),
    ('mean_volume: -6 dB', -6.0)])
def test_signed_and_unsigned_decimals_are_parsed(monkeypatch, line, expected):
    monkeypatch.setattr(bgm_measure.subprocess, 'run',
        lambda command, **kwargs: SimpleNamespace(returncode=0,
            stderr=f'[Parsed_volumedetect_0 @ 0x1] {line}\n'))
    assert bgm_measure.rms_db('/a.mp4', 0, 1) == expected


@pytest.mark.parametrize('returncode,stderr', [
    (1, NO_AUDIO_STDERR),
    (0, ''),
    (0, '[Parsed_volumedetect_0 @ 0x1] n_samples: 0\n'),
    (1, 'ffmpeg: error loading input file\n')])
def test_no_audio_track_or_unparseable_output_raises(monkeypatch, returncode, stderr):
    monkeypatch.setattr(bgm_measure.subprocess, 'run',
        lambda command, **kwargs: SimpleNamespace(returncode=returncode, stderr=stderr))
    with pytest.raises(ValueError, match='bgm_measure_no_audio'):
        bgm_measure.rms_db('/a.mp4', 5, 9)


def test_seek_and_duration_precede_the_input_and_use_the_custom_ffmpeg(monkeypatch):
    runs = []
    monkeypatch.setattr(bgm_measure.subprocess, 'run',
        lambda command, **kwargs: (runs.append((list(command), kwargs)),
            SimpleNamespace(returncode=0, stderr=LOUD_STDERR))[1])
    assert bgm_measure.rms_db(Path('/a.mp4'), 12.5, 15.5, ffmpeg='ffwrap') == -23.4
    assert runs == [(['ffwrap', '-nostdin', '-v', 'info', '-ss', '12.500000',
        '-t', '3.000000', '-i', '/a.mp4', '-map', '0:a:0', '-af', 'volumedetect',
        '-f', 'null', '-'], dict(capture_output=True, text=True, timeout=600))]


def test_speech_words_counts_only_real_words_overlapping_the_window():
    words = [
        dict(start=0.0, end=1.0, word='前'),
        dict(start=1.0, end=2.0, word='你好'),
        dict(start=3.5, end=4.5, word='世界'),
        dict(start=5.0, end=6.0, word='再见'),
        dict(start=1.0, end=2.0, word='...'),
        dict(start=1.0, end=2.0, word='，。！'),
        dict(start=1.0, end=2.0, word=' 　'),
        dict(start=2.0, end=3.0, word='100%')]
    assert bgm_measure.speech_words(words, 1.0, 4.0) == 3
    assert bgm_measure.speech_words([], 1.0, 4.0) == 0
    assert bgm_measure.speech_words(None, 1.0, 4.0) == 0


def test_measure_returns_the_exam_bank_row_with_provenance(monkeypatch):
    runs = []
    install_ffmpeg(monkeypatch, runs, {'/a.mp4': LOUD_STDERR})
    row = bgm_measure.measure('t_a', 'q1', Path('/a.mp4'), 10.0, 14.0,
        [dict(start=11.0, end=12.0, word='歌')])
    assert row == dict(case_id='t_a', query_id='q1', duration_s=4.0, music_rms_db=-23.4,
        speech_words=1, audio_path='/a.mp4', start_s=10.0, end_s=14.0,
        method='volumedetect_mean+asr_words')
    assert len(runs) == 1


def test_measure_all_isolates_failures_into_sentinel_rows(monkeypatch):
    runs = []
    install_ffmpeg(monkeypatch, runs, {'/a.mp4': LOUD_STDERR})
    rows = bgm_measure.measure_all([
        dict(case_id='t_a', query_id='q1', audio_path='/a.mp4', start_s=0.0, end_s=3.0, words=[]),
        dict(case_id='t_b', query_id='q1', audio_path='/broken.mp4', start_s=1.0, end_s=2.5, words=[]),
        dict(case_id='t_c', query_id='q1', audio_path='/a.mp4', start_s=0.0, end_s=3.0,
            words=[dict(start=0.0, end=0.5, word='喂')])], ffmpeg='ffwrap')
    assert [row['case_id'] for row in rows] == ['t_a', 't_b', 't_c']
    assert rows[0]['music_rms_db'] == -23.4 and 'error' not in rows[0]
    assert rows[1] == dict(case_id='t_b', query_id='q1', duration_s=1.5, music_rms_db=-120.0,
        speech_words=999, audio_path='/broken.mp4', start_s=1.0, end_s=2.5,
        method='volumedetect_mean+asr_words', error='bgm_measure_no_audio')
    assert rows[2]['speech_words'] == 1 and 'error' not in rows[2]
    assert len(runs) == 3 and all(run[0][0] == 'ffwrap' for run in runs)


def test_measure_all_rows_drive_bgm_eligibility_verdicts(monkeypatch):
    install_ffmpeg(monkeypatch, [], {'/music.mp4': LOUD_STDERR})
    rows = bgm_measure.measure_all([
        dict(case_id='t_a', query_id='q1', audio_path='/music.mp4', start_s=0.0, end_s=4.0, words=[]),
        dict(case_id='t_a', query_id='q2', audio_path='/music.mp4', start_s=10.0, end_s=14.0,
            words=[dict(start=11.0, end=12.0, word='说话')]),
        dict(case_id='t_b', query_id='q1', audio_path='/silent.mp4', start_s=0.0, end_s=3.0, words=[]),
        dict(case_id='t_b', query_id='q2', audio_path='/music.mp4', start_s=20.0, end_s=23.0, words=[]),
        dict(case_id='t_b', query_id='q3', audio_path='/music.mp4', start_s=30.0, end_s=33.0, words=[])])
    result = bgm_eligibility(rows)
    assert result['queries'] == [
        dict(case_id='t_a', query_id='q1', eligible=True, reasons=[]),
        dict(case_id='t_a', query_id='q2', eligible=False, reasons=['speech_words_above_max']),
        dict(case_id='t_b', query_id='q1', eligible=False,
             reasons=['music_rms_below_min', 'speech_words_above_max']),
        dict(case_id='t_b', query_id='q2', eligible=True, reasons=[]),
        dict(case_id='t_b', query_id='q3', eligible=True, reasons=[])]
    assert result['templates'] == {
        't_a': dict(eligible_queries=1, status='excluded_at_design'),
        't_b': dict(eligible_queries=2, status='included')}
