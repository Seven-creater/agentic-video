import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from omni_story.evaluation import template_protocol as protocol
from omni_story.evaluation.template_metrics import score, summarize, validate_judgment, retrieval_metrics, compile_packet_judgment
from omni_story.library.state import json_sha


def fixture(tmp_path):
    assets = []
    for index in range(3):
        path = tmp_path / f'{index}.mp4'
        path.write_bytes(f'offline fixture {index}'.encode())
        digest = protocol.sha256_file(path)
        assets.append(dict(asset_id='v_' + digest, path=str(path), sha256=digest,
                           duplicate_group='g' + str(index), duration_s=10.0, has_audio_track=True))
    template = dict(template_id='t1', family='B', dedup_reviewed=True, assets=assets,
        support=[a['asset_id'] for a in assets[:2]], query=[assets[2]['asset_id']],
        display_name='THIS TITLE CONTAINS THE ANSWER',
        producer_analysis='THIS ANALYSIS CONTAINS QUERY ANSWERS',
        rubric=dict(elements=[dict(id='a', description='opening', required=True),
                              dict(id='b', description='turn', required=True),
                              dict(id='c', description='optional', required=False)],
                    order_edges=[['a', 'b']]))
    return dict(schema_version=protocol.SCHEMA, experiment_id='test_v1', split='development',
        methods=['single_reference', 'multi_reference'], templates=[template],
        readiness='draft_requires_split_review_and_rubric',
        scoring=dict(element_scale=[0, 1, 2], required_weight=2, optional_weight=1))


def judgment(template):
    evidence = [dict(asset_id=template['query'][0], start_s=0.0, end_s=1.0, observation='observed')]
    return dict(elements={k: dict(score=v, evidence=evidence if v else [])
                          for k, v in {'a': 2, 'b': 1, 'c': 0}.items()},
                order={'a>b': True}, claims={'c1': 'supported', 'c2': 'unsupported', 'c3': 'unknown'},
                limitations=[])


def test_freeze_is_immutable_and_detects_media_or_protocol_changes(tmp_path):
    value = fixture(tmp_path)
    target = tmp_path / 'frozen.json'
    frozen = protocol.freeze(value, target)
    assert protocol.load(target) == frozen
    assert protocol.freeze(value, target) == frozen
    changed = copy.deepcopy(value)
    changed['templates'][0]['rubric']['elements'][0]['description'] = 'changed gold'
    with pytest.raises(ValueError, match='cannot_change'):
        protocol.freeze(changed, target)
    media = Path(value['templates'][0]['assets'][0]['path'])
    media.write_bytes(b'changed')
    with pytest.raises(ValueError, match='media_changed'):
        protocol.load(target)
    # Archival scoring explicitly supports media retirement without claiming a
    # new model run or that the original media are still present.
    assert protocol.load(target, verify_files=False) == frozen
    mutated = protocol.read(target)
    mutated['templates'][0]['query'] = []
    target.write_text(json.dumps(mutated))
    with pytest.raises(ValueError, match='identity_changed'):
        protocol.load(target, verify_files=False)


def test_duplicate_groups_and_exact_bytes_cannot_cross_split(tmp_path):
    value = fixture(tmp_path)
    value['templates'][0]['assets'][2]['duplicate_group'] = 'g0'
    with pytest.raises(ValueError, match='group_leakage'):
        protocol.validate(value)
    value = fixture(tmp_path)
    other = copy.deepcopy(value['templates'][0])
    other.update(template_id='t2', support=[other['assets'][2]['asset_id']],
                 query=[other['assets'][0]['asset_id']])
    for asset in other['assets']:
        asset['duplicate_group'] = 'renamed_' + asset['duplicate_group']
    value['templates'].append(other)
    with pytest.raises(ValueError, match='group_leakage'):
        protocol.validate(value)


def test_learner_never_gets_gold_title_query_or_producer_report(tmp_path):
    template = fixture(tmp_path)['templates'][0]
    inputs = protocol.learner_inputs(template, 'multi_reference')
    serialized = json.dumps(inputs)
    assert len(inputs) == 2
    assert 'ANSWER' not in serialized and template['query'][0] not in serialized
    assert len(protocol.learner_inputs(template, 'single_reference')) == 1
    with pytest.raises(ValueError, match='method_not_implemented'):
        protocol.learner_inputs(template, 'guess')


