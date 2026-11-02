"""The picture_aware edit stays near the reference length; legacy replies keep their own rule."""
import json
import math
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from omni_story.library import clean_chain, contracts, pipeline, prompts, replay_check as replay
from omni_story.library import story_finecut as fine
from omni_story.library.media import sha256_file
from omni_story.library.state import LibraryStopped, LibraryState, json_sha, write_json
from test_picture_aware import aware
from test_resume_from import failed, read
from test_story_finecut import State, media, single_duration_plan  # noqa: F401 - media fixture reuse

CASES = read(Path(__file__).parent / 'fixtures/s34_edit_length_limit.json')


@pytest.mark.parametrize('reference_s,expected', [(float(key), value) for key, value in CASES['budgets'].items()])
def test_budget_helper(reference_s, expected):
    assert contracts.picture_aware_edit_max_s(reference_s) == expected


@pytest.mark.parametrize('invalid', [None, 0, -4, '119.4', float('nan')])
def test_budget_helper_requires_a_real_duration(invalid):
    with pytest.raises(ValueError, match='picture_aware_edit_max_s'):
        contracts.picture_aware_edit_max_s(invalid)


def long_windows(failed, count):
    """Distinct watched windows so repeated-footage checks stay satisfied."""
    windows = []
    for index in range(count):
        window = deepcopy(failed.window)
        window.update(window_id=f'watched_{index}', source_start_s=100 + 10 * index,
                      source_end_s=110 + 10 * index)
        window['observation'] = {**window['observation'], 'window_id': window['window_id']}
        windows.append(window)
    return windows


def edit_plan(sha, total_s, windows):
    """A minimal valid creative plan whose EDL sums to exactly total_s seconds."""
    segments, remaining = [], round(total_s, 6)
    while remaining > 1e-6:
        window = windows[len(segments)]
        motion = min(20.0, remaining)  # speed 0.5 over each 10 s watched range
        segments.append(dict(segment_id=f'seg{len(segments)}', slot_id='slot', source_id='film',
            window_id=window['window_id'], role_ids=[], source_in_s=window['source_start_s'],
            source_out_s=window['source_start_s'] + motion * 0.5,
            speed=0.5, look='none', framing='fit'))
        remaining = round(remaining - motion, 6)
    ids = [row['segment_id'] for row in segments]
    return dict(reference_sha256=sha, audio_mode='silent', reference_audio=None, width=640,
        height=360, fps=30, limitations=[],
        slots=[dict(slot_id='slot', intended_takeaway='珍惜陪伴', segment_ids=ids)],
        segments=segments, audio_draft=[dict(id='line', text='珍惜陪伴，把重要的事做完。', voice='Ethan',
            mode='narration', picture_relation='additive', segment_ids=ids)])


def validate_options(failed):
    return dict(allow_unfocused=True, require_audio_draft=True, reference=failed.reading), dict(
        sources=[dict(source_id='film', sha256=sha256_file(failed.source), duration_s=300)])


def test_over_limit_plan_fails_and_within_limit_plan_passes(failed):
    limit = CASES['edit_max_s']
    options, catalog = validate_options(failed)
    windows = long_windows(failed, 8)
    sha = sha256_file(failed.ref)
    over = edit_plan(sha, CASES['plan_total_s']['over_limit'], windows)
    assert sum((row['source_out_s'] - row['source_in_s']) / row['speed'] for row in over['segments']) == 157.0
    with pytest.raises(ValueError, match=f'plan:duration_exceeds_{limit}_seconds'):
        contracts.validate_plan(over, catalog, windows, sha, 4,
            creative_style='picture_aware', max_duration_s=limit, **options)
    contracts.validate_plan(edit_plan(sha, CASES['plan_total_s']['within_limit'], windows), catalog,
        windows, sha, 4, creative_style='picture_aware', max_duration_s=limit, **options)
    # The same over-long plan stays within the classic route's flat 180 s ceiling.
    contracts.validate_plan(over, catalog, windows, sha, 4, max_duration_s=180, **options)


def budget_context(**extra):
    context = dict(creative_style='picture_aware', creative_audio=True, reference_duration_s=119.4,
        reference_audio_stream_index=None,
        edit_length_budget=dict(reference_duration_s=CASES['reference_duration_s'],
                                edit_max_s=CASES['edit_max_s']))
    context.update(extra)
    return context


@pytest.mark.parametrize('base', ['classic_template', 'progress_template'], ids=['classic', 'progress'])
def test_plan_prompt_states_one_budget_and_no_flat_ceiling(base):
    factory = (prompts.plan_prompt if base == 'classic_template'
               else clean_chain.ROUGH_PROMPTS.for_round(1).plan_prompt)
    context = budget_context()
    prompt = prompts.picture_first_plan_prompt(factory(context), context)
    assert prompts.PICTURE_AWARE_EDIT_LENGTH_MARKER in prompt
    assert '参考时长119.4秒' in prompt and '不得超过149.2秒' in prompt
    assert '180秒' not in prompt
    # Without a budget (other styles and legacy contexts) the bytes stay untouched.
    plain = factory({key: value for key, value in context.items() if key != 'edit_length_budget'})
    assert prompts.picture_first_plan_prompt(plain, dict(creative_style='classic')) == plain


def test_revision_prompt_fixes_within_the_budget_without_adding_shots():
    feedback = dict(must_fix=[], issues=[], instruction='修订')
    context = budget_context(silent_feedback=feedback)
    prompt = prompts.picture_first_plan_prompt(prompts.plan_prompt(context), context)
    assert '静音审查反馈' in prompt
    assert '不得靠新增镜头或加长定格解决' in prompt and '仍不得超过149.2秒' in prompt
    first = prompts.picture_first_plan_prompt(prompts.plan_prompt(budget_context()), budget_context())
    assert '不得靠新增镜头' not in first


