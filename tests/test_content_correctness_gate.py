"""Content must be expressed with pictures and lines; all model/media calls are fake."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from omni_story.library import contracts, pipeline, prompts, server_cli as cli
from omni_story.library.media import sha256_file
from omni_story.library.state import LibraryState, LibraryStopped, json_sha, write_json
from test_picture_aware import aware, ReachedTTS
from test_resume_from import failed, read


CASES = read(Path(__file__).parent / 'fixtures/s32_content_reviews.json')


@pytest.mark.parametrize('case', CASES, ids=CASES)
def test_content_rounds_before_tts(aware, monkeypatch, case):
    scenario = CASES[case]
    aware.issues[:] = scenario['issues']
    aware.jumps[:] = [False] * len(aware.issues)
    if 'roles' in scenario:
        aware.roles[0] = scenario['roles']
    monkeypatch.setattr(cli, '_run_task', lambda args: aware.chain())
    args = SimpleNamespace(output=aware.output)
    if scenario.get('stop'):
        with pytest.raises(LibraryStopped, match='content_not_expressed:.*line'):
            cli._run(args)
        outcome = read(aware.output / 'run_outcome.json')
        assert outcome['outcome'] == 'stopped' and outcome['final_video'] is None
        assert outcome['error_code'] == 'content_not_expressed'
        assert outcome['stage'] == 'content_correctness_gate'
        diagnostic = outcome['stop_diagnostic']
        assert diagnostic['revision_count'] == 2
        assert diagnostic['remaining_must_fix'][0]['line_id'] == 'line'
        assert diagnostic['remaining_must_fix'][0]['evidence']
        assert 'TTS' not in aware.events
        assert not (aware.output / 'result.json').exists()
    else:
        with pytest.raises(ReachedTTS):
            cli._run(args)
        assert aware.events[-1] == 'TTS'
        assert read(aware.output / 'result.json')['selected_round'] == scenario['selected_round']
    review = read(aware.output / 'silent_review.json')
    assert not review['advisory']
    assert len(review['reviews']) == len(aware.issues)
    assert [row['revision_count'] for row in review['reviews']] == list(range(len(aware.issues)))
    assert review['revision_count'] == len(aware.issues) - 1
    for row in review['reviews']:
        assert row['content_acceptable'] == (not row['must_fix'])
    assert read(aware.output / 'run_outcome.json')['silent_review'] == review
    assert [name for name in aware.sent if name.startswith('plan_')] == [
        f'plan_{i}' for i in range(1, len(aware.issues))]
    assert len(aware.sent) == 3 * len(aware.issues) - 1  # Initial plan is a received fixture.
    assert not any(name.startswith(('search_', 'fine_', 'chain_')) for name in aware.sent)
    if 'roles' in scenario:
        feedback = aware.seen['plan_1']
        assert scenario['roles']['evidence'] in feedback
        assert '一个故事角色对应一个电影人物' in feedback
        assert '首次出现用画面或台词交代谁是谁' in feedback
        assert 'must_fix必须修好' in feedback
    if aware.issues[0] == 'contradiction':
        feedback = aware.seen['plan_1'].split('静音审查反馈：', 1)[1]
        assert feedback.index('must_fix') < feedback.index('issues')
        assert feedback.index('(a)') < feedback.index('(b)') < feedback.index('(c)')
        assert '最小可见条件' in feedback


def test_role_failure_persists_stops_with_role_evidence(aware):
    aware.issues[:] = ['ok'] * 3
    aware.jumps[:] = [False] * 3
    for role in aware.roles:
        role.update(consistent=False, evidence='line：顾客从鹅换成师父。')
    with pytest.raises(LibraryStopped, match='content_not_expressed:.*顾客从鹅换成师父') as caught:
        aware.chain()
    assert caught.value.diagnostics['remaining_must_fix'] == [
        dict(issue='roles.consistent', evidence='line：顾客从鹅换成师父。')]
    assert 'TTS' not in aware.events


@pytest.mark.parametrize('revision', [0, 1])
def test_unavailable_content_check_continues_and_is_in_outcome(aware, monkeypatch, revision):
    aware.issues[:] = ['contradiction', 'ok'] if revision else ['ok']
    aware.failures[f'silent_review_{revision}'] = 'failed_known'
    monkeypatch.setattr(cli, '_run_task', lambda args: aware.chain())
    with pytest.raises(ReachedTTS):
        cli._run(SimpleNamespace(output=aware.output))
    review = read(aware.output / 'silent_review.json')
    assert review['content_acceptable'] is None and review['must_fix'] == []
    assert review['revision_count'] == revision and review['review_unavailable']['reason']
    assert read(aware.output / 'run_outcome.json')['silent_review'] == review
    assert 'plan_2' not in aware.sent
    assert aware.events[-1] == 'TTS'


def test_compacted_legacy_review_without_roles_replays_advisory_unchanged(aware, monkeypatch):
    aware.issues[:] = ['contradiction', 'contradiction']
    with monkeypatch.context() as patch:
        patch.setattr(pipeline, '_recorded_picture_aware_review', lambda *a: dict(prompt_bytes=1, verdict={}))
        first = aware.rough()
    review = first['silent_review']
    assert review['advisory'] and all('roles' not in row['verdict'] for row in review['reviews'])
    assert all('prompt_bytes' in row and 'content_acceptable' not in row for row in review['reviews'])
    record = (aware.output / 'silent_review.json').read_bytes()
    files = {path: path.read_bytes() for path in (aware.output / 'calls').glob('*/*.json')}
    sent = list(aware.sent)
    assert aware.rough()['silent_review'] == review
    assert (aware.output / 'silent_review.json').read_bytes() == record
    write_json(aware.output / '.omni-server/job.json', dict(job_id='parent', state='failed'))
    checkpoint = cli.resume_config(aware.output, aware.failed.ref)
    child = aware.output / 'evaluations/resume'
    assert aware.rough(child, checkpoint)['silent_review'] == review
    assert (child / 'silent_review.json').read_bytes() == record
    assert aware.sent == sent and all(path.read_bytes() == value for path, value in files.items())


def test_receipt_without_legacy_report_gets_new_digest_and_call(aware, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(pipeline, '_recorded_picture_aware_review', lambda *a: dict(prompt_bytes=1, verdict={}))
        aware.rough()
    saved = read(aware.output / 'library_state.json')
    old = next(call for call in saved['calls'] if call['name'] == 'silent_review_0')
    old_reply = aware.output / 'calls' / old['id'] / 'response.json'
    old_bytes = old_reply.read_bytes()
    (aware.output / 'silent_review.json').unlink()  # Simulate a crash before recording the report.
    before = len(aware.sent)
    review = aware.rough()['silent_review']
    assert aware.sent[before:] == ['silent_review_0']
    assert review['content_acceptable'] and review['reviews'][0]['verdict']['roles']['clear']
    calls = [call for call in read(aware.output / 'library_state.json')['calls'] if call['name'] == 'silent_review_0']
    assert len(calls) == 2 and calls[0]['request_sha256'] != calls[1]['request_sha256']
    assert old_reply.read_bytes() == old_bytes
    sent = list(aware.sent)
    assert aware.rough()['silent_review'] == review and aware.sent == sent


@pytest.mark.parametrize('stopped', [False, True])
def test_plan_2_replays_without_new_calls_across_resume_generations(aware, stopped):
    aware.issues[:] = ['contradiction', 'contradiction', 'contradiction' if stopped else 'ok']
    aware.jumps[:] = [False] * 3
    def run(target=None, checkpoint=None):
        if stopped:
            with pytest.raises(LibraryStopped, match='content_not_expressed:'):
                aware.rough(target, checkpoint)
        else:
            assert aware.rough(target, checkpoint)['selected_round'] == 2
    run()
    original = (aware.output / 'silent_review.json').read_bytes()
    sent = list(aware.sent)
    run()
    assert (aware.output / 'silent_review.json').read_bytes() == original
    parent = aware.output
    for generation in range(2):
        write_json(parent / '.omni-server/job.json', dict(job_id='parent', state='failed'))
        checkpoint = cli.resume_config(parent, aware.failed.ref)
        child = parent / f'evaluations/resume_{generation}'
        run(child, checkpoint)
        assert (child / 'silent_review.json').read_bytes() == original
        assert read(child / 'library_state.json')['request_count'] == 0
        parent = child
    assert aware.sent == sent


def test_new_prompt_does_not_reuse_unrecorded_legacy_reply(tmp_path, monkeypatch):
    timeline = [dict(line_id='line', text='我是店主。', picture_relation='additive', mode='dialogue',
        speaker=dict(window_id='w', role_id='A'), cast_id='owner', start_s=0, end_s=4,
        segments=[dict(segment_id='seg')])]
    old_prompt = prompts.silent_review_prompt({}, {}, timeline, render_sha256='render', content_check=False)
    new_prompt = prompts.silent_review_prompt({}, {}, timeline, render_sha256='render')
    assert json_sha(dict(prompt=old_prompt)) != json_sha(dict(prompt=new_prompt))
    verdict = dict(lines=[dict(line_id='line', issue='contradiction', evidence='空白卷轴并无整改内容。')],
        scene_jumps_confusing=False,
        readable_situation=dict(who='无法判断', where='店里', relationship='无法判断', emotion='震惊'))
    parent = LibraryState(tmp_path / 'parent', dict(configuration=dict(creative_style='picture_aware')))
    call, _ = parent.begin_call('silent_review_0', dict(tool='text_completion', arguments=dict(prompt=old_prompt)))
    parent.complete_call(call, dict(result=dict(content=[dict(type='text', text=json.dumps(verdict, ensure_ascii=False))])))
    ledger = parent.output / 'library_state.json'
    child = LibraryState(tmp_path / 'child', dict(configuration=dict(creative_style='picture_aware',
        resume_from=dict(ledger=dict(path=str(ledger), sha256=sha256_file(ledger))))))
    validator = lambda value: contracts.validate_silent_review(value, [dict(id='line')], require_roles=True)
    assert pipeline._resume_silent_reply(child, 'silent_review_0', new_prompt, validator,
                                         render_sha256='render') is None
    # A successful reply without an old report must receive the new request digest.
    assert not child.data['artifacts'].get('picture_aware_reply_reuse')
    assert 'content_check' not in json.loads(old_prompt.rsplit('\n', 1)[1])
    context = json.loads(new_prompt.rsplit('\n', 1)[1])
    assert context['content_check'] and context['timeline'][0]['cast_id'] == 'owner'
    monkeypatch.setattr(pipeline, '_silent_blind_reading', lambda *a: {})
    def new_call(name, prompt, validator):
        value = dict(verdict, roles=dict(clear=True, consistent=True, evidence='台词交代了谁是店主。'))
        validator(value)
        call, _ = child.begin_call(name, dict(tool='text_completion', arguments=dict(prompt=prompt)))
        child.complete_call(call, dict(result=dict(content=[dict(type='text', text=json.dumps(value))])))
        return value
    plan = dict(segments=[dict(segment_id='seg', window_id='w', source_in_s=100, source_out_s=104)],
        audio_draft=[dict(id='line', text='我是店主。', picture_relation='additive', mode='dialogue',
                         speaker=dict(window_id='w', role_id='A'), cast_id='owner', segment_ids=['seg'])])
    window = dict(window_id='w', source_id='film', source_start_s=100, source_end_s=110, observation=dict(events=[]))
    entry = pipeline._picture_aware_review(child, SimpleNamespace(call_text=new_call), dict(sha256='render'),
                                         plan, {}, [window], 0, tmp_path)
    assert child.data['request_count'] == 1 and not entry['content_acceptable']
    assert entry['verdict']['roles']['clear']


@pytest.mark.parametrize('roles', [None, {}, dict(clear='false', consistent=True, evidence='证据'),
    dict(clear=True, consistent=0, evidence='证据'), dict(clear=True, consistent=True, evidence='')])
def test_new_review_requires_roles_but_legacy_schema_stays_valid(roles):
    verdict = dict(lines=[], scene_jumps_confusing=False,
        readable_situation=dict(who='未知', where='未知', relationship='未知', emotion='未知'))
    contracts.validate_silent_review(verdict, [])
    if roles is not None:
        verdict['roles'] = roles
    with pytest.raises(ValueError, match='roles'):
        contracts.validate_silent_review(verdict, [], require_roles=True)