def test_weighted_scores_missing_events_and_unknown_claim_coverage(tmp_path):
    template = fixture(tmp_path)['templates'][0]
    value = judgment(template)
    validate_judgment(template, value, claims=['c1', 'c2', 'c3'])
    result = score(template, value)
    assert result['template_score'] == 60
    assert result['unsupported_rate'] == .5
    assert result['claim_verification_coverage'] == pytest.approx(2/3)
    value['elements']['b'] = dict(score=0, evidence=[])
    assert score(template, value)['order_accuracy'] == 0
    assert score(template, None)['template_score'] == 0


@pytest.mark.parametrize('change', ['missing_element', 'out_of_range', 'support_evidence', 'missing_claim'])
def test_judge_must_use_actual_query_evidence_and_complete_contract(tmp_path, change):
    template = fixture(tmp_path)['templates'][0]
    value = judgment(template)
    if change == 'missing_element': value['elements'].pop('c')
    if change == 'out_of_range': value['elements']['a']['evidence'][0]['end_s'] = 11
    if change == 'support_evidence': value['elements']['a']['evidence'][0]['asset_id'] = template['support'][0]
    if change == 'missing_claim': value['claims'].pop('c1')
    with pytest.raises(ValueError):
        validate_judgment(template, value, claims=['c1', 'c2', 'c3'])


def test_pending_and_failed_remain_in_the_frozen_denominator(tmp_path):
    value = protocol.freeze(fixture(tmp_path), tmp_path / 'frozen.json')
    rows = [dict(protocol_sha256=value['protocol_sha256'], template_id='t1', method='single_reference',
                 status='blocked_budget')]
    result = summarize(value, rows)
    assert len(result['rows']) == 2
    assert result['groups'][0]['planned'] == 1
    assert result['groups'][0]['statuses'] == {'blocked_budget': 1}
    assert result['groups'][1]['statuses'] == {'pending': 1}
    assert all(g['mean_template_score_all_planned'] == 0 for g in result['groups'])
    with pytest.raises(ValueError, match='unplanned_duplicate'):
        summarize(value, rows * 2)
    assert retrieval_metrics(['bad', 'good'], ['good']) == dict(recall_at_k=1, reciprocal_rank=.5)


def test_real_media_inventory_and_packet_preserve_audio_facts(tmp_path):
    import shutil
    import subprocess
    from omni_story.evaluation.template_runner import _packet
    if not shutil.which('ffmpeg'):
        pytest.skip('ffmpeg required for media integration')
    root = tmp_path / 'uploads'
    folder = root / '01_example'
    folder.mkdir(parents=True)
    for index, color in enumerate(['red', 'blue']):
        args = ['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i', f'color=c={color}:s=160x90:r=15:d=1']
        if index:
            args += ['-f', 'lavfi', '-i', 'sine=frequency=440:duration=1', '-c:a', 'aac']
        subprocess.run(args + ['-c:v', 'libx264', '-threads', '1', str(folder / f'{index}.mp4')],
                       check=True, capture_output=True)
    scan = protocol.inventory(root)
    assert not scan['errors'] and len(scan['templates'][0]['assets']) == 2
    assets = scan['templates'][0]['assets']
    assert [a['has_audio_track'] for a in assets] == [False, True]
    template = dict(assets=assets, query=[a['asset_id'] for a in assets])
    media, binding = _packet(template, tmp_path / 'packet')
    assert media.exists()
    assert [m['original_has_audio_track'] for m in binding['mapping']] == [False, True]
    assert _packet(template, tmp_path / 'packet')[1] == binding
    media.write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='packet_changed'):
        _packet(template, tmp_path / 'packet')
    (folder / 'broken.mp4').write_bytes(b'not a video')
    broken_scan = protocol.inventory(root)
    assert len(broken_scan['errors']) == 1
    # A bad uploaded sample cannot silently disappear from the benchmark.
    with pytest.raises(ValueError, match='upload_errors'):
        protocol.freeze(protocol.draft(broken_scan, 'must_not_freeze', support_count=1, query_count=1),
                        tmp_path / 'invalid_frozen.json')


