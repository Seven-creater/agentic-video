"""Natural subtitle boundaries, balanced fallbacks and measured-time offsets."""
import math
import string
import unicodedata

import pytest

from omni_story.library import audio_render
from test_subtitle_punctuation_cue import subtitles


EVIDENCE = '问题一条条认下来，整改当着大家的面定了。顾客较真，商家认账，这买卖才做得下去。'


def visible(text):
    return sum(not (char.isspace() or unicodedata.category(char).startswith('P') or
                   unicodedata.normalize('NFKC', char) in string.punctuation)
               for char in text)


def test_production_evidence_keeps_sentence_and_tail_together(subtitles):
    assert visible(EVIDENCE) == 34 and len(EVIDENCE) == 39
    cues = subtitles([dict(text=EVIDENCE, start_s=98, end_s=107)])
    # The visible-character rule keeps this 34-letter line in one cue.
    assert cues == [(98, 107, ['问题一条条认下来，整改当着大家的面定了。',
                               '顾客较真，商家认账，这买卖才做得下去。'])]
    assert all(4 <= visible(row) <= 18 for row in cues[0][2])


def test_over_limit_evidence_splits_at_sentence_end_with_proportional_times(subtitles):
    text = '今天现场，' + EVIDENCE
    assert visible(text) == 38
    cues = subtitles([dict(text=text, start_s=98, end_s=107)])
    texts = [''.join(rows) for _, _, rows in cues]
    assert texts == ['今天现场，问题一条条认下来，整改当着大家的面定了。',
                     '顾客较真，商家认账，这买卖才做得下去。']
    assert cues[0][0] == 98 and cues[-1][1] == 107
    assert cues[0][1] == cues[1][0] == pytest.approx(
        98 + 9*len(texts[0])/len(text), abs=.000501)
    assert all(6 <= visible(text) <= 36 for text in texts)
    assert all(4 <= visible(row) <= 18 for _, _, rows in cues for row in rows)
    assert all(row != '下去。' for _, _, rows in cues for row in rows)


@pytest.mark.parametrize('length', [37, 38, 39, 40, 73, 105, 109, 144])
def test_unbroken_text_uses_fewest_balanced_cues(subtitles, length):
    cues = subtitles([dict(text='字'*length, start_s=2, end_s=22)])
    lengths = [visible(''.join(rows)) for _, _, rows in cues]
    assert len(cues) == math.ceil(length/36)
    assert max(lengths)-min(lengths) <= 1
    assert all(6 <= length <= 36 for length in lengths)
    for _, _, rows in cues:
        assert len(rows) == (2 if visible(''.join(rows)) > 18 else 1)
        assert all(4 <= visible(row) <= 18 for row in rows)
    assert all(a[1] == b[0] for a, b in zip(cues, cues[1:]))


@pytest.mark.parametrize('phrase', ['你一样一样给我说清楚', '条条属实', '糊弄不过去'])
def test_available_commas_keep_phrases_whole_in_cues_and_rows(subtitles, phrase):
    text = '甲'*20 + '，' + phrase + '，' + '乙'*18 + '。'
    cues = subtitles([dict(text=text, start_s=0, end_s=10)])
    assert len(cues) == 2
    assert ''.join(cues[0][2]) == '甲'*20 + '，'
    assert cues[1][2] == [phrase + '，', '乙'*18 + '。']


@pytest.mark.parametrize('text, expected', [
    ('这些问题全部都在这里，你一样一样给我说清楚。',
     ['这些问题全部都在这里，', '你一样一样给我说清楚。']),
    ('你一样一样给我说清楚，今天发现的这些问题条条属实。',
     ['你一样一样给我说清楚，', '今天发现的这些问题条条属实。']),
    ('这些问题都要讲清楚，条条属实，谁也糊弄不过去。',
     ['这些问题都要讲清楚，', '条条属实，谁也糊弄不过去。']),
])
def test_rows_follow_nearby_commas(subtitles, text, expected):
    assert subtitles([dict(text=text, start_s=0, end_s=4)])[0][2] == expected


@pytest.mark.parametrize('strong, weak', [('。', '，'), ('！', '；'), ('…', '：'),
                                        ('，', ' '), ('；', ' '), ('—', ' ')])
def test_cues_prefer_boundary_strength_before_balance(strong, weak):
    text = '甲'*12 + strong + '乙'*8 + weak + '丙'*20
    assert audio_render._subtitle_chunks(text, 36) == [
        '甲'*12 + strong, '乙'*8 + weak + '丙'*20]


def test_cues_choose_most_balanced_equal_strength_boundaries():
    text = '甲'*24 + '，' + '乙'*6 + '，' + '丙'*6 + '，' + '丁'*24
    assert audio_render._subtitle_chunks(text, 36) == [
        '甲'*24 + '，' + '乙'*6 + '，', '丙'*6 + '，' + '丁'*24]


