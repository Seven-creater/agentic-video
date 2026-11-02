"""Silent review: each line sits next to the blind facts seen in its own time span.

Server job prod_27_发现三个问题_11021100/evaluations/rule2/evaluations/len1/
evaluations/len2 (release 3b289bc): the blind reading of the rough cut said
"大熊猫打开卷轴，发现里面是空白的" (91-103 s) and "拿着空白卷轴，困惑" (104-118 s),
yet the per-line review judged ad_13 "……我接。回去就一件件改。" (103-113 s) ok
from library window notes. The fine review then stopped the task on exactly
this contradiction (needs_material_reselection).
"""
import json

from omni_story.library import prompts

BLIND = dict(observed_story='…', main_characters=[], apparent_theme='…', confusions=[], evidence=[
    dict(start_s=79, end_s=90, observed_fact='在绿色柱子的大厅里，小熊猫将一个卷轴交给大熊猫。'),
    dict(start_s=91, end_s=103, observed_fact='大熊猫打开卷轴，发现里面是空白的，表情从期待转为困惑。'),
    dict(start_s=104, end_s=118, observed_fact='大熊猫拿着空白卷轴，困惑地看着周围，然后盖世五侠出现在他身后。'),
    dict(start_s=119, end_s=139, observed_fact='小熊猫离开，大熊猫独自站在盖世五侠面前，显得不知所措。'),
    dict(start_s=110, end_s=110, observed_fact='点时间事实'),
])


def line(ident, text, start, end):
    return dict(line_id=ident, text=text, picture_relation='sync_speech', mode='dialogue',
                speaker='B', start_s=start, end_s=end, segments=[dict(segment_id='seg_' + ident)])


def context(prompt):
    return json.loads(prompt.rsplit('\n', 1)[-1])


def test_each_line_carries_the_blind_facts_of_its_span():
    timeline = [line('ad_12', '别再推了，这一回，你绕不开。', 90, 103),
                line('ad_13', '……我接。回去就一件件改。', 103, 113)]
    prompt = prompts.silent_review_prompt(BLIND, dict(theme='主旨'), timeline, render_sha256='r')
    lines = {row['line_id']: row for row in context(prompt)['timeline']}
    assert [row['start_s'] for row in lines['ad_13']['blind_facts']] == [104, 110]
    assert [row['start_s'] for row in lines['ad_12']['blind_facts']] == [91]
    assert '先对照它' in prompt and '判contradiction' in prompt


def test_old_prompts_are_unchanged():
    timeline = [line('ad_13', '……我接。', 103, 113)]
    for kwargs in (dict(content_check=False), dict(legacy=True)):
        prompt = prompts.silent_review_prompt(BLIND, dict(theme='主旨'), timeline, render_sha256='r', **kwargs)
        assert 'blind_facts' not in prompt


def test_missing_or_untimed_evidence_gives_empty_facts():
    timeline = [line('ad_1', '台词', 0, 5)]
    for blind in ({}, dict(evidence=[dict(observed_fact='无时间')])):
        prompt = prompts.silent_review_prompt(blind, {}, timeline, render_sha256='r')
        assert context(prompt)['timeline'][0]['blind_facts'] == []


def test_recorded_reviews_rebuild_their_original_prompt_bytes(tmp_path):
    # A review recorded before line-aligned blind facts keeps its exact prompt,
    # so a restart or --resume-from compares equal (prompt_bytes unchanged).
    import importlib.util, subprocess
    head = subprocess.run(['git', 'show', 'eb3cc22:src/omni_story/library/prompts.py'],
                          capture_output=True, text=True, check=True).stdout
    path = tmp_path / 'prompts_head.py'
    path.write_text(head.replace('from .contracts import', 'from omni_story.library.contracts import'),
                    encoding='utf-8')
    spec = importlib.util.spec_from_file_location('prompts_head', path)
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    timeline = [line('ad_13', '……我接。回去就一件件改。', 103, 113)]
    assert (prompts.silent_review_prompt(BLIND, dict(theme='主旨'), timeline, render_sha256='r', blind_facts=False) ==
            old.silent_review_prompt(BLIND, dict(theme='主旨'), timeline, render_sha256='r'))


def test_settled_request_prompt_reads_the_request_actually_answered(tmp_path):
    from types import SimpleNamespace
    from omni_story.library import pipeline
    from omni_story.library.state import write_json
    for ident, prompt in (('glm_001_silent_review_0', 'old prompt'), ('glm_002_blind_1', 'other')):
        write_json(tmp_path / 'calls' / ident / 'request.json', dict(arguments=dict(prompt=prompt)))
    state = SimpleNamespace(output=tmp_path, _reload=lambda: None, data=dict(calls=[
        dict(id='glm_001_silent_review_0', name='silent_review_0'), dict(id='glm_002_blind_1', name='blind_1')]))
    assert pipeline._settled_request_prompt(state, 'silent_review_0') == 'old prompt'
    assert pipeline._settled_request_prompt(state, 'silent_review_9') is None