def test_batch_identity_cannot_change_output_or_code(tmp_path, monkeypatch):
    from omni_story.evaluation import template_batch as batch
    manifest = protocol.freeze(fixture(tmp_path), tmp_path / 'manifest.json')
    monkeypatch.setenv('HUNTER_RECEIPTS_DIR', str(tmp_path / 'receipts'))
    monkeypatch.setenv('OMNI_EVAL_BUDGET_FILE', str(tmp_path / 'existing_budget.json'))
    monkeypatch.setattr(batch, 'code_identity', lambda: 'version1')
    original = batch.bind(tmp_path / 'output', manifest)
    assert batch.bind(tmp_path / 'output', manifest) == original
    with pytest.raises(ValueError, match='registered_identity_changed'):
        batch.bind(tmp_path / 'other_output', manifest)
    monkeypatch.setattr(batch, 'code_identity', lambda: 'version2')
    with pytest.raises(ValueError, match='registered_identity_changed'):
        batch.bind(tmp_path / 'output', manifest)


def test_batch_task_reuses_completed_result_and_isolates_failure(tmp_path, monkeypatch):
    from omni_story.evaluation import template_batch as batch, template_runner as runner
    manifest_path = tmp_path / 'manifest.json'
    protocol.freeze(fixture(tmp_path), manifest_path)
    output = tmp_path / 'batch'
    output.mkdir()
    (output / 'batch_state.json').write_text(json.dumps({'identity': {'code_sha256': 'v1'}}))
    monkeypatch.setattr(batch, 'code_identity', lambda: 'v1')
    calls = []
    def understand(args):
        calls.append(args.method)
        if args.method == 'single_reference':
            raise RuntimeError('hunter_transport_uncertain_no_replay')
    monkeypatch.setattr(runner, 'understand', understand)
    monkeypatch.setattr(runner, 'judge', lambda args: dict(status='completed'))
    monkeypatch.setattr(runner, 'verify_record', lambda row: None)
    args = SimpleNamespace(manifest=manifest_path, output=output, template_id='t1',
                           method='single_reference', home=tmp_path)
    batch.task(args)
    failed = protocol.read(output / 'jobs/t1/single_reference/record.json')
    assert failed['status'] == 'blocked_unknown'
    args.method = 'multi_reference'
    batch.task(args)
    assert protocol.read(output / 'jobs/t1/multi_reference/record.json')['status'] == 'completed'
    batch.task(args)
    args.method = 'single_reference'
    batch.task(args)
    assert calls == ['single_reference', 'multi_reference']


def test_native_packet_clock_is_compiled_without_changing_received_scores():
    mapping = [dict(asset_id='second', packet_start_s=36.933333,
                    packet_end_s=74.733333, source_duration_s=37.8)]
    raw = dict(time_base='packet_seconds', elements={'opening': dict(score=2, evidence=[
        dict(asset_id='second', start_s=43.0, end_s=45.0, observation='visible action')]),
        'whole': dict(score=1, evidence=[dict(asset_id='second', start_s=37.0, end_s=74.733333)])})
    original = copy.deepcopy(raw)
    compiled = compile_packet_judgment(raw, mapping)
    assert raw == original
    assert compiled['elements']['opening']['score'] == 2
    assert compiled['elements']['opening']['evidence'][0]['start_s'] == pytest.approx(6.066667)
    assert compiled['elements']['whole']['evidence'][0]['end_s'] == 37.8
    assert compiled['time_base'] == 'source_seconds'


def test_packet_clock_never_guesses_local_seconds_or_accepts_padding_as_evidence():
    mapping = [dict(asset_id='second', packet_start_s=10.0, packet_end_s=20.1, source_duration_s=10.0)]
    raw = dict(elements={})
    with pytest.raises(ValueError, match='explicit_packet_time_base'):
        compile_packet_judgment(raw, mapping)
    raw = dict(time_base='packet_seconds', elements={'event': dict(score=2, evidence=[
        dict(asset_id='second', start_s=1.0, end_s=2.0)])})
    with pytest.raises(ValueError, match='outside_packet_asset'):
        compile_packet_judgment(raw, mapping)
    raw['elements']['event']['evidence'][0].update(start_s=20.01, end_s=20.09)
    with pytest.raises(ValueError, match='only_in_transport_padding'):
        compile_packet_judgment(raw, mapping)