def plan_validator(failed, context, windows):
    """Rebuild validate_current_plan exactly as replay_check does from a recorded prompt."""
    strategy = contracts.reference_strategy(failed.reading, creative_audio=True)
    return replay.nested_validator(pipeline.execute, 'validate_current_plan', context=context,
        creative_audio=True, editing_reference=None, editing_v2=False, maximum_segments=32,
        progress_driven=True, ref=dict(sha256=sha256_file(failed.ref), duration_s=4, audio_stream_index=None),
        reference_reading=failed.reading, semantic_audit=False, semantic_pipeline=None,
        sources=dict(sources=[dict(source_id='film', sha256=sha256_file(failed.source), duration_s=300)]),
        strategy=strategy, visual_only=strategy['adaptation_mode'] == 'reinterpret', windows=windows)


def test_replay_judges_a_record_by_the_rule_its_prompt_carried(failed):
    sha = sha256_file(failed.ref)
    windows = long_windows(failed, 8)
    over = edit_plan(sha, CASES['plan_total_s']['over_limit'], windows)
    legacy = dict(creative_style='picture_aware', creative_audio=True, reference_duration_s=4,
        reference_audio_stream_index=None)  # Recorded prompts never stated a budget.
    plan_validator(failed, legacy, windows)(deepcopy(over))  # legacy record: no length rule applied
    current = budget_context()
    with pytest.raises(ValueError, match=f'plan:duration_exceeds_{CASES["edit_max_s"]}_seconds'):
        plan_validator(failed, current, windows)(deepcopy(over))


def frame_rounding_plan(sha, frames, windows, fps=30):
    """A plain plan whose nominal second sum stays inside the cap but whose
    per-segment rounding compiles to `frames` frames."""
    budget = dict(reference_sha256=sha, audio_mode='silent', reference_audio=None, width=640,
        height=360, fps=fps, limitations=[],
        slots=[dict(slot_id='slot', intended_takeaway='珍惜陪伴', segment_ids=[])],
        segments=[], audio_draft=[])
    ids = []
    for index, frame_count in enumerate(frames):
        duration = (frame_count - .5 + .15) / fps  # rounds up to frame_count, sums below the cap
        window = windows[index]
        segments = budget['segments']
        segments.append(dict(segment_id=f'seg{index}', slot_id='slot', source_id='film',
            window_id=window['window_id'], role_ids=[], source_in_s=window['source_start_s'],
            source_out_s=window['source_start_s'] + duration * .5,
            speed=.5, look='none', framing='fit'))
        ids.append(f'seg{index}')
    budget['slots'][0]['segment_ids'] = ids
    budget['audio_draft'] = [dict(id='line', text='珍惜陪伴，把重要的事做完。', voice='Ethan',
        mode='narration', picture_relation='additive', segment_ids=ids)]
    return budget


def test_frame_rounding_cannot_slip_the_budget(failed):
    """fsum 149.14 s passes the 149.2 s cap but compiles to 4477 frames > floor(149.2*30)."""
    sha = sha256_file(failed.ref)
    windows = long_windows(failed, 8)
    options, catalog = validate_options(failed)
    frames = [559] * 7 + [564]
    plan = frame_rounding_plan(sha, frames, windows)
    assert sum(frames) == 4477 and math.fsum((row['source_out_s'] - row['source_in_s']) / row['speed']
                                              for row in plan['segments']) <= CASES['edit_max_s']
    assert not any(row.get('freeze_tail_s', 0) or row.get('caption') for row in plan['segments'])
    budget = dict(reference_duration_s=CASES['reference_duration_s'], edit_max_s=CASES['edit_max_s'])
    with pytest.raises(ValueError, match='plan:quantized_duration_exceeds_149.2_seconds'):
        contracts.validate_plan(plan, catalog, windows, sha, 4,
            creative_style='picture_aware', max_duration_s=CASES['edit_max_s'],
            edit_length_budget=budget, **options)
    # Without the budget the same plan keeps the legacy behaviour: no freeze,
    # caption or editing plan, so no quantized check ever ran.
    contracts.validate_plan(plan, catalog, windows, sha, 4,
        creative_style='picture_aware', max_duration_s=180, **options)
    # The live validator derives the same frame cap from the recorded context.
    with pytest.raises(ValueError, match='plan:quantized_duration_exceeds_149.2_seconds'):
        plan_validator(failed, budget_context(), windows)(deepcopy(plan))


def plan_seed(tmp_path, ident, value, prompt):
    task = tmp_path / ident
    media = task / 'sheet.bin'
    media.parent.mkdir(parents=True)
    media.write_bytes(ident.encode())
    request = dict(arguments=dict(image_source=str(media), prompt=prompt), media_sha256=sha256_file(media))
    reply = dict(result=dict(content=[dict(type='text', text=json.dumps(value, ensure_ascii=False))]))
    write_json(task / 'calls' / ident / 'request.json', request)
    write_json(task / 'calls' / ident / 'response.json', reply)
    call = dict(id=ident, name='plan_0', status='received', request_sha256=json_sha(request),
                response_sha256=json_sha(reply))
    write_json(task / 'library_state.json', dict(task_id=ident, request_count=1, calls=[call],
        artifacts={}, input_lock=dict(configuration=dict(creative_audio=True))))
    ledger = task / 'library_state.json'
    return dict(ledger=dict(path=str(ledger), sha256=sha256_file(ledger)), call_id=ident)


def test_resume_judges_each_seed_by_its_own_prompt(tmp_path):
    context = budget_context()
    over = dict(duration_s=CASES['plan_total_s']['over_limit'])
    seen = []

    def validator(plan):
        bounded = context.get('edit_length_budget') is not None
        seen.append(bounded)
        if bounded and plan['duration_s'] > CASES['edit_max_s']:
            raise ValueError(f'plan:duration_exceeds_{CASES["edit_max_s"]}_seconds')

    legacy = plan_seed(tmp_path, 'legacy_prompt', over, 'recorded prompt without the contract')
    plan, seed, rejected = pipeline._resume_plan(dict(ledger=legacy['ledger'], plan_seeds=[legacy]),
        validator, context=context)
    assert plan == over and seed['call_id'] == 'legacy_prompt' and not rejected and seen == [False]
    assert context['edit_length_budget'] == budget_context()['edit_length_budget']
    current = plan_seed(tmp_path, 'budget_prompt', over,
        prompts.picture_first_plan_prompt('recorded prompt', budget_context()))
    plan, seed, rejected = pipeline._resume_plan(dict(ledger=current['ledger'], plan_seeds=[current]),
        validator, context=context)
    assert plan is None and seed is None and seen == [False, True]
    assert rejected[0]['validation_error'] == 'plan:duration_exceeds_149.2_seconds'


