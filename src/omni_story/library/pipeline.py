"""Local execution for a Codex task using the official vision MCP.

The queue is an external supported-agent tool connection, not a Coding Plan
model API endpoint. The optional OpenCode adapter supplies a supported independent
agent connection; it does not change the existing Codex route.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
import json
from itertools import count
import math
import os
import re
from pathlib import Path
from types import SimpleNamespace
import time
import traceback

from . import contracts, prompts
from .media import (MEDIA_EXTENSIONS, create_contact_sheet, create_navigation_sheet, inventory_sources, prepare_window,
                    probe_media, sha256_file, verify_source)
from .state import (LibraryState, LibraryStopped, json_sha, write_json, scope_fingerprint,
                    text_agent_reply, text_call_reinterpretation)
from .editing import (compact_timeline, validate_candidate_dispositions, validate_method_review,
                      validate_fine_editing)
from .shot_timeline import detect_shot_timeline, associate_edl_boundaries


TEXT_ARGV_MAX_BYTES = 100_000


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def validate_creative_style(config):
    style = config.get('creative_style')
    if style not in {None, 'classic', 'picture_first', 'picture_aware'}:
        raise ValueError('unknown_creative_style')
    if style == 'picture_aware' and not config.get('creative_audio'):
        raise ValueError('picture_aware_requires_creative_audio')
    if style == 'picture_first' and (not config.get('creative_audio') or config.get('material_search') == 'question_first'):
        raise ValueError('picture_first_requires_creative_library_or_film_task')


def _review_line_bindings(context):
    return [{**{key: row.get(key) for key in
                ('line_id', 'text', 'picture_relation', 'mode', 'speaker', 'cast_id', 'start_s', 'end_s')},
             'segment_ids': row.get('segment_ids', [segment['segment_id'] for segment in row.get('segments', [])])}
            for row in context.get('timeline', [])]


def _resume_silent_reply(state, name, prompt, validator, *, media_sha256=None, render_sha256=None, used_prompt=None):
    """Reuse a hash-bound blind/check receipt from an explicitly resumed task."""
    checkpoint = state.input_lock['configuration'].get('resume_from')
    seen = set()
    while checkpoint:
        binding = checkpoint['ledger']
        if binding['path'] in seen or sha256_file(binding['path']) != binding['sha256']:
            raise LibraryStopped('resume_from_silent_ledger_changed')
        seen.add(binding['path'])
        parent = Path(binding['path']).parent
        saved = _read(binding['path'])
        for call in reversed(saved['calls']):
            if call['name'] not in {name, name + '_repair', name + '_repair2'}:
                continue
            folder = parent / 'calls' / call['id']
            request = _read(folder / 'request.json')
            if json_sha(request) != call['request_sha256']:
                raise LibraryStopped('resume_from_silent_request_changed')
            reinterpretation = text_call_reinterpretation(folder, call, request)
            status = reinterpretation['effective_status'] if reinterpretation else call['status']
            pinned = next((row for row in checkpoint.get('text_call_reinterpretations', [])
                           if row['call_id'] == call['id'] and row['ledger'] == binding), None)
            if pinned and pinned != dict(ledger=binding, **(reinterpretation or {})):
                raise LibraryStopped('resume_from_silent_text_evidence_changed')
            if reinterpretation:
                receipt = dict(ledger=binding, **reinterpretation)
                if not any(row['sha256'] == json_sha(receipt) for row in
                           state.data['artifacts'].get('text_call_reinterpretation', [])):
                    state.set_artifact('text_call_reinterpretation', receipt)
            old_prompt = request['arguments']['prompt']
            same_review = False
            if render_sha256 is not None:
                # A compacted prompt must not bypass an older unknown review of
                # these same rendered bytes in a parent task.
                for line in old_prompt.splitlines():
                    if not line.startswith('{'):
                        continue
                    try:
                        context, _ = json.JSONDecoder().raw_decode(line)
                    except ValueError:
                        continue
                    if isinstance(context, dict) and context.get('actual_render_sha256') == render_sha256:
                        if status in {'submitted', 'uncertain', 'failed_known', 'received_invalid'}:
                            kind = status if status in {'failed_known', 'received_invalid'} else 'outcome_unknown'
                            raise LibraryStopped(f'resume_from_silent_{kind}_no_replay:' + call['id'])
                        current = json.loads(prompt.rsplit('\n', 1)[-1])
                        same_review = (_review_line_bindings(context) == _review_line_bindings(current) and
                                       context.get('content_check') == current.get('content_check') and
                                       context.get('cast') == current.get('cast'))
                        break
            if (request.get('media_sha256') != media_sha256 or
                    not same_review and old_prompt != prompt and
                    not old_prompt.startswith(prompt + '\n上次输出未通过本地协议校验。')):
                continue
            if status == 'received_invalid':
                raise LibraryStopped('resume_from_silent_received_invalid_no_replay:' + call['id'])
            if status in {'submitted', 'uncertain'}:
                raise LibraryStopped('resume_from_silent_outcome_unknown_no_replay:' + call['id'])
            if (status == 'failed_known' and
                    (reinterpretation or state.input_lock['configuration'].get('creative_style') == 'picture_aware')):
                raise LibraryStopped('resume_from_silent_failed_known_no_replay:' + call['id'])
            failure_path = folder / 'protocol_failure.json'
            if failure_path.exists():
                if state.input_lock['configuration'].get('creative_style') == 'picture_aware':
                    failure = _read(failure_path)
                    if failure.get('attempt', -1) >= failure.get('repair_limit', 1):
                        raise LibraryStopped('resume_from_silent_repair_exhausted:' + call['id'])
                continue
            if status != 'received':
                continue
            response = _read(folder / 'response.json')
            if json_sha(response) != call['response_sha256']:
                raise LibraryStopped('resume_from_silent_response_changed')
            try:
                value = contracts.parse_model_json('\n'.join(row['text'] for row in response['result']['content']
                                                            if row.get('type') == 'text'))
                validator(value)
            except (ValueError, TypeError, KeyError):
                continue
            receipt = dict(stage=name, ledger=binding, call_id=call['id'])
            artifact = ('picture_aware_reply_reuse' if state.input_lock['configuration'].get('creative_style') == 'picture_aware'
                        else 'picture_first_reply_reuse')
            if not any(row['sha256'] == json_sha(receipt) for row in state.data['artifacts'].get(artifact, [])):
                state.set_artifact(artifact, receipt)
            if used_prompt is not None:
                used_prompt.append(old_prompt)
            return value
        checkpoint = saved['input_lock']['configuration'].get('resume_from')
    return None


def _silent_blind_reading(state, glm, rendered, round_no, cache):
    from .visual_story_trial import proxy
    output = Path(state.output)
    source = inventory_sources(rendered['rendered_path'], output / f'silent_catalog_{round_no}')['sources'][0]
    if source['sha256'] != rendered['sha256']:
        raise LibraryStopped('silent_gate_render_changed')
    media = proxy(source, 0, rendered['measured_duration_s'], cache, label=f'silent_rough_{round_no}')
    blind_prompt = prompts.blind_prompt(rendered['measured_duration_s'])
    validate_blind = lambda value: contracts.validate_blind_reading(value, rendered['measured_duration_s'])
    blind = _resume_silent_reply(state, f'blind_{round_no}', blind_prompt, validate_blind,
                                media_sha256=sha256_file(media['path']))
    if blind is None:
        blind = glm.call(f'blind_{round_no}', blind_prompt, media['path'], validate_blind,
            scope=dict(kind='continuous_window', source_sha256=rendered['sha256'],
                       source_start_s=0, source_end_s=rendered['measured_duration_s']))
    return blind


def _silent_story_check(state, glm, rendered, visual_script, round_no, cache):
    """Blindly observe the actual silent render, then compare texts only."""
    output = Path(state.output)
    blind = _silent_blind_reading(state, glm, rendered, round_no, cache)
    check_prompt = prompts.silent_check_prompt(visual_script, blind, render_sha256=rendered['sha256'], round_no=round_no)
    validate_check = lambda value: contracts.validate_silent_check(value, visual_script)
    verdict = _resume_silent_reply(state, f'silent_check_{round_no}', check_prompt, validate_check)
    if verdict is None:
        verdict = glm.call_text(f'silent_check_{round_no}', check_prompt, validate_check)
    passed = all(beat['told'] for beat in verdict['beats']) and not verdict['scene_jumps_confusing']
    entry = dict(round=round_no, render_sha256=rendered['sha256'], blind_reading=blind, verdict=verdict, passed=passed)
    gate_path = output / 'silent_gate.json'
    gate = _read(gate_path) if gate_path.exists() else dict(visual_script_sha256=json_sha(visual_script), checks=[])
    if gate['visual_script_sha256'] != json_sha(visual_script):
        raise LibraryStopped('silent_gate_visual_script_changed')
    previous = next((row for row in gate['checks'] if row['round'] == round_no), None)
    if previous is not None and previous != entry:
        raise LibraryStopped('silent_gate_recorded_check_changed')
    if previous is None:
        gate['checks'].append(entry)
    gate['passed'] = passed
    write_json(gate_path, gate)
    bindings = state.data['artifacts'].get('picture_first_silent_gate', [])
    if not bindings or bindings[-1]['sha256'] != json_sha(gate):
        state.set_artifact('picture_first_silent_gate', gate)
    return entry


def _review_observation(window, segment):
    """Keep watched facts overlapping this cut, in the observation's local clock."""
    observation = window['observation']
    start = segment['source_in_s'] - window['source_start_s']
    end = segment['source_out_s'] - window['source_start_s']
    events = [event for event in observation.get('events', [])
              if event['local_start_s'] < end and start < event['local_end_s']]
    usable = [row for row in observation.get('usable_ranges', [])
              if row['local_in_s'] < end and start < row['local_out_s']]
    role_ids = set(segment.get('role_ids', []))
    for row in [*events, *usable]:
        role_ids.update(row.get('role_ids', []))
    fields = dict(events=('local_start_s', 'local_end_s', 'observed_fact', 'role_ids'),
        usable_ranges=('local_in_s', 'local_out_s', 'role_ids', 'camera', 'continuity_notes'),
        roles=('role_id', 'state', 'identity_confirmed', 'identity_evidence', 'entity_id'))
    groups = dict(events=events, usable_ranges=usable,
                  roles=[role for role in observation.get('roles', []) if role['role_id'] in role_ids])
    return {key: [{field: row[field] for field in fields[key] if field in row} for row in rows]
            for key, rows in groups.items() if key in observation}


def _picture_aware_review(state, glm, rendered, plan, reference, windows, round_no, cache):
    try:
        entry = _picture_aware_review_entry(state, glm, rendered, plan, reference, windows, round_no, cache)
    except (LibraryStopped, ValueError) as error:
        reason = str(error)
        # Only call availability/protocol failures are advisory. Integrity and
        # input-binding failures must still stop the run.
        if not reason.startswith(('recorded_request_not_received_no_replay:',
                'resume_from_silent_outcome_unknown_no_replay:', 'resume_from_silent_failed_known_no_replay:',
                'resume_from_silent_received_invalid_no_replay:',
                'resume_from_silent_repair_exhausted:',
                'text_check_outcome_unknown_no_replay:', 'text_check_launch_failed:',
                'text_check_historical_failed_known_no_replay:', 'text_check_historical_received_invalid_no_replay:',
                'text_check_failed_no_replay:', 'opencode_vision_job_failed_no_retry:',
                'official_MCP_failure:',
                'opencode_launch_failed', 'MCP_wait_timed_out_inspect_original_job_no_replay:',
                'model_protocol_repair_exhausted:blind_', 'model_protocol_repair_exhausted:silent_review_')):
            raise
        names = {f'blind_{round_no}', f'silent_review_{round_no}'}
        calls = [call for call in state.data.get('calls', [])
                 if call['name'].removesuffix('_repair2').removesuffix('_repair') in names]
        ident = re.search(r'glm_\d+_(?:blind|silent_review)_\d+(?:_repair2?)?$', reason)
        call_id = ident.group() if ident else calls[-1]['id'] if calls else None
        if call_id is None:
            raise
        entry = dict(round=round_no, render_sha256=rendered['sha256'], revision_suggested=False, score=None,
                     review_unavailable=dict(reason=reason, call_id=call_id))
        previous = _recorded_picture_aware_review(state, rendered['sha256'], round_no)
        if previous is None or 'content_acceptable' in previous:
            entry.update(content_acceptable=None, must_fix=[], revision_count=round_no)
        request_path = Path(state.output) / 'calls' / call_id / 'request.json'
        if request_path.exists():
            request = _read(request_path)
            if request.get('tool') == 'text_completion':
                entry['prompt_bytes'] = len(request['arguments']['prompt'].encode('utf-8'))
    return _record_picture_aware_review(state, entry)


def _settled_request_prompt(state, name):
    """The prompt of the request a settled call actually answered, if recorded."""
    try:
        state._reload()
        call = next(row for row in reversed(state.data['calls']) if row['name'] == name)
        return _read(Path(state.output) / 'calls' / call['id'] / 'request.json')['arguments']['prompt']
    except (StopIteration, AttributeError, KeyError, TypeError, OSError, ValueError):
        return None


def _recorded_picture_aware_review(state, render_sha256, round_no):
    """Find authenticated old review schemas before rebuilding their exact prompt."""
    ledgers = [(Path(state.output), state.data)]
    checkpoint = state.input_lock['configuration'].get('resume_from')
    seen = set()
    while checkpoint:
        binding = checkpoint['ledger']
        if binding['path'] in seen or sha256_file(binding['path']) != binding['sha256']:
            raise LibraryStopped('resume_from_silent_ledger_changed')
        seen.add(binding['path'])
        saved = _read(binding['path'])
        ledgers.append((Path(binding['path']).parent, saved))
        checkpoint = saved['input_lock']['configuration'].get('resume_from')
    for folder, ledger in ledgers:
        path = folder / 'silent_review.json'
        if not path.exists():
            continue
        record = _read(path)
        bindings = ledger.get('artifacts', {}).get('picture_aware_silent_review', [])
        if not bindings or bindings[-1]['sha256'] != json_sha(record):
            raise LibraryStopped('silent_review_record_changed')
        previous = next((row for row in record['reviews'] if row['round'] == round_no and
                         row['render_sha256'] == render_sha256), None)
        if previous is not None:
            return previous
    return None


def _picture_aware_review_entry(state, glm, rendered, plan, reference, windows, round_no, cache):
    from .line_windows import draft_line_windows, segment_output_timeline
    blind = _silent_blind_reading(state, glm, rendered, round_no, cache)
    segment_times = segment_output_timeline(plan['segments'], fps=plan.get('fps'))
    actual = rendered.get('provenance', [])
    for segment, row in zip(plan['segments'], actual):
        start = row.get('output_in_s', row.get('output_start_s'))
        end = row.get('output_out_s', row.get('output_end_s'))
        if start is not None and end is not None:
            segment_times[segment['segment_id']].update(start_s=start, end_s=end, output_duration_s=end - start)
    timed_segments = [dict(segment_id=segment['segment_id'], **segment_times[segment['segment_id']])
                      for segment in plan['segments']]
    line_times = draft_line_windows(plan['audio_draft'], timed_segments)
    watched = {window['window_id']: window for window in windows}
    previous = _recorded_picture_aware_review(state, rendered['sha256'], round_no)
    legacy = previous is not None and 'prompt_bytes' not in previous and 'review_unavailable' not in previous
    content_check = previous is None or 'roles' in previous.get('verdict', {}) or 'content_acceptable' in previous
    # A recorded review keeps the prompt it was asked with; only new reviews align blind facts per line.
    blind_facts = previous is None or previous.get('line_blind_facts') is True
    by_id = {segment['segment_id']: segment for segment in plan['segments']}
    timeline = []
    for line in plan['audio_draft']:
        segments = []
        for ident in line['segment_ids']:
            segment = by_id[ident]
            window = watched[segment['window_id']]
            segments.append(dict(segment_id=ident, **segment_times[ident], window_id=window['window_id'],
                source_id=window['source_id'], window_source_start_s=window['source_start_s'],
                window_source_end_s=window['source_end_s'],
                source_in_s=segment['source_in_s'], source_out_s=segment['source_out_s'],
                watched_observation=window['observation'] if legacy else _review_observation(window, segment)))
        start, end = line_times[line['id']]
        timeline.append(dict(line_id=line['id'], text=line['text'], picture_relation=line['picture_relation'],
            mode=line['mode'], speaker=line.get('speaker'), start_s=start, end_s=end, segments=segments))
        if content_check and 'cast_id' in line:
            timeline[-1]['cast_id'] = line['cast_id']
    prompt = prompts.silent_review_prompt(blind, reference, timeline, render_sha256=rendered['sha256'], legacy=legacy,
                                         content_check=content_check, cast=plan.get('cast'), blind_facts=blind_facts)
    validator = lambda value: contracts.validate_silent_review(value, plan['audio_draft'], require_roles=content_check)
    used = []
    verdict = _resume_silent_reply(state, f'silent_review_{round_no}', prompt, validator,
                                   render_sha256=rendered['sha256'], used_prompt=used)
    if verdict is None:
        verdict = glm.call_text(f'silent_review_{round_no}', prompt, validator)
        used.append(_settled_request_prompt(state, f'silent_review_{round_no}'))
    # A reused reply keeps the prompt it actually answered: its bytes and rules.
    asked = (used[0] or prompt).split('\n上次输出未通过本地协议校验。')[0]
    issues = [line for line in verdict['lines'] if line['issue'] in contracts.SILENT_REVIEW_TIER1]
    revise = bool(issues) or verdict['scene_jumps_confusing']
    score = round(100 * (1 - (len(issues) + int(verdict['scene_jumps_confusing'])) / (len(verdict['lines']) + 1)), 2)
    entry = dict(round=round_no, render_sha256=rendered['sha256'], blind_reading=blind, timeline=timeline,
                 verdict=verdict, revision_suggested=revise, score=score)
    if not legacy:
        entry['prompt_bytes'] = len(asked.encode('utf-8'))
    if content_check and not legacy and '"blind_facts":' in asked:
        entry['line_blind_facts'] = True
    if content_check:
        must_fix = [line for line in verdict['lines'] if line['issue'] == 'contradiction']
        must_fix += [dict(issue='roles.' + key, evidence=verdict['roles']['evidence'])
                     for key in ('clear', 'consistent') if not verdict['roles'][key]]
        entry.update(content_acceptable=not must_fix, must_fix=must_fix, revision_count=round_no,
                     revision_suggested=revise or bool(must_fix))
    return entry


def _picture_aware_feedback(silent, revision):
    first = ('先修好must_fix及其证据，再酌情处理建议。' if silent['must_fix'] else
             '没有必须修好的问题，只酌情处理下列建议。')
    return dict(must_fix=silent['must_fix'],
        instruction=f'仅用同一批已看窗口修订plan_{revision}，不重新检索。' + first +
            '优先补救顺序：(a)重写或移动台词，使其符合画面；先重新解释，只需满足台词成立的最小可见条件；'
            '(b)在已看窗口内换用其他segments承载该句；'
            '(c)角色问题：全片一个故事角色对应一个电影人物，首次出现用画面或台词交代谁是谁，绝不拆给多人或中途换人。',
        issues=[line for line in silent['verdict']['lines'] if line['issue'] not in {'ok', 'contradiction'}],
        scene_jumps_confusing=silent['verdict']['scene_jumps_confusing'],
        readable_situation=silent['verdict']['readable_situation'])


def _record_picture_aware_review(state, entry):
    path = Path(state.output) / 'silent_review.json'
    record = _read(path) if path.exists() else dict(creative_style='picture_aware',
        advisory='content_acceptable' not in entry, reviews=[],
        score_definition='100 * (1 - (tier1_line_count + confusing_jumps) / (line_count + 1)); invisible_claim is informational')
    previous = next((row for row in record['reviews'] if row['round'] == entry['round']), None)
    if previous is not None and 'review_unavailable' in entry:
        if previous.get('review_unavailable', {}).get('call_id') == entry['review_unavailable']['call_id']:
            entry = previous
        else:
            record['reviews'].remove(previous)
            previous = None
    if previous is not None and previous != entry:
        raise LibraryStopped('silent_review_recorded_check_changed')
    if previous is None:
        record['reviews'].append(entry)
    record.update(score=entry['score'], revision_suggested=entry['revision_suggested'])
    if 'content_acceptable' in entry:
        record.update(advisory=False, content_acceptable=entry['content_acceptable'],
                      must_fix=entry['must_fix'], revision_count=entry['revision_count'])
    if 'review_unavailable' in entry:
        record['review_unavailable'] = entry['review_unavailable']
    write_json(path, record)
    bindings = state.data['artifacts'].get('picture_aware_silent_review', [])
    if not bindings or bindings[-1]['sha256'] != json_sha(record):
        state.set_artifact('picture_aware_silent_review', record)
    return entry


def require_silent_gate(state, rough):
    """The audio entry point must have a passing check of these exact bytes."""
    if not all((Path(state.output) / name).is_file() for name in ('silent_gate.json', 'visual_script.json')):
        raise LibraryStopped('picture_first_requires_recorded_silent_gate')
    gate = _read(Path(state.output) / 'silent_gate.json')
    script = _read(Path(state.output) / 'visual_script.json')
    contracts.validate_visual_script(script)
    bindings = state.data.get('artifacts', {}).get('picture_first_silent_gate', [])
    if not bindings or bindings[-1]['sha256'] != json_sha(gate) or _read(bindings[-1]['path']) != gate:
        raise LibraryStopped('silent_gate_record_changed')
    check = gate['checks'][-1]
    contracts.validate_silent_check(check['verdict'], script)
    if (gate['visual_script_sha256'] != json_sha(script) or check['render_sha256'] != rough['final_sha256'] or
            check['round'] != rough['selected_round'] or not all(beat['told'] for beat in check['verdict']['beats']) or
            check['verdict']['scene_jumps_confusing']):
        raise LibraryStopped('silent_story_not_readable')


def _speech_context(speech):
    """Pack exact segment evidence; raw/word-level records stay in local files."""
    if not isinstance(speech, dict):
        return {'status':'no_speech_evidence'}
    keys = ('status','source_id','source_sha256','source_start_s','source_end_s',
            'source_offset_s','audio_stream_index','language','language_probability','evidence_limit')
    packed = {key:speech[key] for key in keys if key in speech}
    segment_keys = ('segment_id','source_start_s','source_end_s','local_start_s','local_end_s',
                    'text','avg_logprob','no_speech_prob')
    packed['segments'] = [{key:segment[key] for key in segment_keys if key in segment}
                          for segment in speech.get('segments',[])]
    packed['evidence_limit'] = 'Unverified ASR segment text and timings; no speaker identity or music/beat evidence.'
    return packed


def _window_context(window, *, include_speech=True):
    """Keep all model observations and source mapping without codec/cache noise."""
    keys = ('window_id','source_id','source_sha256','source_start_s','source_end_s',
            'source_offset_s','media_duration_s','duration_s','sha256','audio_stream_index',
            'audio_present','time_mapping','mapping_tolerance_s','status','observation','editing_timeline',
            'source_visual_policy','source_subtitle_masks','shared_visual_observation','revisit_of','content_range',
            'question_first_subtitle_bar')
    packed = {key:window[key] for key in keys if key in window}
    if window.get('qfs_identity'):
        packed['qfs_identity'] = window['qfs_identity']
    if include_speech and 'asr' in window:
        packed['asr'] = _speech_context(window['asr'])
    return packed


REINTERPRET_POLICY = ('本次重写故事，复用真实画面。只记录画面中的外形、物件、动作、表情、空间和时间；'
    '不读取原字幕、不使用原对白、片名或电影常识补充身份动机和剧情。新故事的身份、动机、对白和'
    '由剪辑形成的叙事因果可以原创，但画面中实际出现的人物、物件和动作不可凭空改变。')


def fine_identity_context(window, windows, *, material_search=None):
    """Do not offer foreign-source entities as copyable identity choices."""
    prefix = window['source_sha256'] + ':'
    return dict(entity_id_contract=contracts.fine_entity_contract(window),
        already_watched_roles=[dict(role, source_id=previous['source_id'],
            source_sha256=previous['source_sha256']) for previous in windows
            if previous['source_sha256'] == window['source_sha256']
            for role in previous['observation']['roles']
            if (material_search != 'film' or role.get('entity_id_origin') != 'film_window_scoped')
            and (not role.get('entity_id') or role['entity_id'].startswith(prefix))])