def test_cue_count_takes_priority_over_stronger_boundaries():
    text = '甲'*20 + '。' + '乙'*15 + '，' + '丙'*15 + '。' + '丁'*20
    assert audio_render._subtitle_chunks(text, 36) == [
        '甲'*20 + '。' + '乙'*15 + '，', '丙'*15 + '。' + '丁'*20]


def test_mid_text_cuts_only_in_stretches_longer_than_cue_limit():
    text = '甲'*20 + '，' + '乙'*20 + '，' + '丙'*20
    assert audio_render._subtitle_chunks(text, 36) == [
        '甲'*20 + '，', '乙'*20 + '，', '丙'*20]


@pytest.mark.parametrize('strong, weak', [('。', '，'), ('？', '、'), ('…', '：'),
                                        ('，', ' '), ('；', ' '), ('—', ' ')])
def test_rows_prefer_boundary_strength_before_balance(strong, weak):
    text = '甲'*8 + strong + '乙'*4 + weak + '丙'*12
    assert audio_render._subtitle_rows(text) == ['甲'*8 + strong, '乙'*4 + weak + '丙'*12]


def test_rows_choose_nearest_equal_strength_boundary():
    text = '甲'*8 + '，' + '乙'*5 + '，' + '丙'*13
    assert audio_render._subtitle_rows(text) == ['甲'*8 + '，' + '乙'*5 + '，', '丙'*13]


def test_rows_prefer_a_space_to_mid_text():
    assert audio_render._subtitle_rows('甲'*8 + ' ' + '乙'*16) == ['甲'*8 + ' ', '乙'*16]


@pytest.mark.parametrize('text', ['甲'*2 + '，' + '乙'*18, '甲'*18 + '，' + '乙'*2])
def test_row_minimum_rejects_an_orphan_boundary(text):
    rows = audio_render._subtitle_rows(text)
    assert [visible(row) for row in rows] == [10, 10]
    assert ''.join(rows) == text


@pytest.mark.parametrize('tail', range(1, 6))
@pytest.mark.parametrize('leading', [False, True])
def test_tiny_natural_fragment_merges_with_documented_overflow(subtitles, tail, leading):
    text = ('尾'*tail + '，' + '字'*36 if leading else '字'*36 + '，' + '尾'*tail)
    cues = subtitles([dict(text=text, start_s=0, end_s=4)])
    assert len(cues) == 1
    rows = cues[0][2]
    assert len(rows) == 2
    assert ''.join(rows) == text
    assert 36 < visible(text) <= 42
    assert abs(visible(rows[0])-visible(rows[1])) <= 1


def test_six_character_tail_keeps_its_own_cue(subtitles):
    text = '字'*36 + '，' + '尾'*6
    cues = subtitles([dict(text=text, start_s=0, end_s=4)])
    assert [visible(''.join(rows)) for _, _, rows in cues] == [36, 6]


@pytest.mark.parametrize('middle', [32, 33])
def test_tiny_fragments_on_both_ends_never_leave_an_orphan(subtitles, middle):
    text = '头'*5 + '，' + '字'*middle + '，' + '尾'*5
    cues = subtitles([dict(text=text, start_s=0, end_s=4)])
    assert len(cues) == (1 if middle == 32 else 2)
    assert all(6 <= visible(''.join(rows)) <= 42 for _, _, rows in cues)
    assert all(visible(row) >= 4 for _, _, rows in cues for row in rows)


@pytest.mark.parametrize('length', [37, 40, 43, 73])
def test_each_single_boundary_position_preserves_text_and_size_limits(length):
    for boundary in range(1, length):
        text = '甲'*boundary + '，' + '乙'*(length-boundary)
        chunks = audio_render._subtitle_chunks(text, 36)
        assert ''.join(chunks) == text
        assert all(6 <= visible(chunk) <= 42 for chunk in chunks)
        for chunk in chunks:
            rows = audio_render._subtitle_rows(chunk)
            assert ''.join(rows) == chunk
            assert len(rows) == (2 if visible(chunk) > 18 else 1)
            assert all(4 <= visible(row) <= max(18, math.ceil(visible(chunk)/2)) for row in rows)


@pytest.mark.parametrize('opening, closing', [('「', '」'), ('（', '）'), ('“', '”'), ('[', ']')])
def test_quote_and_bracket_attachment_survives_balancing(subtitles, opening, closing):
    text = '甲'*19 + opening + '乙'*21 + closing + '。'
    cues = subtitles([dict(text=text, start_s=0, end_s=4)])
    for _, _, rows in cues:
        for row in rows:
            assert not audio_render._is_opening(row.rstrip()[-1])
            assert not (audio_render._is_punctuation(row[0]) and
                        not audio_render._is_opening(row[0]))


def test_punctuation_and_spaces_do_not_trigger_extra_cues_or_rows(subtitles):
    text = ('「字」， '*18).strip()
    cues = subtitles([dict(text=text, start_s=1, end_s=4)])
    assert cues == [(1, 4, [text])]