def test_duration_repair_hint_follows_the_prompt_rule(tmp_path):
    """Only a prompt that stated the edit-length contract gets duration guidance."""
    legacy_prompt = 'recorded plan prompt, flat 180 s ceiling only'
    assert pipeline.repair_hint('plan:duration_exceeds_180_seconds', '{}') is None
    assert pipeline.repair_hint('plan:quantized_duration_exceeds_180_seconds', '{}', legacy_prompt) is None
    stated = prompts.picture_first_plan_prompt('recorded prompt', budget_context())
    assert '成片总时长超过本次上限' in pipeline.repair_hint(
        'plan:quantized_duration_exceeds_149.2_seconds', '{}', stated)

    def duration_validator(plan):
        cap = (context.get('edit_length_budget') or {}).get('edit_max_s', 180)
        if plan['duration_s'] > cap:
            raise ValueError(f'plan:duration_exceeds_{cap}_seconds')

    context = budget_context()
    far_over = dict(duration_s=190)
    legacy = plan_seed(tmp_path, 'legacy_duration', far_over, legacy_prompt)
    plan, seed, rejected = pipeline._resume_plan(dict(ledger=legacy['ledger'], plan_seeds=[legacy]),
        duration_validator, context=context)
    assert plan is None and rejected[0]['validation_error'] == 'plan:duration_exceeds_180_seconds'
    # The legacy rule's rejected feedback keeps its historical bytes: no edit hint.
    assert rejected[0]['repair_hint'] == ('只修复violations列出的字段及必要依赖；其余时间、ID和绑定保留原值，'
                                          '不要整体改写时间域或新增身份绑定。')
    current = plan_seed(tmp_path, 'stated_duration', dict(duration_s=157), stated)
    plan, seed, rejected = pipeline._resume_plan(dict(ledger=current['ledger'], plan_seeds=[current]),
        duration_validator, context=context)
    assert '成片总时长超过本次上限' in rejected[0]['repair_hint']


def test_length_contract_upgrade_sends_a_fresh_prompt_and_rerun_reuses_it(tmp_path, monkeypatch):
    media = tmp_path / 'sheet.bin'
    media.write_bytes(b'plan navigation sheet')
    state = LibraryState(tmp_path / 'run', dict(configuration=dict(
        provider=pipeline.CodexMCP.provider, creative_style='picture_aware')))
    mcp = pipeline.CodexMCP.__new__(pipeline.CodexMCP)
    mcp.provider = pipeline.CodexMCP.provider
    mcp.state, mcp.output = state, state.output
    sent = []

    def submit(name, request, *, repair_of=None):
        state._reload()
        if any(call['request_sha256'] == json_sha(request) for call in state.data['calls']):
            return pipeline.CodexMCP._submit(mcp, name, request, repair_of=repair_of)
        call, _ = state.begin_call(name, request, repair_of=repair_of)
        sent.append(request['arguments']['prompt'])
        reply = dict(result=dict(content=[dict(type='text', text='{"ok": true}')]))
        state.complete_call(call, reply)
        return call, reply

    monkeypatch.setattr(mcp, '_submit', submit)
    validator = lambda value: value
    legacy = 'recorded plan prompt without the edit-length contract'
    assert mcp.call('plan_1', legacy, media, validator, image=True) == {'ok': True}
    upgraded = prompts.picture_first_plan_prompt(legacy, budget_context())
    assert prompts.PICTURE_AWARE_EDIT_LENGTH_MARKER in upgraded
    # The received legacy reply cannot pin the budget it was never asked for.
    assert mcp.call('plan_1', upgraded, media, validator, image=True) == {'ok': True}
    assert sent == [legacy, upgraded]
    assert mcp.call('plan_1', upgraded, media, validator, image=True) == {'ok': True}
    assert len(sent) == 2  # the rerun reuses the upgraded reply, no resend
    assert [call['name'] for call in state.data['calls']] == ['plan_1', 'plan_1']


def budget_validator(context):
    """A plan validator shaped like validate_current_plan over the live context."""
    def validate_current_plan(value):
        cap = (context.get('edit_length_budget') or {}).get('edit_max_s', 180)
        if 'audio_draft' not in value:
            raise ValueError('plan/audio_draft:list_required')
        if value['duration_s'] > cap:
            raise ValueError(f'plan:duration_exceeds_{cap}_seconds')
    validate_current_plan.protocol_context = dict(stage='plan', creative_style='picture_aware')
    validate_current_plan.edit_length_context = context
    return validate_current_plan


def recorded_plan_task(tmp_path, *, marker, repaired):
    """A recorded plan_1 call with a protocol failure (and optionally its repair).

    The recorded prompts predate today's request bytes, so CodexMCP must keep
    them pinned and judge their replies by the rule those bytes stated.
    """
    media = tmp_path / 'sheet.bin'
    media.write_bytes(b'plan navigation sheet')
    state = LibraryState(tmp_path / 'run', dict(configuration=dict(
        provider=pipeline.CodexMCP.provider, creative_style='picture_aware')))
    base_prompt = ('时长合同（' + prompts.PICTURE_AWARE_EDIT_LENGTH_MARKER +
                   '）：不得超过149.2秒。' if marker else 'recorded plan prompt without the contract')

    def record(name, prompt, value, *, repair_of=None, protocol_failure=False):
        request = dict(tool='analyze_image', arguments=dict(image_source=str(media), prompt=prompt),
                       media_sha256=sha256_file(media), provider=pipeline.CodexMCP.provider,
                       policy_version=prompts.POLICY_VERSION)
        reply = dict(result=dict(content=[dict(type='text', text=json.dumps(value, ensure_ascii=False))]))
        folder = state.output / 'calls' / name
        write_json(folder / 'request.json', request)
        write_json(folder / 'response.json', reply)
        if protocol_failure:
            write_json(folder / 'protocol_failure.json',
                       dict(error='plan/audio_draft:list_required', attempt=0, repair_limit=1))
        return dict(id=name, name=name, status='received', request_sha256=json_sha(request),
                    response_sha256=json_sha(reply), **({'repair_of': repair_of} if repair_of else {}))

    calls = [record('plan_1', base_prompt, dict(duration_s=1), protocol_failure=True)]
    if repaired:
        feedback = dict(validation_error='plan/audio_draft:list_required', repair_attempt=1, violations=[])
        prompt = base_prompt + '\n上次输出未通过本地协议校验。只修复JSON字段、ID和时间域，不得补造画面证据。' + \
            json.dumps(feedback, ensure_ascii=False)
        calls.append(record('plan_1_repair', prompt, dict(duration_s=157, audio_draft=['line']), repair_of='plan_1'))
    state.data = {**state.data, 'calls': calls, 'request_count': len(calls)}
    state._save()
    return state, media, base_prompt


