#!/usr/bin/env python3
"""只读回放故障注入；PYTHONPATH=src python scripts/fault_check.py ROOT --json OUT。"""
from __future__ import annotations

import argparse
import ast
import builtins
from collections import Counter, defaultdict
from copy import deepcopy
from functools import lru_cache
import hashlib
import inspect
import json
import math
from pathlib import Path
import random
import re
import sys
import time
import traceback
from unittest.mock import patch

sys.dont_write_bytecode = True

from omni_story.library import contracts, pipeline, protocol_feedback, replay_check


REPO = Path(__file__).absolute().parent.parent
CLASSES = {
    'E1': '等价数值被拒绝', 'E2': '字典顺序影响结果',
    'M1_crash': '畸形回复引发不可修复异常', 'M1_accept': '必需字段畸形却被接受',
    'M2_json': '失败记录无法严格 JSON 序列化', 'M2_crash': '反馈诊断自身崩溃',
    'M2_empty': '反馈为空', 'M2_many': '单次变异产生超过 8 条反馈',
    'M2_expected': '反馈给出可疑空 ID 集合', 'M2_path': '反馈指向不存在的字段',
    'M2_miss': '反馈未指向实际问题', 'R1': '原稿数值回显触发保护规则',
}


def short_file(filename):
    try:
        return str(Path(filename).relative_to(REPO))
    except ValueError:
        return str(filename)


def error_info(error):
    frames = [dict(file=short_file(f.filename), line=f.lineno, function=f.name,
                   code=f.line) for f in traceback.extract_tb(error.__traceback__)]
    own = [f for f in frames if f['file'].startswith('src/')]
    last = (own or frames or [dict(file='无 traceback', line=0)])[-1]
    result = dict(exception=type(error).__name__, error=str(error),
                  location=f"{last['file']}:{last['line']}", traceback=frames)
    for name in ('violation', 'diagnostics'):
        if getattr(error, name, None) is not None:
            result[name] = contracts.json_safe(getattr(error, name))
    return result


