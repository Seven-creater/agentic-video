#!/usr/bin/env python3
"""Serial generation -> reference-paired evaluation, with version-bound reuse.

Template names are display labels only. All cases use one production baseline and
one evaluator policy. Declared compatible releases are revalidated. Quality findings are reported, never hidden by score-based
regeneration. A failed case does not prevent the rest of the batch from running.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
from html import escape
import json
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

import scene_batch
from omni_story.api import QwenAPI
from omni_story.evaluation import reference_pair as pair
from omni_story.evaluation.prompts import POLICY, REFERENCE_DIMENSIONS
from omni_story.library.media import sha256_file
from omni_story.library.state import file_lock, json_sha, write_json

AUDIT_POLICY = dict(policy='batch-evidence-audit-v1', duration_change_review_fraction=.25,
                    purpose='review_hint_not_a_quality_gate')


def read(path):
    path = Path(path)
    return json.loads(path.read_text()) if path.is_file() else {}


def source_hashes(root):
    root = Path(root)
    return {str(p.relative_to(root)): sha256_file(p) for p in sorted(root.rglob('*'))
            if not p.name.startswith('._') and p.suffix in ('.py', '.mjs') and 'evaluation' not in p.relative_to(root).parts}


def option(command, flag):
    return command[command.index(flag) + 1] if flag in command else None


def generation_options(args):
    from omni_story.library.material_profile import default_path, load
    path = getattr(args, 'material_profile', None) or default_path(args.home)
    if path:
        profile, binding = load(path)
        args.material_profile = Path(binding['path'])
        return {'--library': profile['library'], '--creative-style': 'picture_aware',
                '--material-flow': 'scene_v1', '--asr-model-dir': str(args.asr_model_dir),
                '--visual-library': profile['visual_library_root'], '--material-index': profile['material_index_root'],
                '--material-profile': binding['path']}
    return {'--library': str(args.home/'shared/data/videos'), '--creative-style': 'picture_aware',
            '--material-flow': 'scene_v1', '--asr-model-dir': str(args.asr_model_dir),
            '--visual-library': str(args.home/'shared/data/visual_library/three_films_v2')}


def artifact(attempt, reference_sha):
    target = Path(attempt['output'])
    result = read(target/'dialogue_result.json')
    # Fallback levels and subtitle-free deliveries name their own final file.
    video = Path(result.get('final_video') or target/'final_subtitled.mp4')
    if (read(target/'run_outcome.json').get('outcome') not in ('delivered', 'delivered_with_review_notes') or
        read(target/'reference_reading.json').get('reference_sha256') != reference_sha or not video.is_file()):
        return None
    digest = sha256_file(video)
    if result.get('final_sha256') != digest:
        return None
    return dict(path=str(video), sha256=digest, generation_output=str(target),
                content_delivery_ready=result.get('content_delivery_ready'), delivery_status=result.get('status'),
                fallback_level=result.get('fallback_level', 0), delivery_mode=result.get('delivery_mode', 'normal'))


def generation_release(server, source):
    server, source = Path(server).resolve(), Path(source).resolve()
    if str(source) not in server.read_text():
        raise ValueError('server_wrapper_does_not_bind_generation_source')
    canonical, legacy = hashlib.sha256(), hashlib.sha256()
    for path in sorted(p for p in (source/'src/omni_story/library').iterdir() if p.suffix in ('.py', '.mjs')):
        legacy.update(path.name.encode()); legacy.update(path.read_bytes())
        if not path.name.startswith('._'):
            canonical.update(path.name.encode()); canonical.update(path.read_bytes())
    return dict(server=str(server), server_sha256=sha256_file(server), generation_source=str(source),
                source_sha256=json_sha(source_hashes(source/'src/omni_story')),
                library_sha256=canonical.hexdigest(), legacy_library_sha256=legacy.hexdigest())


def current_contract(attempt):
    """Recheck a declared compatible release; this does not regrade its creative quality."""
    from omni_story.library.joint_dialogue import omit_unfilmed_silent_roles, settle_offscreen_roles, validate_script
    from omni_story.library.scene_coverage import same_actor
    target = Path(attempt['output'])
    lock = read(target/'material_lock.json')
    try:
        if lock.get('editing_policy') != 'joint_av_v1':
            return False
        # material_lock.json is written before joint_dialogue.prepare; apply the same
        # deterministic role normalisation prepare applied before checking (GLM review).
        omit_unfilmed_silent_roles(lock)
        settle_offscreen_roles(lock)
        validate_script(read(target/'dialogue_script.json'), lock)
        claimed = []
        shot_sources = {}
        introduced = set()
        for scene in lock['scenes']:
            for shot in scene.get('coverage_shots', []):
                source = tuple(shot[k] for k in ('source_sha256', 'source_start_s', 'source_end_s'))
                if shot_sources.setdefault(shot['slot_id'], source) != source:
                    return False
            bindings = scene['role_bindings']
            # Same rule as joint_dialogue.prepare: only first on-screen speakers need speech slots.
            spoken = {r['role'] for r in scene['reference_lines'] if r.get('on_screen', True)}
            required, introduced = spoken - introduced, introduced | spoken
            available = {s.get('role') for s in scene.get('speech_slots', [])}
            bound = {b['role_id'] for b in bindings.values() if not b.get('voice_only')}
            visible = {r for shot in scene.get('coverage_shots', []) for r in shot['visible']}
            if (set(scene['roles']) - set(bindings) or required - available or bound - visible or
                    not scene.get('coverage_shots') or scene.get('unverified_cast')):
                return False
            for role, binding in bindings.items():
                if binding.get('voice_only'):
                    continue
                if any(same_actor(binding, used) != (role == other) for other, used in claimed):
                    return False
                claimed.append((role, binding))
    except (ValueError, KeyError, TypeError):
        return False
    return True


def matching_release(attempt, spec):
    command = attempt.get('command', [])
    return next((release for release in [spec, *spec.get('compatible_generators', [])]
                 if command and command[0] == release['server'] and attempt.get('code_sha256') in
                 {release['library_sha256'], release.get('legacy_library_sha256', release['library_sha256'])}), None)


def compatible_generation(attempt, spec, reference_sha):
    command = attempt.get('command', [])
    release = matching_release(attempt, spec)
    return (release is not None and '--asr' in command and '--creative-audio' in command and
            all(option(command, k) == v for k, v in spec['options'].items()) and
            artifact(attempt, reference_sha) is not None and
            (release is spec or current_contract(attempt)))


def last_resort_candidate(key, reference, target, failed, error, generation, args):
    """Generation ended without a usable video: reuse a registered original or make an L4 reel.

    Not for a requested stop or a proven content-filter refusal (replace that reference)."""
    from omni_story.library import emergency_reel, server_jobs
    from omni_story.library.pipeline import _content_rejected
    if failed.get('outcome') == 'provider_rejected' or (target.is_dir() and _content_rejected(error, target)):
        return None
    try:
        if target.is_dir() and server_jobs.status(target).get('stop_requested'):
            return None
    except Exception:  # noqa: BLE001 - no supervisor record
        pass
    marker = 'same_inputs_already_registered_use_original_output:'
    if marker in error:
        original = error.split(marker, 1)[1].strip()
        found = artifact(dict(output=original), key)
        if found:
            return dict(candidate=found, mode='复用注册表原成片', output=original)
    if target.is_dir() and emergency_reel.delivered_result(target):
        found = artifact(dict(output=str(target)), key)
        if found:
            return dict(candidate=found, mode='复用任务内已交付成片', output=str(target))
    number = 1
    while (generation/f'last_resort_{number}').exists():
        number += 1
    out = generation/f'last_resort_{number}'
    try:
        from omni_story.library.material_profile import default_path, load
        selected = getattr(args, 'material_profile', None) or default_path(args.home)
        profile, binding = load(selected) if selected else (None, None)
        emergency_reel.produce(reference, profile['library'] if profile else args.home/'shared/data/videos', out, error=error,
            **({'material_profile': binding['path']} if binding else {}))
    except Exception as exc:  # noqa: BLE001 - physical limits: report generation_failed
        write_json(out/'emergency_l4_failure.json', dict(route_error=error[:2000], reel_error=f'{type(exc).__name__}:{exc}'[:2000]))
        return None
    found = artifact(dict(output=str(out)), key)
    return dict(candidate=found, mode='批次保底片L4', output=str(out)) if found else None


def reusable_evaluation(folder, inputs, evaluator):
    data = read(Path(folder)/'evaluation.json')
    media = [data.get('media', {}).get(k, {}) for k in ('reference', 'candidate')] + [data.get('reel', {})]
    media_ok = all((Path(folder)/'media'/name).is_file() and sha256_file(Path(folder)/'media'/name) == receipt.get('sha256')
                   for name, receipt in zip(('reference.mp4', 'candidate.mp4', 'comparison.mp4'), media))
    return (media_ok and data.get('status') == 'completed' and data.get('policy') == POLICY and
            {k: v.get('sha256') for k, v in data.get('inputs', {}).items()} == inputs and
            data.get('model') == evaluator['model'] and data.get('source_code') == evaluator['source_code'] and
            read(Path(folder)/'manifest.json').get('spec', {}).get('asr_model_dir') == evaluator['asr_model_dir'] and
            (Path(folder)/'report.html').is_file() and all((Path(folder)/'media'/name).is_file()
                for name in ('reference.mp4', 'candidate.mp4', 'comparison.mp4')))


def evaluation_audit(result):
    """Flag measured differences and rubric tensions without rewriting model grades."""
    reference = result['durations']['reference']
    candidate = result['durations']['candidate']
    change = candidate/reference-1
    grades = {s['dimension']: s['score'] for s in result['comparison']['adaptation_scores']}
    flags = []
    if result.get('score_review_dimensions'):
        flags.append(dict(code='issue_claims_need_review', description='部分问题指控未获事实复核支持，相关原始等级待复核：' + '、'.join(REFERENCE_DIMENSIONS[d] for d in result['score_review_dimensions'])))
    if result.get('judge_variation'):
        flags.append(dict(code='judge_reply_variation', description='比较阶段的结构修正回复在部分维度相差至少2级，判断稳定性需复核。'))
    if abs(change) > AUDIT_POLICY['duration_change_review_fraction']:
        description = f'实测时长 {reference:.2f} → {candidate:.2f} 秒（{change:+.1%}）；需复看停顿与时间结构是否仍等效。'
        if grades.get('editing', grades.get('timing')) == 5:
            description += '模型却给时间/剪辑维度满分，满分理由尤其需要复核。'
        flags.append(dict(code='duration_change_review', description=description,
                          model_timing_score=grades.get('editing', grades.get('timing'))))
    requirements = {r['id']: r for r in result['observations']['reference']['requirements']}
    for issue in result['comparison']['issues']:
        ids = issue['requirement_ids']
        if issue['severity'] == 'major' and ids and all(requirements[i]['importance'] == 'supporting' for i in ids):
            flags.append(dict(code='supporting_requirement_major_review', issue_id=issue['id'],
                description=f'问题 {issue["id"]} 被标为 major，但只关联 supporting 要求；严重程度需复核。'))
    return dict(policy=AUDIT_POLICY, duration_change_fraction=change, review_flags=flags)


def render_batch(batch, output):
    names = {'completed': '评测完成', 'generating': '生成中', 'evaluating': '评测中',
             'completed_with_review_notes': '已出片并评测，内容门禁未过，待修订',
             'generation_failed': '生成失败', 'evaluation_failed': '评测失败', 'pending': '等待'}
    rows, cards = [], []
    for row in batch['cases'].values():
        label = row['label']; state = names.get(row['status'], row['status'])
        result = read(output/row.get('evaluation', 'none')/'evaluation.json')
        if row['status'] in ('completed', 'completed_with_review_notes'):
            row['audit'] = evaluation_audit(result)
            c = result['counts']; coverage = f"{c['critical_met']}/{c['critical_total']}（未知 {c['critical_unknown']}）"
            duration = f"{result['durations']['reference']:.2f} → {result['durations']['candidate']:.2f} 秒"
            grades = ' · '.join(f"{REFERENCE_DIMENSIONS[s['dimension']]} {(str(s['score']) + ('（待复核）' if s['dimension'] in result.get('score_review_dimensions', []) else '')) if s['score'] is not None else '?'}"
                                for s in result['comparison']['adaptation_scores'])
            detail = result['comparison']['summary']
            link = row['evaluation']+'/report.html'
            issues = ''.join('<li>'+escape(x['severity']+'：'+x['description'])+'</li>' for x in result['comparison']['issues'])
            flags = ''.join('<li>'+escape(x['description'])+'</li>' for x in row['audit']['review_flags'])
            audit = '<aside class="audit"><strong>自动复核提示（不自动改分）</strong><ul>'+flags+'</ul></aside>' if flags else ''
            cards.append(f'<section><h2>{escape(label)}</h2><p>{escape(duration)} · 关键要求 {escape(coverage)}</p>'
                         f'<p>{escape(grades)}</p>{audit}<p>{escape(detail)}</p><ul>{issues}</ul>'
                         f'<a href="{escape(link)}">打开参考与新片对照报告</a></section>')
        else:
            row.pop('audit', None)
            coverage = duration = '—'; link = ''; detail = row.get('error', '')
            if detail:
                cards.append(f'<section><h2>{escape(label)} · {escape(state)}</h2><p>{escape(str(detail))}</p></section>')
        rows.append([label, state, row.get('generation_mode', '—'), duration, coverage, link, detail])
    counts = Counter(r['status'] for r in batch['cases'].values())
    batch['summary'] = dict(total=len(rows), completed=counts['completed']+counts['completed_with_review_notes'],
        with_review_notes=counts['completed_with_review_notes'], statuses=dict(counts),
        degraded=sum((r.get('fallback_level') or 0) > 0 for r in batch['cases'].values()),
        review_flags=sum(len(r.get('audit', {}).get('review_flags', [])) for r in batch['cases'].values()))
    batch['updated_at'] = datetime.now(timezone.utc).isoformat()
    write_json(output/'batch.json', batch)
    e = lambda x: escape(str(x), quote=True)
    head = '<tr><th>模板</th><th>状态</th><th>生成方式</th><th>参考 → 新片</th><th>关键要求满足</th><th>报告</th></tr>'
    body = ''.join('<tr>'+''.join('<td>'+e(x)+'</td>' for x in r[:5])+'<td>'+
                   (f'<a href="{e(r[5])}">查看</a>' if r[5] else '—')+'</td></tr>' for r in rows)
    html = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>自动生成与参考对照评测</title><style>body{font:16px/1.7 system-ui;background:#f3f5f7;color:#17212b;margin:0}'
            'main{max-width:1200px;padding:28px;margin:auto}section{padding:24px;background:white;margin:20px 0;border-radius:12px}'
            'table{width:100%;border-collapse:collapse}td,th{padding:12px;text-align:left;border-bottom:1px solid #ddd}'
            'a{color:#245b9b}.scroll{overflow:auto}.audit{background:#fff7df;padding:12px;border-radius:8px}small{color:#566}h1{font-size:28px}</style><main>'
            '<h1>自动生成与参考对照评测</h1><p>同一生产路线 · 同一评测配置 · 每条参考独立提取要求</p>'
            f'<p>流程完成 {batch["summary"]["completed"]}/{len(rows)}，其中内容门禁未过 {counts["completed_with_review_notes"]} 条。模型等级尚未人工校准；完成不代表质量验收通过。</p>'
            '<div class="scroll"><table>'+head+body+'</table></div>'+''.join(cards)+
            '<section><h2>运行记录</h2><p>创作：GLM-5.3-flash；独立评测：Qwen3.8-Omni-Flash。'
            '没有按模板设置参数，没有根据低分自动重写视频。素材不足是否导致差距，尚未独立核验。</p>'
            '<p>所有模板统一检查实测时长变化和模型评分依据。时长变化超过 25% 只是待校准的复核提示，不是质量合格线；原始评分与模型回复完整保留。</p>'
            '<a href="batch.json">版本、哈希、失败与续跑记录</a></section></main></html>')
    (output/'report.html').write_text(html)
    lines = ['# 自动生成与参考对照评测', '', '模型自动初评，完成不代表质量验收通过。', '',
             '| 模板 | 状态 | 生成方式 | 参考 → 新片 | 关键要求满足 | 报告 |', '| --- | --- | --- | --- | --- | --- |']
    lines += ['| '+' | '.join(str(x).replace('|', '\\|') for x in r[:5])+
              (' | [查看]('+r[5]+') |' if r[5] else ' | — |') for r in rows]
    for r, case in zip(rows, batch['cases'].values()):
        lines += ['', '## '+r[0], '', r[6]]
        lines += ['', *['- 自动复核提示：'+f['description'] for f in case.get('audit', {}).get('review_flags', [])]]
    lines += ['', '时长变化超过 25% 为全批次统一、待校准的复核提示，不是质量合格线。原始模型评分未修改。']
    (output/'report.md').write_text('\n'.join(lines)+'\n')


def run(args):
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    with file_lock(output/'.batch.lock'):
        return run_locked(args)


def run_locked(args):
    output = args.output
    local_source = Path(pair.__file__).resolve().parents[1]
    deployed_source = args.generation_source/'src/omni_story'
    sources = source_hashes(deployed_source)
    if source_hashes(local_source) != sources:
        raise ValueError('generator_source_differs_from_deployed_release')
    if str(args.generation_source) not in args.server.read_text():
        raise ValueError('server_wrapper_does_not_bind_generation_source')
    api = QwenAPI(); api.cfg['reference_fps_requested'] = args.fps
    evaluator = dict(model=api.cfg, asr_model_dir=str(args.asr_model_dir),
                     source_code={p.name: sha256_file(p) for p in Path(pair.__file__).parent.glob('*.py')})
    spec = dict(generation_release(args.server, args.generation_source), options=generation_options(args),
                compatible_generators=[generation_release(server, source) for server, source in args.compatible_generation])
    # Versions are explicit. Changing code invalidates only affected outputs; history remains intact.
    generation_id = json_sha(spec)[:16]; evaluation_id = json_sha(evaluator)[:16]
    references = {sha256_file(p): p for p in args.references}
    batch = read(output/'batch.json') or dict(created_at=datetime.now(timezone.utc).isoformat(), cases={}, history=[])
    if batch['cases'] and not set(batch['cases']) <= set(references):
        raise ValueError('reference_set_changed_use_new_batch_output')
    for field, identity, value in [('generation', generation_id, spec), ('evaluator', evaluation_id, evaluator)]:
        versions = batch.setdefault(field+'_versions', {})
        old_id = batch.get('generation_id' if field == 'generation' else 'evaluation_id')
        if old_id and field in batch:
            versions.setdefault(old_id, batch[field])
        versions[identity] = value
    batch.update(generation=spec, evaluator=evaluator, generation_id=generation_id, evaluation_id=evaluation_id,
                 orchestrator_sha256=sha256_file(Path(__file__)))
    for key, reference in references.items():
        label = f'{reference.parent.name}/{reference.name}'
        row = batch['cases'].setdefault(key, dict(label=label, reference=str(reference), status='pending'))
        if row.get('generation_id') != generation_id and row.get('generation_id'):
            batch['history'].append(dict(reference_sha256=key, previous=dict(row)))
            row.clear(); row.update(label=label, reference=str(reference), status='pending')
        row['generation_id'] = generation_id
    render_batch(batch, output)
    journals = []
    # A previous version may have archived an unready delivery before trying to
    # regenerate it. Its verified asset is still a candidate, not a lost result.
    for path in [*args.reuse_batch, *sorted((output/'generation').glob('*/history/batch_*.json'))]:
        for sha, row in read(path).get('references', {}).items():
            for attempt in reversed(row.get('attempts', [])):
                journals.append((sha, attempt, str(path)))
    for key, reference in references.items():
        row = batch['cases'][key]
        generation = output/'generation'/key[:16]
        own = read(generation/'batch.json').get('references', {}).get(key, {}).get('attempts', [])
        candidates = [(key, a, str(generation/'batch.json')) for a in reversed(own)] + journals
        chosen = next(((a, p) for sha, a, p in candidates if sha == key and compatible_generation(a, spec, key)), None)
        try:
            if source_hashes(deployed_source) != sources or sha256_file(args.server) != spec['server_sha256']:
                raise ValueError('generation_release_changed_during_batch')
            revise = (chosen and getattr(args, 'revise_unready', False) and
                      artifact(chosen[0], key).get('content_delivery_ready') is False and
                      matching_release(chosen[0], spec) is not spec)
            if chosen and not revise:
                attempt, journal = chosen
                release = matching_release(attempt, spec)
                mode = (('本批次生成（新版校验）' if journal == str(generation/'batch.json') else '复用最新路线（新版校验）')
                        if release is not spec else row.get('generation_mode', '复用同版成片'))
                row.update(candidate=artifact(attempt, key), generation_mode=mode,
                           generation_receipt=journal)
            else:
                if getattr(args, 'evaluation_only', False):
                    row.update(status='generation_failed', error='evaluation_only_requires_verified_existing_video')
                    render_batch(batch, output)
                    continue
                # scene_batch skips delivered outputs regardless of version. Archive stale successes
                # before invoking it; retain failed attempts so its normal --resume-from path applies.
                if own and artifact(own[-1], key):
                    archive = generation/'history'/f'batch_{json_sha(read(generation/"batch.json"))[:16]}.json'
                    archive.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(generation/'batch.json'), archive)
                row.update(status='generating', generation_mode='最新版本重新生成')
                render_batch(batch, output)
                command = [sys.executable, str(Path(__file__).with_name('scene_batch.py')), '--home', str(args.home),
                           '--server', str(args.server), '--output', str(generation), str(reference)]
                if getattr(args, 'material_profile', None):
                    command += ['--material-profile', str(args.material_profile)]
                if revise:
                    command += ['--revision-of', chosen[0]['output'], '--revision-reason',
                                'generator_contract_revision:' + spec['source_sha256']]
                with (output/'generation.log').open('a') as stream:
                    subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT)
                attempts = read(generation/'batch.json').get('references', {}).get(key, {}).get('attempts', [])
                attempt = attempts[-1] if attempts else {}
                if not compatible_generation(attempt, spec, key):
                    target = Path(attempt.get('output', '/nonexistent'))
                    failed = read(generation/'batch.json').get('references', {}).get(key, {})
                    error = (failed.get('error') or read(target/'run_outcome.json').get('error') or
                             failed.get('outcome', 'generation_did_not_deliver'))
                    rescued = last_resort_candidate(key, reference, target, failed, str(error), generation, args)
                    if rescued is None:
                        row.update(status='generation_failed', error=error,
                                   generation_receipt=str(generation/'batch.json'), generation_output=str(target))
                        render_batch(batch, output); continue
                    row.update(candidate=rescued['candidate'], generation_mode=rescued['mode'], generation_error=str(error)[:400],
                               generation_receipt=str(generation/'batch.json'), generation_output=rescued['output'])
                    attempt = None
                else:
                    row.update(candidate=artifact(attempt, key), generation_receipt=str(generation/'batch.json'))
            row['generation_provenance'] = (dict(creation='batch_last_resort', mode=row['generation_mode'],
                validated_against=spec['source_sha256']) if attempt is None else dict(creation_server=attempt['command'][0],
                creation_code_sha256=attempt['code_sha256'], validated_against=spec['source_sha256'],
                validation='exact_release' if matching_release(attempt, spec) is spec else 'declared_compatible_release_and_current_contract'))
            candidate = Path(row['candidate']['path'])
            inputs = dict(reference=key, candidate=row['candidate']['sha256'])
            eval_dir = output/'cases'/key[:16]/f'evaluation_{evaluation_id}_{inputs["candidate"][:12]}'
            row.update(evaluation=str(eval_dir.relative_to(output)), status='evaluating')
            row.pop('error', None)
            render_batch(batch, output)
            if reusable_evaluation(eval_dir, inputs, evaluator):
                row.setdefault('evaluation_mode', '断点复用')
            else:
                reuse = next((p for p in args.reuse_evaluation if reusable_evaluation(p, inputs, evaluator)), None)
                if reuse and not eval_dir.exists():
                    shutil.copytree(reuse, eval_dir)
                    row.update(evaluation_mode='复用相同输入与评测版本', evaluation_reused_from=str(reuse))
                else:
                    command = [sys.executable, '-m', 'omni_story.evaluation.reference_pair', '--reference', str(reference),
                               '--candidate', str(candidate), '--output', str(eval_dir), '--asr-model-dir', str(args.asr_model_dir),
                               '--fps', str(args.fps)]
                    with (output/'evaluation.log').open('a') as stream:
                        completed = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT)
                    row['evaluation_mode'] = '自动评测'
                    if completed.returncode or not reusable_evaluation(eval_dir, inputs, evaluator):
                        row.update(status='evaluation_failed', error='evaluation_failed_see_evaluation_log')
                        render_batch(batch, output); continue
            row.update(status='completed_with_review_notes' if row['candidate'].get('content_delivery_ready') is False
                       else 'completed', evaluation_id=evaluation_id,
                       content_delivery_ready=row['candidate'].get('content_delivery_ready'),
                       fallback_level=row['candidate'].get('fallback_level', 0))
        except Exception as exc:
            row.update(status='evaluation_failed' if row.get('status') == 'evaluating' else 'generation_failed',
                       error=f'{type(exc).__name__}:{str(exc)[:400]}')
        render_batch(batch, output)
        print(json.dumps(dict(reference=row['label'], status=row['status'], error=row.get('error')), ensure_ascii=False), flush=True)
    render_batch(batch, output)
    print(json.dumps(batch['summary'], ensure_ascii=False), flush=True)
    return int(batch['summary']['completed'] != len(references))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('home', 'server', 'generation-source', 'output', 'asr-model-dir', 'credentials-file'):
        parser.add_argument('--'+flag, type=Path, required=True)
    parser.add_argument('--reuse-batch', type=Path, action='append', default=[])
    parser.add_argument('--reuse-evaluation', type=Path, action='append', default=[])
    parser.add_argument('--compatible-generation', type=Path, nargs=2, action='append', default=[],
                        metavar=('SERVER', 'SOURCE'), help='Explicit compatible production baseline; successful artifacts must pass current contracts.')
    parser.add_argument('--evaluation-only', action='store_true', help='Evaluate verified existing videos; never start generation.')
    parser.add_argument('--material-profile', type=Path)
    parser.add_argument('--revise-unready', action='store_true', help='Once per newer generator, explicitly revise verified unready compatible deliveries.')
    parser.add_argument('--fps', type=float, default=4.0)
    parser.add_argument('references', type=Path, nargs='+')
    args = parser.parse_args()
    if not 0 < args.fps <= 10:
        parser.error('--fps must be within (0, 10]')
    for name in ('home', 'server', 'generation_source', 'output', 'asr_model_dir', 'credentials_file'):
        setattr(args, name, getattr(args, name).resolve())
    args.references = [p.resolve(strict=True) for p in args.references]
    if not os.environ.get('DASHSCOPE_API_KEY'):
        os.environ['DASHSCOPE_API_KEY'] = read(args.credentials_file)['DASHSCOPE_API_KEY']
    return run(args)


if __name__ == '__main__':
    raise SystemExit(main())
