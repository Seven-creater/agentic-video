"""BGM fingerprint matching over synthetic chromaprint integers; no media needed."""
import hashlib
import json

import pytest

from omni_story.evaluation.bgm_fingerprint import evaluate, main, similarity

NEAR = 0x0000000F   # four flipped bits: 0.875 agreement with the base track.
NEAR2 = 0x000000F0  # four other bits: 0.75 agreement against NEAR.


def fingerprint(seed, length=60, flip=0):
    stream = hashlib.shake_128(str(seed).encode()).digest(length * 4)
    return [int.from_bytes(stream[4 * i:4 * i + 4], 'big') ^ flip for i in range(length)]


def case(cid, name, same_mechanism=True):
    labels = {} if same_mechanism is None else dict(same_mechanism=same_mechanism)
    return dict(id=cid, name=name, labels=labels,
                reference=dict(path=f'/data/inbox/{name}/01.mp4'),
                query=dict(path=f'/data/inbox/{name}/q1.mp4'))


def row(video, values, error=None):
    fields = dict(video=video, template=video.rpartition('/')[0], fp_bgm=values,
                  fp_mix=[], bgm_rms_db=-20.5, vocals_rms_db=-33.2)
    if error is not None:
        fields['error'] = error
    return fields


def write_rows(tmp_path, rows):
    path = tmp_path / 'rows.jsonl'
    path.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows),
                    encoding='utf-8')
    return path


@pytest.fixture
def world(tmp_path):
    rows = [
        row('01_不烧心/01.mp4', fingerprint(1, flip=NEAR)),
        row('01_不烧心/02.mp4', fingerprint(1, flip=NEAR2)),
        row('01_不烧心/q1.mp4', fingerprint(1)),
        row('02_宽汤/01.mp4', fingerprint(2, flip=NEAR)),
        row('02_宽汤/02.mp4', fingerprint(2, flip=NEAR2)),
        row('02_宽汤/q1.mp4', fingerprint(2)),
        row('03_慢煨/01.mp4', fingerprint(1, flip=NEAR)),  # same music as 01_不烧心
        row('03_慢煨/02.mp4', fingerprint(1, flip=NEAR)),
        row('03_慢煨/q1.mp4', fingerprint(1, flip=NEAR2)),
        row('04_收汁/01.mp4', fingerprint(3)),
        row('04_收汁/02.mp4', fingerprint(1, flip=NEAR)),
        row('04_收汁/03.mp4', fingerprint(1, flip=NEAR2)),
        row('04_收汁/q1.mp4', fingerprint(1)),
        row('05_大火/01.mp4', fingerprint(4, flip=NEAR)),
        row('05_大火/q1.mp4', fingerprint(4)),
        row('06_小火/01.mp4', fingerprint(5, flip=NEAR)),
        row('06_小火/02.mp4', fingerprint(5, flip=NEAR2)),
        row('06_小火/q1.mp4', fingerprint(5)),
        row('07_宽油/01.mp4', fingerprint(6, flip=NEAR)),
        row('07_宽油/02.mp4', fingerprint(6, flip=NEAR2)),
        row('07_宽油/q1.mp4', fingerprint(6)),
        row('08_少盐/01.mp4', fingerprint(8, flip=NEAR)),
        row('08_少盐/02.mp4', fingerprint(8, flip=NEAR2)),
        row('08_少盐/03.mp4', fingerprint(8, flip=NEAR)),
        row('08_少盐/q1.mp4', fingerprint(8), error='asr_decode_failed'),
        row('09_断火/q1.mp4', fingerprint(9)),
        row('10_回锅/01.mp4', [], error='ffmpeg_timeout'),
        row('10_回锅/02.mp4', fingerprint(10, flip=NEAR)),
        row('10_回锅/03.mp4', fingerprint(10, flip=NEAR2)),
        row('10_回锅/q1.mp4', fingerprint(10)),
    ]
    manifest = dict(cases=[
        case('t_a', '01_不烧心'), case('t_b', '02_宽汤'), case('t_c', '03_慢煨'),
        case('t_d', '04_收汁'), case('t_e', '05_大火'), case('t_f', '06_小火', False),
        case('t_g', '07_宽油', None), case('t_h', '08_少盐'), case('t_i', '09_断火'),
        case('t_j', '10_回锅')])
    return manifest, write_rows(tmp_path, rows)


def test_identical_sequences_score_one_and_short_overlap_zero():
    fp = fingerprint(3, 80)
    assert similarity(fp, fp) == 1.0
    assert similarity(fp[:19], fp[:19]) == 0.0
    assert similarity([], []) == 0.0
    assert similarity(fingerprint(1, 15), fingerprint(1, 20)) == 0.0