def repair_boundary():
    """Read the actual handler, rather than guessing which exceptions are repairable."""
    source = Path(inspect.getsourcefile(pipeline))
    tree = ast.parse(source.read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and any(
                isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
                and child.func.id == 'diagnose' for child in ast.walk(node)):
            names = [item.id for item in node.type.elts]
            return tuple(getattr(builtins, name) for name in names), dict(
                types=names, location=f'{short_file(str(source))}:{node.lineno}')
    raise RuntimeError('找不到 pipeline 的格式修复异常边界')


def path_text(tokens):
    return '$' + ''.join(f'[{key}]' if isinstance(key, int) else '.' + key for key in tokens)


def path_tokens(path):
    if not isinstance(path, str):
        return None
    # Some existing numeric_error paths omit the leading $. .
    path = path if path.startswith('$') else '$.' + path
    matches = list(re.finditer(r'\.([A-Za-z_][\w-]*)|\[(\d+)\]', path[1:]))
    if ''.join(m.group(0) for m in matches) != path[1:]:
        return None
    return tuple(m.group(1) if m.group(1) is not None else int(m.group(2)) for m in matches)


def exists(value, tokens):
    if tokens is None:
        return False
    try:
        for key in tokens:
            value = value[key]
        return True
    except (KeyError, IndexError, TypeError):
        return False


def walk(value, tokens=()):
    if isinstance(value, dict):
        for key, child in value.items():
            yield tokens + (key,), child
            yield from walk(child, tokens + (key,))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield tokens + (index,), child
            yield from walk(child, tokens + (index,))


def mutate(value, tokens, operation, replacement=None):
    result = deepcopy(value)
    parent = result
    for key in tokens[:-1]:
        parent = parent[key]
    if operation == 'delete':
        del parent[tokens[-1]]
    else:
        parent[tokens[-1]] = deepcopy(replacement)
    return result


def echoes(value):
    for tokens, child in walk(value):
        if type(child) is float and math.isfinite(child):
            rounded = round(child, 6)
            if rounded != child:
                yield tokens, 'round(x,6)', rounded
            shifted = child + 1e-12
            if shifted != child:
                yield tokens, 'x+1e-12', shifted
            if child.is_integer():
                yield tokens, 'float→int', int(child)
        elif type(child) is int and isinstance(tokens[-1], str) and tokens[-1].endswith('_s'):
            yield tokens, '_s:int→float', float(child)


def echo_all(value, mode):
    if isinstance(value, dict):
        return {key: (float(child) if mode == 'types' and key.endswith('_s') and type(child) is int
                      else echo_all(child, mode)) for key, child in value.items()}
    if isinstance(value, list):
        return [echo_all(child, mode) for child in value]
    if type(value) is float and math.isfinite(value):
        if mode == 'round':
            return round(value, 6)
        if mode == 'epsilon':
            return value + 1e-12
        if mode == 'types' and value.is_integer():
            return int(value)
    return value


def shuffled(value, rng):
    if isinstance(value, dict):
        keys = list(value)
        rng.shuffle(keys)
        if len(keys) > 1 and keys == list(value):
            keys = keys[1:] + keys[:1]
        return {key: shuffled(value[key], rng) for key in keys}
    if isinstance(value, list):
        return [shuffled(child, rng) for child in value]
    return value


def malformed(value, seed, cap=40):
    """All top-level keys; deterministic stratified nested-key sampling."""
    groups = defaultdict(list)
    top = []
    for tokens, child in walk(value):
        # Mutate keys, and list elements as an additional malformed-row check.
        operations = [('delete', None)] if isinstance(tokens[-1], str) else []
        if isinstance(child, list):
            operations.append(('list→{}', {}))
        elif isinstance(child, dict):
            operations.append(('dict→[]', []))
        elif isinstance(child, str):
            operations.append(('str→0', 0))
        elif type(child) in (int, float):
            operations.extend([('number→x', 'x'), ('number→NaN', math.nan),
                               ('number→Infinity', math.inf)])
        for operation, replacement in operations:
            item = (tokens, operation, replacement)
            if len(tokens) == 1:
                top.append(item)
            else:
                # Spread across top-level subtrees and operation types.
                groups[(tokens[0], operation)].append(item)
    rng = random.Random(seed)
    for items in groups.values():
        rng.shuffle(items)
    keys = sorted(groups)
    rng.shuffle(keys)
    result = top[:]
    limit = max(cap, len(top))
    while len(result) < limit and keys:
        for key in keys[:]:
            result.append(groups[key].pop())
            if not groups[key]:
                keys.remove(key)
            if len(result) == limit:
                break
    return result, len(top), sum(len(items) for items in groups.values())


def protocol_context(task, state, call, request, validate):
    """Only restore feedback metadata lost by replay_check's closure extraction.

    This mirrors pipeline.py's two protocol_context assignments, not validators.
    No probe_media fallback is possible in the media-free replay archive.
    """
    context = dict(getattr(validate, 'protocol_context', {}))
    stage = re.sub(r'_repair\d*$', '', call['name'])
    if stage == 'reference':
        ref = replay_check.read(task / 'reference_catalog/inventory.json')['sources'][0]
        context.update(stage='reference', reference_sha=ref['sha256'],
                       duration_s=ref['duration_s'], require_value_fields=True)
    elif re.fullmatch(r'plan_\d+', stage):
        original = replay_check.prompt_context(request, 'reference', 'catalog', 'watched_windows')
        context.update(stage='plan', windows=original['watched_windows'], reference=original['reference'],
                       require_audio_draft=state['input_lock']['configuration'].get('creative_audio') is True)
    scope = request.get('observation_scope', {})
    start, end = scope.get('source_start_s'), scope.get('source_end_s')
    if request.get('tool') != 'analyze_image' and all(contracts.finite_number(x) for x in (start, end)):
        context['media_duration_s'] = end - start
    return context


def required_reason(stage, tokens, operation, value):
    """Conservative, reviewed required-field rules; optional deletions are not bugs.

    Retain every accepted mutation separately so the human audit is reproducible.
    """
    normalized = re.sub(r'\[\d+\]', '[]', path_text(tokens))
    key = tokens[-1]
    if stage.startswith('rough_audio_timing_'):
        if len(tokens) == 1 and key == 'audio_draft':
            return '回复必须包含 audio_draft 数组'
        if normalized == '$.audio_draft[].start_s' or normalized == '$.audio_draft[].end_s':
            return 'story_context 消费每句有限的 start_s/end_s'
    if stage.startswith('chain_e2e_v1_fine_'):
        if any(word in stage for word in ('_plan', '_revise')):
            if normalized in {'$.segments', '$.limitations', '$.audio_draft',
                              '$.segments[].segment_id', '$.segments[].source_id',
                              '$.segments[].window_id', '$.segments[].source_in_s',
                              '$.segments[].source_out_s', '$.segments[].contribution_to',
                              '$.segments[].reason'}:
                return '精剪计划/后续编译必需字段'
            if normalized.startswith('$.audio_draft[]') and key in {'id', 'text', 'voice', 'segment_ids'}:
                return '创意语音草稿必需字段'
            if operation != 'delete' and normalized in {
                    '$.slots', '$.slots[]', '$.slots[].slot_id', '$.slots[].intended_takeaway',
                    '$.slots[].segment_ids', '$.slots[].segment_ids[]', '$.slots[].coverage',
                    '$.slots[].coverage.visual_goal', '$.slots[].coverage.audio_goal',
                    '$.slots[].coverage.gaps'}:
                return 'slots 可省略，但提供时其数组/覆盖字段不能使用错误类型'
        if '_observe' in stage and normalized in {'$.story', '$.facts', '$.limitations',
                                                  '$.story[].id', '$.story[].contribution'}:
            return '观察提示及校验协议必需字段'
        if any(word in stage for word in ('_inspect_', '_detail_')):
            if normalized in {'$.facts', '$.uncertainties', '$.facts[].description', '$.facts[].basis',
                              '$.facts[].start_s', '$.facts[].end_s', '$.facts[].time_s'}:
                return '事实必须有描述、依据和有效时间'
        if '_review' in stage and normalized == '$.revision_reason' and value.get('next_action') == 'revise':
            from omni_story.library.story_context import core_missing
            if not core_missing(value):
                return 'next_action=revise 时 revision_reason 必须是非空字符串；否则在修复区外终止'
    return None


def discarded(view, tokens):
    """An independently discarded zero-length annotation is no longer required."""
    for row in view.get('normalization', []) if isinstance(view, dict) else []:
        if isinstance(row, dict) and row.get('dropped'):
            path = path_tokens(row.get('path'))
            if path and tokens[:len(path)] == path:
                return True
    return False


def acceptance_site(stage, reason):
    from omni_story.library import story_finecut, visual_story_trial
    if 'revision_reason' in reason:
        return source_site('require(isinstance(reason', story_finecut)
    if 'slots' in reason:
        return source_site('def plan_check(self', story_finecut)
    if '_inspect_' in stage or '_detail_' in stage:
        return source_site('def facts_check(value, start', story_finecut)
    return source_site('def observe_check' if '_observe' in stage else 'def plan_check', visual_story_trial)


@lru_cache(maxsize=1)
def revision_guard():
    """Extract the actual post-validation guard, without running any media workflow."""
    from omni_story.library import story_finecut
    filename = inspect.getsourcefile(story_finecut)
    tree = ast.parse(Path(filename).read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if isinstance(node, ast.While) and 'review' in ast.unparse(node.test) and 'core_missing' in ast.unparse(node.test):
            fragment = ast.Module(body=node.body[:2], type_ignores=[])
            return compile(fragment, filename, 'exec'), story_finecut
    raise RuntimeError('无法提取 review 的原始下游 guard')


def downstream_revision_error(value):
    code, module = revision_guard()
    scope = dict(vars(module), review={'target': deepcopy(value)})
    try:
        exec(code, scope)
    except Exception as error:
        return error_info(error)
    return None


def source_site(fragment, module=protocol_feedback):
    path = Path(inspect.getsourcefile(module))
    for line, text in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        if fragment in text:
            return f'{short_file(str(path))}:{line}'
    return f'{short_file(str(path))}:1'


class Audit:
    def __init__(self, root):
        self.root = root
        self.repair_types, self.boundary = repair_boundary()
        self.findings = []
        self.accepted = []
        self.baselines = []
        self.counts = Counter()
        self.stages = defaultdict(Counter)
        self.raw_stages = defaultdict(Counter)
        self.tasks = []
        self.r1 = []

    def finding(self, category, row, operation, tokens, failure=None, **extra):
        item = dict(category=category, task=row['task'], stage=row['stage'], call_id=row['call_id'],
                    perturbation=operation, path=path_text(tokens) if tokens is not None else None,
                    outcome=CLASSES[category])
        if failure is not None:
            item.update(error_info(failure))
        item.update(extra)
        item.setdefault('location', '无异常（验证器接受）')
        item.setdefault('error', '')
        self.findings.append(item)

    def validate(self, validate, value):
        # Validators can normalize input in place: each attempt gets a fresh copy.
        self.counts['validator_attempts'] += 1
        self.last_view = deepcopy(value)
        try:
            validate(self.last_view)
        except Exception as error:
            return error
        return None

    def feedback(self, row, validate, value, error, context, operation, tokens=None, saved=None):
        self.counts['M2_rejections'] += 1
        violation = saved.get('violation') if saved is not None else getattr(error, 'violation', None)
        diagnostics = saved.get('validation_diagnostics') if saved is not None else getattr(error, 'diagnostics', None)
        for field, data in [('violation', violation), ('diagnostics', diagnostics)]:
            try:
                json.dumps(contracts.json_safe(data), allow_nan=False)
            except Exception as failure:
                self.finding('M2_json', row, operation, tokens, failure, record_field=field)
        safe_violation = contracts.json_safe(violation)
        current = dict(context, validation_error=str(error), violation=safe_violation)
        try:
            items = protocol_feedback.diagnose(row['stage'], deepcopy(value), current)
            # The repair request must also be strict JSON, beyond the requested failure record checks.
            json.dumps(items, allow_nan=False)
        except Exception as failure:
            self.finding('M2_crash', row, operation, tokens, failure, validation_error=str(error))
            return
        self.counts['M2_feedback_items'] += len(items)
        common = dict(feedback=items, validation_error=str(error),
                      repairable=isinstance(error, self.repair_types))
        times_only = bool(items) and all('以实际媒体时长为准' in item.get('hint', '') for item in items)
        common['generic_times_only'] = times_only
        validation_location = error_info(error)['location']
        if saved is not None:
            validation_location = saved['_location']
        common['validation_location'] = validation_location
        if not items:
            self.finding('M2_empty', row, operation, tokens, error, **common)
        if tokens is not None and len(items) > 8:
            self.finding('M2_many', row, operation, tokens, error,
                         location=source_site('if key.endswith') if times_only else source_site('elif (kind.startswith'), **common)
        feedback_paths = [path_tokens(item.get('path')) for item in items]
        if tokens is not None and not any(
                path in {tokens, tokens[:-1]} for path in feedback_paths):
            self.finding('M2_miss', row, operation, tokens, error,
                         location=source_site('if not feedback.items:'), **common)
        phantom = []
        empty_ids = []
        for item, path in zip(items, feedback_paths):
            # Per the requested heuristic, only the actual deleted path is exempt.
            related = tokens is not None and path == tokens
            if not exists(value, path) and not related:
                phantom.append(item['path'])
            expected = item.get('expected')
            if (expected == [] or expected == {'visible_segment_ids': []}) and path and path[-1] in {
                    'speaker', 'identity_anchor_segment_id', 'segment_ids'}:
                segments = value.get('segments', []) if isinstance(value, dict) else []
                ids = [s.get('segment_id', s.get('id')) for s in segments if isinstance(s, dict)]
                # This is a suspicion heuristic, not a claim of proven speaker visibility.
                if ids and (tokens is None or not tokens or tokens[0] != 'segments'):
                    empty_ids.append(dict(path=item['path'], ids=ids, expected=expected))
        if phantom:
            diagnostic_site = source_site('feedback.add(error, value')
            if all(str(path).startswith('$') for path in phantom):
                diagnostic_site = source_site('def _reference' if context.get('stage') == 'reference' else 'def _plan')
            self.finding('M2_path', row, operation, tokens, error,
                         error='不存在或不是 JSON 路径：' + ', '.join(phantom),
                         location=diagnostic_site, phantom_paths=phantom, **common)
        if empty_ids:
            self.finding('M2_expected', row, operation, tokens, error,
                         error='回复中有 segment IDs，反馈的预期 ID 集合却为空（启发式待核）',
                         location=source_site('visible_segment_ids'), empty_ids=empty_ids, **common)

    def protected_echo(self, row, request, validate):
        stage = row['stage']
        coverage = '完整回放验证器'
        if stage.startswith('rough_audio_timing_'):
            context = replay_check.prompt_context(request, 'story_context', 'editable_line_ids')
            previous = {'audio_draft': context['story_context']['audio_draft']}
            protected = [line['id'] for line in previous['audio_draft']
                         if line['id'] not in context['editable_line_ids']]
        elif stage.startswith('chain_e2e_v1_fine_revise'):
            context = replay_check.prompt_context(request, 'reference', 'observed', 'source')
            previous = context['actual_output_feedback']['plan']
            # The validator determines affected segments using its real compile/review logic.
            protected = '由 revision_check 的实际 review 区间判定'
        elif stage.startswith('audio_plan_v'):
            from omni_story.library.visual_audio import plan_revision_check, revision_feedback
            context = replay_check.prompt_context(request, 'observed', 'actual_duration_s', 'feedback')
            feedback = revision_feedback(context['feedback'])
            if not feedback or 'previous_plan' not in feedback:
                return  # An initial audio plan has no protected previous content.
            previous = feedback['previous_plan']
            protected = [line['id'] for line in previous['lines']
                         if line['id'] not in feedback.get('editable_line_ids', [])]
            if validate is None:
                # replay_check currently has no adapter for the complete audio-plan validator.
                # Exercise the real protection guard only; do not duplicate that missing adapter.
                validate = lambda value: plan_revision_check(value, feedback)
                coverage = '仅实际 plan_revision_check 保护 guard；完整验证器无适配器'
        else:
            return
        results = []
        for mode in ('original', 'round', 'epsilon', 'types'):
            value = deepcopy(previous) if mode == 'original' else echo_all(previous, mode)
            error = self.validate(validate, value)
            self.counts['R1_attempts'] += 1
            result = dict(mode=mode, accepted=error is None)
            if error:
                result.update(error_info(error))
                # Other baseline problems are recorded separately, not blamed on echo protection.
                if re.search(r'changed|unrelated', str(error), re.I):
                    self.finding('R1', row, '原稿:' + mode, (), error)
            results.append(result)
        self.r1.append(dict(task=row['task'], stage=stage, call_id=row['call_id'],
                            protected=protected, coverage=coverage, results=results))

    def run(self):
        for ledger in sorted(self.root.rglob('library_state.json')):
            task = ledger.parent
            state = replay_check.read(ledger)
            label = str(task.relative_to(self.root))
            self.tasks.append(dict(task=label, calls=len(state.get('calls', []))))
            for call in state.get('calls', []):
                if call.get('status') != 'received':
                    continue
                row = dict(task=label, stage=call['name'], call_id=call['id'])
                stage = re.sub(r'_repair\d*$', '', call['name'])
                self.stages[stage]['received'] += 1
                self.raw_stages[call['name']]['received'] += 1
                folder = task / 'calls' / call['id']
                validate, value, context = None, None, {}
                try:
                    request = replay_check.read(folder / 'request.json')
                    validate = replay_check.validator(task, state, call, request)
                    context = protocol_context(task, state, call, request, validate)
                    raw, source = replay_check.raw_reply(folder)
                    value = contracts.parse_model_json(raw)
                    error = self.validate(validate, value)
                except Exception as failure:
                    error = failure
                baseline = dict(row, accepted=error is None)
                if error:
                    baseline.update(error_info(error))
                    self.stages[stage]['unsupported' if isinstance(error, replay_check.Unsupported) else 'rejected'] += 1
                    self.raw_stages[call['name']]['unsupported' if isinstance(error, replay_check.Unsupported) else 'rejected'] += 1
                else:
                    self.stages[stage]['ok'] += 1
                    self.raw_stages[call['name']]['ok'] += 1
                self.baselines.append(baseline)
                if validate is not None or call['name'].startswith('audio_plan_v'):
                    try:
                        self.protected_echo(row, request, validate)
                    except replay_check.Unsupported as failure:
                        self.r1.append(dict(row, unavailable=str(failure)))
                failure_path = folder / 'protocol_failure.json'
                if failure_path.is_file():
                    self.counts['historical_failure_files'] += 1
                    saved = replay_check.read(failure_path)
                    saved['_location'] = str(failure_path.relative_to(self.root)) + ':1（历史记录，无 traceback）'
                    historical = ValueError(saved['error'])
                    self.feedback(row, validate, value, historical, context, '历史失败:保存的拒绝', saved=saved)
                    if error and not isinstance(error, replay_check.Unsupported):
                        self.feedback(row, validate, value, error, context, '历史失败:当前验证器')
                if error is not None:
                    continue
                seed = int(hashlib.sha256((label + '/' + call['id']).encode()).hexdigest()[:16], 16)
                for tokens, operation, replacement in echoes(value):
                    self.counts['E1_attempts'] += 1
                    changed = mutate(value, tokens, operation, replacement)
                    failure = self.validate(validate, changed)
                    if failure:
                        self.finding('E1', row, operation, tokens, failure,
                                     before=next(child for path, child in walk(value) if path == tokens),
                                     after=replacement)
                # Also check simultaneous rounding/epsilon, which can change interval relations.
                for mode in ('round', 'epsilon', 'types'):
                    self.counts['E1_aggregate_attempts'] += 1
                    changed = echo_all(value, mode)
                    failure = self.validate(validate, changed)
                    if failure:
                        diffs = [path_text(path) for path, before in walk(value)
                                 if type(before) in (int, float) and not isinstance(before, bool)
                                 and (type(before) != type(get_at(changed, path)) or before != get_at(changed, path))]
                        self.finding('E1', row, '全部数值:' + mode, (), failure, differing_paths=diffs)
                self.counts['E2_attempts'] += 1
                failure = self.validate(validate, shuffled(value, random.Random(seed)))
                if failure:
                    self.finding('E2', row, '所有字典重排', (), failure)
                mutations, top, omitted = malformed(value, seed)
                self.counts['M1_top_level_mutations'] += top
                self.counts['M1_sampled_out'] += omitted
                for tokens, operation, replacement in mutations:
                    self.counts['M1_attempts'] += 1
                    self.counts['M1_nonfinite_attempts' if operation in {'number→NaN', 'number→Infinity'}
                                else 'M1_strict_json_attempts'] += 1
                    changed = mutate(value, tokens, operation, replacement)
                    failure = self.validate(validate, changed)
                    if failure is None:
                        ignored = discarded(self.last_view, tokens)
                        reason = None if ignored else required_reason(stage, tokens, operation, changed)
                        self.accepted.append(dict(row, path=path_text(tokens), operation=operation,
                                                  required_reason=reason, discarded_annotation=ignored))
                        if reason:
                            downstream = downstream_revision_error(changed) if '修复区外终止' in reason else None
                            self.finding('M1_accept', row, operation, tokens, error=reason,
                                         location=acceptance_site(stage, reason), traceback=[],
                                         exception=downstream['exception'] + '（下游）' if downstream else '无异常（校验接受）',
                                         required_reason=reason,
                                         downstream_error=downstream,
                                         severity='high' if '修复区外终止' in reason else 'low')
                    else:
                        self.counts['M1_rejected'] += 1
                        self.counts['M1_rejected_' + type(failure).__name__] += 1
                        if not isinstance(failure, self.repair_types):
                            self.finding('M1_crash', row, operation, tokens, failure)
                        self.feedback(row, validate, changed, failure, context, operation, tokens)
            print(f'已检查 {label}: 累计 {len(self.baselines)} 个回复、{self.counts["validator_attempts"]} 次验证',
                  file=sys.stderr, flush=True)


def get_at(value, tokens):
    for key in tokens:
        value = value[key]
    return value


def cause_for(item):
    category = item['category']
    location = item['location']
    error = item['error']
    if category in {'M2_many', 'M2_miss'} and item.get('generic_times_only'):
        if item['stage'].startswith('chain_e2e_v1_fine_inspect_'):
            return ('通用时间扫描用媒体片长检查源时间，并掩盖真正的拒绝字段', source_site('if key.endswith'),
                    'medium', '按字段时间轴使用 source_start_s/source_end_s；先输出真实 violation，再补充同域问题。')
        return ('通用时间扫描被其他时间问题占满，未定位当前变异（启发式）', source_site('if key.endswith'),
                'medium', '保持诊断与当前正常化规则一致，并始终优先报告当前拒绝字段。')
    if category in {'M2_miss', 'M2_path'}:
        if category == 'M2_path' and any(not str(p).startswith('$') for p in item.get('phantom_paths', [])):
            return ('反馈兜底将错误字符串当成字段路径', source_site('feedback.add(error, value'),
                    'medium', '输出真实 JSON 字段路径；从 violation/diagnostics 提取定位，兜底用 $。')
        return ('反馈没有定位真实变异字段', source_site('if not feedback.items:'),
                'medium', '为各验证分支提供结构化 violation，并保留实际字段路径。')
    if category == 'M2_many':
        if item['stage'].startswith('plan_'):
            return ('缺失容器产生大量派生反馈（超过 8 条启发式）', source_site('def _plan'),
                    'medium', '先报告缺失容器，抑制由同一缺失产生的级联字段错误。')
        return ('反馈扫描了不适用的计划结构', source_site('elif (kind.startswith'),
                'medium', '按当前阶段的回复 schema 扫描，并合并同一根问题的连带错误。')
    if category == 'M2_expected':
        return ('诊断缺少可见性/ID 上下文（启发式待核）', source_site('visible_segment_ids'),
                'medium', '给诊断传入与验证器一致的 segments、cast、windows 和可见性证据。')
    if category == 'M1_accept':
        return ('未完整校验：' + item['required_reason'], location,
                item['severity'], '按当前字段条件检查存在性与类型，在消费前返回带路径的 ValueError。')
    if category == 'R1':
        return ('极小 freeze_tail_s 生成新编译字段，被当成无关段落修改', location,
                'medium', '编译前恢复受保护段原值，或对 motion_frames/freeze_frames 使用有效默认值。')
    if category in {'E1', 'R1'}:
        reason = error.split(':')[0]
        return ('等价数值遭严格边界/类型规则拒绝：' + reason, location,
                'medium', '在源区间、操作上限和矩形求和处使用明确的小数容差，保护字段恢复原值。')
    if category == 'M1_crash':
        return ('校验器遗漏输入形状检查：' + item.get('exception', ''), location,
                'high', '解引用前验证字段类型，抛出带路径的 ValueError 进入既有修复。')
    if category == 'M2_crash':
        return ('诊断器未防御畸形结构：' + item.get('exception', ''), location,
                'high', '诊断器先检查对象/数组类型，确保诊断过程本身不会抛出。')
    if category == 'M2_json':
        return ('失败记录未转为严格 JSON', location, 'high', '递归清理非有限数值后再持久化。')
    return (CLASSES[category], location, 'medium', '消除字典遍历顺序对校验的影响。')


def roots(findings):
    grouped = {}
    for index, item in enumerate(findings):
        title, location, severity, fix = cause_for(item)
        key = title, location
        if key not in grouped:
            grouped[key] = dict(title=title, location=location, severity=severity, suggested_fix=fix,
                                hits=0, stages=set(), finding_indices=[], calls=set())
        row = grouped[key]
        row['hits'] += 1
        row['stages'].add(item['stage'])
        row['calls'].add((item['task'], item['call_id']))
        row['finding_indices'].append(index)
    result = []
    for row in grouped.values():
        row['stages'] = sorted(row['stages'])
        row['affected_calls'] = len(row.pop('calls'))
        result.append(row)
    return sorted(result, key=lambda row: (row['severity'] != 'high', -row['hits'], row['location']))


def cell(value):
    return str(value).replace('|', '\\|').replace('\n', '<br>')


def format_report(report):
    counts = report['counts']
    lines = ['# 离线故障注入审计（仅审计，未实施修复）', '',
             f"代码：`{report['head']}`；回放根目录：`{report['root']}`。运行耗时 {report['elapsed_s']:.2f} 秒。", '',
             '`PYTHONPATH=src python3 scripts/fault_check.py ' + report['root'] +
             ' --json /private/tmp/fault_check.json > /private/tmp/fault_check.md`', '',
             f"共 {len(report['tasks'])} 份账本，{sum(bool(t['calls']) for t in report['tasks'])} 份有调用；"
             f"已接收 {len(report['baselines'])} 个回复，当前通过 {sum(r['accepted'] for r in report['baselines'])} 个。", '',
             f"pipeline 格式修复捕获 {', '.join(report['repair_boundary']['types'])}"
             f"（{report['repair_boundary']['location']}）。其他异常不进入该修复分支。", '',
             'E1 对全部当前通过回复逐数值变异，并额外对全部数值同步变异；E2 对每层字典确定性重排。'
             'M1 保留全部顶层键的删除/类型替换/非有限数值变异，其余预算按顶层子树和操作分层确定性抽样；'
             '每回复约 40 次，顶层操作超过 40 时全部保留。含少量数组元素形状变异。', '',
             'NaN/Infinity 不是 RFC 严格 JSON；它们作为额外的 Python JSON 解析器可接收故障探针单列在明细中。'
             '其余 M1 探针均为严格 JSON 可表示值。', '',
             '所有验证器均由 replay_check.validator 重建，读取 raw_reply 并用 contracts.parse_model_json 解析；'
             '每次验证使用深拷贝。仅缓存冻结文件系统中的 Path.resolve 结果，没有替换校验逻辑。'
             '反馈元数据复原 pipeline 的 reference/plan protocol_context；媒体时长只取归档 observation_scope，未探测媒体。', '',
             '历史失败同时检查保存的错误元数据与当前仍被拒绝时的新错误；历史已修复的回复仍检查原始失败反馈。'
             '不可修复异常的诊断属于额外反事实探针，实际 pipeline 会先退出。'
             '可疑反馈属于启发式发现（包括删除容器后不存在的子字段），完整 feedback/traceback 保存在 JSON；可选字段删除的接受不会自动判成缺陷。'
             '被既有正常化规则独立丢弃的零长度记录，其内部字段变异不计为必需字段漏洞。', '',
             '重点发现：审核的 revision_reason 漏检可在修复分支外终止；微小数值回显仍会触发严格边界与保护规则；'
             '反馈存在时间轴混用、无字段定位和缺少说话者上下文等问题。'
             '本次抽样未发现验证器直接抛不可修复异常、严格 JSON 失败记录序列化失败、诊断崩溃或空反馈。', '',
             '## 实际执行计数', '', '| 项目 | 次数 |', '| --- | ---: |']
    lines.extend(f'| {key} | {value} |' for key, value in sorted(counts.items()))
    lines.extend(['', '发现分类计数（同一变异可同时触发多个反馈标记，不能相加当独立缺陷数）：', '',
                  '| 类别 | 命中 |', '| --- | ---: |'])
    lines.extend(f"| {key} | {value} |" for key, value in report['finding_counts'].items())
    lines.extend(['', '## 任务索引', '', '| 任务 | 相对目录 |', '| --- | --- |'])
    aliases = {task['task']: f'T{index:02}' for index, task in enumerate(report['tasks'], 1)}
    lines.extend(f"| {aliases[task['task']]} | {task['task']} |" for task in report['tasks'])
    for category, title in CLASSES.items():
        rows = [row for row in report['findings'] if row['category'] == category]
        lines.extend(['', f'## {category}：{title}（{len(rows)} 命中）', '',
                      '| stage | 任务 / call id | perturbation / path | outcome | error / exception | file:line | 次数 |',
                      '| --- | --- | --- | --- | --- | --- | ---: |'])
        if not rows:
            lines.append('| — | — | — | 未发现 | — | — | 0 |')
        # Every E1/E2/R1/crash case stays visible. Repetitive feedback/acceptance cases are grouped.
        grouped = defaultdict(list)
        for row in rows:
            key = (row['task'], row['stage'], row['call_id'], row['outcome'], row['error'], row['location'],
                   row['perturbation'] if category in {'E1', 'E2', 'R1', 'M1_crash', 'M2_crash'} else '',
                   row['path'] if category in {'E1', 'E2', 'R1', 'M1_crash', 'M2_crash'} else '')
            grouped[key].append(row)
        entries = list(grouped.items())
        if category == 'M1_accept':
            entries.sort(key=lambda entry: entry[1][0].get('severity') != 'high')
        limit = len(entries) if category in {'E1', 'E2', 'R1', 'M1_crash', 'M2_crash', 'M2_json'} else 20
        for key, group in entries[:limit]:
            first = group[0]
            examples = list(dict.fromkeys(row['perturbation'] + ' ' + str(row['path']) for row in group))
            perturbation = '; '.join(examples[:3]) + (f'; … 共 {len(examples)} 种（完整见 JSON）' if len(examples) > 3 else '')
            if first.get('differing_paths'):
                perturbation += '；字段 ' + ', '.join(first['differing_paths'][:3]) + ' …（JSON 含全部路径）'
            location = first['location']
            if first.get('validation_location'):
                location += '；原拒绝：' + first['validation_location']
            if first.get('downstream_error'):
                location += '；下游 raise：' + first['downstream_error']['location']
            lines.append('| ' + ' | '.join(cell(v) for v in [first['stage'], aliases[first['task']] + ' / ' + first['call_id'], perturbation,
                first['outcome'], (first.get('exception', '') + ': ' + first['error']).strip(': '),
                location, len(group)]) + ' |')
        if len(entries) > limit:
            lines.extend(['', f'表中列出前 {limit} 组可复现样例；另 {len(entries)-limit} 组、'
                          f'{sum(len(group) for _, group in entries[limit:])} 命中见 JSON 的 findings（未丢弃）。'])
    lines.extend(['', '## 独立根因与启发式候选（按代码位置归并；命中数含不同探针及历史/当前双重检查）', '',
                  '| 根因 | file:line | 影响 stages | 命中 / 调用 | 严重度 | 一行修复建议 |',
                  '| --- | --- | --- | ---: | --- | --- |'])
    for row in report['root_causes']:
        lines.append('| ' + ' | '.join(cell(v) for v in [row['title'], row['location'], ', '.join(row['stages']),
            f"{row['hits']} / {row['affected_calls']}", row['severity'], row['suggested_fix']]) + ' |')
    lines.extend(['', '## 无当前通过回复的阶段', '', '| 原始 stage | 已接收 | 拒绝 | 不支持 |',
                  '| --- | ---: | ---: | ---: |'])
    for stage, values in sorted(report['raw_stage_census'].items()):
        if not values.get('ok'):
            lines.append(f"| {stage} | {values['received']} | {values.get('rejected', 0)} | {values.get('unsupported', 0)} |")
    families = [stage for stage, values in sorted(report['stage_census'].items()) if not values.get('ok')]
    lines.extend(['', '以上按原始 stage 名统计，无法直接执行该分支的 E1/E2/M1。合并 _repair 后仍无任何通过回复的阶段族：'
                  + (', '.join(families) or '无') + '。', '',
                  '## R1 覆盖与限制', '', '| 阶段 | 调用数 | 原稿及回显检查数 | 保护误拒绝 | 其他原稿拒绝 |',
                  '| --- | ---: | ---: | ---: | ---: |'])
    for stage in ('rough_audio_timing_', 'chain_e2e_v1_fine_revise', 'audio_plan_v'):
        rows = [r for r in report['protected_echo'] if r['stage'].startswith(stage)]
        results = [s for r in rows for s in r.get('results', [])]
        protection = sum(bool(re.search('changed|unrelated', s.get('error', ''), re.I)) for s in results)
        other = sum(not s['accepted'] for s in results) - protection
        lines.append(f'| {stage}* | {len(rows)} | {len(results)} | {protection} | {other} |')
    lines.extend(['', '`audio_plan_v*` 若为 0，表示归档无该阶段，不构造虚假 previous_plan；'
                  '现有 replay_check 没有该阶段适配器；若以后有该阶段的修订归档，R1 可直接复用实际 '
                  'plan_revision_check 保护 guard，但其完整生产验证分支仍标记不支持。'
                  'R1 回送所有实际记录修订请求的原始 previous（包括当前原回复失败者）；其他原因拒绝逐项留在 JSON。', '',
                  f"M1 接受 {len(report['accepted_mutations'])} 次，其中保守认定必需字段缺陷 "
                  f"{sum(bool(r['required_reason']) for r in report['accepted_mutations'])} 次；"
                  f"{sum(r['discarded_annotation'] for r in report['accepted_mutations'])} 次位于已丢弃记录中。"
                  '其余为可选字段/默认值/未证实候选，完整保存供复核。漏检无异常可引用，表中位置为被审查的校验入口/下游消费者。', '',
                  '高严重度 revision_reason 漏检另从当前 StoryFinecut.run 提取并执行原始下游 guard，'
                  'JSON 的 downstream_error 保存真实异常及 traceback；未运行媒体、渲染或网络流程。', '',
                  '脚本退出码 1 表示发现审计问题，0 表示未发现；报告正常写出。'
                  '本次未穷举所有嵌套畸形组合，未覆盖无基线分支及未记录的阶段；不能据此宣称已排除全部潜在缺陷。', ''])
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('replay_root', type=Path)
    parser.add_argument('--json', dest='json_path', required=True, type=Path)
    args = parser.parse_args(argv)
    root = args.replay_root.resolve()
    output = args.json_path.resolve()
    if not root.is_dir():
        parser.error('回放根目录不存在')
    if output == root or root in output.parents:
        parser.error('输出必须位于只读回放目录外')
    if any(REPO / name == output or REPO / name in output.parents for name in ('src', 'tests')):
        parser.error('审计禁止写入 src/ 或 tests/')
    start = time.monotonic()
    audit = Audit(root)
    # Resolving immutable recorded Linux source paths on macOS otherwise dominates runtime.
    with patch.object(Path, 'resolve', lru_cache(maxsize=4096)(Path.resolve)):
        audit.run()
    import subprocess
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip()
    report = dict(root=str(root), head=head, elapsed_s=time.monotonic() - start,
                  repair_boundary=audit.boundary, counts=dict(audit.counts), tasks=audit.tasks,
                  stage_census=dict(audit.stages), raw_stage_census=dict(audit.raw_stages), baselines=audit.baselines,
                  findings=audit.findings, root_causes=roots(audit.findings),
                  finding_counts={key: sum(row['category'] == key for row in audit.findings) for key in CLASSES},
                  accepted_mutations=audit.accepted, protected_echo=audit.r1)
    output.write_text(json.dumps(contracts.json_safe(report), ensure_ascii=False, indent=2, allow_nan=False) + '\n',
                      encoding='utf-8')
    print(format_report(report))
    return int(bool(audit.findings))


if __name__ == '__main__':
    raise SystemExit(main())