def replayed_mcp(state, sent, reply=None):
    mcp = pipeline.CodexMCP.__new__(pipeline.CodexMCP)
    mcp.provider = pipeline.CodexMCP.provider
    mcp.state, mcp.output = state, state.output

    def submit(name, request, *, repair_of=None):
        state._reload()
        if any(call['request_sha256'] == json_sha(request) for call in state.data['calls']):
            return pipeline.CodexMCP._submit(mcp, name, request, repair_of=repair_of)
        sent.append(request['arguments']['prompt'])
        call, _ = state.begin_call(name, request, repair_of=repair_of)
        canned = dict(duration_s=157, audio_draft=['line']) if reply is None else reply
        response = dict(result=dict(content=[dict(type='text',
            text=json.dumps(canned, ensure_ascii=False))]))
        state.complete_call(call, response)
        return call, response

    mcp._submit = submit
    return mcp


def test_kept_old_prompt_reply_keeps_the_old_length_rule(tmp_path):
    """A pinned legacy prompt's repair reply is judged by the 180 s rule it was asked for."""
    state, media, legacy = recorded_plan_task(tmp_path, marker=False, repaired=True)
    context = budget_context()
    mcp = replayed_mcp(state, sent=[])
    upgraded = prompts.picture_first_plan_prompt(legacy, context)
    assert mcp.call('plan_1', upgraded, media, budget_validator(context), image=True) == \
        dict(duration_s=157, audio_draft=['line'])
    # The recorded repair's 157 s reply passes the legacy ceiling; nothing is resent.
    assert not any(call['name'] == 'plan_1_repair2' for call in state.data['calls'])


def test_kept_prompt_that_stated_the_budget_still_binds_it(tmp_path):
    """The same pinned-repair rerun under a marker-carrying prompt keeps the budget."""
    state, media, stated = recorded_plan_task(tmp_path, marker=True, repaired=True)
    context = budget_context()
    mcp = replayed_mcp(state, sent=[])
    with pytest.raises(ValueError, match='model_protocol_repair_exhausted:plan_1'):
        mcp.call('plan_1', stated, media, budget_validator(context), image=True)


def test_kept_old_prompt_repair_bytes_stay_legacy(tmp_path):
    """A new repair of a pinned legacy prompt carries the historical hint, not the edit hint."""
    state, media, legacy = recorded_plan_task(tmp_path, marker=False, repaired=False)
    context = budget_context()
    sent = []
    mcp = replayed_mcp(state, sent)
    upgraded = prompts.picture_first_plan_prompt(legacy, context)
    assert mcp.call('plan_1', upgraded, media, budget_validator(context), image=True) == \
        dict(duration_s=157, audio_draft=['line'])  # the fresh repair reply also passes the legacy rule
    repair = next(request for request in sent if '上次输出未通过本地协议校验' in request)
    assert '成片总时长超过本次上限' not in repair
    assert '只修复violations列出的字段及必要依赖；其余时间、ID和绑定保留原值，不要整体改写时间域或新增身份绑定。' in repair


def test_execute_records_the_applied_budget_and_states_it_in_capabilities(aware):
    aware.issues[:] = ['contradiction', 'ok']
    aware.rough()
    prompt = aware.seen['plan_1']
    assert prompts.PICTURE_AWARE_EDIT_LENGTH_MARKER in prompt
    assert '参考时长4秒' in prompt and '不得超过30秒' in prompt and '180秒' not in prompt
    assert '不得靠新增镜头或加长定格解决' in prompt
    context = next(json.loads(line) for line in reversed(prompt.splitlines())
                   if line.startswith('{') and '"edit_length_budget"' in line)
    assert context['edit_length_budget'] == dict(reference_duration_s=4, edit_max_s=30)
    assert context['render_capabilities']['max_duration_s'] == 30
    state = read(aware.output / 'library_state.json')
    budget = read(state['artifacts']['picture_aware_edit_length_budget'][-1]['path'])
    assert budget['edit_max_s'] == 30 and budget['reference_duration_s'] == 4
    assert budget['policy'] == contracts.PICTURE_AWARE_EDIT_LENGTH_POLICY
    # The revision round's fresh plan call carried the marker, so its recorded
    # rule is the budget itself.
    assert read(aware.output / 'result.json')['edit_length_budget'] == dict(reference_duration_s=4, edit_max_s=30)


def test_resumed_seed_plan_records_its_own_rule_forward(aware):
    """A --resume-from plan reused with no local plan call still states its rule.

    The failed task's recorded plan prompt now carries the length contract, so
    the reused seed is judged (and recorded) under the budget.
    """
    from omni_story.library import server_cli as cli
    task = aware.failed.task
    folder = task / 'calls' / 'glm_plan_0_repair'
    request = read(folder / 'request.json')
    request['arguments']['prompt'] += ('\n时长合同（' + prompts.PICTURE_AWARE_EDIT_LENGTH_MARKER +
                                       '）：总成片不得超过30秒。')
    write_json(folder / 'request.json', request)
    ledger = read(task / 'library_state.json')
    next(call for call in ledger['calls'] if call['id'] == 'glm_plan_0_repair')['request_sha256'] = \
        json_sha(request)
    write_json(task / 'library_state.json', ledger)
    checkpoint = cli.resume_config(task, aware.failed.ref)
    aware.issues[:] = ['ok']
    target = aware.output / 'evaluations/resume'
    result = aware.rough(target, checkpoint)
    assert result['selected_round'] == 0
    assert not any(name.startswith('plan_') for name in aware.sent)  # fully reused, no local plan call
    assert read(target / 'result.json')['edit_length_budget'] == dict(reference_duration_s=4, edit_max_s=30)


