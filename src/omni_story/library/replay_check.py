"""Read-only validation of received model replies against the current contracts."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import re
from types import CodeType, FunctionType, SimpleNamespace

from . import contracts, pipeline, story_finecut
from .state import LibraryStopped
from .story_context import build_fine_context, validate_story_context

CATEGORIES = ('ok', 'regression', 'fixed', 'still_failing', 'unsupported')


class Unsupported(ValueError):
    pass


def read(path):
    if not path.is_file():
        raise Unsupported('missing artifact: ' + str(path))
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, OSError) as error:
        raise Unsupported('unreadable JSON artifact: ' + str(path) + ': ' + str(error)) from error


def nested_validator(function, name='validate', **bindings):
    """Bind an existing pure validator without entering its media/IO workflow."""
    code = next((code for code in function.__code__.co_consts
                 if isinstance(code, CodeType) and code.co_name == name), None)
    if code is None or not set(code.co_freevars) <= bindings.keys():
        raise Unsupported('validator closure changed: ' + function.__name__ + '/' + name)
    def cell(value):
        return (lambda: value).__closure__[0]
    return FunctionType(code, function.__globals__, closure=tuple(cell(bindings[key]) for key in code.co_freevars))


def prompt_context(request, *keys):
    prompt = request.get('arguments', {}).get('prompt', request.get('prompt', ''))
    decoder = json.JSONDecoder()
    for match in re.finditer(r'(?:^|\n)\s*(?=\{)', prompt):
        try:
            value, _ = decoder.raw_decode(prompt[match.end():])
        except ValueError:
            continue
        if isinstance(value, dict) and set(keys) <= value.keys():
            return value
    raise Unsupported('request context missing: ' + ', '.join(keys))


def raw_reply(folder):
    response = read(folder / 'response.json') if (folder / 'response.json').is_file() else {}
    if isinstance(response.get('text'), str):
        return response['text'], 'response.json:text'
    result = response.get('result', response)
    blocks = result.get('content', []) if isinstance(result, dict) else []
    texts = [row['text'] for row in blocks if row.get('type') == 'text' and isinstance(row.get('text'), str)]
    if texts:
        return '\n'.join(texts), 'response.json:result.content.text'
    if (folder / 'parsed.json').is_file():
        return read(folder / 'parsed.json'), 'parsed.json'
    raise Unsupported('original reply text and parsed.json unavailable: ' + str(folder))


def scope_bounds(request):
    scope = request.get('observation_scope', {})
    start, end = scope.get('source_start_s'), scope.get('source_end_s')
    if not (contracts.finite_number(start) and contracts.finite_number(end) and 0 <= start < end):
        raise Unsupported('recorded observation scope/duration unavailable')
    return start, end


def finecut(task, state):
    binding = read(task / 'skill_finecut/input.json')
    value = story_finecut.StoryFinecut.__new__(story_finecut.StoryFinecut)
    value.state = SimpleNamespace(input_lock=state['input_lock'])
    value.adaptive = state['input_lock']['configuration'].get('workflow') == 'reference_rough_skill_v1'
    value.auth = dict(parent=binding['parent'], reference=binding['reference'],
        target_duration_s=binding['reference']['duration_s'], target_tolerance_s=binding['target_tolerance_s'])
    if value.adaptive:
        value.auth['unbounded_inspections'] = True
    if binding.get('duration_policy'):
        value.auth['duration_policy'] = binding['duration_policy']
    if binding.get('duration_policy') == 'do_not_lengthen_rough':
        value.auth['target_duration_s'] = binding['parent']['duration_s']
    value.reference = dict(reference=read(task / 'reference_reading.json'))
    value.story_context = read(task / 'rough_audio/story_context.json') if (task / 'rough_audio/story_context.json').is_file() else None
    value.render_source = dict(binding['parent'], source_id='chain_parent', audio_stream_index=None)
    return value


def validator(task, state, call, request):
    config = state['input_lock'].get('configuration', {})
    normalize = contracts.time_normalization_enabled(SimpleNamespace(input_lock=state['input_lock'],
        normalize_observation_times=config.get('normalize_observation_times', False)))
    name = re.sub(r'_repair\d*$', '', call['name'])
    if name == 'reference':
        ref = read(task / 'reference_catalog/inventory.json')['sources'][0]
        return nested_validator(pipeline.execute, 'validate_current_reference',
            creative_audio=config.get('creative_audio') is True, ref=ref)
    if re.fullmatch(r'search_\d+', name):
        context = prompt_context(request, 'catalog', 'already_watched')
        if 'reusable_visual_candidates' in context:
            return lambda data: pipeline._validate_library_search(data, context['catalog'],
                context['reusable_visual_candidates'], max_windows=context['max_windows_this_round'],
                received_windows=context['already_watched'])
        return lambda data: contracts.validate_search(data, context['catalog'],
            max_windows=context['max_windows_this_round'])
    if name.startswith('subtitle_regions_'):
        from .audio_contracts import validate_regions
        start, end = scope_bounds(request)
        return lambda data: validate_regions(data, end - start)
    if name.startswith('fine_'):
        context = prompt_context(request, 'window')
        reference = read(task / 'reference_reading.json')
        strategy = contracts.reference_strategy(reference, creative_audio=config.get('creative_audio') is True)
        return nested_validator(pipeline.execute, 'validate_current_fine',
            window=context['window'], visual_only=strategy['adaptation_mode'] == 'reinterpret',
            editing_v2=config.get('editing_v2', False), editing_reference=context.get('editing_reference'),
            normalize_times=normalize)
    if re.fullmatch(r'plan_\d+', name):
        context = prompt_context(request, 'reference', 'catalog', 'watched_windows')
        reference = context['reference']
        strategy = contracts.reference_strategy(reference, creative_audio=config.get('creative_audio') is True)
        if config.get('semantic_audit'):
            raise Unsupported('semantic plan claim context unavailable')
        return nested_validator(pipeline.execute, 'validate_current_plan',
            creative_audio=config.get('creative_audio') is True, editing_reference=context.get('editing_reference'),
            editing_v2=config.get('editing_v2', False), maximum_segments=config.get('maximum_segments', 32),
            progress_driven=config.get('workflow') == 'reference_rough_skill_v1',
            ref=dict(sha256=state['input_lock']['reference_sha256'], duration_s=context['reference_duration_s'],
                     audio_stream_index=context.get('reference_audio_stream_index')),
            reference_reading=reference, semantic_audit=False, semantic_pipeline=None,
            sources=context['catalog'], strategy=strategy, visual_only=strategy['adaptation_mode'] == 'reinterpret',
            windows=context['watched_windows'])
    if name.startswith('rough_audio_timing_'):
        from .visual_audio import revise_draft_timing
        context = prompt_context(request, 'story_context', 'editable_line_ids')
        return nested_validator(revise_draft_timing, context=context['story_context'],
            editable=set(context['editable_line_ids']),
            previous={line['id']: line for line in context['story_context']['audio_draft']},
            validate_story_context=validate_story_context)
    if name.startswith('final_layout_review_'):
        from .final_visual_review import review_final_layout
        start, end = scope_bounds(request)
        return nested_validator(review_final_layout, duration=end - start, normalize=normalize)
    if name.startswith(story_finecut.PREFIX):
        flow = finecut(task, state)
        stage = name[len(story_finecut.PREFIX):]
        if stage == 'observe':
            return flow.observation_check
        if stage.startswith(('inspect_', 'detail_')):
            start, end = scope_bounds(request)
            return lambda data: flow.facts_check(data, start, end, sparse=stage.startswith('detail_'),
                normalize=flow.normalize_times() and not stage.startswith('detail_'))
        if stage == 'plan' or stage.startswith('revise'):
            context = prompt_context(request, 'reference', 'observed', 'source')
            flow.reference = context['reference']
            flow.story_context = context.get('story_context', flow.story_context)
            flow.observed = context['observed']
            if config.get('creative_audio') is True and flow.story_context is None:
                raise Unsupported('creative fine plan parent story context unavailable')
            if stage == 'plan':
                return flow.plan_check
            revision = context.get('actual_output_feedback')
            if not revision or not {'plan', 'review'} <= revision.keys():
                raise Unsupported('fine revision original plan/review unavailable')
            index = int(stage.split('_')[1]) if '_' in stage else 0
            result = read(task / f'skill_finecut/render_{index}/render_result.json')
            return lambda data: flow.revision_check(data, revision['plan'], result, revision['review'])
        if stage.startswith(('blind', 'review')):
            start, end = scope_bounds(request)
            if stage.startswith('blind'):
                return lambda data: flow.review_check(data, end - start, blind=True)
            return nested_validator(story_finecut.StoryFinecut.review, 'target_check',
                self=flow, result=dict(duration_s=end - start))
    raise Unsupported('no current validator adapter for stage: ' + name)


def classify(passed_then, passed_now):
    return ('ok' if passed_now else 'regression') if passed_then else ('fixed' if passed_now else 'still_failing')


def check(row, action):
    try:
        action()
    except Unsupported as error:
        row.update(category='unsupported', passed_now=None, reason=str(error))
    except (ValueError, TypeError, KeyError, IndexError, LibraryStopped) as error:
        row.update(category=classify(row['passed_then'], False), passed_now=False,
            error=str(error), violation=getattr(error, 'violation', None), diagnostics=getattr(error, 'diagnostics', None))
    else:
        row.update(category=classify(row['passed_then'], True), passed_now=True)
    return row


def delivery(task, state, label):
    base = task / 'skill_finecut'
    result = read(base / 'result.json') if (base / 'result.json').is_file() else {}
    selected = result.get('selected_render')
    if selected is None:
        # Reviews are also stored in the received call ledger, before finish can write a result.
        calls = [call for call in state['calls'] if re.fullmatch(story_finecut.PREFIX + r'review(?:_r\d*)?(?:_repair\d*)?', call['name'])
                 and call.get('status') == 'received' and (task / 'calls' / call['id'] / 'parsed.json').is_file()]
        if not calls:
            return []
        last = calls[-1]
        review = read(task / 'calls' / last['id'] / 'parsed.json')
        if review.get('next_action') != 'deliver':
            return []
        match = re.search(r'_r(\d*)(?:_repair\d*)?$', last['name'])
        index = int(match[1] or 1) if match else 0
        path = base / f'render_{index}/render_result.json'
        if not path.is_file():
            return [dict(task=label, stage='delivery', call_id='delivery', passed_then=False,
                         passed_now=None, category='unsupported', reason='selected render receipt unavailable')]
        selected = read(path)
    failure = read(task / 'chain_failure.json') if (task / 'chain_failure.json').is_file() else {}
    row = dict(task=label, stage='delivery', call_id='delivery',
               passed_then=bool(result.get('story_context')) or (base / 'story_context.json').is_file(),
               historical_evidence='skill_finecut/result.json' if result else 'chain_failure.json',
               historical_error=failure.get('error'))
    def replay():
        flow = finecut(task, state)
        if flow.story_context is None:
            raise Unsupported('parent story context unavailable')
        # Use the frozen render input, exactly as finish does, with the actual render provenance.
        path = base / Path(selected['manifest_path']).parent.name / 'render_input.json'
        plan = read(path)['compiled']['plan']
        context = build_fine_context(flow.reference, flow.story_context, plan, selected)
        validate_story_context(context)
        if (base / 'story_context.json').is_file():
            validate_story_context(read(base / 'story_context.json'))
    return [check(row, replay)]


def replay_check(root):
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError('replay data directory required: ' + str(root))
    rows, tasks = [], []
    for ledger in sorted(root.rglob('library_state.json')):
        task = ledger.parent
        state = read(ledger)
        if not state.get('calls') and task.name == 'creative_audio':
            continue
        label = str(task.relative_to(root))
        tasks.append(label)
        repaired = {call.get('repair_of') for call in state['calls'] if call.get('repair_of')}
        for call in state['calls']:
            if call.get('status') != 'received':
                continue
            folder = task / 'calls' / call['id']
            failed = (folder / 'protocol_failure.json').is_file() or call['id'] in repaired
            row = dict(task=label, stage=call['name'], call_id=call['id'], passed_then=not failed,
                historical_evidence='protocol_failure.json' if (folder / 'protocol_failure.json').is_file()
                    else 'repair_of' if failed else 'received_without_protocol_failure')
            def replay():
                request = read(folder / 'request.json')
                try:
                    validate = validator(task, state, call, request)
                except (KeyError, TypeError, IndexError) as error:
                    raise Unsupported('cannot reconstruct validator context: ' + str(error)) from error
                raw, source = raw_reply(folder)
                row['reply_source'] = source
                validate(deepcopy(contracts.parse_model_json(raw)))
            rows.append(check(row, replay))
        rows.extend(delivery(task, state, label))
    summary = {key: sum(row['category'] == key for row in rows) for key in CATEGORIES}
    by_task = [dict(task=task, **{key: sum(row['task'] == task and row['category'] == key for row in rows)
                               for key in CATEGORIES}) for task in tasks]
    return dict(root=str(root), summary=summary, tasks=by_task, rows=rows)


def format_report(report):
    lines = ['task | ' + ' | '.join(CATEGORIES), '--- | ' + ' | '.join('---:' for _ in CATEGORIES)]
    for row in [*report['tasks'], dict(task='TOTAL', **report['summary'])]:
        lines.append(row['task'] + ' | ' + ' | '.join(str(row[key]) for key in CATEGORIES))
    for row in report['rows']:
        if row['category'] in {'regression', 'unsupported'}:
            lines.append('\n' + json.dumps(row, ensure_ascii=False))
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--json', dest='json_path', type=Path)
    args = parser.parse_args(argv)
    if args.json_path and args.json_path.resolve().is_relative_to(args.root.resolve()):
        parser.error('JSON output must be outside the read-only data directory')
    report = replay_check(args.root)
    print(format_report(report))
    if args.json_path:
        args.json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return int(bool(report['summary']['regression']))


if __name__ == '__main__':
    raise SystemExit(main())