def _source_subtitles(glm, source, raw, *, image=False):
    """One localization on the raw view; later semantic observations use masked pixels."""
    from .audio_contracts import validate_regions
    start, end = raw['source_start_s'], raw['source_end_s']
    duration = end - start
    config = getattr(getattr(glm, 'state', None), 'input_lock', {}).get('configuration', {})
    if config.get('material_search') in {'question_first', 'film'}:
        from .subtitle_bar import question_first_masks
        return question_first_masks(source, start, end, Path(glm.state.output) / 'question_first_film_info')
    override = config.get('source_subtitle_regions', {}).get(source['sha256'])
    if override is not None:
        value = dict(masks=[dict(row, start_s=0, end_s=duration) for row in override],
                     protected_text=[], limitations=[])
        validate_regions(value, duration)
        return [dict(row, start_s=start, end_s=end) for row in value['masks']]
    key = json_sha({'media_sha256': raw['sha256'], 'source_start_s': start,
                    'source_end_s': end, 'image': image})[:16]
    prompt = ('只定位画外字幕、标题和说话人标签，不转写文字，不解释剧情；保留人物、动作和场景内文字。'
        '坐标x/y/w/h是原始画面内归一化比例，时间start_s/end_s从本次范围0秒开始。'
        f'本次范围长{duration}秒；无叠字返回masks=[]。返回JSON '
        '{"masks":[{"x":比例,"y":比例,"w":比例,"h":比例,"start_s":秒,"end_s":秒,'
        '"color":"#000000","reason":"实际叠字区域"}],"protected_text":[],"limitations":[]}。')
    if image:
        prompt += ('这是一张带原片时间标签的稀疏联系表。坐标以每个小格中的原画面为准，'
            '不能以整张联系表或时间标签为准。将抽样中实际出现的叠字区域覆盖本次范围；'
            '这仅清理导航抽样，不证明帧间字幕或动作。')
    normalised = []
    stage = 'subtitle_regions_' + key
    value = glm.call(stage, prompt, raw['path'],
                     lambda row: validate_regions(row, duration, normalisations=normalised), image=image,
                     scope={k: raw[k] for k in ('kind', 'source_sha256', 'source_start_s', 'source_end_s')})
    if normalised:
        # Keyed by stage: the accepted reply is the last one that validated.
        write_json(Path(glm.state.output) / 'region_normalisation' / (stage + '.json'), normalised)
    masks = value['masks']
    # Video sampling cannot locate every subtitle onset. A repeated fixed band
    # stays covered throughout this observation, including unsampled gaps.
    if not image and len(masks) > 1:
        geometry = ('x', 'y', 'w', 'h', 'color')
        if all(all(row[k] == masks[0][k] for k in geometry) for row in masks):
            row = dict(masks[0], start_s=0, end_s=duration)
            masks = [row]
    return [dict(row, start_s=row['start_s'] + start, end_s=row['end_s'] + start)
            for row in masks]


def _validate_question_first_windows(windows, source_map, output):
    """Old received windows must also cover the bar before reuse/rendering."""
    from .subtitle_bar import question_first_masks, validate_question_first_masks
    for window in windows:
        source = source_map[window['source_id']]
        start, end = window['source_start_s'], window['source_end_s']
        question_first_masks(source, start, end, Path(output) / 'question_first_film_info')
        bar = source['subtitle_bar']
        validate_question_first_masks(window.get('source_subtitle_masks'), bar, start, end)
        window['question_first_subtitle_bar'] = deepcopy(bar)


def _partial_library_ranges(value, window, *, normalize=False):
    """Retain valid retrieval ranges without inventing replacements for bad ones."""
    if normalize:
        value = deepcopy(value)
        contracts.normalize_fine(value, window['source_end_s'] - window['source_start_s'])
    contracts.validate_fine({**value, 'usable_ranges': []}, window, normalize=False)
    kept, adjustments = [], []
    for index, original in enumerate(contracts.rows(value.get('usable_ranges'), 'fine/usable_ranges', nonempty=False)):
        usable = deepcopy(original)
        if (isinstance(usable, dict) and isinstance(usable.get('event_indices'), list)
                and all(type(usable.get(key)) in (int, float) and math.isfinite(usable[key])
                        for key in ('local_in_s', 'local_out_s'))):
            usable['event_indices'] = [item for item in usable['event_indices'] if type(item) is int
                and 0 <= item < len(value['events'])
                and value['events'][item]['local_start_s'] < usable['local_out_s']
                and usable['local_in_s'] < value['events'][item]['local_end_s']]
        try:
            contracts.validate_fine({**value, 'usable_ranges': [usable]}, window, normalize=False)
        except ValueError as error:
            adjustments.append(dict(kind='rejected_library_usable_range', original_range_index=index,
                                    original_range=original, reason=str(error), needs_local_rewatch=True))
            continue
        kept.append(usable)
        if usable != original:
            adjustments.append(dict(kind='unsupported_range_event_refs_removed', original_range_index=index,
                                    original_range=original, retained_event_indices=usable['event_indices']))
    value['usable_ranges'] = kept
    if adjustments:
        value.setdefault('technical_adjustments', []).extend(adjustments)
        value.setdefault('limitations', []).append('Some proposed ranges lacked usable event evidence; '
            'rejected ranges require local rewatch and are not edit evidence.')
    return value


def _received_note_seed():
    """Load the unchanged private compatibility binding only when needed."""
    path = os.environ.get('OMNI_RECEIVED_NOTE_SEED_FILE')
    if not path:
        raise LibraryStopped('received_note_seed:configuration_missing:OMNI_RECEIVED_NOTE_SEED_FILE')
    try:
        seed = _read(path)
    except (OSError, ValueError):
        raise LibraryStopped('received_note_seed:configuration_unreadable') from None
    if json_sha(seed) != '54b6c2dba891db113e3f00cbaa84706ee5493f40b38ec30a3d62863deda8e528':
        raise LibraryStopped('received_note_seed:configuration_binding_changed')
    return seed


def _fine_navigation_points(value, window, *, partial_ranges=False, normalize=False):
    """Normalize received navigation fields; preserve every original reply."""
    result = deepcopy(value)
    marker = window.get('received_protocol_compatibility')
    if normalize and marker is not None and json_sha(value) != marker['raw_model_json_sha256'] and any(
            item.get('kind') == marker['policy'] for item in value.get('technical_adjustments', []) if isinstance(item, dict)):
        # The bound raw reply is checked separately; this is its saved view.
        marker = None
    if marker is not None:
        if (marker != _received_note_seed() or
                any(window.get(key) != expected for key, expected in marker['window'].items()) or
                json_sha(value) != marker['raw_model_json_sha256']):
            raise LibraryStopped('received_note_seed:original_observation_binding_changed')
        warnings = result.get('uncertainties')
        if (not isinstance(warnings, list) or not warnings or any(
                not isinstance(row, dict) or set(row) != {'note'} or
                not isinstance(row['note'], str) or not row['note'].strip() for row in warnings)):
            raise LibraryStopped('received_note_seed:only_nonempty_note_wrappers_allowed')
        result['uncertainties'] = [row['note'] for row in warnings]
        result.setdefault('technical_adjustments', []).append(dict(
            kind=marker['policy'], original_uncertainties=deepcopy(warnings),
            source_call_id=marker['evidence']['call_id'],
            source_response_sha256=marker['evidence']['response_sha256'],
            allowed_use='received_protocol_shape_only_not_independent_visual_acceptance'))
    for key in ('window_id', 'source_id'):
        if result.get(key) == '固定ID' and isinstance(window.get(key), str):
            result[key] = window[key]
            result.setdefault('technical_adjustments', []).append(dict(
                kind='literal_template_id_from_bound_request', field=key, original_value='固定ID'))
    prefix = window.get('source_sha256', '') + ':'
    references = [row['entity_id'] for row in window.get('reference_board', [])
                  if isinstance(row.get('entity_id'), str) and row['entity_id'].startswith(prefix)]
    for role in result.get('roles', []):
        short = role.get('entity_id') if isinstance(role, dict) else None
        matches = [entity for entity in references if entity[len(prefix):] == short]
        if isinstance(short, str) and len(set(matches)) == 1:
            role['entity_id'] = matches[0]
            result.setdefault('technical_adjustments', []).append(dict(
                kind='entity_namespace_from_supplied_reference', role_id=role.get('role_id'),
                original_entity_id=short, entity_id=matches[0]))
    if window.get('qfs_identity'):
        from .question_first import bind_observed_entities
        bind_observed_entities(result, window['qfs_identity'])
    for index, usable in enumerate(result.get('usable_ranges', [])):
        if not isinstance(usable, dict):
            continue
        aliases = {}
        for alias, key in (('local_start_s', 'local_in_s'), ('local_end_s', 'local_out_s')):
            if key not in usable and alias in usable:
                aliases[alias] = usable.pop(alias)
                usable[key] = aliases[alias]
        if aliases:
            result.setdefault('technical_adjustments', []).append(dict(
                kind='usable_local_time_aliases', usable_range_index=index, original_fields=aliases))
    unconfirmed = {role.get('role_id') for role in value.get('roles', [])
                   if isinstance(role, dict) and role.get('identity_confirmed') is False}
    for index, usable in enumerate(result.get('usable_ranges', [])):
        if not isinstance(usable, dict) or not isinstance(usable.get('role_ids'), list):
            continue
        excluded = [role for role in usable['role_ids'] if role in unconfirmed]
        if excluded:
            # A background/group appearance remains usable as pixels, but cannot
            # establish a stable speaking character. Preserve the event and raw reply.
            usable['role_ids'] = [role for role in usable['role_ids'] if role not in unconfirmed]
            result.setdefault('technical_adjustments', []).append(dict(
                kind='unconfirmed_roles_not_character_bindings', usable_range_index=index,
                excluded_role_ids=excluded, allowed_use='visual_only_not_individual_dialogue'))
    duration = window['source_end_s'] - window['source_start_s']
    events = value.get('events', [])
    removed = {index for index, event in enumerate(events) if isinstance(event, dict)
        and all(type(event.get(key)) in (int, float) and math.isfinite(event[key])
                for key in ('local_start_s', 'local_end_s'))
        and 0 <= event['local_start_s'] == event['local_end_s'] <= duration}
    if not removed:
        if normalize:
            contracts.normalize_fine(result, duration)
        return _partial_library_ranges(result, window, normalize=normalize) if partial_ranges else result
    role_ids = {row.get('role_id') for row in value.get('roles', [])
                if isinstance(row, dict) and isinstance(row.get('role_id'), str)}
    for index in removed:
        contracts.text(events[index].get('observed_fact'), 'fine/observed_fact')
        contracts.refs(events[index].get('role_ids'), role_ids, 'fine/event_roles')
    remap = {old: new for new, old in enumerate(index for index in range(len(events)) if index not in removed)}
    result['events'] = [event for index, event in enumerate(result['events']) if index not in removed]
    if normalize:
        contracts.rows(result.setdefault('normalization', []), 'reference/normalization', nonempty=False).extend(
            dict(path=f'$.events[{index}]', original=deepcopy(events[index]), dropped=True) for index in sorted(removed))
    for usable in result.get('usable_ranges', []):
        if isinstance(usable, dict) and isinstance(usable.get('event_indices'), list):
            usable['event_indices'] = [remap.get(index, index) if type(index) is int else index
                for index in usable['event_indices'] if not (type(index) is int and index in removed)]
    if 'affordances' in result:
        kept = []
        for item in result['affordances']:
            if not isinstance(item, dict) or not isinstance(item.get('event_indices'), list):
                kept.append(item)
                continue
            item['event_indices'] = [remap.get(index, index) if type(index) is int else index
                for index in item['event_indices'] if not (type(index) is int and index in removed)]
            if item['event_indices']:
                kept.append(item)
        result['affordances'] = kept
    result.setdefault('technical_adjustments', []).extend(dict(
        kind='zero_length_event_navigation_only', original_event_index=index,
        original_event=deepcopy(events[index]), allowed_use='navigation_not_EDL_evidence')
        for index in sorted(removed))
    if normalize:
        contracts.normalize_fine(result, duration)
    return _partial_library_ranges(result, window, normalize=normalize) if partial_ranges else result


def _matches_received_fine(value, observation, window):
    return contracts.same_observation(value, observation, lambda item: contracts.validate_fine(item, window, normalize=True))


def _recorded_fine_matches(output, call, window, *, material_search=None):
    """Bind a watched view to an authenticated received reply and either cache format."""
    folder = Path(output) / 'calls' / call['id']
    if call['status'] != 'received' or not call['name'].startswith('fine_') or not (folder / 'parsed.json').is_file():
        return False
    saved = _read(folder / 'parsed.json')
    if saved.get('window_id') not in (window['window_id'], '固定ID'):
        return False
    request, reply = _read(folder / 'request.json'), _read(folder / 'response.json')
    if json_sha(request) != call['request_sha256'] or json_sha(reply) != call['response_sha256']:
        raise LibraryStopped('recorded_fine_request_or_reply_modified:' + call['id'])
    raw = contracts.parse_model_json('\n'.join(row['text'] for row in reply['result']['content'] if row.get('type') == 'text'))
    navigation = window.get('source_visual_policy') == 'silent_subtitle_masked'
    def validate_observation(value):
        contracts.validate_fine(value, window, normalize=True)
        contracts.bind_film_window_entities(dict(window, observation=value), material_search)
    def validate(value):
        if navigation:
            value.update(_fine_navigation_points(value, window, normalize=True))
        elif window.get('qfs_identity'):
            from .question_first import bind_observed_entities
            bind_observed_entities(value, window['qfs_identity'])
        validate_observation(value)
    value = contracts.recorded_view(raw, saved, validate, changed='recorded_parsed_reply_modified:' + call['id'])
    observation = contracts.recorded_view(window['observation'], window['observation'],
        validate_observation, changed='watched_window_not_bound_to_received_fine:' + window['window_id'])
    return request['media_sha256'] == window['sha256'] and contracts.same_normalized(value, observation)


def _received_windows(state, *, visual_only, windows_path=None, normalize=False):
    """Reuse received observations without turning old ASR-derived descriptions into pixels."""
    path = Path(windows_path) if windows_path else state.output / 'watched_windows.json'
    if not path.is_file():
        return []
    material_search = getattr(state, 'input_lock', {}).get('configuration', {}).get('material_search')
    windows = []
    for window in _read(path):
        if visual_only and window.get('source_visual_policy') != 'silent_subtitle_masked':
            continue
        contracts.bind_film_window_entities(window, material_search)
        shared = window.get('shared_visual_observation')
        if shared:
            from .visual_library import VisualLibrary
            record = VisualLibrary(shared['root']).get(shared['observation_id'])
            if json_sha(record) != shared['record_sha256']:
                raise LibraryStopped('shared_visual_observation_changed')
            recovered = _received_library_window(record, shared['root'], source={
                'source_id': window['source_id'], 'sha256': window['source_sha256'],
                'duration_s': window['source_end_s']}, revision_ids=
                window.get('source_identity_revision', {}).get('revision_ids', []), normalize=normalize)
            stored = deepcopy(window)
            if normalize:
                stored['observation'] = contracts.recorded_view(stored['observation'], stored['observation'],
                    lambda item: contracts.validate_fine(item, stored, normalize=True),
                    changed='shared_watched_window_changed:' + window['window_id'])
            if ({key: value for key, value in recovered.items() if key != 'observation'} !=
                    {key: value for key, value in stored.items() if key != 'observation'} or
                    not (contracts.same_normalized(recovered['observation'], stored['observation']) if normalize
                         else recovered['observation'] == stored['observation'])):
                raise LibraryStopped('shared_watched_window_changed:' + window['window_id'])
            windows.append(recovered)
            continue
        for call in reversed(state.data['calls']):
            if call['status'] != 'received' or not call['name'].startswith('fine_'):
                continue
            folder = state.output / 'calls' / call['id']
            if normalize and not (folder / 'parsed.json').is_file() and not window.get('received_protocol_compatibility'):
                continue
            request, reply = _read(folder / 'request.json'), _read(folder / 'response.json')
            if json_sha(request) != call['request_sha256'] or json_sha(reply) != call['response_sha256']:
                raise LibraryStopped('recorded_fine_request_or_reply_modified:' + call['id'])
            try:
                parsed = contracts.parse_model_json('\n'.join(row['text'] for row in reply['result']['content']
                                                            if row.get('type') == 'text'))
            except (ValueError, TypeError, KeyError):
                if not normalize:
                    raise
                continue
            navigation = visual_only or window.get('source_visual_policy') == 'silent_subtitle_masked'
            if normalize and any(parsed.get(key) != window[key] and (not navigation or parsed.get(key) != '固定ID')
                   for key in ('window_id', 'source_id')):
                continue
            marker = window.get('received_protocol_compatibility')
            if normalize and marker and json_sha(parsed) != marker['raw_model_json_sha256']:
                continue
            if normalize and (folder / 'parsed.json').is_file():
                if not _recorded_fine_matches(state.output, call, window, material_search=material_search):
                    continue
            if navigation:
                try:
                    parsed = _fine_navigation_points(parsed, window, normalize=normalize)
                except (ValueError, TypeError, KeyError):
                    if not normalize:
                        raise
                    continue
            elif window.get('qfs_identity'):
                from .question_first import bind_observed_entities
                bind_observed_entities(parsed, window['qfs_identity'])
            contracts.bind_film_window_entities(dict(window, observation=parsed), material_search)
            if not (_matches_received_fine(parsed, window['observation'], window) if normalize
                    else parsed == window['observation']):
                continue
            if request['media_sha256'] != window['sha256'] or sha256_file(window['path']) != window['sha256']:
                raise LibraryStopped('recorded_fine_media_changed:' + call['id'])
            if normalize:
                parsed = contracts.normalized_view(parsed, lambda item: contracts.validate_fine(item, window, normalize=True))
                window['observation'] = parsed
            else:
                contracts.validate_fine(parsed, window)
            windows.append(window)
            break
        else:
            raise LibraryStopped('watched_window_not_bound_to_received_fine:' + window['window_id'])
    return windows


def _received_library_window(record, root, *, source, revision_ids=None, normalize=False):
    """Reuse one indexed observation after checking its original call and media."""
    original = record['window']
    evidence = record['evidence']
    if (evidence.get('status') != 'received' or original.get('status') != 'watched'
            or original.get('source_visual_policy') != 'silent_subtitle_masked'
            or original.get('audio_present') is not False
            or original['source_sha256'] != source['sha256']
            or not 0 <= original['source_start_s'] < original['source_end_s'] <= source['duration_s']):
        raise LibraryStopped('shared_visual_source_or_policy_changed')
    ledger = Path(evidence['ledger_path'])
    marker = original.get('received_protocol_compatibility')
    if marker is not None and (marker != _received_note_seed() or
            any(evidence.get(key) != expected for key, expected in marker['evidence'].items()) or
            sha256_file(ledger) != marker['parent_ledger_file_sha256'] or
            _read(ledger)['task_id'] != marker['parent_task_id']):
        raise LibraryStopped('received_note_seed:shared_record_evidence_changed')
    call = next((row for row in _read(ledger)['calls'] if row['id'] == evidence['call_id']), None)
    if (call is None or call['status'] != 'received' or
            any(call.get(key) != evidence[key] for key in ('request_sha256', 'response_sha256'))):
        raise LibraryStopped('shared_visual_received_call_changed')
    folder = ledger.parent / 'calls' / call['id']
    request, reply = _read(folder / 'request.json'), _read(folder / 'response.json')
    if (json_sha(request) != call['request_sha256'] or json_sha(reply) != call['response_sha256']
            or request['media_sha256'] != original['sha256']
            or sha256_file(original['path']) != original['sha256']):
        raise LibraryStopped('shared_visual_request_reply_or_media_changed')
    lineage = _read(Path(original['path']).parent / 'lineage.json')
    keys = ('source_id', 'source_sha256', 'source_start_s', 'source_end_s',
            'source_visual_policy', 'source_subtitle_masks', 'audio_present', 'sha256')
    if (any(original.get(key) != lineage.get(key) for key in keys)
            or lineage.get('kind') != 'continuous_window'):
        raise LibraryStopped('shared_visual_media_lineage_changed')
    if 'observation_scope' in request and scope_fingerprint(request['observation_scope']) != scope_fingerprint(lineage):
        raise LibraryStopped('shared_visual_request_scope_changed')
    base = original.get('base_proxy')
    if base:
        base_lineage = _read(Path(base['path']).parent / 'lineage.json')
        if (sha256_file(base['path']) != base['sha256'] or base_lineage['sha256'] != base['sha256']
                or any(base_lineage.get(key) != original.get(key) for key in keys[:-1])):
            raise LibraryStopped('shared_visual_base_proxy_changed')
    for exemplar in [*original.get('reference_board', []), *original.get('entity_exemplars', [])]:
        if sha256_file(exemplar['path']) != exemplar['sha256']:
            raise LibraryStopped('shared_visual_character_reference_changed')
    parsed = contracts.parse_model_json('\n'.join(row['text'] for row in reply['result']['content']
                                                 if row.get('type') == 'text'))
    if normalize:
        def validate(value):
            cleaned = _fine_navigation_points(value, original, partial_ranges=
                record.get('observation_method', {}).get('usable_range_policy') == 'retain_valid_ranges_v1', normalize=True)
            value.clear()
            value.update(cleaned)
            contracts.validate_fine(value, original, normalize=False)
        if (folder / 'parsed.json').is_file():
            parsed = contracts.recorded_view(parsed, _read(folder / 'parsed.json'), validate,
                changed='shared_visual_received_observation_changed')
        parsed = contracts.recorded_view(parsed, record.get('received_observation', original['observation']), validate,
            changed='shared_visual_received_observation_changed')
        received = deepcopy(parsed)
        derived = contracts.recorded_view(original['observation'], original['observation'],
            lambda item: contracts.validate_fine(item, original, normalize=True),
            changed='shared_visual_derived_observation_changed')
    else:
        parsed = _fine_navigation_points(parsed, original, partial_ranges=
            record.get('observation_method', {}).get('usable_range_policy') == 'retain_valid_ranges_v1')
        received = record.get('received_observation', original['observation'])
        if parsed != received:
            raise LibraryStopped('shared_visual_received_observation_changed')
        derived = deepcopy(original['observation'])
    raw = deepcopy(received)
    if len(derived.get('roles', [])) != len(raw.get('roles', [])):
        raise LibraryStopped('shared_visual_role_table_changed')
    for local, observed in zip(derived.get('roles', []), raw.get('roles', [])):
        if observed.get('entity_id') is not None and local.get('entity_id') != observed['entity_id']:
            raise LibraryStopped('shared_visual_matched_entity_changed')
        if local.get('entity_id') is not None and not local['entity_id'].startswith(original['source_sha256'] + ':'):
            raise LibraryStopped('shared_visual_entity_source_changed')
        for key in ('entity_id', 'identity_status'):
            local.pop(key, None)
            observed.pop(key, None)
    if not (contracts.same_normalized(derived, raw) if normalize else derived == raw):
        raise LibraryStopped('shared_visual_derived_observation_changed')
    if revision_ids:
        from .visual_library import VisualLibrary
        window = VisualLibrary(root).effective_window(record, revision_ids)
    else:
        window = deepcopy(original)
    if normalize:
        window['observation'] = contracts.normalized_view(window['observation'], lambda item: contracts.validate_fine(item, original, normalize=True))
    # Source IDs are catalog-local; immutable movie bytes and original evidence
    # stay bound while a task uses its current catalog ID.
    window['source_id'] = source['source_id']
    window['observation']['source_id'] = source['source_id']
    window['shared_visual_observation'] = dict(root=str(Path(root).resolve()),
        observation_id=record['observation_id'], record_sha256=json_sha(record))
    contracts.validate_fine(window['observation'], window, normalize=False)
    return window


def _library_query(reference):
    """Use reference goals as a retrieval query, not as stored footage facts."""
    def strings(value):
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [part for item in value for part in strings(item)]
        if isinstance(value, dict):
            return [part for item in value.values() for part in strings(item)]
        return []
    return ' '.join(strings({key: reference.get(key, '') for key in
        ('theme', 'intended_takeaway', 'visual_form', 'visual_requirements', 'retrieval_queries', 'editing_methods')}))