def test_legacy_seed_plan_records_the_unbounded_rule(aware):
    """The same resume with a legacy prompt records null, keeping the fine stage unbounded."""
    aware.issues[:] = ['ok']
    result = aware.rough()
    assert result['selected_round'] == 0
    assert read(aware.output / 'result.json')['edit_length_budget'] is None


# The delivered video comes from the skill fine stage: the rough budget must
# ride its authorization, or story-audio fine cuts can grow past it unchecked.

def fine_stage(tmp_path, *, budget):
    """A story-audio fine stage over the fixture's 119.4 s reference."""
    value = fine.StoryFinecut.__new__(fine.StoryFinecut)
    value.base = tmp_path
    value.state = State(tmp_path)
    value.state.input_lock = {'configuration': {'creative_audio': True}}
    value.auth = {'parent': {'duration_s': 300}, 'reference': {'duration_s': CASES['reference_duration_s']},
                  'target_duration_s': CASES['reference_duration_s'], 'target_tolerance_s': 2.0,
                  'duration_policy': 'story_and_actual_narration'}
    if budget:
        value.auth['edit_length_budget'] = dict(reference_duration_s=CASES['reference_duration_s'],
                                                edit_max_s=CASES['edit_max_s'])
    value.render_source = {'source_id': 'chain_parent', 'path': 'synthetic-parent.mp4',
                           'sha256': 'a' * 64, 'duration_s': 300, 'audio_stream_index': None}
    value.observed = {'story': [{'id': 'beat_a'}, {'id': 'beat_b'}]}
    value.knowledge, value.reference, value.inspections = 'generic skill', {}, []
    value.story_context = {'reference': {'theme': '完整表达优先'}, 'received_tts': []}
    return value


def narration_plan(duration):
    proposed = single_duration_plan(duration)
    proposed['audio_draft'] = [dict(id='voice_a', text='连贯完整的旁白。', voice='Serena',
                                    segment_ids=[proposed['segments'][0]['segment_id']])]
    return proposed


def test_fine_contract_and_validation_enforce_the_recorded_budget(tmp_path):
    value = fine_stage(tmp_path, budget=True)
    contract = value.duration_contract()
    assert contract['maximum_duration_s'] == CASES['edit_max_s'] == 149.2
    assert contract['maximum_output_frames'] == math.floor(149.2 * fine.FPS) == 4476
    assert contract['minimum_duration_s'] is None and contract['minimum_output_frames'] == 1
    with pytest.raises(ValueError, match='story_finecut:edit_length_budget_exceeded_149.2_seconds') as caught:
        value.plan_check(narration_plan(CASES['plan_total_s']['over_limit']))
    assert caught.value.diagnostics['duration_contract']['maximum_output_frames'] == 4476
    assert caught.value.diagnostics['proposed_duration_s'] == CASES['plan_total_s']['over_limit']
    value.plan_check(narration_plan(CASES['plan_total_s']['within_limit']))
    value.plan_check(narration_plan(4476 / fine.FPS))  # the frame domain's last passing frame count
    with pytest.raises(ValueError, match='story_finecut:edit_length_budget_exceeded_149.2_seconds'):
        value.plan_check(narration_plan(4477 / fine.FPS))
    held = narration_plan(CASES['plan_total_s']['over_limit'])
    held['segments'][0]['freeze_tail_s'] = 2  # freeze tails count toward the same frame cap
    with pytest.raises(ValueError, match='story_finecut:edit_length_budget_exceeded'):
        value.plan_check(held)


def test_fine_prompt_states_the_budget_and_revision_prompts_keep_it(tmp_path):
    value = fine_stage(tmp_path, budget=True)
    prompt = value.plan_prompt(value.observed)
    assert '参考时长119.4秒' in prompt and '不得超过149.2秒' in prompt
    assert '149.2' in prompt
    assert '没有硬总时长上限' not in prompt and '参考时长仅为节奏风格提示' not in prompt
    context = next(json.loads(line) for line in reversed(prompt.splitlines())
                   if line.startswith('{') and '"duration_contract"' in line)
    assert context['duration_contract']['maximum_output_frames'] == 4476
    assert context['max_duration_s'] == context['duration_contract']['maximum_duration_s'] == 149.2
    revision = value.plan_prompt(value.observed, revision=dict(
        plan=narration_plan(CASES['plan_total_s']['within_limit']),
        review=dict(target=dict(problems=[dict(start_s=1, end_s=2, description='具体问题')]))))
    assert '不得超过149.2秒' in revision and '没有硬总时长上限' not in revision
    assert '不得新增段落' in revision


def test_fine_contract_and_prompt_are_unchanged_without_a_budget(tmp_path):
    value = fine_stage(tmp_path, budget=False)
    assert value.duration_contract() == dict(
        policy='story_audio_content_duration_v1', fps=fine.FPS,
        minimum_duration_s=None, maximum_duration_s=None,
        minimum_output_frames=1, maximum_output_frames=None,
        output_frames_formula='sum(round((source_out_s-source_in_s)/speed*fps) + round(freeze_tail_s*fps))',
        rounding='Python round: nearest integer, ties to even',
        reference_duration_s=CASES['reference_duration_s'],
        duration_policy='story_and_actual_narration')
    prompt = value.plan_prompt(value.observed)
    assert '参考时长仅为节奏风格提示，按完整内容与实测配音分配时长，' in prompt
    assert 'maximum_output_frames为null表示没有硬总时长上限。无需装入原参考音轨或接近参考时长，' in prompt
    assert '149.2' not in prompt
    # The same over-long narration plan stays acceptable on the unbounded route.
    value.plan_check(narration_plan(CASES['plan_total_s']['over_limit']))


