import json
from pathlib import Path

import pytest

from omni_story.evaluation.prefix_subset import main, summarize


def case(case_id, information=None, cut=5):
    labels = dict(prefix_cut_s=cut)
    if information is not None:
        labels['prefix_information'] = information
    return dict(id=case_id, name=f'01_{case_id}', labels=labels)


def row(case_id, rate=0, status='scored_provisional', task='forecast', **metrics):
    return dict(case_id=case_id, template='ignored', task=task, status=status,
        metrics=dict(full_match_rate=rate, **metrics))


def test_groups_use_manifest_labels_and_only_scored_metrics():
    manifest = dict(cases=[case('e', 'explicit'), case('i', 'implied'),
        case('a', 'ambiguous'), dict(id='u', name='04_未标注')])
    report = dict(rows=[
        row('e', .25, counts=dict(matched=1, partial=2, missing=3, contradicted=4,
            label_disputed=5), inference_validity='supported', prefix_information='ambiguous'),
        row('e', .75, counts=dict(matched=2), inference_validity='plausible'),
        row('e', 99, status='failed', counts=dict(matched=100), inference_validity='contradicted'),
        row('i', .8, inference_validity='insufficient'),
        row('a', 0, inference_validity='contradicted'), row('u', .4),
        row('e', 100, task='story')])
    result = summarize(manifest, report)
    assert result['policy'] == 'prefix_subset_v1'
    assert result['include_implied'] is False
    assert result['note'] == ('Subset by annotated prefix information; '
        'labels are model-assisted unless confirmed.')
    groups = result['groups']
    assert set(groups) == {'explicit', 'implied', 'ambiguous', 'unlabeled'}
    assert groups['explicit'] == dict(planned=3, scored=2,
        statuses=dict(scored_provisional=2, failed=1), mean_full_match_rate=.5,
        element_counts=dict(matched=3, partial=2, missing=3, contradicted=4, label_disputed=5),
        inference_validity=dict(supported=1, plausible=1, insufficient=0, contradicted=0, missing=0))
    for information, rate in [('implied', .8), ('ambiguous', 0), ('unlabeled', .4)]:
        assert groups[information]['planned'] == groups[information]['scored'] == 1
        assert groups[information]['mean_full_match_rate'] == rate
    assert groups['implied']['inference_validity']['insufficient'] == 1
    assert groups['ambiguous']['inference_validity']['contradicted'] == 1
    assert groups['unlabeled']['inference_validity']['missing'] == 1
    assert result['eligible_cases'] == [dict(case_id='e', name='01_e', prefix_cut_s=5,
        status=status, full_match_rate=rate) for status, rate in
        [('scored_provisional', .25), ('scored_provisional', .75), ('failed', 99)]]
    assert result['eligible_mean_full_match_rate'] == .5


def test_missing_and_null_inference_validity_count_as_missing():
    result = summarize(dict(cases=[case('e', 'explicit')]),
        dict(rows=[row('e'), row('e', inference_validity=None)]))
    assert result['groups']['explicit']['inference_validity'] == dict(
        supported=0, plausible=0, insufficient=0, contradicted=0, missing=2)


def test_include_implied_uses_scored_row_mean():
    manifest = dict(cases=[case('e', 'explicit'), case('i', 'implied', 10), case('a', 'ambiguous')])
    report = dict(rows=[row('e', .2), row('e', .4), row('i', .9),
        row('i', 100, status='pending'), row('a', 1)])
    default = summarize(manifest, report)
    included = summarize(manifest, report, include_implied=True)
    assert default['eligible_mean_full_match_rate'] == pytest.approx(.3)
    assert included['include_implied'] is True
    assert included['eligible_mean_full_match_rate'] == pytest.approx(.5)
    assert [item['case_id'] for item in included['eligible_cases']] == ['e', 'e', 'i', 'i']
    assert included['eligible_cases'][2]['prefix_cut_s'] == 10
    assert included['groups'] == default['groups']


def test_unscored_and_empty_groups_have_no_mean():
    result = summarize(dict(cases=[case('e', 'explicit')]),
        dict(rows=[dict(case_id='e', task='forecast', status='pending')]))
    assert result['eligible_mean_full_match_rate'] is None
    assert result['eligible_cases'][0]['full_match_rate'] is None
    for group in result['groups'].values():
        assert group['scored'] == 0
        assert group['mean_full_match_rate'] is None
        assert group['element_counts'] == dict(matched=0, partial=0, missing=0,
            contradicted=0, label_disputed=0)
        assert group['inference_validity'] == dict(supported=0, plausible=0,
            insufficient=0, contradicted=0, missing=0)
    empty = summarize(dict(cases=[]), dict(rows=[]))
    assert empty['eligible_cases'] == []
    assert empty['eligible_mean_full_match_rate'] is None


@pytest.mark.parametrize('task', ['forecast', 'story'])
def test_unknown_case_raises(task):
    with pytest.raises(ValueError, match='^prefix_subset_unknown_case$'):
        summarize(dict(cases=[]), dict(rows=[row('unknown', task=task)]))


@pytest.mark.parametrize('flags', [[], ['--include-implied']])
def test_main_prints_json_from_in_memory_inputs(monkeypatch, capsys, flags):
    manifest = dict(cases=[case('i', 'implied')])
    report = dict(rows=[row('i', .5)])
    inputs = {'manifest.json': manifest, 'report.json': report}
    monkeypatch.setattr(Path, 'read_text', lambda path, encoding: json.dumps(inputs[path.name]))
    assert main(['manifest.json', 'report.json', *flags]) == 0
    assert json.loads(capsys.readouterr().out) == summarize(
        manifest, report, include_implied=bool(flags))