def _library_retrieval_needs(queries):
    """Keep proposed visual uses with queries, never with observed facts."""
    if not isinstance(queries, list):
        raise ValueError('shared_search:retrieval_queries_array_required')
    needs = []
    for index, query in enumerate(queries):
        if isinstance(query, str):
            need = {'query': query}
        elif isinstance(query, dict) and all(isinstance(query.get(key), str) and query[key].strip()
                for key in ('query', 'visual_goal', 'visual_path')):
            need = {key: query[key].strip() for key in ('query', 'visual_goal', 'visual_path')}
            conditions = query.get('must_have', [])
            if not isinstance(conditions, list) or any(not isinstance(item, str) for item in conditions):
                raise ValueError('shared_search:must_have_strings_required')
            need['must_have'] = conditions
            if 'slot_id' in query:
                if not isinstance(query['slot_id'], str) or not query['slot_id'].strip():
                    raise ValueError('shared_search:slot_id_required')
                if re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', query['slot_id']) is None:
                    raise contracts.numeric_error('plan/slots:unsafe_id', f'$.retrieval_queries[{index}].slot_id',
                        query['slot_id'], '[A-Za-z][A-Za-z0-9_-]{0,63}')
                need['slot_id'] = query['slot_id']
        else:
            raise ValueError('shared_search:invalid_visual_retrieval_query')
        need['query'] = ' '.join(need['query'].split())
        if need not in needs:
            needs.append(need)
    return needs


def _task_cast_contract():
    return contracts.plan_field_contract()['cast']


def _library_story_slots(search):
    """Search develops the same slots later completed with actual segments."""
    slots = search.get('slots', [])
    if not isinstance(slots, list):
        raise ValueError('shared_search:slots_array_required')
    seen = set()
    for index, slot in enumerate(slots):
        if not isinstance(slot, dict) or not isinstance(slot.get('slot_id'), str) or not slot['slot_id'].strip():
            raise ValueError('shared_search:slot_id_required')
        if re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', slot['slot_id']) is None:
            raise contracts.numeric_error('plan/slots:unsafe_id', f'$.slots[{index}].slot_id',
                slot['slot_id'], '[A-Za-z][A-Za-z0-9_-]{0,63}')
        if slot['slot_id'] in seen:
            raise ValueError('shared_search:duplicate_slot_id')
        seen.add(slot['slot_id'])
        if not isinstance(slot.get('intended_takeaway'), str) or not slot['intended_takeaway'].strip():
            raise ValueError('shared_search:slot_takeaway_required')
        coverage = slot.get('coverage', {})
        if (not isinstance(coverage, dict) or any(not isinstance(coverage.get(key), str) or not coverage[key].strip()
                for key in ('visual_goal', 'audio_goal'))):
            raise ValueError('shared_search:slot_coverage_required')
        for values in (coverage.get('gaps', []), slot.get('observation_ids', [])):
            if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
                raise ValueError('shared_search:slot_string_arrays_required')
    return slots


def _selected_library_observations(search):
    return list(dict.fromkeys([*search.get('observation_ids', []),
        *(ident for slot in search.get('slots', []) for ident in slot.get('observation_ids', []))]))


def _visual_request_key(request, source, windows):
    scope = dict(source_sha256=source['sha256'], start=request['start_s'], end=request['end_s'])
    revisit = request.get('revisit_window_id')
    if revisit is not None:
        previous = next((window for window in windows if window['window_id'] == revisit), None)
        if (previous is None or previous.get('status') != 'watched' or
                previous['source_sha256'] != source['sha256'] or
                not previous['source_start_s'] <= request['start_s'] < request['end_s'] <= previous['source_end_s']):
            raise ValueError('shared_search:revisit_requires_received_source_range')
        scope.update(revisit_of=revisit, question=request['question'].strip())
    return json_sha(scope)[:16]


def _known_fine_transport_failure(state, error, name, window):
    """Only a ledger-confirmed creative window failure permits losing that window."""
    if state.input_lock['configuration'].get('workflow') != 'reference_rough_skill_v1':
        return None
    code, _, call_id = str(error).partition(':')
    if code not in {'opencode_vision_job_failed_no_retry', 'recorded_request_not_received_no_replay'}:
        return None
    state._reload()
    call = next((c for c in state.data['calls'] if c['id'] == call_id), None)
    if (call is None or call['status'] != 'failed_known' or
            call['name'] not in {name, name + '_repair', name + '_repair2'}):
        return None
    return dict(window_id=window['window_id'], call_id=call_id, call_status=call['status'],
        request_sha256=call['request_sha256'], error=str(error),
        **{k: window[k] for k in ('source_id', 'source_sha256', 'source_start_s', 'source_end_s')},
        status='not_watched_no_EDL_permission')


def _validate_library_search(value, sources, cards, *, max_windows, received_windows=()):
    contracts.validate_search(value, sources, max_windows=max_windows, allow_empty=True)
    requested = value.get('observation_ids', [])
    if not isinstance(requested, list) or any(not isinstance(item, str) for item in requested):
        raise ValueError('shared_search:observation_ids_array_required')
    _library_story_slots(value)
    # Fresh fine observations have window IDs, not candidate-card IDs. These
    # windows have already passed _received_windows or a received fine call;
    # keep their original IDs and evidence rather than inventing library cards.
    known = {row['observation_id'] for row in cards}
    selectable = known | {row['window_id'] for row in received_windows
                          if row.get('status') == 'watched'}
    selected = set(_selected_library_observations(value))
    if len(requested) != len(set(requested)) or not selected <= selectable:
        bad = [ident for index, ident in enumerate(requested) if ident not in selectable or ident in requested[:index]]
        bad += sorted(selected - selectable - set(bad))
        path = '$.observation_ids' if bad and any(ident in requested for ident in bad) else '$.slots'
        raise contracts.numeric_error('shared_search:unknown_or_duplicate_observation', path, bad,
            dict(valid_candidates=sorted(selectable), unique_observation_ids=True))
    rejected = value.get('rejected_observation_ids', [])
    if (not isinstance(rejected, list) or any(not isinstance(item, str) for item in rejected)
            or len(rejected) != len(set(rejected)) or not set(rejected) <= known
            or set(rejected).intersection(selected)):
        bad = ([ident for index, ident in enumerate(rejected) if not isinstance(ident, str) or
            ident not in known or ident in selected or ident in rejected[:index]] if isinstance(rejected, list) else rejected)
        raise contracts.numeric_error('shared_search:invalid_rejected_observation', '$.rejected_observation_ids', bad,
            dict(valid_candidates=sorted(known - selected), unique_observation_ids=True))
    _library_retrieval_needs(value.get('retrieval_queries', []))
    by_id = {source['source_id']: source for source in sources['sources']}
    from .content_ranges import offer_content_windows, check_content_range
    available = {row.get('observation_id', row.get('window_id')): row for row in [*cards, *received_windows]}
    for ident in selected:
        row = available[ident]
        if 'source_sha256' in row and 'source_start_s' in row:
            offered = offer_content_windows([row], sources)
            if not offered:
                source = next(s for s in sources['sources'] if s['sha256'] == row['source_sha256'])
                index = requested.index(ident) if ident in requested else None
                check_content_range(source, row['source_start_s'], row['source_end_s'],
                    f'$.observation_ids[{index}]' if index is not None else '$.slots', fields=('', ''))
    for index, request in enumerate(value['windows']):
        try:
            _visual_request_key(request, by_id[request['source_id']], received_windows)
        except ValueError as error:
            raise contracts.numeric_error(str(error), f'$.windows[{index}].revisit_window_id',
                request.get('revisit_window_id'), dict(rule='watched, same source SHA, source_start_s ≤ start_s < end_s ≤ source_end_s',
                    received_ranges=[{key: window.get(key) for key in
                        ('window_id', 'status', 'source_sha256', 'source_start_s', 'source_end_s')}
                        for window in received_windows])) from error
    return value


def _expand_library_candidates(state, store, sources, cards, search, *, round_no, revision_ids, limit):
    """Use this received search's queries locally; freeze additions for the next search."""
    from .visual_library import _terms
    needs = [need for need in _library_retrieval_needs(search.get('retrieval_queries', []))
             if _terms(need['query'])]
    queries = list(dict.fromkeys(need['query'] for need in needs))
    if not queries:
        return [], {'queries': [], 'new_candidate_count': 0}
    path = state.output / f'retrieval_{round_no}.json'
    if path.exists():
        saved = _read(path)
        payload = saved['result']
        if (json_sha(payload) != saved['sha256'] or payload['revision_ids'] != revision_ids
                or payload['queries'] != queries or payload['search_sha256'] != json_sha(search)):
            raise LibraryStopped('shared_visual_retrieval_snapshot_changed')
        return payload['candidates'], payload
    by_sha = {row['sha256']: row['source_id'] for row in sources['sources']}
    excluded = {row['observation_id'] for row in cards} | set(search.get('rejected_observation_ids', []))
    additions = {}
    # A page is split between requested mechanisms rather than filled entirely
    # by the first query. It is not a total library or observation budget.
    page_size = max(1, math.ceil(limit / len(queries)))
    for query in queries:
        found = store.search(query, source_sha256s=list(by_sha), limit=page_size,
            revision_ids=revision_ids, exclude_observation_ids=excluded)
        for card in found:
            if card.get('match_strength') == 'weak_single_character':
                continue
            card['source_id'] = by_sha[card['source_sha256']]
            candidate = additions.setdefault(card['observation_id'], card)
            candidate.setdefault('retrieval_matches', []).extend(
                deepcopy(need) for need in needs if need['query'] == query)
    candidates = list(additions.values())
    payload = dict(queries=queries, visual_needs=needs, candidates=candidates, revision_ids=revision_ids,
        search_sha256=json_sha(search), new_candidate_count=len(candidates),
        evidence_limit='Local received-record recall only; not new visual observation or a quality pass.')
    write_json(path, {'result': payload, 'sha256': json_sha(payload)})
    return candidates, payload


def _received_library_search(state, name, sources, cards, *, max_windows, received_windows=()):
    """Resume received selection against its known pool without a new model call."""
    calls = [call for call in state.data['calls'] if call['status'] == 'received'
             and call['name'] in {name, name + '_repair', name + '_repair2'}]
    if not calls:
        return None
    call = calls[-1]
    folder = state.output / 'calls' / call['id']
    request, reply = _read(folder / 'request.json'), _read(folder / 'response.json')
    if json_sha(request) != call['request_sha256'] or json_sha(reply) != call['response_sha256']:
        raise LibraryStopped('shared_visual_received_search_changed')
    media = request['arguments'].get('image_source') or request['arguments'].get('video_source')
    if sha256_file(media) != request['media_sha256']:
        raise LibraryStopped('shared_visual_received_search_media_changed')
    value = contracts.parse_model_json('\n'.join(row['text'] for row in reply['result']['content']
                                                 if row.get('type') == 'text'))
    return _validate_library_search(value, sources, cards, max_windows=max_windows,
                                    received_windows=received_windows)


def _received_visual_seed(seed, sources, *, normalize=False):
    """Use a received visual catalog without paying to observe the same footage again."""
    ledger, catalog = Path(seed['ledger']['path']), Path(seed['windows']['path'])
    if any(sha256_file(row['path']) != row['sha256'] for row in seed.values()):
        raise LibraryStopped('received_visual_seed_changed')
    saved = _read(ledger)
    windows = _received_windows(SimpleNamespace(output=ledger.parent, data=saved,
        input_lock=saved.get('input_lock', {})),
                                visual_only=True, windows_path=catalog, normalize=normalize)
    source_map = {row['source_id']: row for row in sources['sources']}
    for window in windows:
        source = source_map.get(window['source_id'])
        if window.get('shared_visual_observation'):
            # _received_windows already checked the original lineage before
            # applying the task's catalog-local source ID.
            if (source is None or source['sha256'] != window['source_sha256'] or
                    not 0 <= window['source_start_s'] < window['source_end_s'] <= source['duration_s']):
                raise LibraryStopped('received_visual_seed_source_changed')
            continue
        lineage = _read(Path(window['path']).parent / 'lineage.json')
        keys = ('source_id', 'source_sha256', 'source_start_s', 'source_end_s',
                'source_visual_policy', 'source_subtitle_masks', 'audio_present')
        if (source is None or source['sha256'] != window['source_sha256'] or
                not 0 <= window['source_start_s'] < window['source_end_s'] <= source['duration_s'] or
                any(window.get(key) != lineage.get(key) for key in keys)):
            raise LibraryStopped('received_visual_seed_source_changed')
    return windows


def _received_plan_text(seed):
    """Verify the original receipt before parsing it under the current code."""
    ledger = Path(seed['ledger']['path'])
    if sha256_file(ledger) != seed['ledger']['sha256']:
        raise LibraryStopped('received_plan_ledger_changed')
    call = next(row for row in _read(ledger)['calls'] if row['id'] == seed['call_id'])
    if call['status'] != 'received' or not call['name'].startswith('plan_'):
        raise LibraryStopped('received_plan_required')
    folder = ledger.parent / 'calls' / call['id']
    request, reply = _read(folder / 'request.json'), _read(folder / 'response.json')
    if json_sha(request) != call['request_sha256'] or json_sha(reply) != call['response_sha256']:
        raise LibraryStopped('received_plan_record_changed')
    media = request['arguments'].get('image_source') or request['arguments'].get('video_source')
    if sha256_file(media) != request['media_sha256']:
        raise LibraryStopped('received_plan_media_changed')
    return '\n'.join(row['text'] for row in reply['result']['content'] if row.get('type') == 'text')


def _received_plan_seed(seed):
    """Read a model-authored plan already received before a local execution failure."""
    return contracts.parse_model_json(_received_plan_text(seed))


def _received_plan_prompt(seed):
    """The exact prompt bytes a recorded plan seed was asked for."""
    folder = Path(seed['ledger']['path']).parent / 'calls' / seed['call_id']
    return _read(folder / 'request.json').get('arguments', {}).get('prompt', '')


def _prompt_states_edit_length(prompt, marker=prompts.PICTURE_AWARE_EDIT_LENGTH_MARKER):
    """Whether request bytes actually carried the edit-length contract.

    Plan prompts state it with the marker; a finecut refinement prompt states it
    through the evidence JSON it embeds, so the quoted JSON key (which a reply
    echo or a repair feedback block can never produce) recognises it.
    """
    text = prompt or ''
    return marker in text or '"edit_length_budget":' in text


@contextmanager
def _legacy_edit_length_rule(context, prompt):
    """Withhold the edit-length budget while a prompt that never stated it is judged.

    A reply is only bounded by the length rule its own request carried, so a
    legacy prompt keeps the legacy ceiling even under today's context.
    """
    if not isinstance(context, dict) or 'edit_length_budget' not in context or _prompt_states_edit_length(prompt):
        yield
        return
    withheld = context.pop('edit_length_budget')
    try:
        yield
    finally:
        context['edit_length_budget'] = withheld


def _stated_plan_budget(budget, prompt):
    """The length rule a plan's own prompt stated; a legacy prompt stated none."""
    return deepcopy(budget) if budget is not None and _prompt_states_edit_length(prompt) else None


def _authored_plan_budget(state, output, round_no, budget, stage='plan'):
    """The rule the recorded call whose reply authored a round's plan was judged by.

    A fresh round is normally asked with today's prompt, but CodexMCP may keep a
    recorded prompt (same-name prior call with a protocol failure or an unknown
    outcome) and a bound cast correction replays under its own alias; the
    accepted plan's own call record states which rule that was.
    """
    if budget is None:
        return None
    base = f'{stage}_{round_no}'
    names = {base, f'{base}_repair', f'{base}_repair2',
             base + '_cast_correction_v1', base + '_cast_correction_v1_preflight_v1'}
    state._reload()
    call = next((c for c in reversed(state.data['calls'])
                 if c.get('name') in names and c.get('status') == 'received'), None)
    if call is None:
        return None
    prompt = _read(Path(output) / 'calls' / call['id'] / 'request.json').get('arguments', {}).get('prompt', '')
    return _stated_plan_budget(budget, prompt)


def _resume_plan(checkpoint, validator, *, stage='plan_0', context=None):
    """Newest passing reply wins; invalid replies supply the usual repair feedback."""
    from .protocol_feedback import diagnose
    rejected = []
    for seed in checkpoint['plan_seeds']:
        text = _received_plan_text(seed)  # Integrity failures must never become new paid work.
        prompt = _received_plan_prompt(seed)
        raw = None
        try:
            with _legacy_edit_length_rule(context, prompt):
                raw = contracts.parse_model_json(text)
                plan = deepcopy(raw)
                validator(plan)
        except (ValueError, TypeError, KeyError) as error:
            feedback = dict(call_id=seed['call_id'], ledger=seed['ledger'],
                validation_error=str(error),
                violations=diagnose(stage, raw, {**getattr(validator, 'protocol_context', {}),
                    'validation_error': str(error), 'violation': getattr(error, 'violation', None)}),
                repair_hint='只修复violations列出的字段及必要依赖；其余时间、ID和绑定保留原值，'
                    '不要整体改写时间域或新增身份绑定。' + (repair_hint(str(error), text, prompt) or ''))
            if isinstance(getattr(error, 'diagnostics', None), dict):
                feedback['validation_diagnostics'] = error.diagnostics
            rejected.append(feedback)
        else:
            return plan, seed, rejected
    # An unknown plan outcome cannot authorize another call on the same evidence.
    bindings = {row['path']: row for row in
                [checkpoint['ledger'], *(seed['ledger'] for seed in checkpoint['plan_seeds'])]}
    for binding in bindings.values():
        if sha256_file(binding['path']) != binding['sha256']:
            raise LibraryStopped('resume_from_seed_changed')
        if any(call['status'] in {'submitted', 'uncertain'} and call['name'] in
               {stage, stage + '_repair', stage + '_repair2'} for call in _read(binding['path'])['calls']):
            raise LibraryStopped('resume_from_unknown_plan_outcome_no_replay')
    return None, None, rejected


def _picture_aware_checkpoint(checkpoint, round_no):
    """Replay each original plan round, including plan_2 omitted by older resume manifests."""
    seeds = []
    names = {f'plan_{round_no}', f'plan_{round_no}_repair', f'plan_{round_no}_repair2'}
    if round_no == 2:
        current, seen, unknown = checkpoint, set(), False
        while current:
            binding = current['ledger']
            if binding['path'] in seen or sha256_file(binding['path']) != binding['sha256']:
                raise LibraryStopped('resume_from_seed_changed')
            seen.add(binding['path'])
            saved = _read(binding['path'])
            seeds.extend(dict(ledger=binding, call_id=call['id']) for call in reversed(saved['calls'])
                         if call['status'] == 'received' and call['name'] in names)
            unknown |= any(call['status'] in {'submitted', 'uncertain'} and call['name'] in names
                           for call in saved['calls'])
            current = saved['input_lock']['configuration'].get('resume_from')
        if unknown and not seeds:
            raise LibraryStopped('resume_from_unknown_plan_outcome_no_replay')
        return {**checkpoint, 'plan_seeds': seeds}
    for seed in checkpoint['plan_seeds']:
        binding = seed['ledger']
        if sha256_file(binding['path']) != binding['sha256']:
            raise LibraryStopped('resume_from_seed_changed')
        if any(call['id'] == seed['call_id'] and call['name'] in names for call in _read(binding['path'])['calls']):
            seeds.append(seed)
    return {**checkpoint, 'plan_seeds': seeds}


def _resume_checkpoint(state, config, *, plan_seed=None, rejected=()):
    checkpoint = config['resume_from']
    saved = {**checkpoint['calls_saved'], 'plan': len(checkpoint['plan_seeds']) if plan_seed else 0}
    saved['total'] = sum(saved.values())
    state.set_artifact('resume_checkpoint', dict(failed_task=checkpoint['failed_task'],
        failed_stage=checkpoint['failed_stage'],
        reused=dict(reference_call_id=checkpoint['reference_seed']['source_call_id'],
            windows_sha256=config['received_visual_seed']['windows']['sha256'],
            plan_call_id=plan_seed['call_id'] if plan_seed else 'new_plan'),
        plan_seed=plan_seed, calls_saved=saved,
        new_plan_calls=sum(call['name'] in {'plan_0', 'plan_0_repair', 'plan_0_repair2'} for call in state.data['calls']),
        current_violations=list(rejected)))


def _received_selection(config, sources):
    """Reuse received range choices as navigation, never their old plot descriptions."""
    seed = config.get('material_selection_seed')
    if not seed:
        return []
    path = Path(seed['path']).resolve(strict=True)
    if sha256_file(path) != seed['sha256']:
        raise LibraryStopped('material_selection_seed_changed')
    saved = _read(path)
    windows = _received_windows(SimpleNamespace(output=path.parent, data=saved), visual_only=False,
        normalize=contracts.creative_time_normalization(config))
    source_map = {row['source_id']: row for row in sources['sources']}
    ranges = []
    for window in windows:
        if (window['source_id'] not in source_map or
                window['source_sha256'] != source_map[window['source_id']]['sha256']):
            raise LibraryStopped('material_selection_seed_source_changed')
        ranges.append(dict(source_id=window['source_id'], start_s=window['source_start_s'],
            end_s=window['source_end_s'], role_ids=[],
            question='观察这些已选范围的实际人物、物件、动作和反应，用于按参考目标重新创作。'))
    contracts.validate_search(dict(reason='Received model-selected ranges only.', windows=ranges),
        sources, max_windows=None)
    return ranges


def _usage_for(output, job_id):
    for entry in reversed(_http_evidence(output, job_id)):
        if entry.get('type') == 'response' and entry.get('job_id') == job_id:
            try:
                return json.loads(entry['body']).get('usage', {})
            except (ValueError, TypeError):
                return {}
    return {}


def _http_evidence(output, job_id):
    records = []
    for name in ('mcp_http.jsonl', 'mcp_http_sf_0.jsonl', 'mcp_http_sf_3.jsonl'):
        journal = Path(output) / name
        if not journal.exists():
            continue
        # A full-film library journal can exceed RAM. Retain only this job's
        # entries while reading one original line at a time.
        with journal.open('rb') as stream:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line.decode('utf-8'))
                except (json.JSONDecodeError, UnicodeDecodeError) as error:
                    if not line.endswith(b'\n'):
                        continue  # A live writer may not have finished its last line.
                    raise LibraryStopped('corrupt_complete_http_journal_line:' + name) from error
                if entry.get('job_id') == job_id:
                    records.append(entry)
    return records


def _captured_reply(entries):
    for entry in reversed(entries):
        if entry.get('type') == 'response' and entry.get('status') == 200:
            body = json.loads(entry['body'])
            if body.get('choices'):
                choice = body['choices'][0]
                content = choice.get('message', {}).get('content') or ''
                return entry, {'status': 'complete', 'recovered_from_original_http': True,
                    'finish_reason': choice.get('finish_reason'),
                    'result': {'content': [{'type': 'text', 'text': content}]}}
    return None, None


def _outcome_unknown(entries):
    # With bounded network retries (mcp_guard.mjs networkRetryPermit) a job may
    # log several request attempts; the latest attempt decides its outcome —
    # attempts superseded by a newer responded one are history, not unknown.
    responses = {e.get('seq') for e in entries if e.get('type') == 'response'}
    latest = {}
    for entry in entries:
        if entry.get('type') == 'request':
            latest[entry.get('job_id')] = entry.get('seq')
    for entry in entries:
        if entry.get('type') == 'unknown_result' and entry.get('seq') not in responses:
            if entry.get('seq') == latest.get(entry.get('job_id')):
                return True  # the newest attempt itself died without a response
            if entry.get('job_id') not in latest:
                return True  # outcome record without any surviving request entry
    return any(seq not in responses for seq in latest.values())


def _kept_prompt_validator(validator, prompt):
    """Judge a kept recorded prompt's reply by the length rule that prompt stated.

    CodexMCP reuses a recorded request whenever its bytes must stay pinned (a
    prior protocol failure with a recorded repair, or a still-unknown outcome);
    such a reply was never asked for today's edit-length budget, so the budget
    is withheld for every validation of it, repairs included.
    """
    context = getattr(validator, 'edit_length_context', None)
    if (not isinstance(context, dict) or 'edit_length_budget' not in context or
            _prompt_states_edit_length(prompt)):
        return validator

    def judged(value):
        with _legacy_edit_length_rule(context, ''):
            return validator(value)
    judged.protocol_context = {key: item for key, item in
                               getattr(validator, 'protocol_context', {}).items()
                               if key != 'edit_length_budget'}
    return judged


