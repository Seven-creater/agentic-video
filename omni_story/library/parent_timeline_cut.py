"""Pure local finecut contracts over an existing video's output timeline."""
from __future__ import annotations

import json
import math


def _require(ok, label):
    if not ok:
        raise ValueError('parent_timeline_cut:' + label)


def _number(value, low, high, label):
    _require(type(value) in (int, float) and math.isfinite(value)
             and low <= value <= high, label)
    return value


def _text(value, label):
    _require(isinstance(value, str) and bool(value.strip()), label)


def slot_prompt(slot, knowledge=''):
    duration = slot['end_s'] - slot['start_s']
    return ('精剪当前附件中的成片段落；不是重新从电影库找镜头。附件时间从0开始。'
            '原段落说明是之前模型的可错导航，必须观察附件确认。'
            '抽取能讲清内容的关键姿态、动作和结果；删除等待和重复，动作高光可慢放，'
            '过程可加速，重要身份和结果留够观看时间。不要强迫每段缩短。'
            '保持原顺序，镜头不能重叠，时间不能越出附件。保持内容和前后呼应。'
            '冻结只能延长最后实际画面，不能创造动作；不要添加字幕或新画面。'
            '输出一个JSON对象，字段如下；所有时间是附件局部秒，speed范围0.5–2，'
            'hold_s范围0–3，shots为1–8个，limitations为字符串数组：\n' +
            json.dumps({'slot_id': slot['slot_id'], 'shots': [{'in_s': 0, 'out_s': duration,
                'speed': 1, 'hold_s': 0, 'reason': '说明为何保留以及剪辑作用',
                'visible_content': '实际画面发生什么'}], 'preserved_meaning': '保留下来的可见内容',
                'limitations': []}, ensure_ascii=False) + '\n原模型段落导航：' +
            json.dumps(slot, ensure_ascii=False) + '\n通用剪辑知识：' + knowledge)


def validate_slot_cut(value, slot):
    _require(isinstance(value, dict) and value.get('slot_id') == slot['slot_id'], 'slot_id')
    shots = value.get('shots')
    _require(isinstance(shots, list) and 1 <= len(shots) <= 8, 'shots')
    duration, previous = slot['end_s'] - slot['start_s'], 0
    for shot in shots:
        _require(isinstance(shot, dict), 'shot')
        start = _number(shot.get('in_s'), 0, duration, 'in_s')
        end = _number(shot.get('out_s'), 0, duration, 'out_s')
        speed = _number(shot.get('speed'), .5, 2, 'speed')
        _number(shot.get('hold_s'), 0, 3, 'hold_s')
        _require(start >= previous and end > start, 'chronological_nonoverlap')
        _require(round((end - start) / speed * 30) >= 1, 'shorter_than_frame')
        for key in ('reason', 'visible_content'):
            _text(shot.get(key), key)
        previous = end
    _text(value.get('preserved_meaning'), 'preserved_meaning')
    _require(isinstance(value.get('limitations'), list)
             and all(isinstance(x, str) for x in value['limitations']), 'limitations')
    return value


def build_plan(parent_source, outline, cuts, *, audio_mode='source', reference=None):
    """Map model-owned local seconds to the immutable parent's actual timeline."""
    slots = outline['slots']
    _require(len(cuts) == len(slots) and slots, 'slot_coverage')
    _require(audio_mode in {'source', 'reference', 'silent'}, 'audio_mode')
    _require(parent_source['sha256'] == outline['parent_sha256'], 'parent_sha256')
    source_id = parent_source['source_id']
    _require(source_id == 'src_' + parent_source['sha256'][:16], 'source_id')
    _require(len({s['slot_id'] for s in slots}) == len(slots), 'duplicate_slot_id')
    rows, previous, changed = [], 0, False
    for slot, cut in zip(slots, cuts, strict=True):
        start, end = slot['start_s'], slot['end_s']
        _number(start, 0, parent_source['duration_s'], 'slot_start')
        _number(end, 0, parent_source['duration_s'], 'slot_end')
        _require(end > start and abs(start - previous) <= .001, 'slot_contiguous')
        validate_slot_cut(cut, slot)
        kept = sum(s['out_s'] - s['in_s'] for s in cut['shots'])
        changed |= round((end - start - kept) * 30) >= 1
        for shot in cut['shots']:
            changed |= (round((shot['out_s'] - shot['in_s']) / shot['speed'] * 30)
                        != round((shot['out_s'] - shot['in_s']) * 30))
            changed |= round(shot['hold_s'] * 30) >= 1
            rows.append({'source_id': source_id, 'window_id': slot['slot_id'],
                'slot_id': slot['slot_id'], 'source_in_s': start + shot['in_s'],
                'source_out_s': start + shot['out_s'], 'speed': shot['speed'],
                'freeze_tail_s': shot['hold_s'], 'look': 'none', 'framing': 'fit'})
        previous = end
    _require(abs(previous - parent_source['duration_s']) <= .001, 'parent_coverage')
    _require(changed, 'no_actual_edit')
    plan = {'fps': 30, 'width': 720, 'height': 1280, 'audio_mode': audio_mode,
            'segments': rows, 'slots': [dict(s) for s in slots]}
    if audio_mode == 'reference':
        _require(isinstance(reference, dict), 'reference_audio_required')
        plan['reference_audio'] = dict(reference)
    return plan


def review_prompt(sha256, duration_s, slot_intervals=None):
    return ('静音审核附件实际成片，不猜预设剧本。描述实际可见的人物、行动、结果及整体含义。'
            '检查等待/重复是否冗长，高光慢放是否有作用，重要身份和结果是否来得及看清。'
            '字幕信息与画面信息分别说明。给出输出局部时间证据，不把自信度当质量。'
            '输出JSON：video_sha256,observed_meaning,status(pass/partial/fail),'
            'slots数组{slot_id,status(pass/partial/fail),readability, redundancy,'
            'evidence:[{start_s,end_s,visible_content}]},limitations字符串数组。\n' +
            json.dumps({'video_sha256': sha256, 'actual_duration_s': duration_s,
                        'slot_intervals': slot_intervals or []}, ensure_ascii=False))


def validate_review(value, sha256, duration_s, slot_ids):
    _require(isinstance(value, dict) and value.get('video_sha256') == sha256, 'review_sha')
    _require(value.get('status') in {'pass', 'partial', 'fail'}, 'review_status')
    _text(value.get('observed_meaning'), 'observed_meaning')
    rows = value.get('slots')
    _require(isinstance(rows, list) and all(isinstance(s, dict) for s in rows)
             and [s.get('slot_id') for s in rows] == list(slot_ids), 'review_slots')
    for row in rows:
        _require(row.get('status') in {'pass', 'partial', 'fail'}, 'slot_review_status')
        _text(row.get('readability'), 'readability')
        _text(row.get('redundancy'), 'redundancy')
        evidence = row.get('evidence')
        _require(isinstance(evidence, list) and evidence, 'review_evidence')
        for item in evidence:
            _require(isinstance(item, dict), 'review_evidence_item')
            start = _number(item.get('start_s'), 0, duration_s, 'review_start')
            end = _number(item.get('end_s'), 0, duration_s, 'review_end')
            _require(end > start, 'review_interval')
            _text(item.get('visible_content'), 'review_visible_content')
    _require(isinstance(value.get('limitations'), list)
             and all(isinstance(x, str) for x in value['limitations']), 'review_limitations')
    return value