def rough_stage(tmp_path, ident, *, budget='stated', style='picture_aware',
                reference_s=CASES['reference_duration_s']):
    """A recorded rough task whose result.json carries the forward budget record.

    budget: 'stated' (the selected plan was validated under the budget), None
    (a legacy-prompt reply was reused; the rule was explicitly unbounded) or
    'absent' (a task that predates the contract).
    """
    task = tmp_path / ident
    result = dict(status='rough_planned', selected_round=1, final_video=str(task / 'final.mp4'))
    if budget == 'stated':
        result['edit_length_budget'] = dict(reference_duration_s=reference_s,
                                            edit_max_s=contracts.picture_aware_edit_max_s(reference_s))
    elif budget is None:
        result['edit_length_budget'] = None
    write_json(task / 'result.json', result)
    value = fine.StoryFinecut.__new__(fine.StoryFinecut)
    value.base = task / 'skill_finecut'
    value.state = SimpleNamespace(input_lock=dict(configuration=dict(
        creative_style=style, creative_audio=True)), data={'artifacts': {}})
    value.story_context = {'reference': {'theme': '完整表达优先'}, 'received_tts': []}
    value.auth = {'reference': {'duration_s': reference_s}}
    return value


def continued_task(tmp_path, ident, parent_result):
    """A continuation task: clean_chain copies the parent's result.json unchanged."""
    task = tmp_path / ident
    write_json(task / 'result.json', parent_result)  # write_once(output/'result.json', rough)
    return task


def test_fine_stage_reads_only_the_roughs_recorded_budget(tmp_path):
    stated = rough_stage(tmp_path, 'stated_budget')
    assert stated.rough_edit_length_budget() == dict(reference_duration_s=CASES['reference_duration_s'],
                                                     edit_max_s=CASES['edit_max_s'])
    # A resumed legacy rough recorded the null rule: its fine stage stays unbounded.
    assert rough_stage(tmp_path, 'null_budget', budget=None).rough_edit_length_budget() is None
    # Old tasks have no field at all.
    assert rough_stage(tmp_path, 'old_task', budget='absent').rough_edit_length_budget() is None
    assert rough_stage(tmp_path, 'other_style', style='classic').rough_edit_length_budget() is None
    stale = rough_stage(tmp_path, 'stale_record')
    saved = read(stale.base.parent / 'result.json')
    saved['edit_length_budget']['edit_max_s'] = 999  # cannot re-establish today's rule
    write_json(stale.base.parent / 'result.json', saved)
    assert stale.rough_edit_length_budget() is None


def test_continuations_keep_the_recorded_budget_at_any_depth(tmp_path):
    """Each continuation copies its parent's result record, so depth never loses the rule."""
    parent = rough_stage(tmp_path, 'rough')
    first = continued_task(tmp_path, 'continuation_1', read(parent.base.parent / 'result.json'))
    second = continued_task(tmp_path, 'continuation_2', read(first / 'result.json'))
    deepest = fine.StoryFinecut.__new__(fine.StoryFinecut)
    deepest.base = second / 'skill_finecut'
    deepest.state = SimpleNamespace(input_lock=dict(configuration=dict(
        creative_style='picture_aware', creative_audio=True)))
    deepest.story_context = {'reference': {'theme': '完整表达优先'}, 'received_tts': []}
    deepest.auth = {'reference': {'duration_s': CASES['reference_duration_s']}}
    assert deepest.rough_edit_length_budget() == dict(reference_duration_s=CASES['reference_duration_s'],
                                                      edit_max_s=CASES['edit_max_s'])
    legacy = rough_stage(tmp_path, 'legacy_rough', budget='absent')
    chain = continued_task(tmp_path, 'legacy_1', read(legacy.base.parent / 'result.json'))
    chain = continued_task(tmp_path, 'legacy_2', read(chain / 'result.json'))
    legacy_fine = fine.StoryFinecut.__new__(fine.StoryFinecut)
    legacy_fine.base = chain / 'skill_finecut'
    legacy_fine.state = deepest.state
    legacy_fine.story_context, legacy_fine.auth = deepest.story_context, deepest.auth
    assert legacy_fine.rough_edit_length_budget() is None


def test_skill_stage_authorization_records_only_a_stated_budget(media, tmp_path, monkeypatch):
    """The real constructor adopts the budget and binds it, or stays byte-identical."""
    from test_story_finecut import FakeMCP
    parent, reference = media
    monkeypatch.setattr(fine, 'validate_story_context', lambda *a, **k: None)  # context shape is not under test

    def stage(ident, *, budget):
        recorded = rough_stage(tmp_path, ident, budget=budget, reference_s=reference['duration_s'])
        state = State(recorded.base.parent)
        state.input_lock = {'configuration': {'workflow': 'reference_rough_skill_v1', 'creative_audio': True,
                                              'creative_style': 'picture_aware'}}
        context = {'reference': {'reference_sha256': reference['sha256']},
                   'story_context': {'reference': {'theme': '完整表达优先'}, 'received_tts': []}}
        return fine.StoryFinecut(state, FakeMCP(state), context, parent, reference, recorded.base)

    expected = dict(reference_duration_s=reference['duration_s'],
                    edit_max_s=contracts.picture_aware_edit_max_s(reference['duration_s']))
    bounded = stage('stated_budget', budget='stated')
    assert bounded.auth['edit_length_budget'] == expected
    assert bounded.duration_contract()['maximum_output_frames'] == math.floor(expected['edit_max_s'] * fine.FPS)
    assert read(bounded.base / 'input.json')['edit_length_budget'] == expected
    legacy = stage('legacy_budget', budget=None)
    assert 'edit_length_budget' not in legacy.auth
    assert 'edit_length_budget' not in read(legacy.base / 'input.json')


# S34d: the finecut call and the bound cast correction also judge each reply by
# the length rule its own request bytes stated, and record only that rule.

def renamed_segments(plan, prefix):
    """Distinct draft ids so a finecut reply can replace every draft segment."""
    mapping = {row['segment_id']: prefix + row['segment_id'] for row in plan['segments']}
    for row in plan['segments']:
        row['segment_id'] = mapping[row['segment_id']]
    for holder in (*plan['slots'], *plan['audio_draft']):
        holder['segment_ids'] = [mapping[ident] for ident in holder['segment_ids']]
    return plan


