"""Standalone reference/candidate demo. Never starts or repairs production jobs."""
from __future__ import annotations

import argparse
from array import array
import copy
import wave
from collections import Counter
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import subprocess
import sys

from omni_story.api import QwenAPI
from omni_story.library.audio_review import review_audio_video, INLINE_BASE64_LIMIT
from omni_story.library.media import inventory_sources, probe_media, sha256_file
from omni_story.library.state import file_lock, json_sha, write_json
from .audio_input import NativeAudioAPI, review_native_audio
from .prompts import (POLICY, REFERENCE_DIMENSIONS, QUALITY_DIMENSIONS, STATUSES,
                      observe_prompt, compare_prompt, modality_prompt)


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def run_media(args):
    result = subprocess.run(args, capture_output=True, timeout=600)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors='replace')[-2000:])


def span_ok(span, duration):
    return (isinstance(span, list) and len(span) == 2 and
            all(type(n) in (float, int) and math.isfinite(n) for n in span) and
            0 <= span[0] < span[1] <= duration + .08)


def validate_observation(data, duration, reference=False):
    if not isinstance(data, dict) or not isinstance(data.get('summary'), str):
        raise ValueError('observation_summary_required')
    segments = data.get('segments')
    if not isinstance(segments, list) or not segments:
        raise ValueError('segments_required')
    ids = [s.get('id') for s in segments]
    if any(not isinstance(x, str) or not x for x in ids) or len(set(ids)) != len(ids):
        raise ValueError('unique_segment_ids_required')
    for s in segments:
        if not span_ok([s.get('start_s'), s.get('end_s')], duration):
            raise ValueError('segment_time_out_of_range')
        if any(not isinstance(s.get(k), str) for k in ('heard', 'seen', 'edit_function')):
            raise ValueError('independent_audio_visual_evidence_required')
    if not isinstance(data.get('uncertainties'), list) or not isinstance(data.get('roles'), list):
        raise ValueError('roles_and_uncertainties_required')
    if reference:
        requirements = data.get('requirements', [])
        qids = [r.get('id') for r in requirements]
        if not qids or any(not isinstance(q, str) or not q for q in qids) or len(set(qids)) != len(qids):
            raise ValueError('unique_reference_requirements_required')
        if not {r.get('dimension') for r in requirements} <= set(REFERENCE_DIMENSIONS):
            raise ValueError('unknown_reference_dimension')
        for r in requirements:
            if r.get('evidence_mode') not in ('visual', 'audio', 'joint'):
                raise ValueError('reference_evidence_mode_required')
            if r.get('importance') not in ('critical', 'supporting'):
                raise ValueError('requirement_importance_required')
            if not span_ok(r.get('reference_span'), duration):
                raise ValueError('reference_requirement_time_out_of_range')
            if not r.get('reference_segment_ids') or not set(r['reference_segment_ids']) <= set(ids):
                raise ValueError('requirement_must_cite_observed_segments')
            if any(not isinstance(r.get(k), str) or not r[k] for k in ('requirement', 'permitted_changes')):
                raise ValueError('requirement_and_adaptation_boundary_required')
    return data


def validate_comparison(data, reference, ref_duration, candidate_duration, modality_checks=None):
    if not isinstance(data, dict) or not isinstance(data.get('summary'), str):
        raise ValueError('comparison_summary_required')
    expected = {r['id'] for r in reference['requirements']}
    rows = data.get('alignment', [])
    if len(rows) != len(expected) or {r.get('requirement_id') for r in rows} != expected:
        raise ValueError('alignment_must_cover_each_frozen_requirement_exactly_once')
    requirements = {r['id']: r for r in reference['requirements']}
    support_conflicts = [r['requirement_id'] + ':required=' + requirements[r['requirement_id']]['evidence_mode'] +
                         ':observed=' + str(r.get('support')) for r in rows
                         if r.get('status') in ('preserved', 'equivalent') and
                         r.get('support') not in (requirements[r['requirement_id']]['evidence_mode'], 'joint')]
    if support_conflicts:
        raise ValueError('spoken_content_cannot_prove_visual_performance:' + ';'.join(support_conflicts))
    for row in rows:
        if row.get('support') not in ('visual', 'audio', 'joint', 'none', 'uncertain'):
            raise ValueError('modality_support_required')
        if any(not isinstance(row.get(k), str) or not row[k] for k in ('visual_evidence', 'audio_evidence')):
            raise ValueError('separate_visible_and_heard_evidence_required')
        if row.get('status') not in STATUSES:
            raise ValueError('invalid_alignment_status')
        spans = row.get('candidate_spans')
        if not isinstance(spans, list) or any(not span_ok(s, candidate_duration) for s in spans):
            raise ValueError('candidate_span_must_use_candidate_local_time')
        if row['status'] in ('preserved', 'equivalent', 'partial', 'contradictory') and not spans:
            raise ValueError('observed_alignment_needs_candidate_span')
        for key in ('reference_evidence', 'candidate_evidence', 'difference'):
            if not isinstance(row.get(key), str) or not row[key]:
                raise ValueError('paired_evidence_required')
    alignment_by_id = {r['requirement_id']: r for r in rows}
    for field, dims in [('adaptation_scores', REFERENCE_DIMENSIONS), ('quality_scores', QUALITY_DIMENSIONS)]:
        scores = data.get(field, [])
        if len(scores) != len(dims) or {s.get('dimension') for s in scores} != set(dims):
            raise ValueError('all_score_dimensions_required:' + field)
        for s in scores:
            if s.get('score') is not None and (type(s['score']) is not int or not 1 <= s['score'] <= 5):
                raise ValueError('score_must_be_1_to_5_or_null')
            if s.get('applicability') not in ('applicable', 'not_applicable', 'uncertain'):
                raise ValueError('score_applicability_required')
            if s['applicability'] != 'applicable' and s.get('score') is not None:
                raise ValueError('inapplicable_or_uncertain_score_must_be_null')
            if not isinstance(s.get('reason'), str) or not s['reason']:
                raise ValueError('score_evidence_required')
            if field == 'adaptation_scores':
                dimension_ids = {r['id'] for r in reference['requirements'] if r['dimension'] == s['dimension']}
                if (not dimension_ids <= set(s.get('requirement_ids', [])) or
                        not set(s.get('requirement_ids', [])) <= expected):
                    raise ValueError('adaptation_score_must_cover_all_requirements_in_dimension:' + s['dimension'] +
                            ':required=' + ','.join(sorted(dimension_ids)) + ':got=' + ','.join(s.get('requirement_ids', [])))
                statuses = {alignment_by_id[q]['status'] for q in dimension_ids}
                if s.get('score') == 5 and not statuses <= {'preserved', 'equivalent'}:
                    raise ValueError('adaptation_full_score_contradicts_alignment:' + s['dimension'] +
                        ':cited_statuses=' + ','.join(sorted(statuses)) + ':score_must_be_below_5_or_null')
                if statuses == {'uncertain'} and s.get('score') is not None:
                    raise ValueError('unobserved_dimension_cannot_have_score')
    issues = data.get('issues', [])
    if not isinstance(issues, list):
        raise ValueError('comparison_list_required:issues')
    if len({i.get('id') for i in issues}) != len(issues):
        raise ValueError('issue_ids_must_be_unique')
    for issue in issues:
        if not issue.get('dimensions') or not set(issue['dimensions']) <= set(REFERENCE_DIMENSIONS):
            raise ValueError('issue_dimensions_required')
        if issue.get('severity') not in ('major', 'minor', 'uncertain') or not issue.get('description'):
            raise ValueError('issue_severity_and_description_required')
        if not set(issue.get('requirement_ids', [])) <= expected:
            raise ValueError('unknown_issue_requirement')
        for field, duration in [('reference_span', ref_duration), ('candidate_span', candidate_duration)]:
            if issue.get(field) is not None and not span_ok(issue[field], duration):
                raise ValueError('issue_time_out_of_range:' + field)
    for field in ('issues', 'limitations', 'observation_corrections'):
        if not isinstance(data.get(field), list):
            raise ValueError('comparison_list_required:' + field)
    checks = data.get('evidence_reconciliation', [])
    if len(checks) != 3 or {r.get('aspect') for r in checks} != {'sound', 'visible_actions_objects', 'speech'}:
        raise ValueError('independent_evidence_reconciliation_required')
    if any(r.get('status') not in ('consistent', 'revised', 'uncertain') or not r.get('finding') for r in checks):
        raise ValueError('evidence_reconciliation_status_and_finding_required')
    diagnostics = data.get('diagnostics', {})
    for key in ('character_identity', 'speech_delivery', 'editing'):
        row = diagnostics.get(key, {})
        if row.get('status') not in ('pass', 'partial', 'fail', 'uncertain', 'not_applicable') or not row.get('evidence'):
            raise ValueError('diagnostic_evidence_required:' + key)
    speech = diagnostics.get('speech_delivery', {})
    if speech.get('candidate_presence') not in ('audible', 'absent', 'uncertain'):
        raise ValueError('candidate_speech_presence_required')
    heard = (modality_checks or {}).get('candidate_audio', {}).get('speech_status')
    if heard == 'audible' and speech['candidate_presence'] == 'absent':
        raise ValueError('independent_speech_evidence_contradicts_silent_candidate')
    music = diagnostics.get('music', {})
    if music.get('track_match') not in ('uncertain', 'not_applicable'):
        raise ValueError('same_music_needs_independent_fingerprint_evidence')
    if music.get('role') not in ('essential', 'supporting', 'none', 'uncertain') or not music.get('evidence'):
        raise ValueError('music_role_and_evidence_required')
    for video in ('reference', 'candidate'):
        presence = music.get(video+'_presence')
        if presence not in ('audible', 'absent', 'uncertain'):
            raise ValueError('music_presence_required')
        audio = (modality_checks or {}).get(video+'_audio', {})
        if audio.get('music_status') == 'audible' and presence == 'absent':
            raise ValueError('local_music_evidence_contradicts_whole_video_absence:' + video)
        if audio.get('music_status') == 'uncertain' and presence == 'absent':
            raise ValueError('uncertain_music_is_not_absence:' + video)
    return data


