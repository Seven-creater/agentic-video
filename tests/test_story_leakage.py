import json

import pytest

from omni_story.evaluation.story_leakage import audit, main


def case(cid, name, paragraphs, targets, evidence=(), theme=None, error=None):
    labels = dict(story_paragraphs=paragraphs, story_target_indices=targets,
                  core_evidence=[dict(element_index=i, observation=text) for i, text in enumerate(evidence)],
                  theme_summary=theme)
    if error is not None:
        labels['story_preparation_error'] = error
    return dict(id=cid, name=name, labels=labels)


def test_template_name_leak_is_separable_when_only_targets_hit():
    manifest = dict(cases=[case('t_1', '01_小瘪三',
        ['完全无关的开头', '他把小瘪三挂在嘴边', '别的情节', '再一段收尾'], [1])])
    result = audit(manifest)
    assert result['policy'] == 'story_leakage_audit_v1'
    assert result['rows'] == [dict(case_id='t_1', name='01_小瘪三', name_token='小瘪三', quotes=[],
        paragraph_hits=[dict(index=1, name_hit=True, quote_hits=[])],
        target_leak_rate=1.0, distractor_leak_rate=0.0, keyword_separable=True)]
    assert result['summary'] == dict(cases=1, cases_with_any_leak=1, keyword_separable=1)


def test_quoted_dialogue_leak_reports_quote_hits():
    manifest = dict(cases=[case('t_2', '02_宽汤',
        ['旁白段落', '他反复说“面要多见，汤要宽”，徒弟照做', '其他情节', '尾声'], [1],
        evidence=('他坚持“面要多见，汤要宽”', '又重复了“面要多见，汤要宽”'))])
    row = audit(manifest)['rows'][0]
    assert row['quotes'] == ['面要多见，汤要宽']
    assert row['paragraph_hits'] == [dict(index=1, name_hit=False, quote_hits=['面要多见，汤要宽'])]
    assert row['keyword_separable'] is True


def test_clean_story_has_no_leak():
    manifest = dict(cases=[case('t_3', '03_不烧心',
        ['一段完全无关', '另一段无关', '第三段无关', '第四段无关'], [2],
        evidence=('画面里人物点头',), theme='主题是踏实做事，不抄近路。他说“慢慢来才稳”。')])
    result = audit(manifest)
    row = result['rows'][0]
    assert row['quotes'] == ['慢慢来才稳']
    assert row['paragraph_hits'] == []
    assert row['target_leak_rate'] == 0.0
    assert row['distractor_leak_rate'] == 0.0
    assert row['keyword_separable'] is False
    assert result['summary'] == dict(cases=1, cases_with_any_leak=0, keyword_separable=0)


def test_distractor_hit_blocks_keyword_separability():
    manifest = dict(cases=[case('t_4', '04_不烧心',
        ['开头也提到不烧心', '目标段落讲不烧心', '无关', '再无关'], [1])])
    row = audit(manifest)['rows'][0]
    assert {h['index'] for h in row['paragraph_hits']} == {0, 1}
    assert row['target_leak_rate'] == 1.0
    assert row['distractor_leak_rate'] == pytest.approx(1 / 3)
    assert row['keyword_separable'] is False


def test_partial_target_leak_keeps_keyword_separability_false():
    manifest = dict(cases=[case('t_5', '05_小瘪三',
        ['无关', '他骂人小瘪三', '无关二', '无关三'], [0, 1])])
    row = audit(manifest)['rows'][0]
    assert row['target_leak_rate'] == 0.5
    assert row['keyword_separable'] is False


def test_cases_without_story_or_with_preparation_error_are_skipped():
    broken = case('t_6', '06_小瘪三', [], [0], error='story length or target range invalid')
    absent = dict(id='t_7', name='07_不烧心', labels=dict(story_target_indices=[0]))
    kept = case('t_8', '08_不烧心', ['无关一段', '无关二段'], [1])
    result = audit(dict(cases=[broken, absent, kept]))
    assert [r['case_id'] for r in result['rows']] == ['t_8']
    assert result['summary'] == dict(cases=1, cases_with_any_leak=0, keyword_separable=0)


def test_short_quote_below_threshold_is_ignored():
    manifest = dict(cases=[case('t_9', '09_不烧心',
        ['他说“烧心”而已', '无关段落', '无关段落二', '无关三'], [0], evidence=('画外音提到“烧心”',))])
    row = audit(manifest)['rows'][0]
    assert row['quotes'] == []
    assert row['paragraph_hits'] == []
    assert row['keyword_separable'] is False


def test_quote_length_threshold_uses_min_quote_chars():
    def manifest_with_quote():
        return dict(cases=[case('t_10', '10_不烧心', ['无关', '无关二', '无关三', '无关四'], [0],
                                 evidence=('旁白念出“多见面”',))])
    assert audit(manifest_with_quote())['rows'][0]['quotes'] == []
    assert audit(manifest_with_quote(), min_quote_chars=3)['rows'][0]['quotes'] == ['多见面']


def test_corner_bracket_quote_from_theme_summary_and_punctuation_trim():
    manifest = dict(cases=[case('t_11', '11_小瘪三',
        ['无关', '他念叨着面要多见才对', '无关二', '无关三'], [1],
        evidence=('台词记录为“，面要多见。”',), theme='主题是「小瘪三不要脸」的清醒。')])
    row = audit(manifest)['rows'][0]
    assert row['quotes'] == ['面要多见', '小瘪三不要脸']
    assert row['paragraph_hits'] == [dict(index=1, name_hit=False, quote_hits=['面要多见'])]


def test_corner_quote_alone_is_separable():
    manifest = dict(cases=[case('t_12', '12_小瘪三',
        ['无关', '老人只说「小瘪三不要脸」就走开', '无关二', '无关三'], [1],
        theme='主题是「小瘪三不要脸」的清醒。')])
    row = audit(manifest)['rows'][0]
    assert row['paragraph_hits'] == [dict(index=1, name_hit=True, quote_hits=['小瘪三不要脸'])]
    assert row['keyword_separable'] is True


def test_story_without_distractors_reports_none_rate():
    manifest = dict(cases=[case('t_13', '13_不烧心',
        ['目标段讲不烧心', '目标二段仍讲不烧心'], [0, 1])])
    row = audit(manifest)['rows'][0]
    assert row['distractor_leak_rate'] is None
    assert row['keyword_separable'] is True


def test_target_outside_paragraphs_is_rejected():
    with pytest.raises(ValueError, match='story_leakage_story_answer_outside_text'):
        audit(dict(cases=[case('t_14', '14_不烧心', ['一段', '二段'], [2])]))


def test_main_prints_json_and_returns_zero(tmp_path, capsys):
    manifest = dict(cases=[case('t_15', '15_小瘪三',
        ['完全无关的开头', '他把小瘪三挂在嘴边', '别的情节', '再一段收尾'], [1])])
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
    assert main([str(path)]) == 0
    text = capsys.readouterr().out
    printed = json.loads(text)
    assert printed['policy'] == 'story_leakage_audit_v1'
    assert printed['summary'] == dict(cases=1, cases_with_any_leak=1, keyword_separable=1)
    assert '小瘪三' in text  # ensure_ascii=False keeps the template name readable.
