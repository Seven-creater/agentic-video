#!/usr/bin/env python3
"""只读真实时长回放；理想修复按文本字数同比缩短时长，不调用TTS或模型。"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import tempfile

from omni_story.library.story_context import schedule_draft, validate_story_context
from omni_story.library.visual_audio import revise_draft_timing, timing_editable_line_ids


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def contexts(root):
    for path in sorted(root.rglob('rough_audio/story_context.json')):
        yield path, read(path)
    for binding in sorted(root.rglob('creative_audio/tts_binding_v*.json')):
        saved = read(binding)
        if not saved.get('timing_problems'):
            continue
        version = int(binding.stem.rsplit('v', 1)[1])
        draft = binding.parent / 'draft_timing_revision.json'
        path = draft if version else binding.parent.parent / 'story_context.json'
        context = read(path)
        context['received_tts'] = saved['lines']
        result = binding.parent / 'audio_result.json'
        if result.is_file():
            result = read(result)
            if result.get('selected_version') == version:
                assert result['timing_problems'] == saved['timing_problems']
        yield binding, context


def ideal_fix(context, editable):
    """Existence witness: each editable line keeps one character, with proportional TTS.

    Keep every immutable line exactly. Move editable lines only within their own
    segment_ids, respecting any existing start anchor. No claim about story quality.
    """
    reply = dict(audio_draft=deepcopy(context['audio_draft']))
    by_id = {row['id']: row for row in context['segments']}
    durations = {row['id']: row['actual_duration_s'] for row in context['received_tts']}
    cursor, solvable = 0., {}
    for line in reply['audio_draft']:
        first, last = by_id[line['segment_ids'][0]], by_id[line['segment_ids'][-1]]
        anchor = line.get('start_anchor')
        anchored = by_id[anchor['segment_id']]['start_s'] + anchor['offset_s'] if anchor else None
        if line['id'] in editable:
            duration = durations[line['id']] / len(line['text'])
            start = max(cursor, first['start_s'] if anchored is None else anchored)
            finish = start + duration
            solvable[line['id']] = (finish <= last['end_s'] + 1e-6 and
                                    (anchored is None or cursor <= anchored + 1e-6))
            if solvable[line['id']]:
                line.update(text=line['text'][:1], start_s=start, end_s=finish)
                durations[line['id']] = duration
            else:
                finish = start + durations[line['id']]
        else:
            start = max(cursor, line['start_s'] if anchored is None else anchored)
            finish = start + durations[line['id']]
            solvable[line['id']] = finish <= line['end_s'] + 1e-6
        cursor = finish
    return reply, durations, solvable


def check(root):
    rows = []
    print('假设：可编辑文本缩到首字，注入时长=实测时长/原字数；仅验证排程可行性及原校验器，未生成TTS，未评判内容质量。')
    with tempfile.TemporaryDirectory(prefix='timing-blocker-') as temporary:
        for index, (path, original) in enumerate(contexts(root)):
            validate_story_context(original)
            measured = schedule_draft(original)
            problems = measured['timing_problems']
            if not problems:
                continue
            # Recomputed legacy fields must match the recorded production diagnosis.
            recorded = original.get('timing_problems')
            if path.name.startswith('tts_binding_'):
                recorded = read(path)['timing_problems']
            assert [{key: p[key] for key in ('line_id', 'actual_duration_s', 'available_s', 'reason')}
                    for p in problems] == recorded
            old = {row['line_id'] for row in problems}
            editable = set(timing_editable_line_ids(problems))
            task_root = path.parent.parent if path.parent.name == 'rough_audio' else path.parent.parent.parent
            family = 'kuangbiao' if 'kuangbiao' in str(path) else 'xiaobiesan'
            task = f'{family}/{task_root.name}/{path.stem if path.name.startswith("tts_binding_") else "rough"}'
            print(f'\n{task} editable_line_ids={sorted(editable)}')
            for problem in problems:
                print(json.dumps(problem, ensure_ascii=False))
            _, _, old_feasible = ideal_fix(measured, old)
            fixed, durations, feasible = ideal_fix(measured, editable)
            assert all(feasible.values()), (task, feasible)
            accepted = False

            class MCP:
                def call(self, stage, prompt, video, validator, **kwargs):
                    nonlocal accepted
                    validator(fixed)
                    accepted = True
                    return fixed

            stage = 'rough_audio_timing_1' if path.parent.name == 'rough_audio' else 'audio_plan_v1'
            output = Path(temporary) / str(index)
            output.mkdir()
            revised = revise_draft_timing(measured, MCP(), __file__, output, stage=stage)
            revised['received_tts'] = [dict(row, actual_duration_s=durations[row['id']])
                                       for row in measured['received_tts']]
            remaining = schedule_draft(revised)['timing_problems']
            assert accepted and not remaining, (task, remaining)
            print('修复注入时长=' + json.dumps({key: round(durations[key], 6) for key in sorted(editable)},
                                            ensure_ascii=False) + '；剩余timing_problems=[]；validator=ACCEPT')
            for problem in problems:
                rows.append((task, problem['line_id'], ','.join(problem['blocked_by']) or '—',
                             old_feasible[problem['line_id']], feasible[problem['line_id']], accepted))
    print('\n| task | problem line | blocker | old solvable? | new solvable? | validator accepts fix? |')
    print('| --- | --- | --- | --- | --- | --- |')
    for task, line, blockers, old, new, accepted in rows:
        print(f'| {task} | {line} | {blockers} | {"yes" if old else "no"} | {"yes" if new else "no"} | {"yes" if accepted else "no"} |')
    print(f'\nTOTAL: {len(rows)} problems; new_solvable={sum(row[4] for row in rows)}; '
          f'validator_accepted={sum(row[5] for row in rows)}; old_unsolvable={sum(not row[3] for row in rows)}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    args = parser.parse_args()
    check(args.root.resolve(strict=True))


if __name__ == '__main__':
    main()