def tally(reference, comparison):
    important = {r['id'] for r in reference['requirements'] if r['importance'] == 'critical'}
    rows = [r for r in comparison['alignment'] if r['requirement_id'] in important]
    counts = Counter(r['status'] for r in rows)
    total, met = len(rows), counts['preserved'] + counts['equivalent']
    return dict(critical_total=total, critical_met=met, critical_unknown=counts['uncertain'],
                critical_status_counts=dict(counts),
                critical_met_fraction=met / total if total else None,
                definition='保留和有证据的等效改编计满足；部分保留单列；未知不移出分母；无综合总分')


def proxy_size(bitrate, *, floor=140000, full=960, smallest=480):
    """Long inputs share one inline budget: shrink the frame so each pixel keeps the
    bitrate the full-size proxy has at its floor, instead of refusing the evaluation."""
    if bitrate >= floor:
        return full
    size = 2 * int(full * math.sqrt(max(bitrate, 0) / floor) / 2)
    if size < smallest:
        raise ValueError('demo_inline_duration_budget_exceeded')
    return size


def _set_aside(receipt, path, reason):
    """A local, deterministic derived file whose binding no longer matches is regenerated;
    the stale copy is kept beside it. Paid replies keep their own strict bindings."""
    stamp = sha256_file(receipt)[:12]
    for item in (receipt, path):
        if Path(item).exists():
            target, n = Path(str(item) + f'.stale_{stamp}'), 1
            while target.exists():  # never overwrite an earlier stale copy
                n += 1
                target = Path(str(item) + f'.stale_{stamp}_{n}')
            Path(item).rename(target)
    log = Path(receipt).parent / 'stale_artifacts.jsonl'
    with log.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(dict(path=str(path), reason=reason, stamp=stamp), ensure_ascii=False) + '\n')


def make_proxy(source, path, label, bitrate, size=960):
    spec = dict(source_sha256=source['sha256'], label=label, bitrate=bitrate,
                policy='pair-proxy-960-audio-v1' if size == 960 else 'pair-proxy-scaled-audio-v1',
                **({} if size == 960 else dict(size=size)))
    band, font = 2*round(24*size/960), round(28*size/960)
    receipt = path.with_suffix('.json')
    if receipt.exists():
        saved = read_json(receipt)
        if saved['spec'] != spec or not Path(path).exists() or sha256_file(path) != saved['sha256']:
            _set_aside(receipt, path, 'proxy_binding_changed')
        else:
            return saved
    part = path.with_suffix('.part.mp4')
    audio_index = source['audio_stream_index']
    args = ['ffmpeg', '-nostdin', '-v', 'error', '-y', '-threads', '1', '-i', source['path']]
    if audio_index is None:
        args += ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo']
    args += ['-map', f"0:{source['video_stream_index']}",
             '-map', f'0:{audio_index}' if audio_index is not None else '1:a:0',
             '-vf', f"setpts=PTS-STARTPTS,scale={size}:{size}:force_original_aspect_ratio=decrease,"
                    f"pad={size}:{size+band}:(ow-iw)/2:{band}+({size}-ih)/2,setsar=1,"
                    f"drawtext=text='{label}':fontcolor=white:fontsize={font}:x={round(20*size/960)}:y={round(10*size/960)}",
             '-af', 'asetpts=PTS-STARTPTS,aresample=48000,apad',
             '-t', str(source['duration_s']), '-r', '30', '-c:v', 'libx264',
             '-preset', 'fast', '-b:v', str(bitrate), '-maxrate', str(bitrate),
             '-bufsize', str(bitrate * 2), '-pix_fmt', 'yuv420p', '-threads', '1',
             '-filter_threads', '1', '-c:a', 'aac', '-ac', '2', '-b:a', '64k',
             '-movflags', '+faststart', str(part)]
    run_media(args)
    measured = probe_media(part)
    if abs(measured['duration_s'] - source['duration_s']) > .12:
        raise ValueError('proxy_duration_changed')
    run_media(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(part), '-f', 'null', '-'])
    part.replace(path)
    saved = dict(spec=spec, path=str(path), sha256=sha256_file(path), duration_s=measured['duration_s'],
                 original_has_audio=audio_index is not None, size_bytes=path.stat().st_size,
                 time_mapping='original_time_s = proxy_time_s (frame rounding <= 0.12 s)')
    write_json(receipt, saved)
    return saved


