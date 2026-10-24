"""Regression tests for the four MINOR findings from the T14 review."""
import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from omni_story.evaluation import prefix_subset, reference_cli as cli
from omni_story.evaluation import reference_protocol as p


def v4_manifest(policy=None):
    labels = dict(provenance='model_assisted_unconfirmed', elements=['要素A', '要素B'],
        prefix_information='explicit', prefix_acceptable_themes=['迈步'])
    if policy != 'before_last_element':
        labels['theme_reveal_s'] = 8
    manifest = dict(protocol_version='v4', protocol_sha256='frozen', experiment_id='test',
        production=dict(),
        cases=[dict(id='t_0', name='00_模板', reference=dict(duration_s=20), labels=labels)])
    if policy:
        manifest['prefix_policy'] = policy
    return manifest


@pytest.fixture
def memory(monkeypatch):
    files = {}
    def put(path, value): files[str(path)] = copy.deepcopy(value)
    def read(path): return copy.deepcopy(files[str(path)])
    monkeypatch.setattr(Path, 'exists', lambda path: str(path) in files)
    monkeypatch.setattr(Path, 'write_text', lambda path, text, **kwargs: put(path, text))
    monkeypatch.setattr(p, 'read', read)
    monkeypatch.setattr(cli, 'write_json', put)
    monkeypatch.setattr(cli, 'verify_scored_row', lambda *args, **kwargs: None)
    monkeypatch.delenv('OMNI_EVAL_BUDGET_FILE', raising=False)
    return files, put, read


def forecast_row(status, condition, cut):
    row = dict(case_id='t_0', task='forecast', status=status, condition=condition, prefix_cut_s=cut)
    if status == 'scored_provisional':
        row['metrics'] = dict(full_match_rate=1, prefix_information='explicit',
            inference_validity='supported',
            counts=dict(matched=1, partial=0, missing=0, contradicted=0, label_disputed=0))
    else:
        row['skipped_reason'] = 'no_core_evidence_times'
    return row


def test_default_v4_report_omits_policy_notes_and_visible_fraction(memory, monkeypatch):
    files, put, read = memory
    manifest = v4_manifest()
    monkeypatch.setattr(p, 'load', lambda *args, **kwargs: manifest)
    put('/memory/output/cases/t_0/forecast@before_reveal.json',
        forecast_row('scored_provisional', 'forecast@before_reveal', 4.0))
    cli.report(SimpleNamespace(manifest='memory', output=Path('/memory/output'), human_reviews=None))
    result = read('/memory/output/report.json')
    assert result['groups']['forecast']['means_scored'] == {'full_match_rate': 1.0}
    text = files['/memory/output/report.md']
    assert '前缀可见比例均值' not in text and '被藏要素数为分母' not in text
    assert '各题cut_s' in text


def test_before_last_element_report_keeps_policy_notes_and_visible_fraction(memory, monkeypatch):
    files, put, read = memory
    manifest = v4_manifest('before_last_element')
    monkeypatch.setattr(p, 'load', lambda *args, **kwargs: manifest)
    put('/memory/output/cases/t_0/forecast@before_last_element.json',
        forecast_row('not_applicable', 'forecast@before_last_element', None))
    cli.report(SimpleNamespace(manifest='memory', output=Path('/memory/output'), human_reviews=None))
    result = read('/memory/output/report.json')
    assert result['groups']['forecast']['means_scored'] == {'visible_fraction': None}
    text = files['/memory/output/report.md']
    assert '前缀可见比例均值（有效题）：null' in text
    assert '被藏要素数为分母（before_last_element策略）' in text and '各题cut_s' in text


@pytest.mark.parametrize('row_extra, expected', [
    (dict(prefix_cut_s=7.5), 7.5), (dict(), 5), (dict(prefix_cut_s=None), 5)])
def test_eligible_cases_use_row_cut_and_fall_back_to_labels(row_extra, expected):
    manifest = dict(cases=[dict(id='t_0', name='00_模板',
        labels=dict(prefix_information='explicit', prefix_cut_s=5))])
    report = dict(rows=[dict(case_id='t_0', task='forecast', status='scored_provisional',
        metrics=dict(full_match_rate=1), **row_extra)])
    result = prefix_subset.summarize(manifest, report)
    assert result['eligible_cases'] == [dict(case_id='t_0', name='00_模板', prefix_cut_s=expected,
        status='scored_provisional', full_match_rate=1)]


@pytest.mark.parametrize('protocol', [None, 'v3'])
def test_prepare_rejects_prefix_policy_without_v4(protocol):
    args = SimpleNamespace(inventory='unused', curation=None, output=Path('/memory/output'),
        receipts=Path('/memory/receipts'), runtime='runtime', experiment_id='test',
        protocol_version=protocol, prefix_policy='before_last_element')
    with pytest.raises(ValueError, match='^reference_eval_prefix_policy_requires_v4$'):
        cli.prepare(args)


def test_cli_prepare_rejects_prefix_policy_without_v4():
    with pytest.raises(ValueError, match='^reference_eval_prefix_policy_requires_v4$'):
        cli.main(['prepare', '--output', '/memory/output', '--prefix-policy', 'before_last_element'])