class CodexMCP:
    """File handoff to the official MCP connected by the active Codex agent.

Only structural validation/format repair happens here. All creative responses
remain model authored. It never constructs or sends model HTTP requests.
"""
    provider = 'official_vision_mcp_in_codex'
    def __init__(self, state, *, timeout_s=720):
        self.state = state
        self.output = state.output
        self.timeout_s = timeout_s
        self.queue = self.output / 'mcp_queue'
        self.queue.mkdir(exist_ok=True)
        if not (self.output / 'mcp_ready.json').exists():
            raise LibraryStopped('connect_official_vision_mcp_in_codex_first')
        self.recover_received()

    def recover_received(self):
        # A received reply may outlive the local process; never resend its POST.
        self.state._reload()
        for call in list(self.state.data['calls']):
            if call['status'] not in {'submitted', 'uncertain', 'failed_known'}:
                continue
            entries = _http_evidence(self.output, call['id'])
            evidence, captured = _captured_reply(entries)
            if captured:
                self.state.reconcile_received(call, captured, evidence=evidence)
                continue
            if call['status'] == 'failed_known' and _outcome_unknown(entries):
                self.state.reclassify_uncertain(call, evidence=entries)
                continue
            response_path = self.queue / (call['id'] + '.response.json')
            if response_path.exists():
                reply = _read(response_path)
                if reply['status'] == 'complete':
                    self.state.complete_call(call, reply, usage=_usage_for(self.output, call['id']))
                else:
                    if call['status'] == 'submitted':
                        self.state.fail_call(call, reply.get('error', 'MCP_error'),
                            uncertain=reply['status'] == 'unknown' or _outcome_unknown(entries))

    def _submit(self, name, request, *, repair_of=None):
        digest = json_sha(request)
        self.state._reload()
        previous = next((c for c in self.state.data['calls'] if c['request_sha256'] == digest), None)
        if previous:
            if previous['status'] == 'received':
                folder = self.output/'calls'/previous['id']
                saved,reply = _read(folder/'request.json'),_read(folder/'response.json')
                if json_sha(saved) != previous['request_sha256'] or json_sha(reply) != previous['response_sha256']:
                    raise LibraryStopped('recorded_model_request_or_reply_modified:' + previous['id'])
                return previous,reply
            if previous['status'] != 'submitted':
                raise LibraryStopped('recorded_request_not_received_no_replay:' + previous['id'])
            call = previous  # Wait for the existing original submission, never send again.
        else:
            if (self.output / 'mcp_stop').exists():
                raise LibraryStopped('official_MCP_connection_stopped_reconnect_in_Codex')
            call, _ = self.state.begin_call(name, request, repair_of=repair_of)
            write_json(self.queue / (call['id'] + '.request.json'),
                       {'job_id': call['id'], 'tool': request['tool'], 'arguments': request['arguments']})
        response_path = self.queue / (call['id'] + '.response.json')
        deadline = time.monotonic() + self.timeout_s
        while not response_path.exists():
            if time.monotonic() > deadline:
                # Keep submitted: the server may still return a paid reply.
                raise LibraryStopped('MCP_wait_timed_out_inspect_original_job_no_replay:' + call['id'])
            time.sleep(0.25)
        reply = _read(response_path)
        if reply['status'] != 'complete':
            entries = _http_evidence(self.output, call['id'])
            evidence, captured = _captured_reply(entries)
            if captured:
                self.state.reconcile_received(call, captured, evidence=evidence)
                return call, captured
            self.state.fail_call(call, reply.get('error', str(reply.get('result'))),
                                 uncertain=reply['status'] == 'unknown' or _outcome_unknown(entries))
            raise LibraryStopped('official_MCP_failure:' + call['id'])
        self.state.complete_call(call, reply, usage=_usage_for(self.output, call['id']))
        return call, reply

    def call_text(self, name, prompt, validator):
        """Text-only checks share the same receipt and format-repair machinery."""
        return self.call(name, prompt, None, validator)

    def _submit_text(self, name, request, *, repair_of=None):
        self.state._reload()
        previous = next((call for call in self.state.data['calls']
                         if call['request_sha256'] == json_sha(request)), None)
        if previous:
            folder = self.output / 'calls' / previous['id']
            if previous['status'] != 'received':
                raise LibraryStopped('recorded_request_not_received_no_replay:' + previous['id'])
            saved, reply = _read(folder / 'request.json'), _read(folder / 'response.json')
            if json_sha(saved) != previous['request_sha256'] or json_sha(reply) != previous['response_sha256']:
                raise LibraryStopped('recorded_model_request_or_reply_modified:' + previous['id'])
            return previous, reply
        baseline = self.state.input_lock['configuration'].get('parent_baseline', {})
        for receipt in baseline.get('text_call_reinterpretations', []):
            if receipt['request_sha256'] != json_sha(request):
                continue
            if (sha256_file(receipt['ledger']['path']) != receipt['ledger']['sha256'] or
                    any(sha256_file(row['path']) != row['sha256'] for row in receipt['evidence'])):
                raise LibraryStopped('parent_text_check_evidence_changed')
            raise LibraryStopped('text_check_historical_' + receipt['effective_status'] + '_no_replay:' + receipt['call_id'])
        if (self.output / 'mcp_stop').exists():
            raise LibraryStopped('official_MCP_connection_stopped_reconnect_in_Codex')
        call, _ = self.state.begin_call(name, request, repair_of=repair_of)
        response_path = self.queue / (call['id'] + '.response.json')
        write_json(self.queue / (call['id'] + '.request.json'),
                   dict(job_id=call['id'], tool='text_completion', arguments=request['arguments']))
        if self.provider == 'official_vision_mcp_in_opencode':
            # Use the supported agent's text model, with tools disabled. No
            # picture/video, script draft, or MCP vision request enters this check.
            import subprocess
            import tempfile
            from .opencode_provider import opencode_configuration, PROVIDER, TEXT_MAX_OUTPUT_TOKENS
            config = opencode_configuration(self.package_root, self.output,
                platform=self.state.input_lock['configuration'].get('vision_platform', 'ZHIPU'))
            config.update(mcp={}, permission={'*': 'deny'}, agent={'silent-check': dict(
                mode='primary', steps=1, prompt='Return only the requested JSON object. Use no tools.',
                permission={'*': 'deny'})})
            config['provider']['zhipuai-coding-plan']['models']['glm-5.3-flash']['limit']['output'] = TEXT_MAX_OUTPUT_TOKENS
            write_json(self.output / 'calls' / call['id'] / 'text_generation.json',
                       dict(agent_model=PROVIDER['agent_model'], max_output_tokens=TEXT_MAX_OUTPUT_TOKENS))
            env = {key: value for key, value in os.environ.items() if not key.startswith('OMNI_LIBRARY_')}
            env['OPENCODE_CONFIG_CONTENT'] = json.dumps(config)
            log = self.output / 'calls' / call['id'] / 'agent_events.jsonl'
            try:
                with tempfile.TemporaryDirectory(prefix='silent-check-') as work, log.open('wb') as stream:
                    # The bun-built binary leaks one /tmp/.<hex>-00000000.so per run; keep its temp dir inside the per-call cwd.
                    env.update(TMPDIR=work, TMP=work, TEMP=work)
                    prompt = request['arguments']['prompt']
                    if len(prompt.encode('utf-8')) > TEXT_ARGV_MAX_BYTES:
                        # OpenCode already supports agent prompts in opencode.json
                        # (the vision adapter uses the same configuration). Keep
                        # the full text out of argv AND environment strings.
                        config['agent']['silent-check']['prompt'] += '\n' + prompt
                        write_json(Path(work) / 'opencode.json', config)
                        env.pop('OPENCODE_CONFIG_CONTENT', None)
                        env['OPENCODE_CONFIG'] = str(Path(work) / 'opencode.json')
                        # Server smoke test 2026-11-02: the vague English cue let the model
                        # summarise instead of answering (0/1); this explicit cue returned
                        # the requested JSON for a 151 KB config-file prompt (2/2).
                        prompt = '请严格执行系统指令末尾的任务，只返回其中要求的JSON对象，不要任何其他文字，不要调用工具。'
                    try:
                        subprocess.run([self.executable, 'run', '--pure', '--format', 'json', '--agent', 'silent-check',
                            '--model', PROVIDER['agent_model'], '--title', call['id'], prompt],
                            cwd=work, env=env, stdin=subprocess.DEVNULL, stdout=stream, stderr=stream,
                            check=True, timeout=self.timeout_s)
                    except OSError as error:
                        # run() binds `process` only after Popen succeeds. An
                        # OSError from communicate/wait/cleanup is still unknown;
                        # only creation failures prove that nothing was sent.
                        if log.stat().st_size or any(frame.f_globals.get('__name__') == 'subprocess' and
                               frame.f_code.co_name == 'run' and
                               getattr(frame.f_locals.get('process'), 'pid', None) is not None
                               for frame, _ in traceback.walk_tb(error.__traceback__)):
                            raise
                        self.state.fail_call(call, 'text_check_launch_failed:' + str(error), uncertain=False)
                        raise LibraryStopped('text_check_launch_failed:' + call['id']) from error
                # Preserve successful text-only CLI receipts from older agents;
                # an empty answer needs an explicit completed step.
                reply = text_agent_reply(log, require_completed=False)
                if reply is None:
                    raise ValueError('text_check_no_completed_reply')
            except LibraryStopped:
                raise
            except Exception as error:
                try:
                    reply = text_agent_reply(log)
                except (OSError, ValueError, TypeError):
                    reply = None
                if reply is None:
                    self.state.fail_call(call, type(error).__name__, uncertain=True)
                    raise LibraryStopped('text_check_outcome_unknown_no_replay:' + call['id']) from None
            finally:
                if log.exists():
                    secret = os.environ.get('Z_AI_API_KEY')
                    if secret:
                        log.write_bytes(log.read_bytes().replace(secret.encode(), b'[REDACTED]'))
            write_json(response_path, reply)
        else:
            deadline = time.monotonic() + self.timeout_s
            while not response_path.exists():
                if time.monotonic() > deadline:
                    raise LibraryStopped('MCP_wait_timed_out_inspect_original_job_no_replay:' + call['id'])
                time.sleep(.25)
            reply = _read(response_path)
            if reply.get('status') != 'complete':
                self.state.fail_call(call, 'text_check_failed', uncertain=reply.get('status') == 'unknown')
                raise LibraryStopped('text_check_failed_no_replay:' + call['id'])
        self.state.complete_call(call, reply, usage=reply.get('usage', {}))
        return call, reply

    def call(self, name, prompt, media, validator, *, image=False, scope=None):
        media = Path(media).resolve(strict=True) if media is not None else None
        if media is not None and media.stat().st_size >= 8_000_000:
            raise ValueError('official_mcp_media_limit_8mb')
        argument = 'image_source' if image else 'video_source'
        original = {'tool': 'analyze_image' if image else 'analyze_video',
                    'arguments': {argument: str(media), 'prompt': prompt},
                    'media_sha256': sha256_file(media), 'provider': self.provider,
                    'policy_version': prompts.POLICY_VERSION} if media is not None else {
                        'tool': 'text_completion', 'arguments': {'prompt': prompt},
                        'provider': self.provider, 'policy_version': prompts.POLICY_VERSION}
        # Preserve the exact digest of already paid original work. New independent
        # jobs carry a measured source scope; changing encoding cannot replay an
        # unknown observation of that same original source interval.
        self.state._reload()
        recorded = any(c['request_sha256'] == json_sha(original) for c in self.state.data['calls'])
        if media is not None and scope is None and not recorded and self.state.data.get('continuation_policy'):
            lineage_path = media.parent / 'lineage.json'
            if lineage_path.exists():
                lineage = _read(lineage_path)
                scope = {k:lineage[k] for k in ('kind','source_sha256','source_start_s','source_end_s')}
            else:
                scope = {'kind':'complete_file','source_sha256':original['media_sha256'],
                         'source_start_s':0,'source_end_s':probe_media(media)['duration_s']}
        if scope is not None:
            lineage_path = media.parent / 'lineage.json'
            if lineage_path.exists() and scope_fingerprint(scope) != scope_fingerprint(_read(lineage_path)):
                raise LibraryStopped('observation_scope_does_not_match_actual_media_lineage')
            original['observation_scope'] = scope
        prior_stage = next((c for c in self.state.data['calls'] if c['name'] == name and not c.get('repair_of')),None)
        content_upgrade = (media is None and name.startswith('silent_review_') and
            self.state.input_lock['configuration'].get('creative_style') == 'picture_aware' and
            '"content_check":true' in prompt)
        length_upgrade = (re.fullmatch(r'plan_\d+', name) and
            self.state.input_lock['configuration'].get('creative_style') == 'picture_aware' and
            prompts.PICTURE_AWARE_EDIT_LENGTH_MARKER in prompt)
        if content_upgrade or length_upgrade:
            # Prefer a prior call that already carries today's request bytes.
            prior_stage = next((c for c in self.state.data['calls'] if c['name'] == name and
                not c.get('repair_of') and c['request_sha256'] == json_sha(original)), prior_stage)
        if prior_stage:
            saved = _read(self.output/'calls'/prior_stage['id']/'request.json')
            if (json_sha(saved) != prior_stage['request_sha256'] or saved.get('media_sha256') != original.get('media_sha256')
                    or saved['tool'] != original['tool']):
                raise LibraryStopped('paid_stage_input_changed_use_original_artifacts:' + name)
            if ((content_upgrade and '"content_check":true' not in saved['arguments']['prompt'] or
                    length_upgrade and prompts.PICTURE_AWARE_EDIT_LENGTH_MARKER not in saved['arguments']['prompt']) and
                    prior_stage['status'] == 'received' and not
                    (self.output/'calls'/prior_stage['id']/'protocol_failure.json').exists()):
                # A received legacy reply without a review report cannot pin the
                # new roles protocol to its old prompt, and a legacy plan prompt
                # cannot pin the edit-length budget it never stated. Unknown
                # calls stay pinned.
                prior_stage = None
            else:
                original = saved
                prompt = saved['arguments']['prompt']
                validator = _kept_prompt_validator(validator, prompt)
        request = original
        repair_limit = 1
        if self.provider == 'official_vision_mcp_in_opencode':
            repair_limit = 2
        elif self.state.data.get('artifacts', {}).get('server_one_chain_e2e_authorization'):
            from . import server_chain_authorization
            if (server_chain_authorization.allowed_stage(name) and
                    server_chain_authorization.load(self.output) is not None):
                repair_limit = 2
        if prior_stage:
            prior_repair = next((c for c in self.state.data['calls']
                                 if c.get('repair_of') == prior_stage['id']), None)
            if prior_repair:
                failure_path = self.output / 'calls' / prior_stage['id'] / 'protocol_failure.json'
                # Old one-repair runs keep their original stopping boundary.
                saved_repair = _read(self.output / 'calls' / prior_repair['id'] / 'request.json')
                tail = saved_repair['arguments']['prompt'][len(prompt):]
                offset = tail.find('{')
                try:
                    repair_feedback = json.loads(tail[offset:]) if offset >= 0 else {}
                except ValueError:
                    repair_feedback = {}
                current_repair = (isinstance(repair_feedback, dict) and
                    repair_feedback.get('repair_attempt') == 1 and isinstance(repair_feedback.get('violations'), list))
                if not current_repair and (not failure_path.exists() or _read(failure_path).get('repair_limit', 1) == 1):
                    repair_limit = 1
        parent = None
        for attempt in range(repair_limit + 1):
            expected_name = name + ('_repair' if attempt == 1 else '_repair2' if attempt == 2 else '')
            call, reply = (self._submit_text if media is None else self._submit)(expected_name, request, repair_of=parent)
            historical_cache = call['name'] != expected_name
            result_text = '\n'.join(c['text'] for c in reply['result']['content'] if c.get('type') == 'text')
            parsed_path = self.output / 'calls' / call['id'] / 'parsed.json'
            value = raw_value = None
            json_normalisations = []
            try:
                if contracts.time_normalization_enabled(self.state):
                    raw_value = contracts.parse_model_json(result_text, normalisations=json_normalisations)
                    if json_normalisations:
                        write_json(parsed_path.with_name('json_normalisation.json'), json_normalisations)
                    if parsed_path.exists():
                        value = contracts.recorded_view(raw_value, _read(parsed_path), validator,
                            changed='recorded_parsed_reply_modified:' + call['id'],
                            incompatible='recorded_reply_incompatible_with_current_contract:' + call['id'])
                    else:
                        value = contracts.normalized_view(raw_value, validator)
                        if historical_cache or parsed_path.with_name('protocol_failure.json').exists():
                            raise LibraryStopped('historical_reply_requires_explicit_derived_binding:' + call['id'])
                        write_json(parsed_path, raw_value)
                else:
                    value = contracts.parse_model_json(result_text, normalisations=json_normalisations)
                    if json_normalisations:
                        write_json(parsed_path.with_name('json_normalisation.json'), json_normalisations)
                    raw_value = deepcopy(value)
                    validator(value)
                    if parsed_path.exists():
                        saved_value = _read(parsed_path)
                        if saved_value != raw_value:
                            # Earlier code saved validator-normalized replies. Compare
                            # both using today's same deterministic validator.
                            try:
                                validator(saved_value)
                            except (ValueError, TypeError, KeyError) as error:
                                if 'empty_after_normalization' in str(error):
                                    raise LibraryStopped('recorded_reply_incompatible_with_current_contract:' +
                                                         call['id'] + ':' + str(error)) from error
                                raise LibraryStopped('recorded_parsed_reply_modified:' + call['id']) from error
                            if ({k: v for k, v in saved_value.items() if k != 'normalization'} !=
                                    {k: v for k, v in value.items() if k != 'normalization'}):
                                raise LibraryStopped('recorded_parsed_reply_modified:' + call['id'])
                    elif historical_cache:
                        raise LibraryStopped('historical_reply_requires_explicit_derived_binding:' + call['id'])
                    else:
                        write_json(parsed_path, raw_value)
                if name == 'reference':
                    selection = dict(call_id=call['id'], reading=value)
                    bindings = self.state.data['artifacts'].get('accepted_reference_reading', [])
                    if not bindings or bindings[-1]['sha256'] != json_sha(selection):
                        self.state.set_artifact('accepted_reference_reading', selection)
                return value
            except (ValueError, TypeError, KeyError) as error:
                if parsed_path.exists() and 'empty_after_normalization' in str(error):
                    raise LibraryStopped('recorded_reply_incompatible_with_current_contract:' +
                                         call['id'] + ':' + str(error)) from error
                failure = {'error': str(error), 'attempt': attempt, 'model_text': result_text,
                           'repair_limit': repair_limit}
                validation_diagnostics = contracts.json_safe(getattr(error, 'diagnostics', None))
                violation = contracts.json_safe(getattr(error, 'violation', None))
                if isinstance(violation, dict):
                    failure['violation'] = violation
                if isinstance(validation_diagnostics, dict):
                    failure['validation_diagnostics'] = validation_diagnostics
                if reply.get('finish_reason') == 'length':
                    self.state._reload()
                    usage = next(c['usage'] for c in self.state.data['calls'] if c['id'] == call['id'])
                    failure['output_limit'] = {'finish_reason': 'length',
                        'completion_tokens': usage.get('completion_tokens'),
                        'reasoning_tokens': (usage.get('completion_tokens_details') or {}).get('reasoning_tokens'),
                        'content_characters': len(result_text)}
                failure_path = self.output / 'calls' / call['id'] / 'protocol_failure.json'
                if historical_cache or failure_path.exists():
                    # Revalidating a paid reply under a later contract must never
                    # rewrite its original parse/failure history.
                    diagnostic = {'stage': name, 'call_id': call['id'],
                        'request_sha256': call['request_sha256'],
                        'response_sha256': call['response_sha256'], **failure}
                    path = self.output / 'artifacts' / 'cached_reply_validation' / (json_sha(diagnostic) + '.json')
                    if not path.exists():
                        write_json(path, diagnostic)
                else:
                    write_json(failure_path, failure)
                if attempt == repair_limit:
                    exhausted = ValueError('model_protocol_repair_exhausted:' + name)
                    if isinstance(violation, dict):
                        exhausted.violation = violation
                    if isinstance(validation_diagnostics, dict):
                        exhausted.diagnostics = validation_diagnostics
                    from .finecut_continuation import accept_exhausted_line_window_plan
                    accepted = accept_exhausted_line_window_plan(self.state, name, exhausted, validator)
                    if accepted is not None:
                        return accepted
                    raise exhausted from error
                parent = call
                self.state._reload()
                repair = next((c for c in self.state.data['calls'] if c.get('repair_of') == call['id']), None)
                if repair:
                    # Preserve the recorded repair's actual prompt and request hash,
                    # even when a new validator reports a different first error.
                    request = _read(self.output / 'calls' / repair['id'] / 'request.json')
                    if json_sha(request) != repair['request_sha256']:
                        raise LibraryStopped('recorded_model_request_modified:' + repair['id'])
                    continue
                if historical_cache:
                    raise LibraryStopped('historical_format_failure_no_new_repair:' + call['id']) from error
                feedback = {'validation_error': str(error), 'previous_response': result_text,
                            'repair_attempt': attempt + 1}
                from .protocol_feedback import diagnose
                context = {**getattr(validator, 'protocol_context', {}),
                           'validation_error': str(error), 'violation': violation}
                if original.get('observation_scope') is not None:
                    context['observation_scope'] = original['observation_scope']
                if not image and media is not None:
                    try:
                        bound = original.get('observation_scope')
                        lineage_path = media.parent / 'lineage.json'
                        if bound is None and lineage_path.exists():
                            bound = _read(lineage_path)
                        if bound is not None:
                            context['observation_scope'] = bound
                        context['media_duration_s'] = (bound['source_end_s'] - bound['source_start_s']
                            if bound is not None else probe_media(media)['duration_s'])
                    except (ValueError, OSError, KeyError, TypeError):
                        pass
                feedback['violations'] = diagnose(name, raw_value, context)
                hint = repair_hint(str(error), result_text, prompt)
                feedback['repair_hint'] = ('只修复violations列出的字段及必要依赖；其余时间、ID和绑定保留原值，'
                    '不要整体改写时间域或新增身份绑定。' + (hint or ''))
                if isinstance(validation_diagnostics, dict):
                    feedback['validation_diagnostics'] = validation_diagnostics
                request = {**original, 'arguments': {**original['arguments'], 'prompt': prompt +
                    '\n上次输出未通过本地协议校验。只修复JSON字段、ID和时间域，不得补造画面证据。' +
                    ('\n上次输出达到生成上限。减少重复论述和长篇理由，优先完整输出所有必需结构；不得删字段或证据。'
                     if reply.get('finish_reason') == 'length' else '') +
                    json.dumps(feedback, ensure_ascii=False)}}
        raise AssertionError('unreachable')


SILENT_WITH_CREATIVE_AUDIO = ('\naudio_mode=silent只表示不使用参考原声和电影原声轨；本次新配音的旁白与对白必须写在'
    'audio_draft里，audio_draft是非空数组，不能省略或写成[]。')


def repair_hint(error, previous, prompt=''):
    """Legacy first-error guidance retained alongside complete violations."""
    if error == 'plan/audio_draft:list_required':
        return ('audio_draft缺失：audio_mode=silent只表示不用原声，新配音文稿仍必须输出。'
                '按reference.strategy.speech_mode补全非空audio_draft，每项含id、text、voice、mode、segment_ids。')
    if (error.startswith(('plan:duration_exceeds_', 'plan:quantized_duration_exceeds_')) and
            prompts.PICTURE_AWARE_EDIT_LENGTH_MARKER in (prompt or '')):
        # Only the edit-length contract ever told the model to cut to a budget;
        # the flat legacy ceiling keeps its historical repair bytes.
        return ('成片总时长超过本次上限：删去不推进主旨的冗余镜头，或缩短镜头与定格，'
                '使sum((source_out_s-source_in_s)/speed)+定格的总和连同逐帧取整结果都不超过'
                '提示中给出的秒数；不要靠提高speed压缩到看不清，也不要删掉讲清主旨的关键镜头。')
    if error.endswith('evidence:invalid_or_out_of_bounds') or error.endswith('editing_method:invalid_or_out_of_bounds'):
        return ('证据时间越界：每项需0≤start_s<end_s≤参考实际时长（以提示里的时长为准，可带小数），'
                '不要把结尾向上取整到超过时长的整秒。')
    if error == 'plan:dialogue_speaker_not_in_selected_segments':
        try:
            plan = json.loads(previous)
            shown = {}
            for row in plan.get('segments', []):
                for role in row.get('role_ids', []):
                    shown.setdefault((row.get('window_id'), role), []).append(row.get('segment_id'))
            bad = []
            for line in plan.get('audio_draft', []):
                speaker = line.get('speaker') or {}
                if line.get('mode') != 'dialogue':
                    continue
                visible = shown.get((speaker.get('window_id'), speaker.get('role_id')), [])
                if not set(visible) & set(line.get('segment_ids', [])) and not line.get('identity_anchor_segment_id'):
                    bad.append(f"{line.get('id')}（说话者{speaker.get('role_id')}只出现在{visible or '无入选镜头'}）")
        except (ValueError, TypeError, AttributeError):
            bad = []
        return ('dialogue的说话者必须在本句segment_ids的镜头中可见，或用identity_anchor_segment_id指向当前或更早已出现该说话者的镜头；'
                '也可把该句改为narration，或改用说话者已出现的镜头。' + (('问题句：' + '；'.join(bad)) if bad else ''))
    return None


