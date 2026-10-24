"""Prefix cut policy driven by annotated theme reveal time."""
from __future__ import annotations

import math

REVEAL_LABEL_SUFFIX = """
额外标注主旨揭示时间：
- theme_reveal_s：数字，传输包累计秒，主旨第一次可被明确判断的时刻，不是结尾；整片都没点题则为 null。
- theme_reveal_evidence：{"start_packet_s": 数字, "end_packet_s": 数字, "observation": "该时刻的画面或台词依据"}，时间范围须覆盖 theme_reveal_s，theme_reveal_s 为 null 时一并省略。
""".strip()

_EXPECTED_INFORMATION = dict(before_reveal='implied_or_ambiguous', after_reveal='explicit',
                             legacy_fixed=None)


def _r3(value):
    return round(float(value), 3)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_reveal(labels, duration_s):
    reveal = labels.get('theme_reveal_s')
    if reveal is None:
        return None
    if not _is_number(reveal) or not 0 < reveal < duration_s:
        raise ValueError('prefix_reveal_label_invalid')
    evidence = labels.get('theme_reveal_evidence')
    if not isinstance(evidence, dict):
        raise ValueError('prefix_reveal_label_invalid')
    start, end = evidence.get('start_packet_s'), evidence.get('end_packet_s')
    if not _is_number(start) or not _is_number(end) or end < start:
        raise ValueError('prefix_reveal_label_invalid')
    return reveal


def cut_points(duration_s, theme_reveal_s, *, min_cut_s=1.5, after_margin_s=1.0) -> list[dict]:
    if theme_reveal_s is None:
        return [dict(name='legacy_fixed', cut_s=_r3(min(5.0, duration_s / 3)),
                     reason='no_theme_reveal_label')]
    before = _r3(max(min_cut_s, 0.5 * theme_reveal_s))
    after = _r3(min(theme_reveal_s + after_margin_s, duration_s - 0.5))
    points = []
    if before >= theme_reveal_s:
        points.append(dict(name='before_reveal', cut_s=None, skipped_reason='reveal_too_early'))
    else:
        points.append(dict(name='before_reveal', cut_s=before))
    if after <= before:
        points.append(dict(name='after_reveal', cut_s=None, skipped_reason='duration_too_short'))
    else:
        points.append(dict(name='after_reveal', cut_s=after))
    return points


def before_last_element(labels, duration_s, *, margin_s=0.5, min_visible_s=1.5):
    point = dict(name='before_last_element', cut_s=None)
    if not _is_number(duration_s) or not math.isfinite(duration_s) or duration_s <= 0:
        return dict(point, skipped_reason='duration_too_short')
    elements = labels.get('elements') or []
    evidence = [e for e in labels.get('core_evidence') or [] if isinstance(e, dict) and
        type(e.get('element_index')) is int and 0 <= e['element_index'] < len(elements) and
        _is_number(e.get('start_packet_s')) and math.isfinite(e['start_packet_s']) and
        0 <= e['start_packet_s'] < duration_s]
    if not evidence:
        return dict(point, skipped_reason='no_core_evidence_times')
    cut = _r3(max(min_visible_s, max(e['start_packet_s'] for e in evidence) - margin_s))
    if not 0 < cut < duration_s:
        return dict(point, skipped_reason='duration_too_short')
    return dict(point, cut_s=cut,
        hidden_element_indices=sorted({e['element_index'] for e in evidence if e['start_packet_s'] >= cut}),
        visible_fraction=_r3(cut / duration_s))


def expected_information(point_name):
    return _EXPECTED_INFORMATION.get(point_name)
