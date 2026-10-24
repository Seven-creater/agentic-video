"""Offline BGM query measurement: volumedetect mean loudness plus word-level ASR overlap."""
from __future__ import annotations

import re
import subprocess


def rms_db(audio_path, start_s, end_s, *, ffmpeg='ffmpeg'):
    """Mean volume of one query window; any unparseable run counts as no audio."""
    completed = subprocess.run([ffmpeg, '-nostdin', '-v', 'info',
        '-ss', f'{start_s:.6f}', '-t', f'{end_s - start_s:.6f}', '-i', str(audio_path),
        '-map', '0:a:0', '-af', 'volumedetect', '-f', 'null', '-'],
        capture_output=True, text=True, timeout=600)
    mean = re.search(r'mean_volume:\s*([+-]?\d+(?:\.\d+)?)\s*dB', completed.stderr)
    if mean is None:
        raise ValueError('bgm_measure_no_audio')
    return float(mean.group(1))


def speech_words(words, start_s, end_s):
    """Count words overlapping the window, skipping pure punctuation or whitespace tokens."""
    return sum(word['start'] < end_s and word['end'] > start_s
               and any(character.isalnum() for character in word['word'])
               for word in words or [])


def measure(case_id, query_id, audio_path, start_s, end_s, words, *, ffmpeg='ffmpeg') -> dict:
    return dict(case_id=case_id, query_id=query_id, duration_s=end_s - start_s,
        music_rms_db=rms_db(audio_path, start_s, end_s, ffmpeg=ffmpeg),
        speech_words=speech_words(words, start_s, end_s), audio_path=str(audio_path),
        start_s=start_s, end_s=end_s, method='volumedetect_mean+asr_words')


def measure_all(items, *, ffmpeg='ffmpeg') -> list[dict]:
    """One failed item must not stop the rest; failed rows keep sentinel values that
    bgm_eligibility rejects on every axis (None would crash its numeric comparisons)."""
    rows = []
    for item in items:
        try:
            rows.append(measure(item['case_id'], item['query_id'], item['audio_path'],
                item['start_s'], item['end_s'], item['words'], ffmpeg=ffmpeg))
        except Exception as error:
            rows.append(dict(case_id=item.get('case_id'), query_id=item.get('query_id'),
                duration_s=item.get('end_s', 0) - item.get('start_s', 0),
                music_rms_db=-120.0, speech_words=999, audio_path=str(item.get('audio_path')),
                start_s=item.get('start_s'), end_s=item.get('end_s'),
                method='volumedetect_mean+asr_words', error=str(error)))
    return rows
