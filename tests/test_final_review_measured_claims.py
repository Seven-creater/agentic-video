"""S30's untouched reply, local fake native-frame measurements, no model/network."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from omni_story.library import contracts, final_visual_review as review, media as media_tools
from omni_story.library.media import sha256_file
from omni_story.library.opencode_provider import OpenCodeMCP
from omni_story.library.replay_check import nested_validator
from omni_story.library.state import LibraryStopped, json_sha, write_json
from test_protocol_feedback import harness, feedback


RAW = json.loads((Path(__file__).parent/'fixtures/s30_final_review_reply.json').read_text())
DURATION = 118.1


def typed_reply(kind='black_frame', check='edit_coherence_ok'):
    value = deepcopy(RAW)
    value['issues'][0].update(kind=kind, check=check)
    return value


def passed_reply():
    return dict(typed_reply(), status='pass', edit_coherence_ok=True, issues=[])


def validator(normalize=True, *, new_prompt=True):
    # Use precisely the production closure, as the offline replay/fault gates do.
    validate = nested_validator(review.review_final_layout, duration=DURATION, normalize=normalize)
    return lambda value: validate(value, new_prompt=new_prompt)


@pytest.fixture
def local_review(tmp_path, monkeypatch):
    def setup(replies, luma=(36, 40, 43)):
        mcp, video, sent = harness(tmp_path, monkeypatch, replies,
                                  provider='official_vision_mcp_in_opencode')
        mcp.state.input_lock['configuration']['creative_audio'] = True
        mcp.state._save()
        proxy = tmp_path/'proxy.bin'
        proxy.write_bytes(b'reviewed proxy: never used for deterministic measurements')
        digest = sha256_file(video)
        media = dict(path=str(proxy), sha256=sha256_file(proxy), source_sha256=digest,
            kind='continuous_window', source_start_s=0, source_end_s=DURATION, spec=dict(roi=None))
        write_json(tmp_path/'lineage.json', media)
        monkeypatch.setattr(review, 'probe_media', lambda _: dict(duration_s=DURATION))
        monkeypatch.setattr(review, 'proxy', lambda *a, **k: media)
        probes = []
        def run(command, **kwargs):
            assert command[command.index('-i')+1] == str(video.resolve())
            assert '-r' not in command and 'fps=' not in command[command.index('-vf')+1]
            probes.append(command)
            return '\n'.join(f'frame:{i}\nlavfi.signalstats.YAVG={v}' for i, v in enumerate(luma))
        monkeypatch.setattr(media_tools, '_run', run)
        def execute(**kwargs):
            return review.review_final_layout(video, tmp_path/'review', mcp, normalize=True, **kwargs)
        return execute, mcp, video, sent, probes
    return setup


def test_evidence_point_black_claim_widened_refuted_and_receipted(local_review):
    raw = typed_reply()
    execute, mcp, video, sent, probes = local_review([raw])
    result = execute()
    assert result['status'] == 'pass' and result['edit_coherence_ok'] is True and result['issues'] == []
    issue = result['refuted_issues'][0]
    assert [issue['start_s'], issue['end_s']] == [66.5, 67.5]
    measurement = issue['measurement']
    assert measurement['window_s'] == [66.5, 67.5]
    assert measurement['min_luma'] == 36 and measurement['mean_luma'] == pytest.approx(119/3)
    assert measurement['threshold'] == pytest.approx(7.65) and measurement['black_frame_count'] == 0
    assert measurement['frame_count'] == 3 and measurement['picture_area']['h'] == .5
    assert result['normalization'][0]['reason'] == 'point_time_widened'
    assert result['normalization'][0]['original'] == raw['issues'][0]
    assert json.loads(Path(result['receipt_path']).read_text()) == result
    call_dir = mcp.state.output/'calls'/mcp.state.data['calls'][0]['id']
    response = json.loads((call_dir/'response.json').read_text())
    assert json.loads(response['result']['content'][0]['text']) == raw
    assert json.loads((call_dir/'parsed.json').read_text()) == raw
    assert len(probes) == len(sent) == 1
    assert 'kind' in sent[0]['arguments']['prompt'] and 'check' in sent[0]['arguments']['prompt']


def test_real_black_native_frame_still_stops(local_review):
    execute, _, _, _, probes = local_review([typed_reply()], luma=(36, 0, 43))
    result = execute()
    assert result['status'] == 'needs_revision' and result['edit_coherence_ok'] is False
    assert 'refuted_issues' not in result
    assert result['issues'][0]['measurement']['black_frame_count'] == 1
    assert result['issues'][0]['measurement']['min_luma'] == 0 and len(probes) == 1


@pytest.mark.parametrize('point,window', [(0, [0, .5]), (DURATION, [DURATION-.5, DURATION])])
@pytest.mark.parametrize('normalize', [False, True])
def test_point_window_clips_before_normalization(point, window, normalize):
    value = typed_reply()
    value['issues'][0].update(start_s=point, end_s=point)
    validator(normalize)(value)
    assert [value['issues'][0][k] for k in ('start_s', 'end_s')] == window
    before = deepcopy(value)
    validator(normalize)(value)
    assert value == before  # Stable when recorded raw and saved views are revalidated.


@pytest.mark.parametrize('check', review.CHECKS)
def test_false_check_without_evidence_enters_existing_format_repair(local_review, check):
    invalid = dict(passed_reply(), status='needs_revision', **{check: False})
    with pytest.raises(ValueError, match='final_visual_review_inconsistent_verdict'):
        validator()(deepcopy(invalid))
    execute, mcp, _, sent, _ = local_review([invalid, passed_reply()])
    assert execute()['status'] == 'pass'
    assert len(sent) == 2
    assert feedback(sent[1])['validation_error'] == 'final_visual_review_inconsistent_verdict'
    assert mcp.state.data['calls'][1]['repair_of'] == mcp.state.data['calls'][0]['id']


def test_new_contract_requires_evidence_for_each_false_check():
    value = typed_reply('other')
    value['framing_ok'] = False
    with pytest.raises(ValueError, match='final_visual_review_inconsistent_verdict'):
        validator()(value)


def test_normalized_away_issue_cannot_leave_false_check_unevidenced():
    value = typed_reply('other')
    value['issues'][0].update(start_s=120, end_s=121)
    with pytest.raises(ValueError, match='final_visual_review_inconsistent_verdict'):
        validator()(value)
    assert value['normalization'][0]['dropped'] is True


def test_other_is_never_automatically_refuted(local_review):
    execute, _, _, _, probes = local_review([typed_reply('other')])
    result = execute()
    assert result['status'] == 'needs_revision' and not result['edit_coherence_ok']
    assert len(result['issues']) == 1 and not result.get('refuted_issues') and probes == []


def test_unrefuted_issue_keeps_same_check_false(local_review):
    value = typed_reply()
    value['issues'].append(dict(value['issues'][0], kind='other', description='镜头乱序'))
    execute, _, _, _, _ = local_review([value])
    result = execute()
    assert result['status'] == 'needs_revision' and result['edit_coherence_ok'] is False
    assert len(result['refuted_issues']) == len(result['issues']) == 1


@pytest.mark.parametrize('field', ['kind', 'check'])
@pytest.mark.parametrize('invalid', ['invalid', None, [], 1])
def test_present_invalid_enums_repair_even_when_interval_would_drop(local_review, field, invalid):
    value = typed_reply()
    value['issues'][0][field] = invalid
    value['issues'][0].update(start_s=120, end_s=121)
    with pytest.raises(ValueError, match='final_visual_review_invalid_contract'):
        validator()(deepcopy(value))
    execute, mcp, _, sent, probes = local_review([value, passed_reply()])
    assert execute()['status'] == 'pass' and len(sent) == 2 and probes == []
    assert feedback(sent[1])['validation_error'] == 'final_visual_review_invalid_contract'
    assert mcp.state.data['calls'][1]['repair_of'] == mcp.state.data['calls'][0]['id']


@pytest.mark.parametrize('normalize', [False, True])
def test_legacy_point_time_and_empty_false_check_keep_old_validation(normalize):
    value = deepcopy(RAW)
    validator(normalize, new_prompt=False)(value)
    if normalize:
        expected = deepcopy(RAW)
        contracts.normalize_intervals(expected, expected, 'issues', DURATION)
        assert value == expected and value['issues'] == []
    else:
        assert value == RAW
    empty = dict(RAW, issues=[])
    validator(normalize, new_prompt=False)(empty)
    assert empty == dict(RAW, issues=[])


@pytest.mark.parametrize('missing', [('kind',), ('check',), ('kind', 'check')])
def test_optional_fields_defaults_are_only_receipted(local_review, missing):
    value = typed_reply('other')
    value.update(framing_ok=False)
    value['issues'][0].update(start_s=66, end_s=68)
    for field in missing:
        del value['issues'][0][field]
    if missing == ('kind',):
        # Even an explicitly attributed issue leaves legacy validation unchanged.
        value['issues'][0]['check'] = 'subtitle_ok'
    before = deepcopy(value)
    execute, mcp, _, sent, probes = local_review([value])
    result = execute()
    assert result['status'] == 'needs_revision' and result['issues'] == value['issues']
    assert result['framing_ok'] is False and result['edit_coherence_ok'] is False
    expected = [dict(path=f'$.issues[0].{field}', default='other' if field == 'kind'
        else ['framing_ok', 'edit_coherence_ok']) for field in missing]
    assert result['contract_defaults'] == expected and probes == [] and len(sent) == 1
    folder = mcp.state.output/'calls'/mcp.state.data['calls'][0]['id']
    assert json.loads((folder/'parsed.json').read_text()) == before
    reply = json.loads((folder/'response.json').read_text())
    assert json.loads(reply['result']['content'][0]['text']) == before
    assert json.loads(Path(result['receipt_path']).read_text()) == result


def test_missing_check_supports_every_false_check_and_refutes_together(local_review):
    value = typed_reply()
    value['framing_ok'] = False
    del value['issues'][0]['check']
    execute, _, _, sent, _ = local_review([value])
    result = execute()
    assert result['status'] == 'pass' and len(sent) == 1
    assert result['framing_ok'] and result['edit_coherence_ok']
    assert result['contract_defaults'] == [dict(path='$.issues[0].check',
        default=['framing_ok', 'edit_coherence_ok'])]


def test_untyped_survivor_prevents_refuted_typed_issue_restoring_check(local_review):
    value = typed_reply()
    value['issues'].append(dict(start_s=66, end_s=68, description='镜头乱序'))
    execute, _, _, _, _ = local_review([value])
    result = execute()
    assert result['status'] == 'needs_revision' and not result['edit_coherence_ok']
    assert result['issues'] == value['issues'][1:] and len(result['refuted_issues']) == 1


@pytest.mark.parametrize('saved_kind', ['raw', 'old_normalized', 'old_clamped'])
def test_recorded_old_reply_reused_without_repair_or_rewriting_calls(local_review, saved_kind):
    execute, mcp, video, sent, _ = local_review([typed_reply()])
    stage = 'final_layout_review_' + sha256_file(video)[:16]
    proxy = video.parent/'proxy.bin'
    scope = dict(kind='continuous_window', source_sha256=sha256_file(video),
                 source_start_s=0, source_end_s=DURATION)
    request = dict(tool='analyze_video', arguments=dict(video_source=str(proxy), prompt='旧终审提示'),
        media_sha256=sha256_file(proxy), provider=mcp.provider,
        policy_version=mcp.state.data['policy_version'], observation_scope=scope)
    call, folder = mcp.state.begin_call(stage, request)
    response = dict(result=dict(content=[dict(type='text', text=json.dumps(RAW, ensure_ascii=False))]))
    mcp.state.complete_call(call, response)
    saved = deepcopy(RAW)
    if saved_kind != 'raw':
        contracts.normalize_intervals(saved, saved, 'issues', DURATION)
    if saved_kind == 'old_clamped':
        del saved['normalization']
    write_json(folder/'parsed.json', saved)
    before = {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}
    result = execute()
    expected = deepcopy(RAW)
    contracts.normalize_intervals(expected, expected, 'issues', DURATION)
    assert {key: result[key] for key in expected} == expected
    assert result['status'] == 'needs_revision' and len(sent) == 0
    assert result['contract_defaults'] == [dict(path='$.issues[0].kind', default='other'),
        dict(path='$.issues[0].check', default=['edit_coherence_ok'])]
    assert {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()} == before
    assert len(mcp.state.data['calls']) == 1
    assert execute()['status'] == 'needs_revision' and len(sent) == 0
    # A later altered derived parse must still stop, rather than authorize repair.
    saved['visual_notes'] = ['被改写的观察']
    write_json(folder/'parsed.json', saved)
    with pytest.raises(LibraryStopped, match='recorded_parsed_reply_modified'):
        execute()


def test_resume_new_prompt_misses_parent_review_and_has_new_request_digest(local_review, monkeypatch):
    execute, mcp, video, sent, _ = local_review([typed_reply()])
    stage = 'final_layout_review_' + sha256_file(video)[:16]
    parent = video.parent/'parent'
    ledger = parent/'library_state.json'
    old_request = dict(tool='analyze_video', arguments=dict(video_source=str(video.parent/'proxy.bin'),
        prompt='旧终审提示'), media_sha256=sha256_file(video.parent/'proxy.bin'), provider=mcp.provider,
        policy_version=mcp.state.data['policy_version'], observation_scope=dict(kind='continuous_window',
        source_sha256=sha256_file(video), source_start_s=0, source_end_s=DURATION))
    write_json(parent/'calls/old/request.json', old_request)
    write_json(parent/'calls/old/response.json', RAW)
    write_json(ledger, dict(calls=[dict(id='old', name=stage, status='received',
        request_sha256=json_sha(old_request))]))
    before = {p: p.read_bytes() for p in parent.rglob('*') if p.is_file()}
    owner = OpenCodeMCP.__new__(OpenCodeMCP)
    owner.__dict__.update(mcp.__dict__)
    owner.parent_baseline = dict(linked_ledgers=[dict(path=str(ledger), sha256=sha256_file(ledger))])
    monkeypatch.setattr(owner, '_parent_observation', lambda *a: pytest.fail('final review is not a reusable parent stage'))
    monkeypatch.setattr(mcp, 'call', owner.call)
    assert execute()['status'] == 'pass' and len(sent) == 1
    assert json_sha(sent[0]) != json_sha(old_request)
    restored = deepcopy(sent[0])
    restored['arguments']['prompt'] = old_request['arguments']['prompt']
    assert restored == old_request  # The prompt alone changes the MCP request digest.
    assert {p: p.read_bytes() for p in parent.rglob('*') if p.is_file()} == before
    assert mcp.state.data['calls'][0]['repair_of'] is None


def test_normal_pass_reply_unchanged(local_review):
    value = passed_reply()
    execute, _, _, _, probes = local_review([value])
    result = execute()
    assert {key: result[key] for key in value} == value
    assert 'normalization' not in result and 'refuted_issues' not in result and probes == []


def test_probe_failure_retains_issue(local_review, monkeypatch):
    execute, _, _, _, _ = local_review([typed_reply()])
    monkeypatch.setattr(media_tools, '_run', lambda *a, **k: '')
    result = execute()
    assert result['status'] == 'needs_revision'
    assert result['issues'][0]['measurement']['outcome'] == 'unverified'


@pytest.mark.parametrize('policy,overlap,changed,expected', [
    ('silent_subtitle_masked', False, False, 'pass'), ('absent', False, False, 'pass'),
    (None, False, False, 'needs_revision'), ('silent_subtitle_masked', True, False, 'needs_revision'),
    ('silent_subtitle_masked', False, True, 'needs_revision')])
def test_subtitle_overlap_requires_recorded_burn_cues_and_source_facts(local_review, policy, overlap, changed, expected):
    value = typed_reply('subtitle_overlap', 'subtitle_ok')
    value.update(edit_coherence_ok=True, subtitle_ok=False)
    execute, _, video, _, probes = local_review([value])
    ass = video.with_suffix('.ass')
    ass.write_text('[Events]\nDialogue: 0,0:01:06.00,0:01:07.00,Default,,0,0,0,,旧句\n'
        f'Dialogue: 0,0:01:{"06.90" if overlap else "07.00"},0:01:08.00,Default,,0,0,0,,新句\n')
    write_json(video.with_suffix(video.suffix+'.receipt.json'), dict(operation='final_subtitles',
        sha256=sha256_file(video), subtitle_ass_path=str(ass), subtitle_ass_sha256=sha256_file(ass)))
    if changed:
        ass.write_text('changed cues')
    source_facts = dict(policy=policy, story_context_sha256=json_sha(dict(source_visual_policy=policy)))
    result = execute(source_subtitles=source_facts)
    assert result['status'] == expected and probes == []
    if expected == 'pass':
        measurement = result['refuted_issues'][0]['measurement']
        assert measurement['overlapping_pairs'] == [] and len(measurement['cues']) == 2
        assert measurement['source_subtitles'] == source_facts and result['subtitle_ok'] is True
        assert set(measurement['evidence_sha256']) <= set(result['evidence_sha256'])


def test_srt_alone_does_not_prove_source_subtitles_absent(local_review):
    value = typed_reply('subtitle_overlap', 'subtitle_ok')
    value.update(edit_coherence_ok=True, subtitle_ok=False)
    execute, _, video, _, _ = local_review([value])
    video.with_suffix('.srt').write_text('1\n00:01:06,000 --> 00:01:08,000\n正常字幕\n')
    result = execute()
    assert result['status'] == 'needs_revision'
    assert result['issues'][0]['measurement']['reason'] == 'subtitle_burn_receipt_missing'


def test_measurement_cannot_pass_changed_final_bytes(local_review, monkeypatch):
    execute, _, video, _, _ = local_review([typed_reply()])
    probe = review.probe_luma_window
    def measure(*args):
        value = probe(*args)
        video.write_bytes(b'changed final bytes')
        return value
    monkeypatch.setattr(review, 'probe_luma_window', measure)
    with pytest.raises(LibraryStopped, match='final_visual_review_source_changed'):
        execute()


@pytest.mark.parametrize('normalize', [True, False])
def test_new_prompt_point_time_without_kind_is_widened_not_dropped(normalize):
    # The production reply shape (67→67, no kind) answering the new prompt keeps its evidence.
    value = deepcopy(RAW)
    validator(normalize, new_prompt=True)(value)
    assert [(row['start_s'], row['end_s']) for row in value['issues']] == [(66.5, 67.5)]
    assert any(row.get('reason') == 'point_time_widened' for row in value['normalization'])