def test_best_alignment_is_found_within_max_shift():
    fp = fingerprint(7, 200)
    assert similarity(fp, fp[70:]) == 1.0
    assert similarity(fp[70:], fp) == 1.0
    assert similarity(fp, fp[119:]) == 1.0
    assert similarity(fp, fp[121:]) < 0.65
    assert similarity(fp, fp[121:], max_shift=121) == 1.0


def test_unrelated_fingerprints_stay_below_threshold():
    rate = similarity(fingerprint(1), fingerprint(2))
    assert 0.4 < rate < 0.65


def test_unique_hit_scores_with_full_metrics(world):
    manifest, rows_path = world
    assert evaluate(manifest, rows_path)['t_b'] == dict(
        case_id='t_b', template='02_宽汤', status='scored_provisional',
        method='chromaprint_local_v1',
        metrics=dict(music_template_hit=1, unique_template_hit=1, candidate_count=1,
                     best_similarity=0.875, own_reference_similarity=0.875))


def test_hit_with_multiple_candidates_is_not_unique(world):
    manifest, rows_path = world
    result = evaluate(manifest, rows_path)
    assert result['t_a']['metrics'] == dict(music_template_hit=1, unique_template_hit=0,
        candidate_count=2, best_similarity=0.875, own_reference_similarity=0.875)
    assert result['t_c']['metrics'] == dict(music_template_hit=1, unique_template_hit=0,
        candidate_count=2, best_similarity=0.75, own_reference_similarity=0.75)


def test_miss_reports_other_templates_as_candidates(world):
    manifest, rows_path = world
    metrics = evaluate(manifest, rows_path)['t_d']['metrics']
    assert metrics == dict(music_template_hit=0, unique_template_hit=0, candidate_count=2,
        best_similarity=0.875,
        own_reference_similarity=similarity(fingerprint(1), fingerprint(3)))


def test_query_is_never_the_reference_fingerprint(world):
    manifest, rows_path = world
    metrics = evaluate(manifest, rows_path)['t_b']['metrics']
    assert metrics['own_reference_similarity'] == 0.875  # 1.0 would mean the reference was the query.


def test_templates_without_signature_bgm_are_not_applicable(world):
    manifest, rows_path = world
    row = evaluate(manifest, rows_path)['t_e']
    assert row['status'] == 'not_applicable_no_signature_bgm'
    assert 'metrics' not in row


def test_unconfirmed_cross_video_mechanism_is_not_applicable(world):
    manifest, rows_path = world
    result = evaluate(manifest, rows_path)
    assert result['t_f']['status'] == 'not_applicable_cross_video_unverified'
    assert result['t_g']['status'] == 'not_applicable_cross_video_unverified'
    assert all('metrics' not in result[cid] for cid in ('t_f', 't_g'))


def test_missing_or_errored_audio_is_ineligible_even_with_a_full_group(world):
    manifest, rows_path = world
    result = evaluate(manifest, rows_path)
    assert [result[cid]['status'] for cid in ('t_h', 't_i', 't_j')] == ['ineligible_audio'] * 3
    assert all('metrics' not in result[cid] for cid in ('t_h', 't_i', 't_j'))


def test_threshold_and_min_group_parameters_are_honoured(world):
    manifest, rows_path = world
    relaxed = evaluate(manifest, rows_path, min_group=2)
    assert relaxed['t_e']['status'] == 'scored_provisional'
    assert relaxed['t_e']['metrics']['unique_template_hit'] == 1
    strict = evaluate(manifest, rows_path, threshold=0.9)
    assert strict['t_a']['status'] == 'not_applicable_no_signature_bgm'


def test_input_rows_file_is_never_rewritten(world):
    manifest, rows_path = world
    before = rows_path.read_text(encoding='utf-8')
    evaluate(manifest, rows_path)
    assert rows_path.read_text(encoding='utf-8') == before


def test_main_prints_json_and_returns_zero(tmp_path, capsys):
    rows = [row('01_不烧心/01.mp4', fingerprint(1, flip=NEAR)),
            row('01_不烧心/02.mp4', fingerprint(1, flip=NEAR2)),
            row('01_不烧心/q1.mp4', fingerprint(1))]
    manifest_path = tmp_path / 'manifest.json'
    manifest_path.write_text(json.dumps(dict(cases=[case('t_a', '01_不烧心')]),
                                        ensure_ascii=False), encoding='utf-8')
    assert main([str(manifest_path), str(write_rows(tmp_path, rows))]) == 0
    out = capsys.readouterr().out
    printed = json.loads(out)
    assert printed['t_a']['status'] == 'scored_provisional'
    assert printed['t_a']['metrics']['unique_template_hit'] == 1
    assert '01_不烧心' in out  # ensure_ascii=False keeps the template name readable.
