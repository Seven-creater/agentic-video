import json
from pathlib import Path
import shutil
import subprocess

import numpy as np
import pytest

from omni_story.library import audio_render as ar


pytestmark = pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'),
                                reason='Real media fixtures require FFmpeg and FFprobe')


def run(args):
    result = subprocess.run(args, capture_output=True, timeout=120)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    return result.stdout


@pytest.fixture(scope='module')
def media(tmp_path_factory):
    folder = tmp_path_factory.mktemp('audio_render_media')
    video, speech, music = folder / 'source.mp4', folder / 'speech.wav', folder / 'music.wav'
    run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=red:s=96x64:r=25:d=2',
         '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=2',
         '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(video)])
    run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-f', 'lavfi', '-i',
         'sine=frequency=1200:sample_rate=24000:duration=0.6', '-c:a', 'pcm_s16le', str(speech)])
    run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-f', 'lavfi', '-i',
         'sine=frequency=700:sample_rate=48000:duration=2', '-c:a', 'pcm_s16le', str(music)])
    return video, speech, music


def frame(path, timestamp):
    data = run(['ffmpeg', '-nostdin', '-v', 'error', '-ss', str(timestamp), '-i', str(path),
                '-frames:v', '1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'])
    return np.frombuffer(data, dtype=np.uint8).reshape(64, 96, 3)


def lines(media, start=.5, end=1.5):
    return [{'id': 'one', 'audio_path': str(media[1]), 'start_s': start, 'end_s': end}]


def rms(data, start, end):
    part = data[round(start * ar.SAMPLE_RATE):round(end * ar.SAMPLE_RATE)]
    return float(np.sqrt(np.mean(part ** 2)))


def magnitude(data, hz, start, end):
    values = data[round(start * ar.SAMPLE_RATE):round(end * ar.SAMPLE_RATE), 0]
    return abs(np.sum(values * np.exp(-2j * np.pi * hz * np.arange(len(values)) / ar.SAMPLE_RATE)))


def test_mask_changes_only_requested_pixels_and_interval_without_timeline_change(media, tmp_path):
    output = tmp_path / 'masked.mp4'
    result = ar.mask_video(media[0], [{'x': .25, 'y': .25, 'w': .5, 'h': .5,
                                      'start_s': .4, 'end_s': 1.2, 'color': '#000000'}], output)
    assert result['fully_decoded'] and result['duration_s'] == 2
    assert (result['width'], result['height'], result['fps']) == (96, 64, '25')
    assert frame(output, .2)[32, 48, 0] > 180
    during = frame(output, .8)
    assert during[32, 48].max() < 15
    assert during[8, 8, 0] > 180
    assert frame(output, 1.4)[32, 48, 0] > 180
    assert ar.sha256_file(output) == result['sha256']
    assert Path(result['receipt_path']).is_file()


@pytest.mark.parametrize('change', [
    {'x': -.01}, {'w': 1.2}, {'start_s': -1}, {'end_s': 2.1}, {'color': 'red;injected'}])
def test_invalid_masks_rejected_before_render(media, tmp_path, change):
    mask = dict(x=0, y=0, w=.5, h=.5, start_s=0, end_s=1, color='#000000') | change
    output = tmp_path / 'invalid.mp4'
    with pytest.raises(ValueError):
        ar.mask_video(media[0], [mask], output)
    assert not output.exists()


def test_replacement_audio_keeps_full_speech_and_removes_old_voice(media, tmp_path):
    result = ar.mix_speech(media[0], lines(media), tmp_path / 'spoken.mp4')
    assert result['duration_s'] == 2 and result['source_audio_removed']
    assert result['fully_decoded'] and not result['speech_truncated']
    row = result['lines'][0]
    assert row['duration_s'] == .6 and row['decoded_sample_count'] == 28800
    retained = ar._samples(result['audio_artifacts']['voice']['path'])
    assert rms(retained, .15, .3) == 0
    assert rms(retained, .55, .95) > .04
    assert rms(retained, 1.04, 1.08) > .04, 'the complete speech tail must remain'
    assert rms(retained, 1.2, 1.4) == 0, 'spare window time must remain silent'
    final = ar._samples(result['path'])
    assert magnitude(final, 1200, .65, .95) > magnitude(final, 440, .65, .95) * 100
    assert rms(final, .15, .3) < .002, 'original 440Hz video sound must be removed'
    assert result['audio_artifacts']['mix']['probe']['frames'] == 96000
    assert result['loudnorm'] is not None
    assert json.loads(Path(result['receipt_path']).read_text())['sha256'] == result['sha256']


def test_music_duck_uses_actual_speech_end_not_window_end(media, tmp_path):
    result = ar.mix_speech(media[0], lines(media), tmp_path / 'music.mp4', music_path=media[2])
    ducked = ar._samples(result['audio_artifacts']['music_ducked']['path'])
    assert rms(ducked, .65, .95) < rms(ducked, .1, .3) * .25
    assert rms(ducked, 1.3, 1.45) > rms(ducked, .65, .95) * 4
    assert result['lines'][0]['actual_end_s'] == pytest.approx(1.1)
    assert result['lines'][0]['end_s'] == 1.5


def test_oversized_line_returns_actual_window_delta_without_output(media, tmp_path):
    output = tmp_path / 'oversized.mp4'
    with pytest.raises(ar.AudioTimingError) as raised:
        ar.mix_speech(media[0], lines(media, start=.5, end=.8), output)
    assert raised.value.actual_s == .6
    assert raised.value.window_s == pytest.approx(.3)
    assert raised.value.delta_s == pytest.approx(.3)
    assert not output.exists() and not (tmp_path / 'oversized_audio').exists()