def make_reel(reference, candidate, folder):
    path = folder / 'comparison.mp4'
    spec = dict(reference_sha256=reference['sha256'], candidate_sha256=candidate['sha256'])
    receipt = path.with_suffix('.json')
    if receipt.exists():
        saved = read_json(receipt)
        if saved['spec'] != spec or not Path(path).exists() or sha256_file(path) != saved['sha256']:
            _set_aside(receipt, path, 'comparison_reel_binding_changed')
        else:
            return saved
    # Names are evaluator-owned literals, never user-supplied concat directives.
    playlist = folder / 'concat.txt'
    playlist.write_text("file 'reference.mp4'\nfile 'candidate.mp4'\n", encoding='utf-8')
    part = path.with_suffix('.part.mp4')
    run_media(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-f', 'concat', '-safe', '1',
               '-i', str(playlist), '-c', 'copy', '-movflags', '+faststart', str(part)])
    duration = probe_media(part)['duration_s']
    if abs(duration - reference['duration_s'] - candidate['duration_s']) > .15:
        raise ValueError('comparison_reel_duration_changed')
    if 4 * ((part.stat().st_size + 2) // 3) >= INLINE_BASE64_LIMIT:
        raise ValueError('comparison_reel_exceeds_inline_limit_use_shorter_inputs')
    run_media(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(part), '-f', 'null', '-'])
    part.replace(path)
    saved = dict(spec=spec, path=str(path), sha256=sha256_file(path), duration_s=duration,
                 reference_offset_s=0, candidate_offset_s=reference['duration_s'],
                 reference_duration_s=reference['duration_s'], candidate_duration_s=candidate['duration_s'],
                 order=['REFERENCE', 'CREATED'], mapping_tolerance_s=.15)
    write_json(receipt, saved)
    return saved


VISUAL_PROXY_BYTES = 7_200_000  # base64 of this stays under the 10 MB inline limit


def visual_proxy_geometry(duration):
    """Keep a whole-picture proxy inline: shrink frame size, not per-pixel bitrate, for long inputs."""
    bitrate = min(400000, int(VISUAL_PROXY_BYTES * 8 / max(duration, 1) - 64000))
    return bitrate, proxy_size(bitrate, floor=400000)