def refinement_reply(final_plan, draft):
    """A finecut reply meeting the actual active_finecut refinement contract."""
    ids = [row['segment_id'] for row in final_plan['segments']]
    total = round(sum((row['source_out_s'] - row['source_in_s']) / row['speed']
                      + row.get('freeze_tail_s', 0) for row in final_plan['segments']), 3)
    return dict(plan=deepcopy(final_plan),
        decisions=[dict(segment_id=ident, new_information='新增关键信息', in_out_reason='动作完整',
            speed_reason='原速自然', hold_reason='无需停留', origin='general_optimization',
            reference_method_ids=[]) for ident in ids],
        obligation_coverage=[dict(slot_id=slot['slot_id'], segment_ids=ids, status='preserved',
            reason='义务由最终镜头承载') for slot in draft['slots']],
        draft_dispositions=[dict(segment_id=row['segment_id'], decision='removed',
            replacement_segment_ids=[], reason='以更完整的关键瞬间替代') for row in draft['segments']],
        duration=dict(target_s=total, total_s=total, over_target_reason='无'))


def finecut_validator(failed, context, windows, draft):
    """The two-layer contract execute judges a finecut reply with."""
    from omni_story.library import active_finecut
    plan_check = plan_validator(failed, context, windows)

    def validate_refinement(value):
        active_finecut.validate_refinement(value, draft, 32)
        plan_check(value['plan'])
    # execute wires the live context into its refinement validator the same way.
    validate_refinement.edit_length_context = context
    return validate_refinement


def recorded_finecut_task(tmp_path, *, current):
    """A recorded finecut_0 call with a protocol failure, pinned to its own bytes.

    current: the recorded request already embeds today's budget in its evidence
    JSON; otherwise the recorded bytes are legacy and never stated a rule.
    """
    from omni_story.library import active_finecut
    media = tmp_path / 'finecut_sheet.bin'
    media.write_bytes(b'finecut navigation sheet')
    state = LibraryState(tmp_path / 'run', dict(configuration=dict(
        provider=pipeline.CodexMCP.provider, creative_style='picture_aware')))
    if current:
        active_finecut.enable_policy(state, True)
        prompt = active_finecut.refinement_prompt(state, dict(segments=[], slots=[]), budget_context())
        assert prompts.PICTURE_AWARE_EDIT_LENGTH_MARKER not in prompt and '"edit_length_budget":' in prompt
    else:
        prompt = 'recorded finecut prompt without the contract'
    request = dict(tool='analyze_image', arguments=dict(image_source=str(media), prompt=prompt),
                   media_sha256=sha256_file(media), provider=pipeline.CodexMCP.provider,
                   policy_version=prompts.POLICY_VERSION)
    reply = dict(result=dict(content=[dict(type='text', text='{}')]))
    folder = state.output / 'calls' / 'finecut_0'
    write_json(folder / 'request.json', request)
    write_json(folder / 'response.json', reply)
    write_json(folder / 'protocol_failure.json',
               dict(error='finecut:plan_required', attempt=0, repair_limit=1))
    state.data = {**state.data, 'calls': [dict(id='finecut_0', name='finecut_0', status='received',
        request_sha256=json_sha(request), response_sha256=json_sha(reply))], 'request_count': 1}
    state._save()
    return state, media, prompt


def test_kept_old_finecut_prompt_reply_keeps_the_old_length_rule(tmp_path, failed):
    """A pinned legacy finecut request's 157 s refinement reply passes the rule it stated."""
    sha = sha256_file(failed.ref)
    windows = long_windows(failed, 8)
    draft = renamed_segments(edit_plan(sha, CASES['plan_total_s']['within_limit'], windows), 'draft_')
    state, media, legacy = recorded_finecut_task(tmp_path, current=False)
    over = refinement_reply(edit_plan(sha, CASES['plan_total_s']['over_limit'], windows), draft)
    mcp = replayed_mcp(state, sent=[], reply=over)
    today = prompts.picture_first_plan_prompt(legacy, budget_context())
    accepted = mcp.call('finecut_0', today, media,
                        finecut_validator(failed, budget_context(), windows, draft), image=True)
    assert accepted['plan']['segments'] == over['plan']['segments']  # the real validator normalised the rest
    assert accepted['duration'] == over['duration']
    state._reload()
    assert not any(call['name'] == 'finecut_0_repair2' for call in state.data['calls'])


def test_kept_finecut_prompt_that_stated_the_budget_still_binds_it(tmp_path, failed):
    """The same rerun under a recorded budget-carrying finecut prompt keeps the budget."""
    sha = sha256_file(failed.ref)
    windows = long_windows(failed, 8)
    draft = renamed_segments(edit_plan(sha, CASES['plan_total_s']['within_limit'], windows), 'draft_')
    state, media, current = recorded_finecut_task(tmp_path, current=True)
    over = refinement_reply(edit_plan(sha, CASES['plan_total_s']['over_limit'], windows), draft)
    mcp = replayed_mcp(state, sent=[], reply=over)
    with pytest.raises(ValueError, match='model_protocol_repair_exhausted:finecut_0'):
        mcp.call('finecut_0', current, media,
                 finecut_validator(failed, budget_context(), windows, draft), image=True)


def test_execute_wires_its_refinement_validator_into_the_per_request_rule():
    """The refinement validator carries the live context CodexMCP withholds from."""
    import inspect
    assert 'validate_refinement.edit_length_context = context' in inspect.getsource(pipeline.execute)


def received_call(state, ident, name, prompt):
    """A received call record with the given request prompt bytes."""
    write_json(state.output / 'calls' / ident / 'request.json', dict(arguments=dict(prompt=prompt)))
    return dict(id=ident, name=name, status='received', request_sha256='0' * 64, response_sha256='0' * 64)


