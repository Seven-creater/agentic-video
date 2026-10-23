"""Transcript-derived questions, frozen independently of production answers."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

from ..library.media import sha256_file, probe_media
from ..library.state import json_sha, write_json, file_lock

TASKS = ('elements', 'forecast', 'story', 'bgm')
SCHEMA = 'production_reference_evaluation_v1'
METHODS = dict(elements='meeting 00:01:25', forecast='meeting 00:02:20',
               story='meeting 00:03:10-00:03:34', bgm='meeting 00:04:09/00:05:11')


def read(path):
    return json.loads(Path(path).read_text())


def freeze(value, path):
    validate(value)
    frozen = dict(value, protocol_sha256=json_sha(value))
    path = Path(path)
    with file_lock(path.with_suffix('.lock'), wait=True):
        if path.exists() and read(path) != frozen:
            raise ValueError('reference_eval_frozen_manifest_changed')
        write_json(path, frozen)
    return frozen


def load(path, *, media=True):
    value = read(path)
    if json_sha({k: v for k, v in value.items() if k != 'protocol_sha256'}) != value['protocol_sha256']:
        raise ValueError('reference_eval_protocol_changed')
    validate(value, media=media)
    return value


def validate(value, *, media=True):
    if value.get('schema_version') != SCHEMA or len(value['cases']) != 10:
        raise ValueError('reference_eval_requires_ten_templates')
    if len({c['id'] for c in value['cases']}) != 10:
        raise ValueError('reference_eval_duplicate_case')
    if value['methods'] != METHODS or len(value['human_review_cases']) != 6:
        raise ValueError('reference_eval_transcript_or_review_scope_changed')
    if not set(value['human_review_cases']) <= {c['id'] for c in value['cases']}:
        raise ValueError('reference_eval_review_case_unknown')
    all_support = {c['reference']['sha256'] for c in value['cases']}
    all_query = {c['query']['sha256'] for c in value['cases']}
    if all_support & all_query:
        raise ValueError('reference_eval_unseen_video_leakage')
    for case in value['cases']:
        for asset in (case['reference'], case['query']):
            if media and sha256_file(asset['path']) != asset['sha256']:
                raise ValueError('reference_eval_original_media_changed')
        if case['labels']['provenance'] != 'model_assisted_unconfirmed':
            raise ValueError('reference_eval_do_not_forge_human_labels')
        q = case['labels']
        if 'preparation_error' in q:
            continue  # A failed planned item remains visible; it is not fake gold.
        if not q['elements'] or not all(isinstance(x, str) and x.strip() for x in q['elements']):
            raise ValueError('reference_eval_elements_required')
        if not 0 < q['cut_s'] < case['query']['duration_s']:
            raise ValueError('reference_eval_cut_outside_unseen_video')
        if not q['future_events'] or not q['story_paragraphs'] or not q['story_target_indices']:
            raise ValueError('reference_eval_questions_incomplete')
        if any(type(i) is not int or not 0 <= i < len(q['story_paragraphs']) for i in q['story_target_indices']):
            raise ValueError('reference_eval_story_answer_outside_text')


def public_question(case, task, understanding, story):
    """Only the exam adapter receives this object; answers stay outside it."""
    if task not in {'forecast', 'story'}:
        raise ValueError('reference_eval_no_fake_audio_input')
    common = dict(understanding=understanding,
                  instruction='利用已保存的参考理解回答试题；不要重做参考分析。')
    if task == 'forecast':
        return dict(**common, question='只根据实际提供的视频前缀，预测之后的关键事件及先后顺序。',
                    response_contract=dict(events=['按发生顺序预测的事件'], uncertainties=[]))
    return dict(**common, question='从以下长故事中找出与参考表达机制最匹配的连续情节，返回段落ID和逐字原文。',
                story=story, response_contract=dict(paragraph_ids=['段落ID'], quotations=['逐字原文']))


def story_text(manifest):
    return [dict(id=f'p{index:03d}', text=text)
            for index, text in enumerate(text for case in manifest['cases']
                                         for text in case['labels'].get('story_paragraphs', []))]


def story_gold(manifest, selected):
    offset = 0
    for case in manifest['cases']:
        if case['id'] == selected:
            return {f'p{offset + i:03d}' for i in case['labels']['story_target_indices']}
        offset += len(case['labels'].get('story_paragraphs', []))
    raise ValueError('reference_eval_story_case_missing')


def prefix(asset, cut_s, output):
    """Physically remove hidden audio and pictures, with a new zero-based clock."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    target, receipt = output / 'prefix.mp4', output / 'prefix.json'
    binding = dict(source_sha256=asset['sha256'], start_s=0, end_s=cut_s,
                   policy='actual_prefix_av_v1')
    if sha256_file(asset['path']) != asset['sha256']:
        raise ValueError('reference_eval_prefix_source_changed')
    if receipt.exists():
        saved = read(receipt)
        if saved['binding'] != binding or sha256_file(target) != saved['sha256']:
            raise ValueError('reference_eval_prefix_changed')
        return target
    temp = output / 'prefix.part.mp4'
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-threads', '2',
        '-i', asset['path'], '-t', str(cut_s), '-map', '0:v:0', '-map', '0:a?',
        '-vf', 'scale=640:-2', '-r', '15', '-c:v', 'libx264', '-preset', 'veryfast',
        '-threads', '2', '-c:a', 'aac', '-af', 'asetpts=PTS-STARTPTS', str(temp)],
        check=True, capture_output=True, timeout=600)
    # AAC/frame rounding is allowed only at the encoded boundary, never as a
    # whole extra scene. The original is never an input to the exam request.
    if probe_media(temp)['duration_s'] > cut_s + .1:
        raise ValueError('reference_eval_prefix_leaks_hidden_tail')
    temp.replace(target)
    write_json(receipt, dict(binding=binding, sha256=sha256_file(target)))
    return target


def imported_human_reviews(path, manifest):
    if path is None or not Path(path).exists():
        return {}
    value = read(path)
    if (value.get('protocol_sha256') != manifest['protocol_sha256'] or
            value.get('confirmed_by') != 'user' or
            set(value.get('cases', {})) - set(manifest['human_review_cases'])):
        raise ValueError('reference_eval_human_confirmation_binding_invalid')
    for review in value['cases'].values():
        if set(review)-set(TASKS)-{'judgment_agreement','notes'}:
            raise ValueError('reference_eval_human_review_task_unknown')
        if any(v not in {'confirmed','rejected'} for k,v in review.items() if k in TASKS):
            raise ValueError('reference_eval_human_verdict_required')
        if any(k not in TASKS or type(v) is not bool for k,v in review.get('judgment_agreement',{}).items()):
            raise ValueError('reference_eval_human_judgment_agreement_invalid')
    return value['cases']