def make_modality_proxy(source, path, kind, start_s=0, end_s=None):
    end_s = source['duration_s'] if end_s is None else end_s
    duration = end_s-start_s
    bitrate, size = (400000, 960) if kind == 'audio' else visual_proxy_geometry(duration)
    spec = dict(policy='modality-evidence-v2-full-picture', source_sha256=source['sha256'],
                kind=kind, source_span=[start_s, end_s], **({} if size == 960 else dict(bitrate=bitrate, size=size)))
    receipt = path.with_suffix('.json')
    if receipt.exists():
        saved = read_json(receipt)
        if saved['spec'] != spec or not Path(path).exists() or sha256_file(path) != saved['sha256']:
            _set_aside(receipt, path, 'modality_proxy_binding_changed')
        else:
            return saved
    part = path.with_suffix('.part.mp4')
    args = ['ffmpeg', '-nostdin', '-v', 'error', '-y', '-threads', '1', '-ss', str(start_s), '-i', source['path']]
    if kind == 'audio':
        args += ['-f', 'lavfi', '-i', 'color=black:s=320x240:r=10', '-map', '1:v:0']
        if source['audio_stream_index'] is not None:
            args += ['-map', f"0:{source['audio_stream_index']}"]
        else:
            args += ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo', '-map', '2:a:0']
        args += ['-vf', 'setpts=PTS-STARTPTS']
    else:
        args += ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo',
                 '-map', f"0:{source['video_stream_index']}", '-map', '1:a:0',
                 '-vf', 'setpts=PTS-STARTPTS,'
                        # Encoders need even sides: an aspect-preserving fit can produce 960x619.
                        f'scale={size}:{size}:force_original_aspect_ratio=decrease,scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1']
    args += ['-af', 'asetpts=PTS-STARTPTS,aresample=48000,apad', '-t', str(duration),
             '-c:v', 'libx264', '-preset', 'fast', '-b:v', str(bitrate), '-maxrate', str(bitrate), '-bufsize', str(2*bitrate),
             '-threads', '1', '-filter_threads', '1', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '64k',
             '-movflags', '+faststart', str(part)]
    run_media(args)
    measured = probe_media(part)
    if abs(measured['duration_s'] - duration) > .12:
        raise ValueError('modality_proxy_duration_changed')
    if 4 * ((part.stat().st_size + 2) // 3) >= INLINE_BASE64_LIMIT:
        raise ValueError('modality_proxy_exceeds_inline_limit')
    part.replace(path)
    saved = dict(spec=spec, path=str(path), sha256=sha256_file(path), duration_s=measured['duration_s'])
    write_json(receipt, saved)
    return saved


def validate_modality(data, kind, duration, signal_dbfs=None):
    if not isinstance(data, dict) or not isinstance(data.get('uncertainties'), list):
        raise ValueError('modality_observation_required')
    segments = data.get('segments')
    if not isinstance(segments, list) or not segments:
        raise ValueError('modality_segments_required')
    previous_end = 0
    for row in segments:
        if not span_ok([row.get('start_s'), row.get('end_s')], duration):
            raise ValueError('modality_time_out_of_range')
        if row['start_s'] > previous_end + .5:
            raise ValueError('modality_unobserved_gap')
        previous_end = max(previous_end, row['end_s'])
        if kind == 'audio':
            if any(row.get(k) not in ('audible', 'absent', 'uncertain') for k in ('music_status', 'speech_status')) or not row.get('heard'):
                raise ValueError('interval_music_and_speech_evidence_required')
        elif not row.get('seen'):
            raise ValueError('visible_evidence_required')
    if previous_end < duration-.5:
        raise ValueError('modality_unobserved_tail')
    if kind == 'audio' and signal_dbfs is not None and signal_dbfs > -55:
        silent_claim = all(any(word in row.get('heard', '') for word in
            ('完全静音', '全程静音', '无任何声音', '无音频信号', '无声音信号')) for row in segments)
        if silent_claim:
            raise ValueError('decoded_audio_is_not_silent:rms_dbfs=' + str(round(signal_dbfs, 1)) +
                ':能量不证明是语音或音乐，请重新实听并报告实际声音类型，不得声称无信号')
    if kind == 'audio' and not isinstance(data.get('speech_notes'), list):
        raise ValueError('speech_notes_required')
    return data


def audio_windows(duration, max_seconds=6):
    count = max(1, math.ceil(duration/max_seconds))
    return [(duration*i/count, duration*(i+1)/count) for i in range(count)]


def make_audio_window(source, path, start, end):
    spec = dict(policy='native-audio-window-v1', source_sha256=source['sha256'], source_span=[start, end])
    receipt = path.with_suffix('.json')
    if receipt.exists():
        saved = read_json(receipt)
        if saved['spec'] != spec or not Path(path).exists() or sha256_file(path) != saved['sha256']:
            _set_aside(receipt, path, 'audio_window_binding_changed')
        else:
            return saved
    args = ['ffmpeg', '-nostdin', '-v', 'error', '-y', '-threads', '1', '-ss', str(start), '-i', source['path']]
    if source['audio_stream_index'] is None:
        args += ['-f', 'lavfi', '-i', 'anullsrc=r=24000:cl=stereo', '-map', '1:a:0']
    else:
        args += ['-map', f"0:{source['audio_stream_index']}"]
    run_media(args+['-vn', '-af', 'asetpts=PTS-STARTPTS,apad', '-t', str(end-start),
                   '-ac', '2', '-ar', '24000', '-c:a', 'pcm_s16le', str(path)])
    with wave.open(str(path)) as wav:
        samples = array('h', wav.readframes(wav.getnframes()))
        duration = wav.getnframes()/wav.getframerate()
    if abs(duration-(end-start)) > .02:
        raise ValueError('native_audio_duration_changed')
    rms = math.sqrt(sum(x*x for x in samples)/max(1, len(samples)))/32768
    saved = dict(spec=spec, path=str(path), sha256=sha256_file(path), duration_s=duration,
                 rms_dbfs=20*math.log10(max(rms, 1e-6)), transport='input_audio')
    write_json(receipt, saved)
    return saved


def observe_audio(source, video, media, output, api, receipts):
    segments, notes, uncertainties, signals = [], [], [], []
    for index, (start, end) in enumerate(audio_windows(source['duration_s'])):
        proxy = make_audio_window(source, media/f'{video}.audio.native.{index}.wav', start, end)
        reply = call_stage(f'{video}_audio_{index}', Path(proxy['path']), modality_prompt('audio', end-start),
            lambda value, d=end-start, level=proxy['rms_dbfs']: validate_modality(value, 'audio', d, level), output, api, receipts,
            coerce=lambda value, d=end-start, level=proxy['rms_dbfs']: salvage_modality(value, 'audio', d, level))
        for row in reply['segments']:
            segments.append(dict(row, id=f'a{len(segments)+1}', start_s=row['start_s']+start,
                                 end_s=min(source['duration_s'], row['end_s']+start), window_index=index))
        notes += [dict(source_span=[start, end], note=n) for n in reply['speech_notes']]
        uncertainties += [dict(source_span=[start, end], note=n) for n in reply['uncertainties']]
        signals.append(dict(source_span=[start, end], rms_dbfs=proxy['rms_dbfs']))
    music = {r['music_status'] for r in segments}
    speech = {r['speech_status'] for r in segments}
    presence = lambda values: 'audible' if 'audible' in values else 'uncertain' if 'uncertain' in values else 'absent'
    return dict(segments=segments, speech_notes=notes, uncertainties=uncertainties, signals=signals,
                music_status=presence(music), speech_status=presence(speech),
                method='native input_audio WAV; independent windows at most six seconds; original coordinates')


def transcript(source, folder, model_dir):
    if not model_dir or source['audio_stream_index'] is None:
        return dict(status='not_run', segments=[], reason='no_asr_model_or_original_audio')
    from omni_story.library.asr import cached_transcript, transcribe_window
    from faster_whisper import WhisperModel
    kwargs = dict(model_size=str(model_dir), language='zh', vad_filter=True)
    saved = cached_transcript(source, 0, source['duration_s'], [folder], **kwargs)
    if saved is None:
        model = WhisperModel(str(model_dir), device='cpu', compute_type='int8', cpu_threads=2, num_workers=1)
        saved = transcribe_window(source, 0, source['duration_s'], folder, model=model, **kwargs)
    return dict(status='automatic_unverified', language=saved['language'],
                source_sha256=source['sha256'], segments=[dict(start_s=r['source_start_s'],
                end_s=r['source_end_s'], text=r['text']) for r in saved['segments']])


def normalize_protocol(value):
    """Normalize representation only; never alter grades or audiovisual evidence."""
    value = copy.deepcopy(value)
    if not isinstance(value, dict):
        return value
    changes = []
    for field in ('issues', 'limitations', 'observation_corrections', 'quality_scores'):
        if field in value and value[field] is None:
            value[field] = []; changes.append(field+':null->[]')
    for row in value.get('evidence_reconciliation', []) or []:
        aliases = {'corrected': 'revised', 'inconsistent': 'uncertain', 'conflict': 'uncertain',
                   'contradictory': 'uncertain', 'verified': 'consistent'}
        if row.get('status') in aliases:
            row['status'] = aliases[row['status']]; changes.append('reconciliation_status_alias')
        elif 'status' not in row and row.get('finding'):
            row['status'] = 'uncertain'; changes.append('missing_reconciliation_status->uncertain')
    music = value.get('diagnostics', {}).get('music', {})
    if music and 'role' not in music:
        music['role'] = 'uncertain'; changes.append('missing_music_role->uncertain')
    speech = value.get('diagnostics', {}).get('speech_delivery', {})
    if speech and 'candidate_presence' not in speech:
        speech['candidate_presence'] = 'uncertain'; changes.append('missing_speech_presence->uncertain')
    if changes:
        value['protocol_normalizations'] = changes
    return value


def coerce_support_conflicts(data, reference):
    """After the one repair: downgrade only verdicts whose own support cannot prove the frozen
    evidence mode (audio-only 'preserved' for a visual/joint requirement) to 'partial', and a
    resulting contradictory full score to 4. Recorded; never raises any verdict."""
    if not isinstance(data, dict) or not isinstance(data.get('alignment'), list):
        return []
    requirements = {r['id']: r for r in reference['requirements']}
    changed = []
    for row in data['alignment']:
        required = requirements.get(row.get('requirement_id') if isinstance(row, dict) else None)
        if (required and row.get('status') in ('preserved', 'equivalent') and
                row.get('support') not in (required['evidence_mode'], 'joint')):
            changed.append(dict(requirement_id=row['requirement_id'], from_status=row['status'],
                                support=row.get('support'), required=required['evidence_mode']))
            row['status'] = 'partial'
            row['program_note'] = f'裁判仅以{row.get("support")}支撑而参考要求{required["evidence_mode"]}，程序降为partial'
    ids = {c['requirement_id'] for c in changed}
    for score in data.get('adaptation_scores', []) if changed else []:
        if isinstance(score, dict) and score.get('score') == 5 and ids & set(score.get('requirement_ids', [])):
            score['score'] = 4
            score['program_note'] = '引用的要求被程序降为partial，满分降为4'
    if changed:
        data['program_downgrades'] = changed
    return changed


UNKNOWN = '裁判未提供或与证据矛盾，程序标为无法判断'


def _note(changes, field, before, after):
    changes.append(dict(field=field, before=before, after=after))


def salvage_comparison(data, reference, ref_duration, candidate_duration, modality_checks=None):
    """After the one repair, move only invalid or missing judge fields toward 'uncertain' (never
    toward a better verdict), complete bookkeeping, and record every change."""
    if not isinstance(data, dict):
        return []
    changes = [dict(field='alignment', **c) for c in coerce_support_conflicts(data, reference)]
    expected = {r['id'] for r in reference['requirements']}
    statuses = {r.get('requirement_id'): r.get('status') for r in data.get('alignment', []) if isinstance(r, dict)}
    # 'issues' is never invented as an empty list: a missing/garbled issue list stays a violation.
    for field in ('limitations', 'observation_corrections', 'quality_scores'):
        if not isinstance(data.get(field), list):
            _note(changes, field, data.get(field), [])
            data[field] = []
    for score in data.get('adaptation_scores', []) if isinstance(data.get('adaptation_scores'), list) else []:
        if not isinstance(score, dict):
            continue
        ids = {r['id'] for r in reference['requirements'] if r['dimension'] == score.get('dimension')}
        cited = sorted(ids | (set(score.get('requirement_ids') or []) & expected))
        if ids and cited != score.get('requirement_ids'):
            _note(changes, f'adaptation_scores.{score.get("dimension")}.requirement_ids', score.get('requirement_ids'), cited)
            score['requirement_ids'] = cited
        cited_status = {statuses.get(q) for q in ids}
        if score.get('score') == 5 and not cited_status <= {'preserved', 'equivalent'}:
            _note(changes, f'adaptation_scores.{score.get("dimension")}.score', 5, 4)
            score['score'] = 4
        if cited_status == {'uncertain'} and score.get('score') is not None:
            _note(changes, f'adaptation_scores.{score.get("dimension")}.score', score.get('score'), None)
            score.update(score=None, applicability='uncertain')
    for issue in data['issues'] if isinstance(data.get('issues'), list) else []:
        if not isinstance(issue, dict):
            continue
        if not set(issue.get('requirement_ids', [])) <= expected:
            kept = [q for q in issue.get('requirement_ids', []) if q in expected]
            _note(changes, f'issues.{issue.get("id")}.requirement_ids', issue.get('requirement_ids'), kept)
            issue['requirement_ids'] = kept
        for field, duration in (('reference_span', ref_duration), ('candidate_span', candidate_duration)):
            if issue.get(field) is not None and not span_ok(issue[field], duration):
                _note(changes, f'issues.{issue.get("id")}.{field}', issue[field], None)
                issue[field] = None
        if issue.get('severity') not in ('major', 'minor', 'uncertain'):
            _note(changes, f'issues.{issue.get("id")}.severity', issue.get('severity'), 'uncertain')
            issue['severity'] = 'uncertain'
    rows = [r for r in data.get('evidence_reconciliation', []) if isinstance(r, dict)] if isinstance(
        data.get('evidence_reconciliation'), list) else []
    seen_aspects = [r.get('aspect') for r in rows]
    by_aspect = {r.get('aspect'): r for r in rows if seen_aspects.count(r.get('aspect')) == 1}
    fixed = []
    for aspect in ('sound', 'visible_actions_objects', 'speech'):
        row = by_aspect.get(aspect) or dict(aspect=aspect)
        if seen_aspects.count(aspect) > 1:
            # Duplicated rows disagree with each other: neither is taken.
            _note(changes, f'evidence_reconciliation.{aspect}', 'duplicated_rows', 'uncertain')
            row = dict(aspect=aspect, status='uncertain', finding=UNKNOWN)
        elif row.get('status') not in ('consistent', 'revised', 'uncertain') or not row.get('finding'):
            _note(changes, f'evidence_reconciliation.{aspect}', {k: row.get(k) for k in ('status', 'finding')}, 'uncertain')
            row = dict(row, status='uncertain', finding=row.get('finding') or UNKNOWN)
        fixed.append(row)
    data['evidence_reconciliation'] = fixed
    diagnostics = data.setdefault('diagnostics', {}) if isinstance(data.get('diagnostics', {}), dict) else {}
    data['diagnostics'] = diagnostics
    for key in ('character_identity', 'speech_delivery', 'editing'):
        row = diagnostics.get(key) if isinstance(diagnostics.get(key), dict) else {}
        if row.get('status') not in ('pass', 'partial', 'fail', 'uncertain', 'not_applicable') or not row.get('evidence'):
            _note(changes, f'diagnostics.{key}', {k: row.get(k) for k in ('status', 'evidence')}, 'uncertain')
            row = dict(row, status='uncertain', evidence=row.get('evidence') or UNKNOWN)
        diagnostics[key] = row
    speech = diagnostics['speech_delivery']
    heard = (modality_checks or {}).get('candidate_audio', {}).get('speech_status')
    if speech.get('candidate_presence') not in ('audible', 'absent', 'uncertain') or (
            heard == 'audible' and speech.get('candidate_presence') == 'absent'):
        _note(changes, 'diagnostics.speech_delivery.candidate_presence', speech.get('candidate_presence'), 'uncertain')
        speech['candidate_presence'] = 'uncertain'
    music = diagnostics.get('music') if isinstance(diagnostics.get('music'), dict) else {}
    diagnostics['music'] = music
    if music.get('track_match') not in ('uncertain', 'not_applicable'):
        _note(changes, 'diagnostics.music.track_match', music.get('track_match'), 'uncertain')
        music['track_match'] = 'uncertain'
    if music.get('role') not in ('essential', 'supporting', 'none', 'uncertain') or not music.get('evidence'):
        _note(changes, 'diagnostics.music.role', {k: music.get(k) for k in ('role', 'evidence')}, 'uncertain')
        music.update(role='uncertain' if music.get('role') not in ('essential', 'supporting', 'none', 'uncertain')
                     else music['role'], evidence=music.get('evidence') or UNKNOWN)
    for video in ('reference', 'candidate'):
        presence = music.get(video + '_presence')
        local = (modality_checks or {}).get(video + '_audio', {}).get('music_status')
        if presence not in ('audible', 'absent', 'uncertain') or (presence == 'absent' and local in ('audible', 'uncertain')):
            _note(changes, f'diagnostics.music.{video}_presence', presence, 'uncertain')
            music[video + '_presence'] = 'uncertain'
    if changes:
        data['program_downgrades'] = changes
    return changes


def salvage_modality(data, kind, duration, signal_dbfs=None):
    """Fill unobserved gaps and tail with explicit 'uncertain' segments, clamp times into the clip,
    and turn a silence claim contradicted by the decoded signal into 'uncertain'. Recorded."""
    if not isinstance(data, dict):
        return []
    changes = []
    if not isinstance(data.get('uncertainties'), list):
        _note(changes, 'uncertainties', data.get('uncertainties'), [])
        data['uncertainties'] = []
    if kind == 'audio' and not isinstance(data.get('speech_notes'), list):
        _note(changes, 'speech_notes', data.get('speech_notes'), [])
        data['speech_notes'] = []

    def filler(start, end):
        row = dict(start_s=round(start, 3), end_s=round(end, 3), program_filled=True)
        if kind == 'audio':
            row.update(music_status='uncertain', speech_status='uncertain', heard='裁判未覆盖此段，程序补齐为无法判断')
        else:
            row.update(seen='裁判未覆盖此段，程序补齐为无法判断')
        return row
    rows, cursor = [], 0.0
    raw = data.get('segments') if isinstance(data.get('segments'), list) else []
    usable = [r for r in raw if isinstance(r, dict) and all(type(r.get(k)) in (int, float) for k in ('start_s', 'end_s'))]
    if len(usable) != len(raw):
        _note(changes, 'segments', f'{len(raw) - len(usable)} malformed rows', 'dropped')
    for row in sorted(usable, key=lambda r: r['start_s']):
        start, end = max(0.0, min(row['start_s'], duration)), max(0.0, min(row['end_s'], duration))
        if end <= start:
            _note(changes, 'segments', [row['start_s'], row['end_s']], 'dropped_out_of_range')
            continue
        if (start, end) != (row['start_s'], row['end_s']):
            _note(changes, 'segments', [row['start_s'], row['end_s']], [start, end])
        if start > cursor + .5:
            _note(changes, 'segments', None, [cursor, start])
            rows.append(filler(cursor, start))
        row = dict(row, start_s=start, end_s=end)
        if kind == 'audio':
            for key in ('music_status', 'speech_status'):
                if row.get(key) not in ('audible', 'absent', 'uncertain'):
                    _note(changes, f'segments.{key}', row.get(key), 'uncertain')
                    row[key] = 'uncertain'
            if not row.get('heard'):
                _note(changes, 'segments.heard', None, UNKNOWN)
                row['heard'] = UNKNOWN
        elif not row.get('seen'):
            _note(changes, 'segments.seen', None, UNKNOWN)
            row['seen'] = UNKNOWN
        rows.append(row)
        cursor = max(cursor, end)
    if cursor < duration - .5:
        _note(changes, 'segments', None, [cursor, duration])
        rows.append(filler(cursor, duration))
    if kind == 'audio' and signal_dbfs is not None and signal_dbfs > -55 and rows and all(
            any(word in r.get('heard', '') for word in ('完全静音', '全程静音', '无任何声音', '无音频信号', '无声音信号'))
            for r in rows):
        for row in rows:
            row.update(heard='裁判声称无声但解码有信号，程序改为无法判断', music_status='uncertain', speech_status='uncertain')
        _note(changes, 'segments.heard', 'silence_claim', 'uncertain')
    data['segments'] = rows
    if changes:
        data['program_downgrades'] = changes
    return changes


def salvage_observation(data, duration, reference=False):
    """Observation stages: clamp segment and requirement times into the clip, drop segments left
    empty, fill missing evidence text as unknown; for the reference, a missing evidence mode or
    importance takes the strictest value (joint, critical). Roles are never invented. Recorded."""
    changes = []
    if not isinstance(data, dict):
        return changes

    def clamp(span):
        if isinstance(span, list) and len(span) == 2 and all(type(t) in (int, float) for t in span):
            return [max(0.0, min(span[0], duration)), max(0.0, min(span[1], duration))]
        return span
    if not isinstance(data.get('uncertainties'), list):
        _note(changes, 'uncertainties', data.get('uncertainties'), [])
        data['uncertainties'] = []
    kept = []
    for row in data.get('segments') if isinstance(data.get('segments'), list) else []:
        if not isinstance(row, dict):
            _note(changes, 'segments', 'malformed row', 'dropped')
            continue
        span = clamp([row.get('start_s'), row.get('end_s')])
        if span != [row.get('start_s'), row.get('end_s')]:
            if not (isinstance(span, list) and type(span[0]) in (int, float) and span[1] > span[0]):
                _note(changes, f'segments.{row.get("id")}', [row.get('start_s'), row.get('end_s')], 'dropped_out_of_range')
                continue
            _note(changes, f'segments.{row.get("id")}', [row.get('start_s'), row.get('end_s')], span)
            row.update(start_s=span[0], end_s=span[1])
        for key in ('heard', 'seen', 'edit_function'):
            if not isinstance(row.get(key), str):
                _note(changes, f'segments.{row.get("id")}.{key}', row.get(key), UNKNOWN)
                row[key] = UNKNOWN
        kept.append(row)
    if isinstance(data.get('segments'), list):
        data['segments'] = kept
    if reference and isinstance(data.get('requirements'), list):
        defined = [r for r in data['requirements'] if isinstance(r, dict) and isinstance(r.get('requirement'), str)
                   and r['requirement'].strip()]
        if defined and len(defined) != len(data['requirements']):
            # A requirement the judge never stated cannot be judged; dropped and recorded.
            _note(changes, 'requirements', [r.get('id') if isinstance(r, dict) else r for r in data['requirements']
                                            if r not in defined], 'dropped_undefined_requirement')
            data['requirements'] = defined
        for row in data['requirements']:
            if not isinstance(row, dict):
                continue
            if not isinstance(row.get('permitted_changes'), str) or not row['permitted_changes'].strip():
                _note(changes, f'requirements.{row.get("id")}.permitted_changes', row.get('permitted_changes'), 'none_stated')
                row['permitted_changes'] = '未说明，视为不允许改动'
            if row.get('evidence_mode') not in ('visual', 'audio', 'joint'):
                _note(changes, f'requirements.{row.get("id")}.evidence_mode', row.get('evidence_mode'), 'joint')
                row['evidence_mode'] = 'joint'
            if row.get('importance') not in ('critical', 'supporting'):
                _note(changes, f'requirements.{row.get("id")}.importance', row.get('importance'), 'critical')
                row['importance'] = 'critical'
            span = clamp(row.get('reference_span'))
            if span != row.get('reference_span') and isinstance(span, list) and span[1] > span[0]:
                _note(changes, f'requirements.{row.get("id")}.reference_span', row.get('reference_span'), span)
                row['reference_span'] = span
    if changes:
        data['program_downgrades'] = changes
    return changes


def call_stage(stage, media, prompt, validate, output, api, receipts, coerce=None):
    error = None
    last = None
    for attempt in range(2):
        request_prompt = prompt if error is None else prompt + '\n上次JSON结构校验失败，请重新核对完整媒体并严格按格式返回：' + error
        key = json_sha(dict(prompt=request_prompt, video=sha256_file(media), config=api.cfg))[:20]
        folder = output / 'calls' / f'{stage}_{key}'
        print(json.dumps(dict(stage=stage, attempt=attempt, event='observe'), ensure_ascii=False), flush=True)
        runner = NativeAudioAPI(api) if media.suffix == '.wav' else api
        review = review_native_audio if media.suffix == '.wav' else review_audio_video
        reply = review(media, folder, prompt=request_prompt, api=runner)
        receipts.append(dict(stage=stage, directory=str(folder.relative_to(output)),
                             cache_hit=reply['cache_hit'], usage=reply['usage'],
                             request_sha256=reply['request_sha256'], http_status=reply['http_status'],
                             media_kind='input_audio' if media.suffix == '.wav' else 'video_url'))
        if reply['http_status'] != 200:
            raise RuntimeError(f'{stage}:http_status_{reply["http_status"]}')
        try:
            if reply['parse_error']:
                raise ValueError(reply['parse_error'])
            last = normalize_protocol(reply['parsed'])
            value = validate(last)
        except Exception as exc:  # noqa: BLE001 - a malformed reply is a violation to repair, not a crash
            error = f'{type(exc).__name__}:{exc}' if not isinstance(exc, ValueError) else str(exc)
            if stage == 'compare':
                error += ('；若仅声音支持而参考要求画面/声画，须据实判部分保留/缺失/未知，不可为校验虚填joint；一次性复查全部字段：各维度须包含本维度所有requirement_ids，允许额外引用已知要求；'
                          '引用任一partial/missing/contradictory/uncertain要求时不得给5分；'
                          'issues、limitations、observation_corrections、quality_scores必须是数组，没有内容填[]不能null。')
            write_json(folder / 'validation.json', dict(valid=False, error=error))
        else:
            write_json(folder / 'validation.json', dict(valid=True))
            return value
    if coerce is not None and last is not None and coerce(last):
        # A narrow, recorded downgrade keeps the evaluation; any other violation still fails.
        try:
            value = validate(last)
        except (ValueError, KeyError, TypeError) as exc:
            error = f'{error}；程序降级后仍不合规:{exc}'
        else:
            write_json(folder / 'validation.json', dict(valid=True, program_downgrades=last.get('program_downgrades')))
            return value
    raise ValueError(f'{stage}:invalid_response_after_one_repair:{error}')


def comparison_variation(output, prompt=None):
    """Expose disagreement in recorded protocol-repair replies; never pick the highest grade."""
    grades = {d: [] for d in REFERENCE_DIMENSIONS}
    for path in sorted((output/'calls').glob('compare_*/response.json')):
        try:
            recorded = read_json(path.parent/'request.json')['prompt'].split('\n上次JSON结构校验失败', 1)[0]
            if prompt is not None and recorded != prompt:
                continue
            body = json.loads(read_json(path)['body_text'])
            value = json.loads(body['choices'][0]['message']['content'])
        except (KeyError, ValueError, TypeError):
            continue
        for row in value.get('adaptation_scores', []):
            if row.get('dimension') in grades and type(row.get('score')) is int:
                grades[row['dimension']].append(row['score'])
    return {d: dict(minimum=min(v), maximum=max(v), replies=len(v)) for d, v in grades.items()
            if v and max(v)-min(v) >= 2}


def run(args):
    output = Path(args.output).resolve()
    sources = {kind: Path(getattr(args, kind)).resolve(strict=True) for kind in ('reference', 'candidate')}
    if any(output == p.parent or output in p.parents or p.parent in output.parents for p in sources.values()):
        raise ValueError('evaluation_output_must_be_separate_from_input_directory')
    output.mkdir(parents=True, exist_ok=True)
    with file_lock(output / '.evaluation.lock'):
        return run_locked(args, sources, output)


def run_locked(args, paths, output):
    inputs = {k: dict(path=str(p), sha256=sha256_file(p)) for k, p in paths.items()}
    spec = dict(policy=POLICY, inputs=inputs, fps=args.fps,
                asr_model_dir=str(Path(args.asr_model_dir).resolve()) if args.asr_model_dir else None)
    manifest = output / 'manifest.json'
    if manifest.exists() and read_json(manifest)['spec'] != spec:
        raise ValueError('evaluation_inputs_changed_use_new_output')
    source_code = {p.name: sha256_file(p) for p in Path(__file__).parent.glob('*.py')}
    now = datetime.now(timezone.utc).isoformat()
    write_json(manifest, dict(spec=spec, source_code=source_code, updated_at=now,
                             created_at=read_json(manifest)['created_at'] if manifest.exists() else now))
    inventories = {k: inventory_sources([p], output / 'inputs' / k)['sources'][0] for k, p in paths.items()}
    media = output / 'media'
    media.mkdir(exist_ok=True)
    durations = {k: source['duration_s'] for k, source in inventories.items()}
    bitrate = min(600000, int(5_800_000 * 8 / (sum(durations.values()) + 1) - 64000))
    size = proxy_size(bitrate)
    proxies = {k: make_proxy(s, media / f'{k}.mp4', 'REFERENCE' if k == 'reference' else 'CREATED', bitrate, size)
               for k, s in inventories.items()}
    reel = make_reel(proxies['reference'], proxies['candidate'], media)
    # Whole-picture proxies are needed late; build (and size-check) them before any paid request.
    for kind_name in ('reference', 'candidate'):
        make_modality_proxy(inventories[kind_name], media/f'{kind_name}.visual.mp4', 'visual')
    write_json(output / 'current_status.json', dict(stage='transcribe', status='running'))
    transcripts = {k: transcript(s, output / 'asr' / k, args.asr_model_dir) for k, s in inventories.items()}
    write_json(output / 'transcripts.json', transcripts)
    api = QwenAPI()
    api.cfg['reference_fps_requested'] = args.fps
    observations, receipts = {}, []
    for kind in ('reference', 'candidate'):
        write_json(output / 'current_status.json', dict(stage=kind, status='running'))
        prompt = observe_prompt(kind, durations[kind], proxies[kind]['original_has_audio'], transcripts[kind])
        observations[kind] = call_stage(kind, Path(proxies[kind]['path']), prompt,
            lambda value, k=kind: validate_observation(value, durations[k], k == 'reference'), output, api, receipts,
            coerce=lambda value, k=kind: salvage_observation(value, durations[k], k == 'reference'))
        write_json(output / f'{kind}_observation.json', observations[kind])
    modality_checks = {}
    for video in ('reference', 'candidate'):
        stage = video+'_audio'
        write_json(output/'current_status.json', dict(stage=stage, status='running'))
        modality_checks[stage] = observe_audio(inventories[video], video, media, output, api, receipts)
        write_json(output/'modality_checks.json', modality_checks)
        stage = video+'_visual'
        write_json(output/'current_status.json', dict(stage=stage, status='running'))
        proxy = make_modality_proxy(inventories[video], media/f'{video}.visual.mp4', 'visual')
        modality_checks[stage] = call_stage(stage, Path(proxy['path']), modality_prompt('visual', durations[video]),
            lambda value, d=durations[video]: validate_modality(value, 'visual', d), output, api, receipts,
            coerce=lambda value, d=durations[video]: salvage_modality(value, 'visual', d))
        write_json(output/'modality_checks.json', modality_checks)
    write_json(output / 'current_status.json', dict(stage='compare', status='running'))
    comparison_prompt = compare_prompt(observations['reference'], observations['candidate'], reel, modality_checks)
    comparison = call_stage('compare', Path(reel['path']), comparison_prompt,
        lambda value: validate_comparison(value, observations['reference'], durations['reference'], durations['candidate'], modality_checks),
        output, api, receipts, coerce=lambda value: salvage_comparison(value, observations['reference'],
            durations['reference'], durations['candidate'], modality_checks))
    from . import issue_audit
    issues = comparison['issues']
    audit = dict(findings=[], limitations=[])
    if issues:
        write_json(output/'current_status.json', dict(stage='issue_audit', status='running'))
        audit = call_stage('issue_audit', Path(proxies['candidate']['path']),
            issue_audit.prompt(issues, observations['reference'], modality_checks, durations['candidate']),
            lambda value: issue_audit.validate(value, issues), output, api, receipts)
    disputed = {r['issue_id'] for r in audit['findings'] if r['verdict'] != 'supported'}
    review_dimensions = sorted({d for issue in issues if issue['id'] in disputed for d in issue['dimensions']})
    for kind, path in paths.items():
        if sha256_file(path) != inputs[kind]['sha256']:
            raise ValueError('input_changed_during_evaluation')
    result = dict(policy=POLICY, status='completed', model=api.cfg, inputs=inputs, durations=durations,
        duration_ratio=durations['candidate'] / durations['reference'], observations=observations,
        comparison=comparison, issue_audit=audit, score_review_dimensions=review_dimensions, modality_checks=modality_checks, counts=tally(observations['reference'], comparison), calls=receipts,
        media=proxies, reel=reel, source_code=source_code,
        material_support=dict(status='unknown', reason='本demo未独立检索完整素材库；成片差距不能归因于素材不足。'),
        calibration=dict(status='uncalibrated', reason='自动模型初评，未做人工盲评一致性校准。'),
        limitations=['采样帧率为请求参数，短于采样间隔的动作不能证明已完整核验。',
                     '时间点由模型估计，适合定位复看，不是强制对齐或逐帧测量。',
                     '两段采用有声代理视频顺序呈现，独立观察后进行一次配对评测，未验证顺序稳定性。',
                     '使用与生产终审相同的模型家族；隔离上下文不等于获得独立的人类真值。',
                     '静音独立观察保留完整画面；字幕仍可见，已要求不能用文字推断动作，仍有捷径风险。'])
    result['judge_variation'] = comparison_variation(output, comparison_prompt)
    if result['judge_variation']:
        result['limitations'].append('比较阶段的结构修正回复出现相差至少2级的判断：' +
            '、'.join(REFERENCE_DIMENSIONS[d] for d in result['judge_variation']) +
            '。采用首个通过当前校验的回复，不挑高分；这些维度需要复核。')
    previous = output / 'evaluation.json'
    if previous.exists():
        write_json(output / 'history' / f'evaluation_{sha256_file(previous)[:20]}.json', read_json(previous))
    write_json(output / 'evaluation.json', result)
    from .report import render_reports
    render_reports(result, output)
    write_json(output / 'current_status.json', dict(stage='completed', status='completed', report=str(output / 'report.html')))
    print(json.dumps(dict(status='completed', report=str(output / 'report.html'), counts=result['counts']), ensure_ascii=False), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', required=True)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--asr-model-dir')
    parser.add_argument('--fps', type=float, default=4.0)
    parser.add_argument('--credentials-file', help='Optional existing credential file; only DASHSCOPE_API_KEY is loaded.')
    args = parser.parse_args()
    if not math.isfinite(args.fps) or not 0 < args.fps <= 10:
        parser.error('--fps must be within (0, 10]')
    if args.credentials_file and not os.environ.get('DASHSCOPE_API_KEY'):
        os.environ['DASHSCOPE_API_KEY'] = read_json(args.credentials_file)['DASHSCOPE_API_KEY']
    try:
        run(args)
    except Exception as exc:
        # Do not stringify transport objects or credentials into the public report.
        print(json.dumps(dict(status='failed', error_type=type(exc).__name__, error=str(exc)[:400]), ensure_ascii=False), file=sys.stderr)
        try:
            import traceback
            # Code locations only (no request bodies), so the next failure can be located exactly.
            write_json(Path(args.output) / 'evaluation_failure.json', dict(error_type=type(exc).__name__,
                error=str(exc)[:2000], frames=[f'{f.filename}:{f.lineno} {f.name}' for f in traceback.extract_tb(exc.__traceback__)]))
        except Exception:  # noqa: BLE001 - diagnostics must never mask the failure
            pass
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
