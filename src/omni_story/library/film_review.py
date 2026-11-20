"""P5 (王骐昊 11-17): compare a delivered film with its reference, in text.

Program metrics come from the delivery itself (timeline, lines) and the reference split; one GLM
text call then says, beat by beat, what of the reference structure the film carries, whether the
conflict is set up, and what is similar or different. A report only: nothing here changes a run.
"""
from __future__ import annotations

import collections
import json
from pathlib import Path


POLICY = 'film_review_v1'


def _union(ranges):
    total, end = 0.0, None
    for a, b in sorted(ranges):
        if end is None or a > end:
            total += b - a; end = b
        elif b > end:
            total += b - end; end = b
    return total


def _runs(roles):
    """Longest run of consecutive lines by one role, and how often the speaker changes."""
    longest, current, changes = 0, 0, 0
    for i, role in enumerate(roles):
        current = current + 1 if i and role == roles[i - 1] else 1
        changes += bool(i and role != roles[i - 1])
        longest = max(longest, current)
    return longest, changes


def program_metrics(result, lock, split):
    timeline = result['timeline']
    duration = max(s['end_s'] for s in timeline)
    per_source = collections.defaultdict(list)
    for s in timeline:
        per_source[s.get('source_id')].append((s['source_in_s'], s['source_out_s']))
    unique = sum(_union(r) for r in per_source.values())
    shot_time = collections.Counter()
    for s in timeline:
        shot_time[s['slot_id']] += s['end_s'] - s['start_s']
    film_roles = [l['role'] for l in result['lines']]
    ref_lines = split.get('lines', [])
    ref_roles = [l.get('role') for l in ref_lines]
    longest, changes = _runs(film_roles)
    ref_longest, ref_changes = _runs(ref_roles)
    kept = {i for l in result['lines'] for i in l.get('reference_line_ids', [])}
    return dict(
        film=dict(duration_s=round(duration, 1), shots=len(shot_time), unique_source_s=round(unique, 1),
                  reuse=round(sum(s['source_out_s'] - s['source_in_s'] for s in timeline) / max(unique, 1e-9), 2),
                  top_shot_share=round(shot_time.most_common(1)[0][1] / duration, 3) if shot_time else 0,
                  lines=len(film_roles), lines_by_role=dict(collections.Counter(film_roles)),
                  longest_same_speaker_run=longest, speaker_changes=changes),
        reference=dict(scenes=len(split.get('scenes', [])), lines=len(ref_lines),
                       lines_by_role=dict(collections.Counter(ref_roles)),
                       longest_same_speaker_run=ref_longest, speaker_changes=ref_changes,
                       duration_s=lock.get('reference_duration_s')),
        reference_lines_carried=round(len(kept) / max(1, len(ref_lines)), 2))


def film_text(result, lock):
    """What the viewer gets, as text: each line with who says it to whom over which picture."""
    actions = {}
    for scene in lock.get('scenes', []):
        for shot in scene.get('coverage_shots', []):
            actions[shot['slot_id']] = shot.get('action', '')
    rows = []
    for line in result['lines']:
        rows.append(dict(line_id=line['line_id'], role=line['role'], to=line.get('addressee_roles', []),
                         text=line['text'], picture=[actions.get(s, '') for s in line.get('shot_ids', [])][:2]))
    return rows


def review_prompt(split, film_rows, metrics):
    reference = dict(scenes=[{k: s.get(k) for k in ('scene_id', 'roles', 'interaction', 'template', 'visual_function')}
                             for s in split.get('scenes', [])],
                     lines=[{k: l.get(k) for k in ('line_id', 'role', 'text', 'function')} for l in split.get('lines', [])])
    return ('对照参考视频与二创成片（都已转成文字）。成片用另一部作品的素材改编，人物、物件、台词措辞可以不同；'
        '只比较故事结构和观感：参考的每个情节节拍成片是否交代了，冲突的起因观众能否看懂，说话关系是否清楚，'
        '画面是否支撑台词。program_metrics是程序算出的数字，直接引用，不要重算。'
        'beats按参考的情节顺序逐条写；present取 yes/partial/no，evidence引用成片line_id或画面描述。'
        'similarities与differences各写抽象的一句话结论（跨作品通用的说法），suggestions只写对下一次改编可执行的建议。'
        '返回JSON {"beats":[{"beat":"","present":"yes","evidence":""}],'
        '"conflict_setup":{"clear":true,"evidence":""},"speaker_clarity":{"clear":true,"evidence":""},'
        '"similarities":[],"differences":[],"suggestions":[]}。\n'
        + json.dumps(dict(reference=reference, film=film_rows, program_metrics=metrics), ensure_ascii=False))


