import pytest
from omni_story.evaluation.prefix_policy import (REVEAL_LABEL_SUFFIX, cut_points,
                                                 expected_information, validate_reveal)


def reveal_labels(reveal, start=3.0, end=5.0):
    return dict(theme_reveal_s=reveal, theme_reveal_evidence=dict(
        start_packet_s=start, end_packet_s=end, observation='主角说出“不烧心”'))


def test_reveal_label_suffix_names_required_fields():
    for token in ('theme_reveal_s', 'theme_reveal_evidence', 'start_packet_s',
                  'end_packet_s', 'observation', '传输包累计秒', '第一次',
                  '不是结尾', 'null'):
        assert token in REVEAL_LABEL_SUFFIX


def test_validate_reveal_allows_missing_or_null_reveal():
    assert validate_reveal({}, 30) is None
    assert validate_reveal(dict(theme_reveal_s=None, theme_reveal_evidence=None), 30) is None


def test_validate_reveal_accepts_in_range_reveal_with_ordered_evidence():
    assert validate_reveal(reveal_labels(12), 30) == 12
    assert validate_reveal(reveal_labels(0.001), 30) == 0.001
    assert validate_reveal(reveal_labels(29.999), 30) == 29.999
    assert validate_reveal(reveal_labels(4, start=4, end=4), 30) == 4


@pytest.mark.parametrize('bad', [
    dict(theme_reveal_s=0), dict(theme_reveal_s=30), dict(theme_reveal_s=30.5),
    dict(theme_reveal_s=-3), dict(theme_reveal_s='12'), dict(theme_reveal_s=True),
    dict(theme_reveal_s=[12]),
    dict(theme_reveal_s=12),
    dict(theme_reveal_s=12, theme_reveal_evidence=None),
    dict(theme_reveal_s=12, theme_reveal_evidence='3-5秒'),
    dict(theme_reveal_s=12, theme_reveal_evidence={}),
    dict(theme_reveal_s=12, theme_reveal_evidence=dict(start_packet_s=5.5, end_packet_s=5)),
    dict(theme_reveal_s=12, theme_reveal_evidence=dict(end_packet_s=13)),
    dict(theme_reveal_s=12, theme_reveal_evidence=dict(start_packet_s=11)),
    dict(theme_reveal_s=12, theme_reveal_evidence=dict(start_packet_s='11', end_packet_s=13)),
])
def test_validate_reveal_rejects_bad_labels(bad):
    with pytest.raises(ValueError) as exc:
        validate_reveal(bad, 30)
    assert str(exc.value) == 'prefix_reveal_label_invalid'


def test_cut_points_falls_back_to_legacy_fixed_without_reveal():
    assert cut_points(60, None) == [dict(name='legacy_fixed', cut_s=5.0,
                                         reason='no_theme_reveal_label')]
    assert cut_points(9, None) == [dict(name='legacy_fixed', cut_s=3.0,
                                        reason='no_theme_reveal_label')]
    assert cut_points(6, None)[0]['cut_s'] == 2.0
    assert cut_points(10, None)[0]['cut_s'] == 3.333


def test_cut_points_brackets_a_typical_reveal():
    assert cut_points(60, 20) == [dict(name='before_reveal', cut_s=10.0),
                                  dict(name='after_reveal', cut_s=21.0)]
    assert cut_points(30, 2) == [dict(name='before_reveal', cut_s=1.5),
                                 dict(name='after_reveal', cut_s=3.0)]
    assert cut_points(60, 20, min_cut_s=12, after_margin_s=0.5) == [
        dict(name='before_reveal', cut_s=12.0), dict(name='after_reveal', cut_s=20.5)]


def test_cut_points_skips_before_when_reveal_is_too_early():
    assert cut_points(30, 1.0) == [dict(name='before_reveal', cut_s=None,
                                        skipped_reason='reveal_too_early'),
                                   dict(name='after_reveal', cut_s=2.0)]
    assert cut_points(30, 1.5)[0] == dict(name='before_reveal', cut_s=None,
                                          skipped_reason='reveal_too_early')


def test_cut_points_caps_after_at_tail_and_skips_it_when_not_after_before():
    assert cut_points(30, 29.6) == [dict(name='before_reveal', cut_s=14.8),
                                    dict(name='after_reveal', cut_s=29.5)]
    assert cut_points(2.0, 1.9) == [dict(name='before_reveal', cut_s=1.5),
                                    dict(name='after_reveal', cut_s=None,
                                         skipped_reason='duration_too_short')]
    assert cut_points(2.0, 0.5) == [dict(name='before_reveal', cut_s=None,
                                         skipped_reason='reveal_too_early'),
                                    dict(name='after_reveal', cut_s=None,
                                         skipped_reason='duration_too_short')]


def test_cut_points_rounds_cut_s_to_three_decimals():
    assert cut_points(20, 6.28318) == [dict(name='before_reveal', cut_s=3.142),
                                       dict(name='after_reveal', cut_s=7.283)]
    assert cut_points(20, 6.28318)[0]['cut_s'] == round(0.5 * 6.28318, 3)


def test_cut_points_is_deterministic():
    for duration, reveal in [(60, 20), (30, 1.0), (2.0, 1.9), (45, None), (10, 9.5)]:
        assert cut_points(duration, reveal) == cut_points(duration, reveal)


def test_kept_points_stay_ordered_inside_clip_and_before_reveal_precedes_it():
    for duration, reveal in [(60, 20), (30, 2), (30, 1), (12, 11.5), (9, 8.9), (5, 2.5)]:
        kept = [point for point in cut_points(duration, reveal) if point['cut_s'] is not None]
        assert [point['cut_s'] for point in kept] == sorted(point['cut_s'] for point in kept)
        assert all(0 < point['cut_s'] < duration for point in kept)
        assert all(point['cut_s'] < reveal for point in kept
                   if point['name'] == 'before_reveal')


def test_validated_label_feeds_cut_points():
    assert cut_points(30, validate_reveal(reveal_labels(4, start=3.8, end=4.2), 30)) == [
        dict(name='before_reveal', cut_s=2.0), dict(name='after_reveal', cut_s=5.0)]


def test_expected_information_maps_points_to_levels():
    assert expected_information('before_reveal') == 'implied_or_ambiguous'
    assert expected_information('after_reveal') == 'explicit'
    assert expected_information('legacy_fixed') is None
    assert expected_information('unknown_point') is None