def _catalog(paths, output):
    saved = Path(output) / 'inventory.json'
    if saved.exists():
        catalog = _read(saved)
        actual = {str(p.resolve()) for p in Path(paths).iterdir()
                  if p.is_file() and p.suffix.lower() in MEDIA_EXTENSIONS} if Path(paths).is_dir() else {str(Path(paths).resolve())}
        if {s['path'] for s in catalog['sources']} != actual:
            raise LibraryStopped('library_file_set_changed')
        for source in catalog['sources']:
            verify_source(source)
        return catalog
    return inventory_sources(paths, output)


def _status(output, stage, **details):
    value = {'stage': stage, 'at_unix': time.time(), **details}
    write_json(Path(output) / 'current_status.json', value)
    print(json.dumps(value, ensure_ascii=False), flush=True)


def _unknown_window(source, start, end, inputs):
    return next((row for row in inputs if row['scope'].get('source_sha256') == source['sha256'] and
        row['scope'].get('source_start_s') == start and row['scope'].get('source_end_s') == end), None)


def _adaptive_coarse(state, glm, sources, reference_reading, cache, *, frames, span_s, prompt_module=None,
                     unknown_inputs=(), visual_only=False):
    """Overview then model-selected zooms, with old observations kept as evidence.

    This strategy is recorded separately; it cannot reset locked inputs or usage.
    Completed observations are recovered from hashed raw model replies, not edited
    index summaries. A pending original call must settle before this function runs.
    """
    templates = prompt_module or prompts
    output = state.output
    coarse, failures, by_range, valid_sheets = [], [], {}, {}
    last_valid_sheet = None
    source_map = {s['source_id']:s for s in sources['sources']}
    state._reload()
    for call in state.data['calls']:
        if call['status'] == 'uncertain':
            request = _read(output/'calls'/call['id']/'request.json')
            media = request.get('arguments',{}).get('image_source') or request.get('arguments',{}).get('video_source')
            lineage_path = Path(media).parent/'lineage.json' if media else None
            lineage = _read(lineage_path) if lineage_path and lineage_path.exists() else {}
            failures.append({'call_id':call['id'],'status':'unobserved_unknown_paid_result',
                'source_id':lineage.get('source_id'),
                'requested_coverage_s':[lineage.get('source_start_s'),lineage.get('source_end_s')]})
        if call['status'] != 'received' or call['name'] == 'coarse_zoom_search' or not call['name'].startswith(('coarse_', 'overview_', 'zoom_')):
            continue
        folder = output / 'calls' / call['id']
        request, reply = _read(folder / 'request.json'), _read(folder / 'response.json')
        if json_sha(request) != call['request_sha256']:
            raise LibraryStopped('recorded_model_request_modified:' + call['id'])
        if json_sha(reply) != call['response_sha256']:
            raise LibraryStopped('recorded_model_reply_modified:' + call['id'])
        media = request['arguments'].get('image_source')
        if not media:
            continue
        lineage = _read(Path(media).parent / 'lineage.json')
        if visual_only and lineage.get('source_visual_policy') != 'silent_subtitle_masked':
            continue
        if sha256_file(media) != request['media_sha256']:
            raise LibraryStopped('recorded_coarse_image_modified:' + call['id'])
        if any(lineage[k] != lineage['spec'][k] for k in ('kind','source_sha256','source_start_s','source_end_s')):
            raise LibraryStopped('recorded_coarse_lineage_spec_modified:' + call['id'])
        try:
            model_text = '\n'.join(c['text'] for c in reply['result']['content'] if c.get('type') == 'text')
            value = contracts.parse_model_json(model_text)
            contracts.normalize_coarse_coverage(value, lineage['source_id'],
                [lineage['source_start_s'], lineage['source_end_s']])
            contracts.validate_coarse(value, sources)
            if (value['source_id'] != lineage['source_id'] or value['coverage_s'] !=
                    [lineage['source_start_s'],lineage['source_end_s']] or
                    lineage['source_sha256'] != source_map[value['source_id']]['sha256']):
                raise ValueError('coarse_reply_does_not_match_original_media')
            if any(min(abs(event['timestamp_s']-frame['source_time_s']) for frame in lineage['frames']) > 1.0
                   for event in value['events']):
                raise ValueError('legacy_coarse_event_not_at_sample_time')
        except (ValueError, TypeError, KeyError) as error:
            failures.append({'call_id':call['id'],'status':'unobserved_protocol_failure','error':str(error)})
            continue
        key = (value['source_id'],*value['coverage_s'])
        by_range[key] = value
        valid_sheets[key] = lineage
        last_valid_sheet = lineage
    coarse = list(by_range.values())

    def observe(source, start, end, name, phase):
        nonlocal last_valid_sheet
        from .content_ranges import content_range_for
        bounds = content_range_for(source)
        if bounds:
            start, end = max(start, bounds[0]), min(end, bounds[1])
            if start >= end:
                return None
        key = (source['source_id'],start,end)
        blocked = _unknown_window(source, start, end, unknown_inputs)
        if blocked:
            failures.append({'call_id': blocked['call_id'], 'source_id': source['source_id'],
                'requested_coverage_s': [start, end], 'status': 'unobserved_unknown_paid_result'})
            return None
        if key in by_range:
            last_valid_sheet = valid_sheets[key]
            return last_valid_sheet
        sheet = create_contact_sheet(source,start,end,cache,frame_count=frames)
        if visual_only:
            masks = _source_subtitles(glm, source, sheet, image=True)
            sheet = create_contact_sheet(source,start,end,cache,frame_count=frames,subtitle_masks=masks)
        if any(f.get('status') == 'unobserved_unknown_paid_result' and
               f.get('source_id') == source['source_id'] and f.get('requested_coverage_s') == [start,end]
               for f in failures):
            return sheet  # Never resubmit an unknown page, including on resume.
        _status(output,phase,source_id=source['source_id'],source_range=[start,end],requests=state.usage()['requests'])
        known = {r['role_id']:r for c in coarse if c['source_id'] == source['source_id'] for r in c['roles']}
        context = {'reference':reference_reading,'known_roles':list(known.values()),
                   'source_filename_metadata':Path(source['path']).name,
                   'evidence_limit':'Only sampled instants observed; gaps and continuous actions remain unknown.'}
        if visual_only:
            context.pop('source_filename_metadata')
            context['source_visual_policy'] = 'silent_subtitle_masked'
        def validate(value):
            contracts.normalize_coarse_coverage(value, source['source_id'], [start, end])
            contracts.validate_coarse(value,sources)
            if value['source_id'] != source['source_id'] or value['coverage_s'] != [start,end]:
                raise ValueError('coarse_does_not_match_requested_page')
            if any(min(abs(event['timestamp_s']-f['source_time_s']) for f in sheet['frames']) > 1.0
                   for event in value['events']):
                raise ValueError('coarse_event_not_at_provided_sample_time')
        try:
            coarse_prompt = templates.coarse_prompt(source['source_id'],[start,end],
                [{k:f[k] for k in ('frame_id','source_time_s')} for f in sheet['frames']],context)
            if prompt_module is None:
                coarse_prompt += '\ncoverage_s必须原样照抄'+json.dumps([start,end])+'，它是请求范围，不能替换为首尾抽样帧时间。只选最多6个有导航价值的可见瞬间，每条一句话最多60字，人物最多8个，未知最多4项。不要逐帧长篇分析。'
            if visual_only:
                coarse_prompt += '\n' + REINTERPRET_POLICY
            value = glm.call(name,coarse_prompt,
                sheet['path'],validate,image=True,
                scope={k:sheet[k] for k in ('kind','source_sha256','source_start_s','source_end_s')})
        except (ValueError,LibraryStopped) as error:
            if not (str(error).startswith('model_protocol_repair_exhausted:') or str(error).startswith('official_MCP_failure:')):
                raise
            failure = {'source_id':source['source_id'],'requested_coverage_s':[start,end],
                       'status':'unobserved_protocol_failure','error':str(error)}
            failures.append(failure)
            state.set_artifact('coarse_protocol_failure',failure)
        else:
            coarse.append(value)
            by_range[key] = value
            valid_sheets[key] = sheet
            last_valid_sheet = sheet
        write_json(output/'coarse_index.json',coarse)
        write_json(output/'coarse_failures.json',failures)
        return sheet

    for source in sources['sources']:
        sheet = observe(source,0,source['duration_s'],'overview_'+source['source_id'][-8:],'global_overview')
    if last_valid_sheet is None:
        raise LibraryStopped('no_valid_coarse_evidence_available_for_search')
    compact = {'sources':[{k:s[k] for k in ('source_id','sha256','duration_s','audio_stream_index')}
                         | ({'content_range': s['content_range']} if 'content_range' in s else {})
                         | {'filename':Path(s['path']).name} for s in sources['sources']]}
    if visual_only:
        for item in compact['sources']:
            item.pop('filename', None)
    zoom_context = {'reference':reference_reading,'catalog':compact,'coarse_index':coarse,
                    'coarse_failures':failures,'max_windows':4,'max_window_s':span_s}
    zoom_prompt = (prompts.BASE + '这些概览间隔很大，仅用于导航。围绕固定参考，选择最有必要进一步抽样的区域，'
        '用于定位角色事件及支持连续精看。全库最多4个区域，每个区域不超过'+str(span_s)+'秒。'
        '不要重复已经提供的相同coverage；缺乏证据时可探索未观察区域。这里尚不输出剪辑入出点。'
        '只返回JSON {"reason":"需要展开哪些缺项","windows":[{"source_id":"ID","start_s":0,"end_s":600,'
        '"question":"要确认什么","role_ids":["candidate_A"]}]}\n'+json.dumps(zoom_context,ensure_ascii=False))
    if prompt_module is not None:
        zoom_prompt = templates.zoom_search_prompt(zoom_context, max_regions=4, span_s=span_s)
    if visual_only:
        zoom_prompt += '\n' + REINTERPRET_POLICY
    zoom_media = last_valid_sheet
    state._reload()
    prior_zoom = next((c for c in state.data['calls'] if c['name'] == 'coarse_zoom_search' and not c.get('repair_of')),None)
    if prior_zoom:
        prior_request = _read(output/'calls'/prior_zoom['id']/'request.json')
        zoom_media = _read(Path(prior_request['arguments']['image_source']).parent/'lineage.json')
    _status(output,'choosing_coarse_regions',requests=state.usage()['requests'],valid_sparse_pages=len(coarse))
    try:
        zoom = glm.call('coarse_zoom_search',zoom_prompt,zoom_media['path'],
                        lambda v:contracts.validate_search(v,sources,max_windows=4,max_window_s=span_s),image=True,
                        scope={k:zoom_media[k] for k in ('kind','source_sha256','source_start_s','source_end_s')})
    except ValueError as error:
        if not str(error).startswith('model_protocol_repair_exhausted:'):
            raise
        state.set_artifact('coarse_zoom_protocol_failure',{'error':str(error),
            'action':'Request continuous windows directly from verified sparse overviews; no invented regional decision.'})
        zoom = {'windows':[]}
    else:
        write_json(output/'coarse_zoom_search.json',zoom)
    for requested in zoom['windows']:
        key = json_sha(requested)[:16]
        sheet = observe(source_map[requested['source_id']],requested['start_s'],requested['end_s'],
                        'zoom_'+key,'query_driven_coarse')
    planning_image = (create_navigation_sheet(list(valid_sheets.values()), cache)['path']
                      if len(valid_sheets) > 1 else last_valid_sheet['path'])
    return coarse,failures,planning_image