def validate_review(value):
    if not isinstance(value, dict) or not isinstance(value.get('beats'), list) or not value['beats']:
        raise ValueError('film_review:beats_required')
    for beat in value['beats']:
        if (not isinstance(beat, dict) or not str(beat.get('beat', '')).strip()
                or beat.get('present') not in ('yes', 'partial', 'no') or not str(beat.get('evidence', '')).strip()):
            raise ValueError('film_review:each_beat_needs_present_yes_partial_no_and_evidence')
    for key in ('conflict_setup', 'speaker_clarity'):
        item = value.get(key)
        if not isinstance(item, dict) or not isinstance(item.get('clear'), bool) or not str(item.get('evidence', '')).strip():
            raise ValueError(f'film_review:{key}_clear_and_evidence')
    for key in ('similarities', 'differences', 'suggestions'):
        if not isinstance(value.get(key), list) or any(not isinstance(x, str) for x in value[key]):
            raise ValueError(f'film_review:{key}_strings')
    return value


def find_split(run):
    """The reference split of a run or of its nearest resume ancestor that has one."""
    for folder in [Path(run), *Path(run).parents]:
        found = sorted(folder.glob('artifacts/scene_split_*.json'))
        if found:
            data = json.loads(found[-1].read_text(encoding='utf-8'))
            return data.get('value', data)
    raise FileNotFoundError('film_review:no_scene_split_in_run_or_ancestors')


def markdown(report):
    m, r = report['metrics'], report['review']
    f, ref = m['film'], m['reference']
    out = [f"# 成片与参考对照（{POLICY}）", '',
           '| 项 | 成片 | 参考 |', '|---|---|---|',
           f"| 时长 | {f['duration_s']} 秒 | {ref['duration_s']} 秒 |",
           f"| 台词数 | {f['lines']} | {ref['lines']} |",
           f"| 各角色台词 | {f['lines_by_role']} | {ref['lines_by_role']} |",
           f"| 同一人连续最多说 | {f['longest_same_speaker_run']} 句 | {ref['longest_same_speaker_run']} 句 |",
           f"| 说话人切换次数 | {f['speaker_changes']} | {ref['speaker_changes']} |",
           f"| 镜头数 / 复用倍数 / 最常用镜头占比 | {f['shots']} / {f['reuse']} / {f['top_shot_share']:.0%} | — |",
           f"| 参考台词被承载比例 | {m['reference_lines_carried']:.0%} | — |", '',
           '## 情节节拍', '', '| 节拍 | 成片 | 依据 |', '|---|---|---|']
    out += [f"| {b['beat']} | {b['present']} | {b['evidence']} |" for b in r['beats']]
    out += ['', f"- 冲突起因清楚：{r['conflict_setup']['clear']}（{r['conflict_setup']['evidence']}）",
            f"- 说话关系清楚：{r['speaker_clarity']['clear']}（{r['speaker_clarity']['evidence']}）", '',
            '## 相似之处', *[f'- {x}' for x in r['similarities']], '',
            '## 不同之处', *[f'- {x}' for x in r['differences']], '',
            '## 下一次改编建议', *[f'- {x}' for x in r['suggestions']], '']
    return '\n'.join(out)


def review_film(preview, creative, output, call):
    preview, creative, output = Path(preview), Path(creative), Path(output)
    result = json.loads((preview / 'dialogue_result.json').read_text(encoding='utf-8'))
    lock = json.loads((preview / 'material_lock.json').read_text(encoding='utf-8'))
    split = find_split(creative)
    metrics = program_metrics(result, lock, split)
    validate = validate_review
    validate.repair_limit = 2
    review = call('material_scene_film_review_' + result['final_sha256'][:12],
                  review_prompt(split, film_text(result, lock), metrics), validate)
    report = dict(policy=POLICY, final_sha256=result['final_sha256'], metrics=metrics, review=review)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'film_review.json').write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    (output / 'film_review.md').write_text(markdown(report), encoding='utf-8')
    return report


def main(argv=None):
    import argparse, os
    from .opencode_provider import PROVIDER, OpenCodeMCP
    from .pipeline import _material_call
    from .server_cli import _home, credential, settings
    from .state import LibraryState
    parser = argparse.ArgumentParser(description='compare a delivered film with its reference (text)')
    parser.add_argument('preview'); parser.add_argument('creative'); parser.add_argument('output')
    args = parser.parse_args(argv)
    server = settings(_home()); os.environ['Z_AI_API_KEY'] = credential(_home())
    result = json.loads((Path(args.preview) / 'dialogue_result.json').read_text(encoding='utf-8'))
    state = LibraryState(Path(args.output) / 'ledger', dict(final_sha256=result['final_sha256'],
        configuration={**PROVIDER, 'workflow': POLICY}), max_requests=None, registry_path=Path(args.output) / '.runs.json')
    glm = OpenCodeMCP(state, package_root=server['mcp_package_root'], executable=server['opencode_executable'])
    report = review_film(args.preview, args.creative, args.output,
                         lambda n, p, v: _material_call(state, glm, n, p, v))
    print(json.dumps(report['metrics'], ensure_ascii=False))


if __name__ == '__main__':
    main()