@pytest.mark.parametrize('bad_lines', ['overlap', 'outside', 'duplicate'])
def test_overlapping_or_out_of_bounds_speech_rejected_before_output(media, tmp_path, bad_lines):
    value = lines(media)
    if bad_lines == 'overlap':
        value += [dict(value[0], id='two', start_s=1, end_s=1.9)]
    elif bad_lines == 'outside':
        value[0]['end_s'] = 2.1
    else:
        value += value
    output = tmp_path / 'invalid_mix.mp4'
    with pytest.raises(ValueError):
        ar.mix_speech(media[0], value, output)
    assert not output.exists()


def test_nonzero_source_pts_and_early_aac_do_not_shift_picture_or_new_speech(media, tmp_path):
    source = tmp_path / 'offset.mp4'
    run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=red:s=96x64:r=25:d=2',
         '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=2',
         '-output_ts_offset', '5', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(source)])
    info = ar._video_info(source)
    assert info['start_s'] == 5
    masked = ar.mask_video(source, [dict(x=.25, y=.25, w=.5, h=.5, start_s=.4, end_s=1.2,
                                         color='#000000')], tmp_path / 'masked_offset.mp4')
    assert masked['output_video_start_s'] == 0
    assert frame(masked['path'], .2)[32, 48, 0] > 180
    assert frame(masked['path'], .8)[32, 48].max() < 15
    mixed = ar.mix_speech(source, lines(media), tmp_path / 'mixed_offset.mp4')
    assert mixed['output_video_start_s'] == 0 and mixed['duration_s'] == 2
    audio = next(s for s in mixed['probe']['streams'] if s['codec_type'] == 'audio')
    assert float(audio['start_time']) == 0 and float(audio['duration']) == 2
    values = ar._samples(mixed['path'])
    assert rms(values, .1, .3) < .002
    assert rms(values, .55, .95) > .04


def test_mask_preserves_variable_frame_intervals(tmp_path):
    source = tmp_path / 'variable.mp4'
    run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-f', 'lavfi', '-i',
         'color=red:s=96x64:r=100:d=2', '-vf', "select='eq(mod(n,3),0)+eq(mod(n,7),0)'",
         '-fps_mode', 'vfr', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(source)])
    target = tmp_path / 'variable_masked.mp4'
    ar.mask_video(source, [dict(x=0, y=0, w=.5, h=.5, start_s=0, end_s=1, color='#000000')], target)
    def times(path):
        data = json.loads(run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                               '-show_entries', 'frame=best_effort_timestamp_time', '-of', 'json', str(path)]))
        return [float(s['best_effort_timestamp_time']) for s in data['frames']]
    before, after = times(source), times(target)
    assert len(before) == len(after) and len(set(np.round(np.diff(before), 5))) > 1
    assert after == pytest.approx(before, abs=.0001)


def test_completed_mix_is_sha_verified_and_reused_without_rendering(media, tmp_path, monkeypatch):
    output = tmp_path / 'cached.mp4'
    original = ar.mix_speech(media[0], lines(media), output, music_path=media[2])
    assert original['cache_hit'] is False
    old_mtime = output.stat().st_mtime_ns
    old_run = ar._run
    def validation_only(args):
        assert args[0] == 'ffmpeg' and args[-2:] == ['null', '-'], 'cached output must not be rendered again'
        return old_run(args)
    monkeypatch.setattr(ar, '_run', validation_only)
    cached = ar.mix_speech(media[0], lines(media), output, music_path=media[2])
    assert cached['cache_hit'] is True and cached['sha256'] == original['sha256']
    assert output.stat().st_mtime_ns == old_mtime


@pytest.mark.parametrize('change', ['line_time', 'line_source', 'music', 'video', 'candidate',
                                   'stem', 'stem_probe', 'missing_stem', 'missing_receipt', 'old_policy'])
def test_existing_mix_binding_or_artifact_changes_stop_without_overwrite(media, tmp_path, change):
    video, speech, music = (tmp_path / name for name in ('input.mp4', 'speech.wav', 'music.wav'))
    for source, target in zip(media, (video, speech, music)):
        shutil.copyfile(source, target)
    output = tmp_path / 'cached.mp4'
    value = [dict(id='one', audio_path=str(speech), start_s=.5, end_s=1.5)]
    result = ar.mix_speech(video, value, output, music_path=music)
    saved_candidate = output.read_bytes()
    receipt = Path(result['receipt_path'])
    if change == 'line_time':
        value[0]['start_s'] = .6
    elif change in {'line_source', 'music', 'video'}:
        path = {'line_source': speech, 'music': music, 'video': video}[change]
        path.write_bytes(path.read_bytes() + b'changed-but-still-decodable')
    elif change == 'candidate':
        output.write_bytes(saved_candidate + b'changed')
    elif change == 'stem':
        path = Path(result['audio_artifacts']['voice']['path'])
        path.write_bytes(path.read_bytes() + b'changed')
    elif change == 'missing_stem':
        Path(result['audio_artifacts']['voice']['path']).unlink()
    elif change == 'missing_receipt':
        receipt.unlink()
    else:
        record = json.loads(receipt.read_text())
        if change == 'old_policy':
            record['policy'] = 'old-policy'
        else:
            record['audio_artifacts']['voice']['probe']['frames'] = 1
        receipt.write_text(json.dumps(record))
    with pytest.raises(ValueError, match='audio_render_existing_'):
        ar.mix_speech(video, value, output, music_path=music)
    assert output.read_bytes() == (saved_candidate + b'changed' if change == 'candidate' else saved_candidate)