def execute(reference, library, output, *, span_s=600, frames=18, max_fine=16, max_requests=80,
            asr=True, editing_v2=False, semantic_audit=False, active_finecut=False,
            model_factory=None, provider_config=None, registry_path=None, reference_seed=None,
            failure_report_name='failure.json', prompt_module=None, render_fn=None, asr_model_dir=None,
            slice_observation_prompt=None, slice_observation_validator=None):
    templates = prompt_module or prompts
    if failure_report_name not in {'failure.json', 'failure_capacity_recovery_v1.json',
                                  'failure_remaining_candidate_v1.json', 'failure_interval_resume_v1.json', 'failure_schema_resume_v1.json',
                                  'failure_plan_cast_correction_v1.json','failure_plan_cast_transport_v1.json',
                                  'failure_plan_binding_repair_v1.json','failure_caption_reflow_repair_v1.json',
                                  'failure_tts_audio_resume_v1.json'}:
        raise ValueError('unsupported_failure_report_name')
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    sources = _catalog(library, output / 'catalog')
    ref_catalog = _catalog(reference, output / 'reference_catalog')
    ref = ref_catalog['sources'][0]
    source_map = {s['source_id']: s for s in sources['sources']}
    config = {'span_s': span_s, 'frames': frames, 'max_fine': max_fine, 'asr': asr,
              'max_rounds': 2, 'max_renders': 2, 'provider': 'official_vision_mcp_in_codex'}
    if provider_config is not None:
        if model_factory is None:
            raise ValueError('provider_configuration_requires_model_factory')
        config.update(provider_config)
    validate_creative_style(config)
    picture_first = config.get('creative_style') == 'picture_first'
    picture_aware = config.get('creative_style') == 'picture_aware'
    picture_review = picture_first or picture_aware
    if config.get('resume_from') and (not config.get('creative_audio') or
            config.get('material_search') == 'question_first'):
        raise LibraryStopped('resume_from_requires_creative_library_or_film_task')
    if config.get('resume_from'):
        checkpoint = config['resume_from']
        if reference_seed != checkpoint['reference_seed']:
            raise LibraryStopped('resume_from_accepted_reference_seed_required')
        if any(sha256_file(row['path']) != row['sha256'] for row in
               (checkpoint['ledger'], checkpoint['reference_reading'])):
            raise LibraryStopped('resume_from_checkpoint_changed')
    from .plan_normalisation import configure_output_canvas
    if config.get('workflow') == 'reference_rough_skill_v1':
        configure_output_canvas(config, output)
    from .content_ranges import recorded_content_ranges, bind_content_ranges
    if config.get('creative_audio') is True and config.get('visual_library_root'):
        snapshot = recorded_content_ranges(output, config['visual_library_root'], sources, config.get('content_ranges'))
        if snapshot is not None:
            config['content_ranges'] = snapshot
        bind_content_ranges(sources, snapshot)
    qfs_films = None
    if config.get('material_search') == 'question_first':
        from .question_first import configure
        qfs_films = configure(config, sources, output)
    progress_driven = config.get('workflow') == 'reference_rough_skill_v1'
    creative_audio = config.get('creative_audio') is True
    normalize_times = contracts.creative_time_normalization(config)
    if creative_audio:
        config['creative_strategy_policy'] = 'reference_remix_v1'
    if max_fine is None and not progress_driven:
        raise ValueError('unbounded_windows_require_progress_driven_workflow')
    if progress_driven:
        config.update(max_rounds=None, max_renders=None, max_fine=None)
    if reference_seed is not None:
        config['reference_seed_sha256'] = json_sha(reference_seed)
    lock = {'reference_sha256': ref['sha256'], 'library_sources':
            [{'source_id': s['source_id'], 'sha256': s['sha256']} for s in sources['sources']], 'configuration': config}
    state = LibraryState(output, lock, max_requests=max_requests, registry_path=registry_path)
    from . import active_finecut as precision
    active_finecut = precision.enable_policy(state, active_finecut)
    from . import semantic_audit as semantic, semantic_prompts, semantic_pipeline
    semantic_audit = semantic_pipeline.enable_policy(state, semantic_audit or active_finecut)
    editing_v2 = editing_v2 or semantic_audit
    # The input and hard-budget lock stays intact. A forward policy cannot turn
    # old paid plans into new work or authorize an additional render.
    editing_policy = state.data['artifacts'].get('editing_execution_policy')
    if isinstance(editing_policy,list):
        if editing_policy:
            record = editing_policy[-1]
            editing_policy = _read(record['path'])
            if json_sha(editing_policy) != record['sha256']:
                raise LibraryStopped('recorded_editing_execution_policy_modified')
        else:
            editing_policy = None
    if editing_v2 and not editing_policy:
        if any(c['name'].startswith('plan_') for c in state.data['calls']):
            raise LibraryStopped('editing_v2_cannot_reinterpret_existing_plans_or_reset_render_budget')
        editing_policy = {
            'policy':prompts.EDITING_PROTOCOL, 'input_and_hard_budgets_unchanged':True,
            'applies_to':'New plans and their actual-output reviews only.',
            'quality_is_not_implied_by_structural_validation':True,
            'scene_change_threshold_percent':3.0}
        state.set_artifact('editing_execution_policy',editing_policy)
    editing_v2 = bool(editing_policy)
    if editing_v2 and editing_policy.get('policy') != prompts.EDITING_PROTOCOL:
        raise LibraryStopped('unsupported_recorded_editing_execution_policy')
    glm = (model_factory or CodexMCP)(state)
    unknown_inputs = ([*config.get('parent_baseline', {}).get('unknown_inputs', []),
        *getattr(glm, 'exclusions', [])] if progress_driven else [])
    unknown_inputs = list({json_sha(row): row for row in unknown_inputs}.values())
    cache = output / 'media_cache'
    if progress_driven:
        from .visual_story_trial import proxy as visual_proxy
        reference_media = (visual_proxy(ref, 0, ref['duration_s'], cache,
            label='clean_reference_full')['path'] if reference_seed is None else None)
    else:
        reference_media = reference if Path(reference).stat().st_size < 8_000_000 else prepare_window(
            ref,0,ref['duration_s'],cache,fps=12)['path']
    model = None
    def transcript(source, start, end, *, reference_speech=False):
        nonlocal model
        if not (asr or reference_speech) or source['audio_stream_index'] is None:
            return {'status': 'not_requested'}
        from .asr import transcribe_window
        if model is None:
            from faster_whisper import WhisperModel
            model = WhisperModel(str(asr_model_dir) if asr_model_dir else 'small', device='cpu', compute_type='int8',
                                 cpu_threads=int(os.environ.get('OMNI_ASR_THREADS', '4')),
                                 download_root=str(output.parent / 'library_models'))
        try:
            return transcribe_window(source, start, end, output / 'asr_cache', model=model)
        except ValueError as error:
            # Speech is optional. Reject failed alignment rather than inventing
            # words/times; cache integrity or changed inputs remain fatal.
            if not str(error).startswith(('asr_timestamp_', 'asr_segment_', 'asr_word_timestamp_')):
                raise
            rejected = {'status':'rejected_local_asr_alignment','error':str(error),
                        'source_id':source['source_id'],'source_range':[start,end],
                        'segments':[],'evidence_limit':'No usable speech evidence supplied.'}
            state.set_artifact('local_asr_alignment_failure',rejected)
            return rejected

    try:
        _status(output, 'reference_observation', requests=state.usage()['requests'])
        ref_asr = (transcript(ref, 0, ref['duration_s'], reference_speech=creative_audio)
                   if reference_seed is None or config.get('reference_storyboard') == 'v1' else
                   {'status': 'not_requested', 'reason': 'received_reference_reused'})
        write_json(output / 'reference_asr.json', ref_asr)
        def validate_current_reference(value):
            # Only this fresh reply (and its _repair revalidation) must carry the
            # value fields; reloaded readings from older runs may predate them.
            contracts.validate_reference(value, ref['sha256'], ref['duration_s'],
                                         require_value_fields=True)
            if creative_audio and not all(key in value.get('strategy', {}) for key in
                    ('adaptation_mode', 'speech_mode', 'timing_driver')):
                raise ValueError('creative_reference_speech_strategy_required')
            if creative_audio:
                contracts.validate_creative_reference_strategy(value,
                    music_bed_configured=config.get('audio_music_bed') is not None,
                    override=config.get('reference_strategy'))
        validate_current_reference.protocol_context = dict(stage='reference', reference_sha=ref['sha256'],
            duration_s=ref['duration_s'], require_value_fields=True)
        if reference_seed is None:
            reference_prompt = templates.reference_prompt(ref['sha256'], ref['duration_s']) + \
                '\n本地ASR只提供自动识别的语音文字与时间，未核验；不证明音乐、音色或说话者身份：' + json.dumps(ref_asr, ensure_ascii=False)
            if config.get('reference_context'):
                reference_prompt += ('\n参考来源上下文（热点标题、作者发布文字及已收到的多作品比较）；'
                    '这些是理解玩法/双关的线索，不是画面事实或指定主旨。结合实际参考独立判断共同表达，'
                    '保留不确定项，不把幽默双关自动当成真实操作知识问题：' +
                    json.dumps(config['reference_context'], ensure_ascii=False))
                if config['reference_context'].get('received_av_observation'):
                    reference_prompt += ('\nreceived_av_observation来自同一参考的实际音画听看，'
                        '可补充本次无声代理与自动ASR的缺口，但仍保留模型不确定性与相互矛盾。'
                        '区分角色对白、旁白、演唱歌词和纯音乐，判断声音怎样推动动作、情绪或笑点。'
                        '不要把歌曲或歌词自动变成长篇旁白故事，也不要把低置信ASR当成确定台词。')
            if creative_audio:
                reference_prompt += ('\n同时输出strategy对象：adaptation_mode=reinterpret，speech_mode从'
                    'narration/dialogue/mixed/none中选择，timing_driver从speech/music/action中选择。'
                    '依据本参考的表达方式判断新作品适合如何叙述；无语音参考不必强配语音。'
                    '另输出speech_form，简述叙述视角、角色发言关系以及旁白的作用，不能只记录有无语音。'
                    '另输出visual_form={description,evidence}：description说明画面如何通过动作、反应、'
                    '物件、环境或持续表演推进表达，evidence引用参考内实际start_s/end_s与observed_fact。'
                    '长镜头、快切和声画互补均按本参考判断，不规定切镜频率或镜头种类配额。'
                    '主旨与观众感受用现有theme/intended_takeaway表达实际内容、态度、情绪或笑点，'
                    '不要仅以跳舞、叠字幕、快切等表达方法代替主旨；表达方法另记visual_form。'
                    '人物、数额和镜头数量不自动固定。')
                reference_prompt += '\n本次新作品的策略执行合同：' + json.dumps(
                    contracts.creative_reference_strategy_contract(
                        music_bed_configured=config.get('audio_music_bed') is not None), ensure_ascii=False)
                reference_prompt += ('\n本次素材是异源电影库，不会出现参考中的原人物、原场地或同款衣服。'
                    '在theme/intended_takeaway中区分可迁移的表达目标与原作的具体实例；'
                    '窗景、真人/动画、服装颜色应作为观察事实保留，不自动列为新作品必须复刻的目标。'
                    '仍须明确核心动作、变化、节奏或笑点的作用，不能只留下国风、热血等泛泛情绪；'
                    '也不能把没有真实动作证据的氛围拼贴当成已迁移。')
            if progress_driven:
                reference_prompt += ('\nMedia transfer limit: the normal-speed visual proxy physically omits audio '
                    'and covers the complete reference timeline; native cloud frame sampling and audio craft '
                    'remain unverified.')
            reference_reading = glm.call('reference', reference_prompt,
                reference_media, validate_current_reference,
                **({'scope': {'kind': 'continuous_window', 'source_sha256': ref['sha256'],
                    'source_start_s': 0, 'source_end_s': ref['duration_s']}} if progress_driven else {}))
        else:
            reference_reading = reference_seed['full_response']['reference']
            if normalize_times:
                reference_reading = contracts.recorded_view(reference_reading, reference_reading,
                    lambda value: contracts.validate_reference(value, ref['sha256'], ref['duration_s']),
                    changed='recorded_reference_seed_changed')
            else:
                contracts.validate_reference(reference_reading, ref['sha256'], ref['duration_s'])
            state.set_artifact('server_cached_reference_navigation', reference_seed)
            selection = dict(call_id=reference_seed.get('source_call_id'), seed_sha256=json_sha(reference_seed),
                             reading=reference_reading)
            bindings = state.data['artifacts'].get('accepted_reference_reading', [])
            if not bindings or bindings[-1]['sha256'] != json_sha(selection):
                state.set_artifact('accepted_reference_reading', selection)
        if creative_audio:
            override = config.get('reference_strategy')
            if override:
                reference_reading = {**reference_reading, 'strategy': dict(override)}
                if config.get('reference_speech_form'):
                    reference_reading['speech_form'] = config['reference_speech_form']
                contracts.validate_reference(reference_reading, ref['sha256'], ref['duration_s'])
                state.set_artifact('reference_strategy_user_correction', override)
            elif not reference_reading.get('strategy'):
                raise LibraryStopped('cached_reference_speech_strategy_required_no_narration_default')
        strategy = (contracts.validate_creative_reference_strategy(reference_reading,
            music_bed_configured=config.get('audio_music_bed') is not None) if creative_audio else
            contracts.reference_strategy(reference_reading))
        if creative_audio and strategy['timing_driver'] == 'music':
            from .visual_audio import music_source
            soundtrack = music_source(ref['path'], {'reference': reference_reading}, config.get('audio_music_bed'))
            reference_reading = {**reference_reading, 'audio_delivery': dict(
                kind='reference_original_soundtrack' if soundtrack == Path(ref['path']).resolve() else 'configured_music_bed',
                path=str(soundtrack), sha256=sha256_file(soundtrack),
                original_speed=True, separated_music_stem=False,
                note='This audio is available to the final mix; beat alignment still requires actual output review.')}
        write_json(output / 'reference_reading.json', reference_reading)
        visual_only = creative_audio and strategy['adaptation_mode'] == 'reinterpret'
        editing_reference = None
        if editing_v2:
            reference_timeline = compact_timeline(detect_shot_timeline(ref, cache / 'shot_timelines',threshold=3.0))
            if reference_seed is None:
                editing_reference = glm.call('editing_reference_v2',
                    prompts.editing_reference_prompt(reference_reading, reference_timeline), reference_media,
                    lambda v:contracts.validate_editing_reference(v,ref['sha256'],ref['duration_s'],reference_reading))
            else:
                editing_reference = reference_seed['full_response']['editing_reference']
                contracts.validate_editing_reference(editing_reference, ref['sha256'],ref['duration_s'],reference_reading)
            write_json(output / 'editing_reference_v2.json',editing_reference)
        if not state.data['artifacts'].get('strategy_transition'):
            state.set_artifact('strategy_transition', {
                'strategy':'adaptive_coarse_v2','previous':'fixed_30_page_grid',
                'overview_frames_per_source':frames,'model_selected_regions_max':4,
                'region_max_s':span_s,'fine_soft_cap_per_round':8,
                'input_lock_and_hard_budgets_unchanged':True,
                'unknown_results_excluded_from_evidence':True})
        state.enable_independent_continuation()
        if not state.data['artifacts'].get('theme_transfer_policy'):
            state.set_artifact('theme_transfer_policy', {
                'policy':'same_meaning_different_story_facts_v2',
                'user_requirement':'Reference fixes intended meaning; library may change characters, events and slot structure.',
                'reason':'The first review treated the specific reference protagonist as a mandatory identity match.',
                'scope':'Future prompts and reviews only. Historical reference reading and reviews remain unchanged.',
                'no_forced_quality_verdict':True})
        if not state.data['artifacts'].get('model_context_policy'):
            state.set_artifact('model_context_policy', {
                'policy':'exact_evidence_packing_v2',
                'reason':'First plan input used 178988 prompt tokens; codec metadata and word records were duplicated.',
                'retained':['all fine observations','window and source SHAs','source time mapping','exact ASR segment text and timings'],
                'excluded_from_future_prompts':['codec/container metadata','local cache paths','duplicated ASR word records'],
                'original_records_unchanged':True,'new_requests_only':True})
        navigation_reference = ({**reference_reading,'editing_reference':editing_reference}
                                if editing_v2 else reference_reading)
        if reference_seed is not None:
            navigation_reference = {**navigation_reference,
                'reference_protocol_limit': reference_seed['evidence_limit']}
        visual_seed = config.get('received_visual_seed') if visual_only else None
        windows = (_received_visual_seed(visual_seed, sources, normalize=normalize_times) if visual_seed else
                   _received_windows(state, visual_only=visual_only, normalize=normalize_times) if creative_audio else [])
        if config.get('material_search') in {'question_first', 'film'}:
            _validate_question_first_windows(windows, source_map, output)
        if visual_seed or config.get('material_search') == 'film':
            for window in windows:
                contracts.bind_film_window_entities(window, config.get('material_search'))
            write_json(output / 'watched_windows.json', windows)
        plan_seed = config.get('received_plan_seed') if creative_audio else None
        received_plan = _received_plan_seed(plan_seed) if plan_seed else None
        resume = config.get('resume_from')
        if resume and not windows:
            raise LibraryStopped('resume_from_requires_watched_windows')
        shared_library, library_cards, identity_revision_ids = None, [], []
        library_root = (config.get('visual_library_root')
                        if visual_only and not resume and qfs_films is None and config.get('material_search') != 'film' else None)
        if library_root:
            from .visual_library import VisualLibrary
            shared_library = VisualLibrary(library_root, content_sources=sources)
            previous = state.data['artifacts'].get('shared_visual_library', [])
            if previous:
                saved = _read(previous[0]['path'])
                if (json_sha(saved) != previous[0]['sha256'] or
                        saved['root'] != str(Path(library_root).resolve())):
                    raise LibraryStopped('shared_visual_candidate_snapshot_changed')
                library_cards = saved['candidates']
                identity_revision_ids = saved.get('revision_ids', [])
            else:
                query = _library_query(reference_reading)
                source_shas = [row['sha256'] for row in sources['sources']]
                identity_revision_ids = sorted({ident for sha in source_shas
                    for ident in shared_library.entity_revision_ids(sha)})
                candidate_limit = config.get('visual_library_candidate_limit', 30)
                library_cards = shared_library.search(query, source_sha256s=source_shas,
                    limit=candidate_limit, revision_ids=identity_revision_ids)
                # No lexical hit does not mean no reusable footage. Provide a
                # diverse local page for model selection before re-observation.
                if not library_cards:
                    library_cards = shared_library.search('', source_sha256s=source_shas,
                        limit=candidate_limit, revision_ids=identity_revision_ids)
                current_ids = {row['sha256']: row['source_id'] for row in sources['sources']}
                for card in library_cards:
                    card['source_id'] = current_ids[card['source_sha256']]
                state.set_artifact('shared_visual_library', dict(root=str(Path(library_root).resolve()),
                    query=query, candidates=library_cards, revision_ids=identity_revision_ids,
                    evidence_limit='Conditional uses guide selection, not stored visible facts or a quality pass.'))
        selected_ranges = _received_selection(config, sources) if creative_audio and not resume else []
        qfs_report = None
        if qfs_films is not None:
            from .question_first import QuestionFirstRunner, to_search_result
            qfs_runner = QuestionFirstRunner(state, qfs_films)
            storyboard = (qfs_runner.build_storyboard(reference_reading, ref_asr.get('segments', []),
                ref['duration_s'], ref['path']) if config.get('reference_storyboard') == 'v1' else None)
            qfs_report = (qfs_runner.run(reference_reading, storyboard=storyboard) if storyboard is not None
                          else qfs_runner.run(reference_reading))
            # Carry retrieval gaps to the existing story/AV review through its
            # reference context, including the deferred creative rough review.
            reference_reading = {**reference_reading, 'question_first_gaps': qfs_report['question_first_gaps']}
            if storyboard is not None:
                reference_reading.update(reference_storyboard=storyboard,
                                         storyboard_merges=qfs_report['storyboard_merges'])
            write_json(output / 'reference_reading.json', reference_reading)
            if not state.data['artifacts'].get('question_first_gaps'):
                state.set_artifact('question_first_gaps', qfs_report['question_first_gaps'])
            # A retrieval gap is not a story-quality verdict. Observe eligible
            # footage and let planning/review assess it with all gaps retained.
            # Keep the stop when there is no eligible footage to observe.
            if qfs_report['question_first_gaps'] and not to_search_result(
                    qfs_report, sources, max_windows=None)['windows']:
                raise LibraryStopped('question_first:material_gap')
        if windows:
            first = windows[0]
            sheet = create_contact_sheet(source_map[first['source_id']], first['source_start_s'],
                first['source_end_s'], cache, frame_count=frames,
                subtitle_masks=first.get('source_subtitle_masks') if visual_only else None)
            coarse, coarse_failures, planning_image = [], [], sheet['path']
        elif selected_ranges or qfs_report is not None:
            coarse, coarse_failures, planning_image = [], [], None
        elif library_cards:
            # Selection uses the already understood reference plus compact
            # cards and one received candidate's sparse source navigation.
            card = library_cards[0]
            first = _received_library_window(shared_library.get(card['observation_id']), library_root,
                source=source_map[card['source_id']], revision_ids=identity_revision_ids, normalize=normalize_times)
            sheet = create_contact_sheet(source_map[first['source_id']], first['source_start_s'],
                first['source_end_s'], cache, frame_count=frames,
                subtitle_masks=first.get('source_subtitle_masks'))
            coarse, coarse_failures, planning_image = [], [], sheet['path']
        else:
            coarse, coarse_failures, planning_image = _adaptive_coarse(
                state,glm,sources,navigation_reference,cache,frames=frames,span_s=span_s,
                unknown_inputs=unknown_inputs,
                **({'visual_only': True} if visual_only else {}),
                **({'prompt_module': prompt_module} if prompt_module is not None else {}))
        compact_catalog = {'sources': [{k:s[k] for k in ('source_id','sha256','duration_s','audio_stream_index')}
                                       | ({'content_range': s['content_range']} if 'content_range' in s else {})
                                       | {'filename': Path(s['path']).name} for s in sources['sources']]}
        if visual_only:
            for item in compact_catalog['sources']:
                item.pop('filename', None)
        visual_script, silent_feedback, silent_revision = None, None, 0
        if picture_first:
            script_path = output / 'visual_script.json'
            script_seed = config.get('resume_from', {}).get('visual_script')
            if script_seed:
                if sha256_file(script_seed['path']) != script_seed['sha256']:
                    raise LibraryStopped('resume_from_visual_script_changed')
                visual_script = _read(script_seed['path'])
                write_json(script_path, visual_script)
            elif script_path.exists():
                visual_script = _read(script_path)
            if visual_script is not None:
                contracts.validate_visual_script(visual_script)
        rejected_library_ids, retrieval_feedback, story_slots = set(), None, []
        edit_length_budget = (dict(reference_duration_s=ref['duration_s'],
            edit_max_s=contracts.picture_aware_edit_max_s(ref['duration_s'])) if picture_aware else None)
        plan_duration_cap = (edit_length_budget['edit_max_s'] if picture_aware else
            None if progress_driven else 180)
        if picture_aware and not state.data['artifacts'].get('picture_aware_edit_length_budget'):
            state.set_artifact('picture_aware_edit_length_budget', dict(
                policy=contracts.PICTURE_AWARE_EDIT_LENGTH_POLICY, **edit_length_budget,
                prompt_marker=prompts.PICTURE_AWARE_EDIT_LENGTH_MARKER,
                applies_to='New plan prompts and their validation; recorded replies keep the rule their own prompt stated.'))
        plans = {}
        # The length rule each round's accepted plan was actually judged by, so
        # the rough can record the decision forward instead of tracing it back.
        plan_budgets = {}
        renders = []
        last_review = None
        attempted_windows, executed_plans = set(), set()
        first_round = 0
        if state.data['artifacts'].get('server_remaining_candidate_feedback'):
            from .server_capacity_recovery import remaining_candidate
            continuation = remaining_candidate(output)
            windows = _read(continuation['watched_windows_path'])
            if visual_only:
                windows = [row for row in windows if row.get('source_visual_policy') == 'silent_subtitle_masked'
                           and row.get('source_subtitle_masks') is not None]
            if config.get('material_search') in {'question_first', 'film'}:
                _validate_question_first_windows(windows, source_map, output)
            if config.get('material_search') == 'film':
                for window in windows:
                    contracts.bind_film_window_entities(window, 'film')
                write_json(output / 'watched_windows.json', windows)
            first_round = continuation['remaining_candidate']
            last_review = continuation['feedback']
            if picture_aware:
                silent_revision = first_round
                silent_feedback = last_review
        if qfs_report is not None and first_round > 0:
            from .question_first import recorded_search_result
            if not state.data['artifacts'].get('question_first_search_0'):
                raise LibraryStopped('question_first:resume_requires_pinned_search_0')
            search_zero = recorded_search_result(state, qfs_report, sources, max_windows=0)
            story_slots = deepcopy(search_zero['slots'])
        rounds = count(first_round) if progress_driven else range(first_round, 3 if picture_aware else 2)
        for round_no in rounds:
            observed_before = len(windows)
            previous_review = last_review
            phase_templates = (templates.for_round(round_no)
                               if hasattr(templates, 'for_round') else templates)
            remaining = (state.max_requests - state.usage()['requests']
                         if state.max_requests is not None else None)
            recorded_plan = any(c['name'] == f'plan_{round_no}' for c in state.data['calls'])
            recorded_search = any(c['name'] == f'search_{round_no}' for c in state.data['calls'])
            if (renders and remaining is not None and
                    remaining < (20 if active_finecut else 16 if semantic_audit else 12) and not recorded_plan):
                reservation = {'stage':'revision', 'action':'retain_actual_render',
                    'reason':'Insufficient requests for another watched window, plan and actual-render reviews.',
                    'remaining':remaining}
                if semantic_audit:
                    semantic_pipeline.record_budget_reservation(state,reservation)
                else:
                    state.set_artifact('budget_reservation',reservation)
                break
            window_cap = (None if progress_driven else precision.fine_window_budget(state,round_no,max_fine,len(windows),remaining)
                          if active_finecut else semantic_pipeline.fine_window_budget(state,round_no,max_fine,len(windows),remaining)
                          if semantic_audit else min(8, max_fine-len(windows),
                                                     max(0,(remaining-8)//2) if remaining is not None else 8))
            if recorded_search and not semantic_audit and not progress_driven:
                # Previously submitted searches are recovered through the
                # original request/response, not charged or replanned. A lower
                # remaining budget cannot erase already completed evidence.
                window_cap = min(8,max_fine-len(windows))
            if picture_review and silent_feedback is not None:
                search = {'reason': 'Silent revision uses the same watched windows; no search.', 'windows': []}
            elif qfs_report is not None:
                from .question_first import recorded_search_result
                if round_no == 0:
                    search = recorded_search_result(state, qfs_report, sources, max_windows=window_cap)
                    story_slots = deepcopy(search['slots'])
                else:
                    search = {'reason': 'Question-first selection is complete; review decides material gaps.', 'windows': []}
            elif visual_seed and windows and (received_plan is not None or resume) and round_no == first_round:
                search = {'reason': 'Reuse verified received visual plan.', 'windows': []}
            elif shared_library and round_no == first_round:
                search = None
            elif creative_audio and windows and round_no == first_round:
                search = {'reason': 'Plan directly from received source-bound observations.', 'windows': []}
            elif selected_ranges and round_no == first_round:
                search = {'reason': 'Reuse received range selection, with fresh cleaned visual observation.',
                          'windows': selected_ranges}
            elif window_cap == 0:
                if not windows:
                    raise LibraryStopped('budget_reserved_but_no_fine_window_possible')
                search = {'reason':'No remaining observation budget; plan from existing watched evidence.', 'windows':[]}
            else:
                search = None
            if picture_first and visual_script is None:
                search = None
            _status(output, 'requesting_fine_windows', round=round_no, requests=state.usage()['requests'])
            search_context = {'reference': reference_reading, 'catalog': compact_catalog,
                              'creative_audio': config.get('creative_audio') is True,
                              'coarse_index': coarse, 'already_watched': [_window_context(w, include_speech=not visual_only) for w in windows],
                              'remaining_window_budget': None if progress_driven else max_fine-len(windows),
                              'max_windows_this_round':window_cap, 'coarse_failures':coarse_failures,
                              'previous_review': last_review,
                              'instruction': '最多选择8个精看窗口；第二轮只补具体缺项；不要换参考。'}
            if picture_first:
                search_context.update(creative_style='picture_first', visual_script=visual_script)
            if editing_v2:
                search_context['editing_reference'] = editing_reference
            if shared_library:
                from .visual_library import candidate_groups, compact_candidates
                used = {window.get('shared_visual_observation', {}).get('observation_id') for window in windows}
                search_context['reusable_visual_candidates'] = compact_candidates([card for card in library_cards
                    if card['observation_id'] not in used | rejected_library_ids])
                search_context['rejected_observation_ids'] = sorted(rejected_library_ids)
                search_context['slots'] = story_slots
                search_context['candidate_groups'] = [group for group in candidate_groups(
                    [card for card in library_cards if card['observation_id'] not in rejected_library_ids])
                    if len(group['observation_ids']) > 1]
                if retrieval_feedback:
                    search_context['local_retrieval_feedback'] = {key: retrieval_feedback[key]
                        for key in ('queries', 'visual_needs', 'new_candidate_count') if key in retrieval_feedback}
                search_context['instruction'] = ('先根据参考主旨和visual_form确定各段画面要让观众看见的变化、'
                    '选择或反应，以及声音补充什么，在reason中简述视觉表达路线，再挑选能组成全故事的多个observation_ids。'
                    '同一主旨可通过不同动作、反应、细节或环境组合表达；根据实际素材取舍，不提前锁死逐句对白。'
                    '可同时用windows仅补具体缺项。候选已有received连续观察，不要为读取记录重新看视频。'
                    '潜在用途有成立条件，不能当原画面事实；需联合考虑人物、服装、物件及动作和反应镜头。'
                    'affordances与speech_fit中的主题和风格偏好不是永久限制；新TTS可以改变语境，但不能改变真实人物和动作。'
                    '当前卡不足时用retrieval_queries申请本地查库下一批，写具体动作、互动、表情或物件，'
                    '不要只堆抽象主题词；本轮仍可选现有卡，新召回的卡下一轮才能选。'
                    '不合当前任务的未选卡可列rejected_observation_ids，后续不再提供。'
                    'match_strength=weak_single_character只是单字弱召回，不足以证明主题相符。'
                    '不把可说话等同于最适合选用；反应、动作、物件要为故事增加信息或感受，不能仅为切镜而装饰。'
                    'retrieval_matches记录的是本任务的查询意图，must_have是待核对条件，不证明候选已经满足。'
                    '画面与声音已能共同表达主旨、人物路线明确时retrieval_queries=[]，停止查库和复看。')
            if unknown_inputs:
                search_context['unavailable_observation_ranges'] = [dict(call_id=row['call_id'],
                    status='Result unknown; this original source range must not be replayed.',
                    **row['scope']) for row in unknown_inputs]
            if search is None and shared_library and not picture_first:
                search = _received_library_search(state, f'search_{round_no}', sources,
                    [card for card in library_cards if card['observation_id'] not in rejected_library_ids],
                    max_windows=window_cap, received_windows=windows)
            if search is None:
                search_prompt = templates.search_prompt(search_context)
                if visual_only:
                    search_prompt += '\n' + REINTERPRET_POLICY
                if shared_library:
                    search_prompt += ('\n另外输出observation_ids数组，可引用reusable_visual_candidates内的observation_id'
                        '或already_watched内已确认的window_id；必须逐字复制，不能编造或改写ID。'
                        '多个入选记录会提供完整事件和usable_ranges用于联合计划，不是仅选最高相关的一个。'
                        '同时在现有slots里推进段落任务，每项{slot_id,intended_takeaway,'
                        'coverage:{visual_goal,audio_goal,gaps:[]},observation_ids:[]}。'
                        '第一次建立任务，后续保留或依据真实候选修订；这是最终剪辑的同一组slots，尚不填写切点和台词。'
                        '已入选记录可继续关联段落；candidate_groups仅帮助比较人物组合，不证明同框或同一现场。'
                        '需要更多候选时输出retrieval_queries=[{"visual_goal":"本段画面要表达什么",'
                        '"slot_id":"对应段落ID","visual_path":"一种可替代的镜头表达办法","query":"可见动作、互动或物件检索词",'
                        '"must_have":["成立所需的实际画面条件"]}]与rejected_observation_ids=[]；也兼容短查询字符串。'
                        '可提出不同视觉路径，不只展开同一动作的同义词；数量由当前缺项决定。'
                        '查库只读已收记录，不额外观看视频，已排除卡不能重新选。'
                        '已有窗口够用时可以observation_ids=[]、windows=[]、retrieval_queries=[]；'
                        '具体局部细节不足时才明确question并申请windows补看。'
                        '若需在已收窗口内确认新细节，可在该windows请求加revisit_window_id引用已收窗口，'
                        '范围须在其内部；说明具体未解决问题，不因换主题就重复观察。')
                search_prompt = prompts.picture_first_search_prompt(search_prompt,
                    {**search_context, 'creative_style': 'picture_aware'} if picture_aware else search_context)
                def validate_current_search(value):
                    if shared_library:
                        _validate_library_search(value, sources,
                            [card for card in library_cards if card['observation_id'] not in rejected_library_ids],
                            max_windows=window_cap, received_windows=windows)
                    else:
                        contracts.validate_search(value, sources, max_windows=window_cap,
                            **({'allow_empty': True} if progress_driven else {}))
                    if picture_first:
                        contracts.validate_visual_script(value.get('visual_script'))
                        if visual_script is not None and value['visual_script'] != visual_script:
                            raise ValueError('visual_script:recorded_beats_must_not_change')
                if shared_library:
                    search = glm.call(f'search_{round_no}', search_prompt,
                        planning_image, validate_current_search, image=True)
                else:
                    search = glm.call(f'search_{round_no}', search_prompt, planning_image,
                                      validate_current_search, image=True)
            if picture_first and visual_script is None:
                visual_script = deepcopy(search['visual_script'])
                write_json(output / 'visual_script.json', visual_script)
                state.set_artifact('picture_first_visual_script', visual_script)
            if silent_feedback is None:
                write_json(output / f'search_{round_no}.json', search)
            if shared_library:
                if search.get('slots'):
                    story_slots = deepcopy(search['slots'])
                by_sha = {row['sha256']: row for row in sources['sources']}
                selected_ids = _selected_library_observations(search)
                for observation_id in selected_ids:
                    if any(window['window_id'] == observation_id for window in windows):
                        continue  # This task's received window is already loaded.
                    record = shared_library.get(observation_id)
                    source = by_sha.get(record['window']['source_sha256'])
                    if source is None:
                        raise LibraryStopped('shared_visual_candidate_source_unavailable')
                    window = _received_library_window(record, library_root, source=source,
                        revision_ids=identity_revision_ids, normalize=normalize_times)
                    if not any(existing['window_id'] == window['window_id'] for existing in windows):
                        windows.append(window)
                if selected_ids:
                    write_json(output / 'watched_windows.json', windows)
                rejected_library_ids.update(search.get('rejected_observation_ids', []))
            for requested in search['windows']:
                if max_fine is not None and len(windows) >= max_fine:
                    break
                source = source_map[requested['source_id']]
                blocked = _unknown_window(source, requested['start_s'], requested['end_s'], unknown_inputs)
                if blocked:
                    state.set_artifact('unknown_observation_query_skipped', {
                        'call_id': blocked['call_id'], 'source_id': source['source_id'],
                        'scope': blocked['scope'], 'status': 'unobserved_unknown_paid_result_no_replay'})
                    continue
                key = _visual_request_key(requested, source, windows)
                window_id = 'window_' + key
                if progress_driven and key in attempted_windows:
                    continue
                attempted_windows.add(key)
                if any(w['window_id'] == window_id for w in windows):
                    continue
                _status(output, 'fine_observation', window_id=window_id, question=requested['question'],
                        requests=state.usage()['requests'])
                raw = prepare_window(source, requested['start_s'], requested['end_s'], cache, fps=12,
                                     **({'include_audio': False} if visual_only else {}))
                if visual_only or qfs_films is not None:
                    masks = _source_subtitles(glm, source, raw)
                    window = prepare_window(source, requested['start_s'], requested['end_s'], cache, fps=12,
                                            include_audio=False, subtitle_masks=masks)
                    if config.get('material_search') in {'question_first', 'film'}:
                        window['question_first_subtitle_bar'] = deepcopy(source['subtitle_bar'])
                else:
                    window = raw
                window['window_id'] = window_id
                if requested.get('qfs_identity'):
                    window['qfs_identity'] = deepcopy(requested['qfs_identity'])
                if 'content_range' in source:
                    window['content_range'] = deepcopy(source['content_range'])
                if requested.get('revisit_window_id'):
                    window['revisit_of'] = requested['revisit_window_id']
                if editing_v2:
                    proxy_source = inventory_sources(window['path'],cache / ('shot_catalog_' + key))['sources'][0]
                    window['editing_timeline'] = {
                        'time_domain':'analysis_proxy_local_seconds',
                        'source_mapping':'Add source_start_s for a source-second estimate; not original-film native PTS.',
                        'proxy_fps':12,
                        'timeline':compact_timeline(detect_shot_timeline(proxy_source,cache / 'shot_timelines',threshold=3.0))}
                speech = transcript(source, requested['start_s'], requested['end_s']) if not visual_only else None
                if speech is not None:
                    window['asr'] = speech
                fine_context = {'reference':reference_reading, 'question': requested['question'],
                                'candidate_role_ids': requested['role_ids'],
                                **fine_identity_context(window, windows, material_search=config.get('material_search')),
                                'render_capabilities': ['source_audio','reference_audio','mix','fit','crop','grayscale']}
                if window.get('qfs_identity'):
                    from .question_first import observation_context
                    fine_context.update(observation_context(window['qfs_identity']))
                if window.get('revisit_of'):
                    previous = next(row for row in windows if row['window_id'] == window['revisit_of'])
                    fine_context['received_prior_observation'] = _window_context(previous, include_speech=False)
                    fine_context['revisit_policy'] = '只依据本次实际画面补充question中的未决细节，旧描述不是新像素证据。'
                if visual_only:
                    fine_context.update(source_visual_policy='silent_subtitle_masked',
                                        render_capabilities=['silent','fit','crop','grayscale'])
                else:
                    fine_context['asr_original_timestamps'] = _speech_context(speech)
                if editing_v2:
                    fine_context['editing_reference'] = editing_reference
                    fine_context['render_capabilities'] += ['freeze_tail','static_caption']
                def validate_current_fine(value):
                    if visual_only:
                        cleaned = _fine_navigation_points(value, window, normalize=normalize_times)
                        value.clear()
                        value.update(cleaned)
                    elif window.get('qfs_identity'):
                        from .question_first import bind_observed_entities
                        bind_observed_entities(value, window['qfs_identity'])
                    contracts.validate_fine(value,window,normalize=normalize_times)
                    if editing_v2:
                        validate_fine_editing(value,window,editing_reference)
                try:
                    fine_prompt = (prompts.editing_fine_prompt if editing_v2 else phase_templates.fine_prompt)(
                        _window_context(window, include_speech=False), fine_context)
                    if visual_only:
                        fine_prompt += '\n' + REINTERPRET_POLICY
                    observation = glm.call('fine_' + key, fine_prompt, window['path'], validate_current_fine)
                except LibraryStopped as error:
                    failure = _known_fine_transport_failure(state, error, 'fine_' + key, window)
                    if failure is None:
                        raise
                    state.set_artifact('fine_observation_transport_failure', failure)
                    failures = reference_reading.setdefault('fine_observation_failures', [])
                    if failure not in failures:
                        failures.append(failure)
                    write_json(output / 'reference_reading.json', reference_reading)
                    continue
                except ValueError as error:
                    if not str(error).startswith('model_protocol_repair_exhausted:'):
                        raise
                    state.set_artifact('fine_protocol_failure', {'window_id':window_id,'error':str(error),
                        'status':'not_watched_no_EDL_permission'})
                    continue
                window.update(status='watched', observation=observation)
                contracts.bind_film_window_entities(window, config.get('material_search'))
                if shared_library:
                    state._reload()
                    calls = [call for call in state.data['calls'] if call['status'] == 'received'
                             and call['name'] in {'fine_' + key, 'fine_' + key + '_repair', 'fine_' + key + '_repair2'}]
                    if calls:
                        call = calls[-1]
                        ident = 'task_' + json_sha([str(state.path), call['id']])[:20]
                        stored_window = deepcopy(window)
                        stored_window.pop('content_range', None)
                        for role in stored_window['observation']['roles']:
                            if not role.get('entity_id'):
                                role['entity_id'] = window['source_sha256'] + ':' + window_id + '_' + role['role_id']
                                role['identity_status'] = 'new_local_entity_not_cross_window_confirmation'
                        shared_library.add_observation(dict(observation_id=ident, window=stored_window,
                            received_observation=deepcopy(observation), evidence=dict(status='received',
                                ledger_path=str(state.path), call_id=call['id'],
                                request_sha256=call['request_sha256'], response_sha256=call['response_sha256']),
                            observation_method=dict(input_kind='continuous_silent_proxy',
                                context_scope='reference_conditioned', time_precision='model_estimate',
                                limitations=['Reference-conditioned observations retain their original request context.'])))
                        window = _received_library_window(shared_library.get(ident), library_root, source=source,
                            revision_ids=identity_revision_ids, normalize=normalize_times)
                        if 'content_range' in source:
                            window['content_range'] = deepcopy(source['content_range'])
                windows.append(window)
                write_json(output / 'watched_windows.json', windows)
            if windows and (planning_image is None or shared_library):
                sheets = [create_contact_sheet(source_map[w['source_id']], w['source_start_s'],
                    w['source_end_s'], cache, frame_count=frames,
                    subtitle_masks=w.get('source_subtitle_masks') if visual_only else None) for w in windows]
                planning_image = create_navigation_sheet(sheets, cache)['path']
            if shared_library and silent_feedback is None:
                additions, retrieval_feedback = _expand_library_candidates(state, shared_library, sources,
                    library_cards, search, round_no=round_no, revision_ids=identity_revision_ids,
                    limit=config.get('visual_library_candidate_limit', 30))
                if retrieval_feedback['queries']:
                    known = {card['observation_id'] for card in library_cards}
                    library_cards.extend(card for card in additions if card['observation_id'] not in known)
                    if additions or len(windows) > observed_before:
                        # The model requested more material. Provide local
                        # results in the next existing search before planning.
                        continue
                    state.set_artifact('shared_retrieval_no_progress', {
                        'round': round_no, 'queries': retrieval_feedback['queries'],
                        'reason': 'No unseen received candidate or newly watched material; stop rather than repeat queries.'})
                    if renders:
                        break
                    raise LibraryStopped('shared_retrieval_no_new_candidates')
            if progress_driven and not windows:
                state.set_artifact('rough_no_observed_footage', {
                    'round': round_no, 'reason': 'No watched footage is available for a valid edit plan.',
                    'search_reason': search['reason'], 'requested_window_count': len(search['windows']),
                    'watched_window_count': 0, 'new_observed_window_count': len(windows) - observed_before})
                raise LibraryStopped('no_watched_window_available_for_planning')
            _status(output, 'planning_slots_and_edit', round=round_no, requests=state.usage()['requests'])
            if picture_review:
                round_no = silent_revision
            if not state.data['artifacts'].get('role_identity_policy'):
                state.set_artifact('role_identity_policy', {
                    'policy':'window_local_roles_with_model_owned_focus_bindings_v1',
                    'reason':'Observed role IDs collide across continuous windows; strings alone are not global identities.',
                    'scope':'New plans only; old raw observations remain unchanged.',
                    'human_identity_assignments':[]})
            context = {'reference':reference_reading, 'reference_duration_s':ref['duration_s'],
                       'reference_audio_stream_index':ref['audio_stream_index'], 'catalog':compact_catalog,
                       'watched_windows': [_window_context(w, include_speech=not visual_only) for w in windows], 'previous_review':last_review,
                       'role_identity_scope':'每个fine的角色编号仅在(window_id, role_id)内有效；相同字母不能直接当作跨窗口同一人。焦点人物跨窗口对应必须由你根据实际外形/身份线索在focus_role_bindings中明确给出，不能引用外部剧情常识替代观察。',
                       'audio_semantics_policy':'参考音频中的具体人物和事实不能与新画面冲突；根据已提供ASR核验，冲突时采用其他可用音频模式并记录局限。',
                       'framing_behavior':{'fit':'Keep full frame with letterbox padding.',
                                           'crop':'Center crop only; no tracking or adaptive reframing.'},
                       'render_capabilities': {'speed':[0.5,2], 'max_duration_s':180, 'max_segments':32,
                            'audio_modes':['reference','source','mix','silent'],
                            'unsupported':['J/L_cut','audio_source_separation','synthetic_video']}}
            if picture_first:
                context.update(creative_style='picture_first', visual_script=visual_script, silent_feedback=silent_feedback)
            elif picture_aware:
                # The budget rides in the context: the prompt rule, validation
                # and the recorded context all state the same number.
                context.update(creative_style='picture_aware', silent_feedback=silent_feedback,
                    edit_length_budget=deepcopy(edit_length_budget))
                context['render_capabilities']['max_duration_s'] = edit_length_budget['edit_max_s']
            if qfs_report is not None:
                context.update(slots=deepcopy(story_slots),
                    question_first_gaps=qfs_report['question_first_gaps'],
                    cast_contract=_task_cast_contract())
            if progress_driven:
                context['render_capabilities'].update(
                    max_duration_s=edit_length_budget['edit_max_s'] if picture_aware else None,
                    max_segments=None)
            if creative_audio:
                if 'output_canvas' in config:
                    context['output_canvas'] = config['output_canvas']
                if config.get('film_cast'):
                    context['film_cast'] = config['film_cast']
                context.update(creative_audio=True, strategy=strategy, audio_draft_contract={
                    'fields': ['id', 'text', 'voice', 'mode', 'segment_ids', 'speaker'],
                    'voices': ['Ethan', 'Cherry', 'Serena', 'Moon'], 'speech_mode': strategy['speech_mode'],
                    'status': 'planned_text_only_not_synthesized_or_heard',
                    'segment_ids': 'One or more contiguous segments in EDL order; established character dialogue and narration may cross cuts.'})
                context['visual_coverage_contract'] = {
                    'slots.coverage': {'visual_goal': '画面负责表现什么', 'audio_goal': '声音负责补充什么',
                        'gaps': '真实素材尚不足的具体项'},
                    'segments.shot_role': ['dialogue', 'reaction', 'action', 'insert', 'establishing',
                        'payoff', 'transition', 'other'],
                    'segments.reason': '本镜头怎样推进内容，不能只重复台词',
                    'audio_draft.audio_relation': '可选[{segment_id,relation}]；relation=on_screen/offscreen/reaction/cutaway/narration',
                    'audio_draft.identity_anchor_segment_id': '画外对白前已出现的说话者镜头，可选',
                    'audio_draft.start_anchor': '可选{segment_id,offset_s}，以当前镜头起点加秒数开始',
                    'policy': '按参考风格联合安排画面和声音，不强制每种镜头或固定切镜头次数。'}
                context['render_capabilities']['unsupported'] = ['audio_source_separation', 'synthetic_video']
                context['render_capabilities']['static_caption'] = True
                context['visual_coverage_contract']['segments.caption'] = {
                    'text': '可选的新故事短字幕；不是原素材对白转写',
                    'start_s/end_s': '本镜头输出内局部秒数', 'position': ['top', 'center', 'bottom'],
                    'font_size': '12..120整数',
                    'evidence': '[{window_id,event_indices}]引用本次选中范围内实际事件'}
                if shared_library:
                    selected_ids = {window.get('shared_visual_observation', {}).get('observation_id')
                                    for window in windows}
                    context['visual_retrieval_choices'] = {
                        'selection_reason': search.get('reason', ''),
                        'candidate_uses': [{'observation_id': card['observation_id'],
                            'retrieval_matches': card['retrieval_matches']} for card in library_cards
                            if card['observation_id'] in selected_ids and card.get('retrieval_matches')],
                        'policy': '任务的视觉表达意图；具体画面事实仍以watched_windows为准。'}
                    context['slots'] = deepcopy(story_slots)
                    context['entity_catalog'] = [{'window_id': window['window_id'],
                        'roles': [{key: role[key] for key in
                            ('role_id', 'entity_id', 'identity_confirmed', 'identity_status', 'state', 'identity_evidence') if key in role}
                            for role in window['observation']['roles']]}
                        for window in windows]
                    context['cast_contract'] = _task_cast_contract()
                audio_seed = config.get('received_audio_seed')
                if audio_seed:
                    if sha256_file(audio_seed['path']) != audio_seed['sha256']:
                        raise LibraryStopped('received_audio_seed_changed')
                    prior_audio = _read(audio_seed['path'])
                    if prior_audio['reference']['reference_sha256'] != ref['sha256']:
                        raise LibraryStopped('received_audio_seed_reference_changed')
                    context['received_audio_for_edit'] = {
                        'draft': prior_audio['audio_draft'],
                        'actual_tts_lengths': [{key: row[key] for key in
                            ('id', 'text', 'voice', 'actual_duration_s')} for row in prior_audio['received_tts']],
                        'timing_problems': prior_audio['timing_problems']}
            if visual_only:
                context['audio_semantics_policy'] = REINTERPRET_POLICY
                context['render_capabilities']['audio_modes'] = ['silent']
                context['catalog'] = {'sources': [{k: v for k, v in row.items() if k != 'filename'}
                                                 for row in compact_catalog['sources']]}
            if editing_v2:
                context['editing_reference'] = editing_reference
                context['render_capabilities'].update(freeze_tail_s=[0,10],static_caption=True)
            if reference_seed is not None:
                context['reference_protocol_limit'] = reference_seed['evidence_limit']
            if semantic_audit:
                maximum_segments = (precision.plan_budget(state, round_no) if active_finecut
                                    else semantic_pipeline.plan_budget(state, round_no))
                context['render_capabilities']['max_segments'] = maximum_segments
            if reference_reading.get('reference_storyboard') is not None:
                from .storyboard import storyboard_plan_policy
                context['storyboard_beats'] = [dict(id=b['id'], reference_evidence=dict(
                    cell_ids=b['reference_evidence']['cell_ids']), candidates=[{k: c[k] for k in
                    ('source_id', 'film_in_s', 'film_out_s') if k in c} for c in b.get('candidates', [])])
                    for b in qfs_report['beats']] if qfs_report is not None else []
                if not state.data['artifacts'].get('storyboard_plan_policy'):
                    state.set_artifact('storyboard_plan_policy', storyboard_plan_policy(
                        reference_reading['reference_storyboard'], ref['duration_s'],
                        max_duration_s=plan_duration_cap, max_segments=None if progress_driven else 32))
            def validate_current_plan(value):
                from .plan_normalisation import normalise_plan, add_valid_choices, validate_repeated_footage, landscape_canvas_key
                # The budget comes from the recorded context, so an old reply is
                # only bounded when its own prompt carried the length rule.
                stated_budget = context.get('edit_length_budget')
                edit_max_s = (stated_budget or {}).get('edit_max_s')
                duration_cap = edit_max_s if edit_max_s is not None else (None if progress_driven else 180)
                if creative_audio:
                    normalise_plan(value, windows, catalog=sources, film_cast=context.get('film_cast'), speech_mode=strategy['speech_mode'],
                        max_segments=None if progress_driven else 32,
                        received_tts=context.get('received_audio_for_edit', {}).get('actual_tts_lengths', []),
                        fps=value.get('fps') if isinstance(value, dict) else None,
                        storyboard_beats=context.get('slots', ()) if reference_reading.get('reference_storyboard') is not None else (),
                        storyboard=reference_reading.get('reference_storyboard'), reference_duration_s=ref['duration_s'],
                        max_duration_s=duration_cap, output_canvas=context.get('output_canvas'),
                        # Recorded locks retain their original cast normalisation and failure order too.
                        derive_cast=context.get('output_canvas') == landscape_canvas_key())
                try:
                    contracts.validate_plan(value,sources,windows,ref['sha256'],ref['duration_s'],
                        reference_audio_stream_index=ref['audio_stream_index'],editing_reference=editing_reference,
                        require_audio_draft=creative_audio,
                        received_tts=context.get('received_audio_for_edit', {}).get('actual_tts_lengths', []),
                        **({'reference': reference_reading, 'allow_unfocused': True} if creative_audio else {}),
                        **({'creative_style': 'picture_first', 'visual_script': context['visual_script']}
                           if context.get('creative_style') == 'picture_first' else {}),
                        **({'creative_style': 'picture_aware'} if context.get('creative_style') == 'picture_aware' else {}),
                        **({'storyboard_beats': context.get('storyboard_beats', ())} if reference_reading.get('reference_storyboard') is not None else {}),
                        **({'edit_length_budget': stated_budget} if stated_budget is not None else {}),
                        max_duration_s=duration_cap, max_segments=None if progress_driven else 32)
                    if creative_audio:
                        validate_repeated_footage(value, windows, sources)
                    if visual_only and value.get('audio_mode') != 'silent':
                        raise ValueError('reinterpret_rough_requires_silent_source_audio')
                    if creative_audio and strategy['speech_mode'] in {'dialogue', 'mixed'}:
                        contracts.validate_reference_dialogue_form(value.get('audio_draft'), strategy['speech_mode'])
                        contracts.validate_dialogue_speakers(value['audio_draft'], value['segments'],
                            cast=value.get('cast'), windows=windows)
                    if editing_v2:
                        validate_candidate_dispositions(value,windows)
                        from .render import validate_caption_layout, compile_library_plan
                        compile_library_plan(sources,value,fps=value['fps'],width=value['width'],height=value['height'])
                        validate_caption_layout(value,value['width'],value['height'])
                    if semantic_audit:
                        semantic_pipeline.validate_plan_claims(value,windows,maximum_segments)
                except (ValueError, TypeError, KeyError) as error:
                    if creative_audio:
                        add_valid_choices(error, value, windows, film_cast=context.get('film_cast'))
                    if not progress_driven:
                        raise
                    diagnostic = {**getattr(error, 'diagnostics', {}),
                        'execution_contract': contracts.plan_execution_diagnostics(value, windows)}
                    raise contracts.PlanEvidenceError(str(error), diagnostic, violation=getattr(error, 'violation', None)) from error
            validate_current_plan.protocol_context = dict(stage='plan', windows=windows, catalog=sources,
                reference=reference_reading, require_audio_draft=creative_audio)
            if picture_first:
                validate_current_plan.protocol_context.update(creative_style='picture_first', visual_script=visual_script)
            elif picture_aware:
                validate_current_plan.protocol_context.update(creative_style='picture_aware',
                    edit_length_budget=deepcopy(edit_length_budget))
                # A recorded prompt that CodexMCP keeps never stated this budget;
                # the withheld rule reaches the closure through the live context.
                validate_current_plan.edit_length_context = context
            plan_prompt = prompts.storyboard_plan_prompt(
                semantic_prompts.plan_prompt if semantic_audit else
                prompts.editing_plan_prompt if editing_v2 else phase_templates.plan_prompt, context,
                max_duration_s=plan_duration_cap, max_segments=None if progress_driven else 32)
            plan_prompt = prompts.landscape_plan_prompt(plan_prompt, context)
            if creative_audio:
                from .reference_requirements import meaning_transfer_prompt
                from .plan_normalisation import PLAN_NORMALISATION_PROMPT
                plan_prompt += meaning_transfer_prompt() + PLAN_NORMALISATION_PROMPT
                from .line_windows import line_window_prompt
                received = context.get('received_audio_for_edit', {})
                window_rule = line_window_prompt(received.get('draft', []), segments=context.get('segments', []),
                    received_tts=received.get('actual_tts_lengths', []), fps=context.get('fps'), program_arranged=True)
                if window_rule not in plan_prompt:
                    plan_prompt += window_rule
                plan_prompt += '\n计划字段合同：' + json.dumps(contracts.plan_field_contract(
                    strategy['speech_mode'], windows=windows, catalog=sources), ensure_ascii=False)
                plan_prompt += ('\n本次同时创作完整audio_draft，作为贯穿粗剪、精剪和最终配音的同一份故事。'
                    '根据reference的theme和intended_takeaway确定观众应理解的核心含义，'
                    '不是只复述原电影剧情或把片名隐喻当作画面证据。audio_draft每项必须有'
                    'id、text、voice、mode、segment_ids；voice仅Ethan/Cherry/Serena/Moon，'
                    '每条text最多600字，较长内容分成连贯的多条，'
                    'mode按reference.strategy.speech_mode与参考声音形式选择，不默认第三人称旁白。'
                    '旁白可跨相邻镜头，每项引用连续且有序的segment_ids。草稿需讲完整内容，'
                    '可补充画面无法直接说出的主题关系；'
                    '仅最小可见事实不支持表达时记录缺项；主题和价值判断由配音承载。当前只创作文稿，尚未合成或听到音频。'
                    '构图默认framing=fit以保留完整人物与空间；除有明确表达必要，不作中心crop。')
                plan_prompt += ('\n联合规划视觉覆盖：各slots.coverage说明visual_goal画面任务、audio_goal声音任务和gaps真实缺项，'
                    '各segments.shot_role与reason说明具体镜头贡献。先判断各段怎样用画面呈现关键变化，再联合确定对白，'
                    '参考形式为可选创作线索，按最小可见要求和实际素材安排'
                    '反应、环境与对白镜头，不靠机械切换或固定比例凑丰富性。对白可跨反应镜头，但先通过'
                    'identity_anchor_segment_id建立实际说话者；audio_relation对象数组中的relation可以标注on_screen/offscreen/'
                    'reaction/cutaway/narration。只按真实时间排声音，不宣称嘴形逐音素同步。')
                if shared_library or qfs_report is not None:
                    plan_prompt += ('\n继续context.slots中的同一段落任务，按实际入选镜头填写segment_ids；'
                        '输出slots每项必须包含segment_ids:["实际segment_id",...]，不能照抄输入的observation_ids代替。'
                        '所有segments须各归属一个slot，其slot_id与slots[].segment_ids双向一致。'
                        '保留对应slot_id，让coverage与真实镜头贡献关联。确需改变段落时基于实际素材修订，'
                        '不能把检索时的假想动作当成观察事实。仅缺最小must_see时记gaps，nice_to_have缺失不算素材缺口。')
                    plan_prompt += ('\n可输出cast数组，每项{cast_id,entity_id,voice,bindings:[{window_id,role_id}]}，'
                        '把本次故事角色绑定到同一实际实体的已看出场。audio_draft可引用cast_id；同人物的不同'
                        '窗口保持同音色。未明确关联的临时entity不得仅因衣服/名字相似合并。')
                if strategy['speech_mode'] == 'none':
                    plan_prompt += ('\n本次无语音，audio_draft=[]，不要为满足字段而添加对白或旁白。'
                        '若参考靠文字表达疑问、双关或反差，使用已支持的segments.caption保留相应的新故事信息，'
                        '不能把不可见的文字含义用长时间沉默或普通人物反应替代；按参考信息密度选择短而完整的画面。')
                elif strategy['speech_mode'] in {'dialogue', 'mixed'}:
                    plan_prompt += ('\n按reference.speech_form组织角色视角对白；参考以问答推进时，新片也以疑问、回答和回应推进。'
                        'dialogue要写角色亲口对另一个角色说的话；不要用旁白转述代替人物回答。'
                        'mixed可用少量旁白连接；必要发言由角色亲口表达，实际行动和反应也可以承担观点、冲突与结果。'
                        '每句dialogue必须有speaker={"window_id":"已观察窗口ID","role_id":"窗口内角色ID"}，'
                        '其segment_ids包含说话者可见镜头，或用identity_anchor_segment_id引用当前或更早已展示该人的镜头。'
                        '身份已清楚建立时，本句可完全覆盖动作、物件或听者反应，不必逐句再插说话者露脸。'
                        '为说话者选稳定音色，让交替角色能听出区别。'
                        '根据已看画面选择适合问答和反应的镜头，可舍弃不服务主旨的长铺垫；不需要照搬原片剧情。')
                if audio_seed:
                    plan_prompt += ('\n这次按已收到TTS的真实长度重新编排镜头，优先保留可用的句子、人物音色和问答。'
                        '复用音频不等于必须保留旧稿全部内容；先删去偏离参考主旨的铺垫和重复解释。'
                        '按reference_duration_s保持相近的内容密度和节奏，不为容纳所有旧语音不断加长影片。'
                        '不要仅把长句挤进旧镜头，也不要为了细小超时反复重做语音。可在已看来源内重新选范围、'
                        '调整镜头长度和组织顺序，让同一镜头内所有轮流说话的真实语音与停顿之和都容得下。'
                        '对白可配合对方的反应镜头，旁白可跨相邻镜头；不同说话者不能相互盖住。'
                        '确实必要时才缩句或删除辅助旁白，参考中由角色表达的核心观点与选择须由角色亲口说出。不要复制旧start_s/end_s估算。')
            if visual_only:
                plan_prompt += '\n' + REINTERPRET_POLICY + '\n原声已移除，audio_mode必须silent，reference_audio=null。'
                if creative_audio and strategy['speech_mode'] != 'none':
                    # "silent" only removes the original soundtrack; the new voice script is still required.
                    plan_prompt += SILENT_WITH_CREATIVE_AUDIO
            plan_prompt = prompts.picture_first_plan_prompt(plan_prompt, context)
            plan_budget = None
            try:
                if resume and (round_no == first_round or picture_aware):
                    checkpoint = _picture_aware_checkpoint(resume, round_no) if picture_aware else resume
                    plan, selected_seed, rejected = _resume_plan(checkpoint, validate_current_plan,
                        stage=f'plan_{round_no}', context=context if picture_aware else None)
                    if plan is not None:
                        _resume_checkpoint(state, config, plan_seed=selected_seed, rejected=rejected)
                        plan_budget = _stated_plan_budget(edit_length_budget,
                                                          _received_plan_prompt(selected_seed))
                    else:
                        plan_prompt += ('\n断点续跑：以下历史计划均未通过当前本地协议校验。基于已看窗口重新规划，'
                            '避免重复这些违规；本次有独立的正常修复预算。\n' +
                            json.dumps({'resume_previous_attempts': rejected}, ensure_ascii=False))
                        _resume_checkpoint(state, config, rejected=rejected)
                        try:
                            plan = glm.call(f'plan_{round_no}', plan_prompt, planning_image,
                                validate_current_plan, image=True)
                        finally:
                            _resume_checkpoint(state, config, rejected=rejected)
                        plan_budget = _authored_plan_budget(state, output, round_no, edit_length_budget)
                elif received_plan is not None and round_no == first_round:
                    plan = received_plan
                    # A reused settled plan keeps the length rule its own prompt stated.
                    with _legacy_edit_length_rule(context, _received_plan_prompt(plan_seed)):
                        validate_current_plan(plan)
                    plan_budget = _stated_plan_budget(edit_length_budget, _received_plan_prompt(plan_seed))
                    state.set_artifact('received_plan_reuse', plan_seed)
                else:
                    from .search_recovery import correct_plan
                    plan = correct_plan(state, glm, f'plan_{round_no}', validate_current_plan)
                    if plan is None:
                        plan = glm.call(f'plan_{round_no}', plan_prompt,
                                    planning_image if reference_seed is not None or shared_library else reference_media,
                                    validate_current_plan, **({'image': True} if reference_seed is not None or shared_library else {}))
                    plan_budget = _authored_plan_budget(state, output, round_no, edit_length_budget)
            except ValueError as error:
                if not (progress_driven and renders and
                        str(error) == f'model_protocol_repair_exhausted:plan_{round_no}'):
                    raise
                state.set_artifact('rough_no_progress', {'round': round_no,
                    'reason': 'New plan and its format repairs remain invalid; retain actual reviewed candidates.',
                    'protocol_error': str(error), 'failed_plan_not_rendered': True})
                break
            refinement = None
            if active_finecut:
                write_json(output / f'draft_plan_{round_no}.json', plan)
                draft = plan
                def validate_refinement(value):
                    precision.validate_refinement(value, draft, maximum_segments)
                    validate_current_plan(value['plan'])
                # A recorded finecut prompt that CodexMCP keeps never stated
                # today's budget; the withheld rule reaches the embedded plan
                # through the same live context as the plan validator's.
                validate_refinement.edit_length_context = context
                _status(output, 'active_finecut', round=round_no, requests=state.usage()['requests'])
                refinement = glm.call(f'finecut_{round_no}', precision.refinement_prompt(state, draft, context),
                                      planning_image if reference_seed is not None or shared_library else reference_media,
                                      validate_refinement, **({'image': True} if reference_seed is not None or shared_library else {}))
                write_json(output / f'finecut_{round_no}.json', refinement)
                plan = refinement['plan']
                # The refinement's own request states the rule the embedded plan
                # was judged by, so the round records that rule forward.
                plan_budget = _authored_plan_budget(state, output, round_no, edit_length_budget,
                                                    stage='finecut')
            if visual_only:
                watched = {row['window_id']: row for row in windows}
                for segment in plan['segments']:
                    window = watched[segment['window_id']]
                    segment['source_subtitle_masks'] = window['source_subtitle_masks']
                    segment['source_visual_policy'] = 'silent_subtitle_masked'
            if creative_audio and story_slots:
                prior_slots = {slot['slot_id']: slot for slot in story_slots}
                for slot in plan['slots']:
                    original = prior_slots.get(slot['slot_id'], {})
                    if 'coverage' not in slot and 'coverage' in original:
                        slot['coverage'] = deepcopy(original['coverage'])
            write_json(output / f'plan_{round_no}.json', plan)
            plans[round_no] = plan
            if picture_aware:
                plan_budgets[round_no] = plan_budget
            if progress_driven:
                fields = ('source_id', 'source_in_s', 'source_out_s', 'speed', 'look', 'framing', 'freeze_tail_s', 'caption')
                execution = json_sha({'segments': [{key: row.get(key) for key in fields} for row in plan['segments']],
                    'audio_mode': plan.get('audio_mode'), 'reference_audio': plan.get('reference_audio'),
                    'source_gain_db': plan.get('source_gain_db'), 'reference_gain_db': plan.get('reference_gain_db')})
                if execution in executed_plans and not picture_review:
                    state.set_artifact('rough_no_progress', {'round': round_no,
                        'reason': 'No executed edit changed; retain existing candidates without another render.'})
                    break
                executed_plans.add(execution)
            slice_audit = None
            if semantic_audit:
                _status(output,'auditing_exact_slices',round=round_no,segments=len(plan['segments']),
                        requests=state.usage()['requests'])
                slice_options = {}
                if slice_observation_prompt is not None:
                    slice_options['observation_prompt'] = slice_observation_prompt
                if slice_observation_validator is not None:
                    slice_options['observation_validator'] = slice_observation_validator
                slice_audit = semantic_pipeline.observe_selected_slices(
                    glm,plan,source_map,windows,cache,output,round_no, **slice_options)
                if active_finecut:
                    slice_audit = precision.bind_draft_obligations(slice_audit, draft, refinement)
                    write_json(output / 'semantic_audit' / f'round_{round_no}' / 'manifest.json', slice_audit)
            from .render import render_library_video
            _status(output, 'rendering', round=round_no, segments=len(plan['segments']))
            rendered = (render_fn or render_library_video)(sources, plan, output / f'render_{round_no}', reference_path=reference,
                fps=plan['fps'], width=plan['width'], height=plan['height'],
                **({'max_duration_s': None} if creative_audio and progress_driven else {}))
            if picture_first:
                silent = _silent_story_check(state, glm, rendered, visual_script, round_no, cache)
                if not silent['passed']:
                    if silent_revision == 1:
                        raise LibraryStopped('silent_story_not_readable')
                    silent_revision = 1
                    silent_feedback = dict(blind_reading=silent['blind_reading'],
                        untold_beats=[beat for beat in silent['verdict']['beats'] if not beat['told']],
                        scene_jumps_confusing=silent['verdict']['scene_jumps_confusing'],
                        instruction='仅用同一批已看窗口修订一次plan_1，让画面自身讲清全部节拍；不重新检索。')
                    last_review = silent_feedback
                    continue
            elif picture_aware:
                silent = _picture_aware_review(state, glm, rendered, plan, reference_reading, windows, round_no, cache)
                if silent.get('content_acceptable') is False and silent_revision >= 2:
                    error = LibraryStopped('content_not_expressed:' +
                        json.dumps(silent['must_fix'], ensure_ascii=False, separators=(',', ':')))
                    error.diagnostics = dict(stage='content_correctness_gate', remaining_must_fix=silent['must_fix'],
                                             revision_count=silent_revision)
                    raise error
                if (silent.get('content_acceptable') is False or
                        silent['revision_suggested'] and silent_revision == 0):
                    silent_revision += 1
                    if 'content_acceptable' in silent:
                        silent_feedback = _picture_aware_feedback(silent, silent_revision)
                    else:
                        # Historical advisory reviews and their plan prompts replay unchanged.
                        silent_feedback = dict(issues=[line for line in silent['verdict']['lines']
                            if line['issue'] in contracts.SILENT_REVIEW_TIER1],
                            scene_jumps_confusing=silent['verdict']['scene_jumps_confusing'],
                            instruction='仅用同一批已看窗口修订一次plan_1，修复上述画面与台词关系问题；不重新检索。')
                    last_review = silent_feedback
                    continue
            if creative_audio:
                review = {'reference_sha256': ref['sha256'], 'theme_status': 'unverifiable',
                    'editing_status': 'unverifiable', 'continuity_status': 'unverifiable',
                    'evidence': [], 'missing_core_meaning': [], 'revision_requests': [],
                    'limitations': ['Executable rough plan only; final audiovisual content has not been reviewed.']}
                result = {'status': 'rough_planned', 'final_video': rendered['rendered_path'],
                    'final_sha256': rendered['sha256'], 'reference_sha256': ref['sha256'],
                    'selected_round': round_no, 'review': review, 'review_kind': 'not_reviewed_until_audio_delivery',
                    'usage': state.usage(), 'actual_fine_windows': len(windows), 'coarse_pages': len(coarse),
                    'joint_quality_gate': False, 'human_creative_inputs': [], 'source_generation_requests': 0,
                    'evidence_limit': 'Technical render only; use the actual dubbed delivery for content review.'}
                if picture_aware:
                    result.update(creative_style='picture_aware', silent_review_path=str(output / 'silent_review.json'),
                                  silent_review=_read(output / 'silent_review.json'),
                                  # The rule the selected plan was validated under;
                                  # null means the plan's own prompt was legacy.
                                  edit_length_budget=plan_budgets.get(round_no))
                write_json(output / 'result.json', result)
                _status(output, 'completed', **result)
                return result
            output_source = inventory_sources(rendered['rendered_path'],
                output / f'render_catalog_{round_no}')['sources'][0]
            output_media = (visual_proxy(output_source, 0, rendered['measured_duration_s'], cache,
                label='clean_actual_rough') if progress_driven else prepare_window(
                    output_source, 0, rendered['measured_duration_s'], cache, fps=12))
            _status(output, 'blind_review', round=round_no, requests=state.usage()['requests'])
            blind = glm.call(f'blind_{round_no}',
                semantic_prompts.blind_prompt(rendered['measured_duration_s'],rendered['sha256'])
                if semantic_audit else templates.blind_prompt(rendered['measured_duration_s']),
                output_media['path'], lambda v: semantic.validate_visual_blind(
                    v,rendered['measured_duration_s'],rendered['sha256']) if semantic_audit
                else contracts.validate_blind_reading(v,rendered['measured_duration_s'], normalize=normalize_times))
            write_json(output / f'blind_reading_{round_no}.json', blind)
            economy = None
            if active_finecut:
                manifest = precision.economy_manifest(plan, rendered)
                write_json(output / f'economy_manifest_{round_no}.json', manifest)
                _status(output, 'independent_economy_review', round=round_no, requests=state.usage()['requests'])
                economy = glm.call(f'economy_{round_no}', precision.economy_prompt(manifest, blind),
                    output_media['path'], lambda v: precision.validate_economy_review(v, manifest))
                write_json(output / f'economy_review_{round_no}.json', economy)
            review_context = {'reference':reference_reading, 'actual_render_sha256':rendered['sha256'],
                 'blind_reading':blind, 'plan':plan, 'provenance':rendered['provenance'],
                 'audio_review_limit':'GLM vision MCP has not heard actual output audio; preserve limitation.'}
            if progress_driven:
                review_context['audio_review_limit'] = ('The normal-speed visual proxy physically omits audio and covers '
                    'the complete output timeline; native cloud frame sampling and audio craft remain unverified.')
            if creative_audio:
                review_context.update(creative_audio=True,
                    audio_draft_status='planned_text_only_not_synthesized_or_heard',
                    review_output_contract=contracts.review_output_contract(require_core_meaning=True))
            if qfs_report is not None:
                review_context['question_first_gaps'] = qfs_report['question_first_gaps']
            if semantic_audit:
                review_context.pop('plan')
                review_context.update(protocol=semantic.SEMANTIC_PROTOCOL,video_sha256=rendered['sha256'],
                    required_claims=slice_audit['required_claims'],
                    source_observations=slice_audit['observations'],segment_checks=slice_audit['segment_checks'])
            if editing_v2:
                actual_source = inventory_sources(rendered['rendered_path'],output / f'render_catalog_{round_no}')['sources'][0]
                actual_timeline = detect_shot_timeline(actual_source,cache / 'shot_timelines',threshold=3.0)
                boundary_associations = associate_edl_boundaries(actual_timeline,rendered)
                write_json(output / f'editing_evidence_{round_no}.json',boundary_associations)
                review_context.update(editing_reference=editing_reference,
                    output_duration_s=rendered['measured_duration_s'],
                    measured_output_timeline=compact_timeline(actual_timeline),
                    edl_boundary_associations=boundary_associations)
            def validate_current_review(value):
                contracts.validate_review(value,ref['sha256'], require_core_meaning=creative_audio)
                if editing_v2:
                    validate_method_review(value,editing_reference,rendered['measured_duration_s'])
                if semantic_audit:
                    semantic.validate_semantic_review(value,blind,slice_audit['observations'],
                        slice_audit['required_claims'],rendered['measured_duration_s'],rendered['sha256'],
                        segment_checks=slice_audit['segment_checks'])
            if reference_seed is not None:
                review_context['reference_protocol_limit'] = reference_seed['evidence_limit']
            review_prompt = (semantic_prompts.review_prompt if semantic_audit else
                             prompts.editing_review_prompt if editing_v2 else phase_templates.review_prompt)(review_context)
            if creative_audio:
                review_prompt += ('\n必须输出missing_core_meaning数组：逐项列出实际画面与audio_draft组合仍不能表达的参考核心含义；'
                    '核心完整为[]。小口型、节奏和构图瑕疵记录limitations，不当作核心缺项。'
                    'audio_draft仅是计划文稿，未合成、未听审；可判断内容但不得声称实际声音已通过。'
                    '不得把观众看不出的关联、片外剧情知识或虚构因果当作主旨已成立。')
            review = glm.call(f'review_{round_no}', review_prompt,
                output_media['path'],validate_current_review)
            write_json(output / f'review_{round_no}.json', review)
            last_review = review
            if active_finecut:
                last_review = {**review, 'independent_economy_review': economy,
                               'unresolved_information_obligations': [c for c in refinement['obligation_coverage']
                                                                      if c['status'] == 'unresolved']}
            renders.append({'render':rendered, 'blind':blind, 'review':review, 'round':round_no,
                            **({'refinement':refinement, 'economy':economy} if active_finecut else {}),
                            **({'segment_checks':slice_audit['segment_checks'],
                                'expected_segment_ids':[s['segment_id'] for s in plan['segments']]}
                               if semantic_audit else {})})
            if creative_audio:
                from .story_context import core_missing
                if core_missing(review):
                    state.set_artifact('rough_needs_material_reselection', {
                        'round': round_no, 'missing_core_meaning': review.get('missing_core_meaning', []),
                        'reason': 'Core content is missing; stop this edit and return to material selection.'})
                    break
            if progress_driven:
                if review['theme_status'] in {'pass', 'partial'} and review['continuity_status'] == 'pass':
                    break
                if previous_review and len(windows) == observed_before:
                    scores = {'pass': 3, 'partial': 2, 'fail': 1, 'unverifiable': 0}
                    improved = any(scores.get(review[key], 0) > scores.get(previous_review[key], 0)
                        for key in ('theme_status', 'editing_status', 'continuity_status'))
                    if not improved:
                        state.set_artifact('rough_no_progress', {'round': round_no,
                            'reason': 'No new observed window and no review improvement; retain candidates.'})
                        break
            if ((semantic.semantic_review_passes(review,blind,slice_audit['segment_checks'],
                    expected_segment_ids=[s['segment_id'] for s in plan['segments']])
                    if semantic_audit else review['theme_status'] == 'pass' and review['continuity_status'] == 'pass')
                    and (not editing_v2 or review['editing_status'] == 'pass') and not review['revision_requests']
                    and (not active_finecut or precision.passes(refinement, economy))):
                break
        selected = renders[0]['round']
        if len(renders) > 1:
            choice_prompt = (prompts.BASE + '比较两个实际成片的盲读和审核记录，保留同主旨且连续性最好的有效版本。' +
                '只返回JSON {"selected_round":0,"reason":"依据及保留局限"}。' + json.dumps(
                    [{'round':r['round'],'blind':r['blind'],'review':r['review']} for r in renders],ensure_ascii=False))
            def validate_selection(value):
                if semantic_audit:
                    if active_finecut:
                        precision.validate_selection(value,renders)
                    else:
                        semantic.validate_semantic_selection(value,renders)
                    return
                if type(value.get('selected_round')) is not int or value['selected_round'] not in {r['round'] for r in renders} or not value.get('reason'):
                    raise ValueError('invalid_render_selection')
            selection_media = output_media['path'] if shared_library else reference_media
            if prompt_module is not None:
                candidates = [{'round':r['round'],'blind':r['blind'],'review':r['review']} for r in renders]
                if progress_driven:
                    for candidate, actual in zip(candidates, renders):
                        candidate['actual_render'] = {'sha256': actual['render']['sha256'],
                            'duration_s': actual['render']['measured_duration_s']}
                    choice_prompt = templates.bound_select_prompt(candidates, {
                        'round': renders[-1]['round'], 'sha256': renders[-1]['render']['sha256'],
                        'source_start_s': 0, 'source_end_s': renders[-1]['render']['measured_duration_s']})
                else:
                    choice_prompt = templates.select_prompt(candidates)
                # A historical lost full-reference request cannot be replayed.
                selection_media = output_media['path']
            if semantic_audit:
                choice_prompt = semantic_prompts.selection_prompt([
                    {k:r[k] for k in ('round','blind','review','segment_checks','expected_segment_ids')} for r in renders])
                if active_finecut:
                    choice_prompt += '\n同时比较信息保留和独立精炼审核；不能用更短或技巧更多证明更好。'
                    choice_prompt += '若有联合通过候选必须从中选择；否则在limitations保留未解决的原表达义务和冗余问题：' + json.dumps([
                        {'round':r['round'],'obligation_coverage':r['refinement']['obligation_coverage'],
                         'economy':r['economy']} for r in renders],ensure_ascii=False)
                selection_media = output_media['path']
            decision = glm.call('select_render', choice_prompt, selection_media, validate_selection)
            selected = decision['selected_round']
            write_json(output / 'render_selection.json', decision)
        best = next(r for r in renders if r['round'] == selected)
        current_review_path = output / f'review_{selected}.json'
        prior_review_call = next(c for c in state.data['calls']
                                if c['name'] == f'review_{selected}' and not c.get('repair_of'))
        original_review_prompt = _read(output/'calls'/prior_review_call['id']/'request.json')['arguments']['prompt']
        if (not (creative_audio and core_missing(best['review'])) and not editing_v2 and
                state.data['artifacts'].get('editing_review_policy') and
                '故事段落顺序相似或slot数量相似' not in original_review_prompt):
            # Compare the selected film under the current task definition. Do not
            # turn a selection rationale into a pass or overwrite the old review.
            old_result = output / 'result.json'
            archive = output / 'result_before_selected_audit.json'
            if old_result.exists() and not archive.exists():
                archive.write_bytes(old_result.read_bytes())
            if not state.data['artifacts'].get('selected_audit_policy'):
                state.set_artifact('selected_audit_policy', {
                    'policy':'review_selected_actual_film_under_current_criteria_v2',
                    'reason':'Selected film was last compared before theme and editing criteria were clarified.',
                    'historical_review':str(current_review_path),'historical_review_sha256':sha256_file(current_review_path),
                    'selection_rationale_is_not_quality_verdict':True,
                    'input_and_budgets_unchanged':True,'no_extra_render':True})
            selected_source = inventory_sources(best['render']['rendered_path'],
                output / f'render_catalog_{selected}')['sources'][0]
            selected_media = (visual_proxy(selected_source, 0, best['render']['measured_duration_s'], cache,
                label='clean_selected_rough') if progress_driven else prepare_window(
                    selected_source,0,best['render']['measured_duration_s'],cache,fps=12))
            _status(output,'selected_quality_audit',selected_round=selected,requests=state.usage()['requests'])
            current_review = glm.call(f'selected_review_v2_{selected}', templates.review_prompt({
                'reference':reference_reading,'actual_render_sha256':best['render']['sha256'],
                'blind_reading':best['blind'],'plan':plans[selected],
                'provenance':best['render']['provenance'],
                **({'creative_audio': True,
                    'audio_draft_status': 'planned_text_only_not_synthesized_or_heard',
                    'review_output_contract': contracts.review_output_contract(require_core_meaning=True)}
                   if creative_audio else {}),
                'audio_review_limit':('The normal-speed visual proxy physically omits audio and covers the complete '
                    'output timeline; native cloud frame sampling and audio craft remain unverified.' if progress_driven else
                    'GLM vision MCP has not heard actual output audio; preserve limitation.')}),
                selected_media['path'],lambda v:contracts.validate_review(v,ref['sha256'],
                    require_core_meaning=creative_audio))
            current_review_path = output / f'selected_review_v2_{selected}.json'
            write_json(current_review_path,current_review)
            best['review'] = current_review
        success = all(best['review'][key] == 'pass' for key in
                      ('theme_status','editing_status','continuity_status'))
        if semantic_audit:
            success = semantic.semantic_review_passes(best['review'],best['blind'],best['segment_checks'],
                expected_segment_ids=best['expected_segment_ids'])
        if active_finecut:
            success = success and precision.passes(best['refinement'],best['economy'])
        result = {'status':'model_checked_library_candidate' if success else 'library_candidate_with_limitations',
                  'final_video':best['render']['rendered_path'], 'final_sha256':best['render']['sha256'],
                  'reference_sha256':ref['sha256'], 'selected_round':selected, 'review':best['review'],
                  'selected_review_path':str(current_review_path),
                  'usage':state.usage(), 'actual_fine_windows':len(windows), 'coarse_pages':len(coarse),
                  'human_creative_inputs':[], 'source_generation_requests':0,
                  'evidence_limit':'Model review is not human truth; audio rhythm remains unverified by vision MCP.'}
        if editing_v2:
            result['editing_protocol'] = prompts.EDITING_PROTOCOL
            result['editing_evidence_path'] = str(output / f'editing_evidence_{selected}.json')
        if semantic_audit:
            result['semantic_protocol'] = semantic.SEMANTIC_PROTOCOL
            result['semantic_evidence_path'] = str(output / 'semantic_audit' / f'round_{selected}' / 'manifest.json')
            result['semantic_gate_passed'] = success
        if active_finecut:
            result.update(active_finecut_protocol=precision.POLICY, active_finecut_gate_passed=success,
                refinement_path=str(output / f'finecut_{selected}.json'),
                economy_review_path=str(output / f'economy_review_{selected}.json'))
        if reference_seed is not None:
            result['reference_navigation_limit'] = reference_seed['evidence_limit']
            result['reference_seed_sha256'] = json_sha(reference_seed)
            result['reference_observation_reused_no_new_reference_request'] = True
        if creative_audio and core_missing(best['review']):
            result.update(status='needs_material_reselection', joint_quality_gate=False,
                          missing_core_meaning=best['review'].get('missing_core_meaning', []))
        if picture_aware:
            result['edit_length_budget'] = plan_budgets.get(selected)
        write_json(output / 'result.json', result)
        _status(output, 'completed', **result)
        return result
    except Exception as error:
        failure = {'error':str(error), 'type':type(error).__name__,
                   'traceback':traceback.format_exc(), 'usage':state.usage(), 'no_automatic_paid_replay':True}
        if isinstance(getattr(error, 'diagnostics', None), dict):
            failure['validation_diagnostics'] = error.diagnostics
        state.set_artifact('failure', failure)
        write_json(output / failure_report_name, failure)
        _status(output, 'stopped', error=str(error), requests=state.usage()['requests'])
        raise
