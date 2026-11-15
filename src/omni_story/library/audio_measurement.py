"""Deterministic audio evidence, measured before any paid AV review."""
from __future__ import annotations

import numpy as np


def thresholds():
    # Finite floor keeps zero samples representable in strict JSON.
    return dict(silent_peak_dbfs=-45.0, silent_line_rms_dbfs=-50.0, zero_floor_dbfs=-120.0)


def measure_samples(samples):
    values = np.asarray(samples, dtype=np.float64)
    if not np.isfinite(values).all():
        raise ValueError('audio_measurement_nonfinite_samples')
    peak = float(np.max(np.abs(values))) if values.size else 0.0
    rms = float(np.sqrt(np.mean(values * values))) if values.size else 0.0
    limits = thresholds()
    def db(value):
        return max(limits['zero_floor_dbfs'], float(20 * np.log10(value))) if value else limits['zero_floor_dbfs']
    return dict(peak_dbfs=db(peak), rms_dbfs=db(rms), silent=db(peak) < limits['silent_peak_dbfs'])


def measure_mix(mix, music, voice, final_mux, lines, *, music_expected):
    from .audio_render import _samples, SAMPLE_RATE
    voice_samples = _samples(voice)
    measurement = dict(thresholds=thresholds(), mix=measure_samples(_samples(mix)),
        final_mux=measure_samples(_samples(final_mux)), music=measure_samples(_samples(music)),
        voice=measure_samples(voice_samples), music_expected=bool(music_expected), line_windows=[])
    for line in lines:
        start, end = line['start_s'], line['actual_end_s']
        window = measure_samples(voice_samples[round(start*SAMPLE_RATE):round(end*SAMPLE_RATE)])
        measurement['line_windows'].append(dict(id=line['id'], start_s=start, actual_end_s=end,
            **window, silent_line=window['rms_dbfs'] < thresholds()['silent_line_rms_dbfs']))
    reasons = []
    if measurement['mix']['silent'] or measurement['final_mux']['silent']:
        reasons.append('audio_measured_silent')
    if music_expected and measurement['music']['silent']:
        reasons.append('audio_music_bed_silent')
    reasons.extend('audio_line_silent:' + row['id'] for row in measurement['line_windows'] if row['silent_line'])
    measurement.update(passed=not reasons, reasons=reasons)
    return measurement


def review_contradictions(review, measurement):
    fields = []
    if review.get('music_audible') is True and measurement['music']['silent']:
        fields.append('music_audible')
    windows = measurement['line_windows']
    if review.get('speech_clear') is True and windows and all(row['silent_line'] for row in windows):
        fields.append('speech_clear')
    return dict(code='review_contradicts_measurement', fields=fields) if fields else None