def test_recorded_rule_follows_the_accepted_finecut_request(tmp_path):
    """The round's record states the rule the accepted finecut request carried."""
    from omni_story.library import active_finecut
    state = LibraryState(tmp_path / 'run', dict(configuration=dict(creative_style='picture_aware')))
    active_finecut.enable_policy(state, True)
    current = active_finecut.refinement_prompt(state, dict(segments=[], slots=[]), budget_context())
    budget = budget_context()['edit_length_budget']
    calls = [received_call(state, 'c0', 'finecut_0', 'recorded finecut prompt without the contract'),
             received_call(state, 'c1', 'finecut_0_repair', current)]
    state.data = {**state.data, 'calls': calls, 'request_count': len(calls)}
    state._save()
    assert pipeline._authored_plan_budget(state, state.output, 0, budget, stage='finecut') == budget
    legacy = [received_call(state, 'c2', 'finecut_0_repair2', 'recorded finecut prompt without the contract')]
    state.data = {**state.data, 'calls': calls + legacy, 'request_count': len(calls) + 1}
    state._save()
    # A kept legacy repair's reply is judged (and recorded) under the old rule.
    assert pipeline._authored_plan_budget(state, state.output, 0, budget, stage='finecut') is None


def test_recorded_rule_follows_the_accepted_correction_alias(tmp_path):
    """A bound cast correction's plan records the rule its own alias request stated."""
    state = LibraryState(tmp_path / 'run', dict(configuration=dict(creative_style='picture_aware')))
    budget = budget_context()['edit_length_budget']
    marker = prompts.picture_first_plan_prompt('plan prompt', budget_context())
    older = received_call(state, 'c0', 'plan_1', marker)
    alias = received_call(state, 'c1', 'plan_1_cast_correction_v1', 'recorded correction prompt')
    state.data = {**state.data, 'calls': [older, alias], 'request_count': 2}
    state._save()
    # The correction's own request never stated the rule, even though the older
    # recorded plan call did.
    assert pipeline._authored_plan_budget(state, state.output, 1, budget) is None
    stated = received_call(state, 'c2', 'plan_1_cast_correction_v1_preflight_v1', marker)
    state.data = {**state.data, 'calls': [older, alias, stated], 'request_count': 3}
    state._save()
    assert pipeline._authored_plan_budget(state, state.output, 1, budget) == budget


def bound_correction(tmp_path, ident, prompt, reply):
    """A task with one bound cast-correction claim over the given request bytes."""
    from omni_story.library import search_recovery
    media = tmp_path / ident / 'sheet.bin'
    media.parent.mkdir(parents=True)
    media.write_bytes(b'correction sheet')
    state = LibraryState(tmp_path / ident / 'task', dict(configuration=dict(
        provider=pipeline.CodexMCP.provider, creative_style='picture_aware')), max_requests=5)
    request = dict(tool='analyze_image', arguments=dict(image_source=str(media), prompt=prompt),
                   media_sha256=sha256_file(media))
    snapshot = tmp_path / ident / 'baseline.json'
    snapshot.write_bytes(state.path.read_bytes())
    proof = tmp_path / ident / 'proof.json'
    write_json(proof, dict(plan_name='plan_1', baseline_snapshot=str(snapshot),
        state_sha256=sha256_file(snapshot), expected_request=request))
    write_json(state.output / 'plan_cast_correction_v1.claim.json',
        dict(proof_path=str(proof), proof_sha256=sha256_file(proof)))

    class Model:
        def __init__(self):
            self.sent = []

        def _submit(self, name, request, *, repair_of=None):
            self.sent.append(name)
            call, _ = state.begin_call(name, request, repair_of=repair_of)
            response = dict(result=dict(content=[dict(type='text',
                text=json.dumps(reply, ensure_ascii=False))]))
            state.complete_call(call, response)
            return call, response
    return state, Model(), search_recovery


def test_bound_correction_reply_keeps_the_rule_its_request_stated(tmp_path):
    """A fixed expected_request without the contract judges its 157 s reply by the old rule."""
    context = budget_context()
    reply = dict(duration_s=CASES['plan_total_s']['over_limit'], audio_draft=['line'])
    state, model, search_recovery = bound_correction(tmp_path, 'legacy',
        'recorded correction prompt without the contract', reply)
    assert search_recovery.correct_plan(state, model, 'plan_1', budget_validator(context)) == reply
    assert model.sent == ['plan_1_cast_correction_v1']


def test_bound_correction_that_stated_the_budget_still_binds_it(tmp_path):
    """The same correction bound to a marker-carrying request keeps the budget."""
    context = budget_context()
    reply = dict(duration_s=CASES['plan_total_s']['over_limit'], audio_draft=['line'])
    state, model, search_recovery = bound_correction(tmp_path, 'stated',
        prompts.picture_first_plan_prompt('recorded correction prompt', context), reply)
    with pytest.raises(LibraryStopped,
                       match='single_received_correction_invalid:plan:duration_exceeds_149.2_seconds'):
        search_recovery.correct_plan(state, model, 'plan_1', budget_validator(context))


def test_bound_binding_plan_keeps_the_rule_its_source_request_stated(tmp_path, monkeypatch):
    """The main-authored binding plan is judged by its recorded source request."""
    from omni_story.library import main_plan_binding, search_recovery
    media = tmp_path / 'binding' / 'sheet.bin'
    media.parent.mkdir(parents=True)
    media.write_bytes(b'source sheet')
    state = LibraryState(tmp_path / 'binding' / 'task', dict(configuration=dict(
        provider=pipeline.CodexMCP.provider, creative_style='picture_aware')), max_requests=5)
    write_json(state.output / 'calls' / 'source_call' / 'request.json',
        dict(arguments=dict(image_source=str(media), prompt='recorded source prompt without the contract')))
    proof = tmp_path / 'binding' / 'proof.json'
    write_json(proof, dict(plan_name='plan_1', source_call_id='source_call'))
    write_json(state.output / 'plan_binding_repair_v1.claim.json',
        dict(proof_path=str(proof), proof_sha256=sha256_file(proof)))
    seen = []
    monkeypatch.setattr(main_plan_binding, 'load',
        lambda state_arg, stage, validator: seen.append(validator) or None)
    context = budget_context()
    assert search_recovery.correct_plan(state, SimpleNamespace(), 'plan_1',
                                        budget_validator(context)) is None
    seen[0](dict(duration_s=CASES['plan_total_s']['over_limit'], audio_draft=['line']))
    assert 'edit_length_budget' not in seen[0].protocol_context
