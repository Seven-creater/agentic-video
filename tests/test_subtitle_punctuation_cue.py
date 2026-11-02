"""Final subtitle splitting stays local; media rendering is stubbed out."""
from pathlib import Path
import string
from types import SimpleNamespace

import pytest

from omni_story.library import audio_render, render


EVIDENCE = '炮火落在身边，他不动如山；风浪再大，包裹也稳稳看得见，每一秒，都有迹可循。'


def seconds(stamp):
    hours, minutes, rest = stamp.replace(',', '.').split(':')
    return int(hours) * 3600 + int(minutes) * 60 + float(rest)


@pytest.fixture
def subtitles(tmp_path, monkeypatch):
    from PIL import ImageFont

    source, font = tmp_path / 'source.mp4', tmp_path / 'font.ttf'
    source.write_bytes(b'fixture video')
    font.write_bytes(b'fixture font')
    monkeypatch.setattr(audio_render, '_video_info', lambda _: dict(
        width=1280, height=720, duration_s=600))
    monkeypatch.setattr(render, '_caption_font', lambda: font)
    monkeypatch.setattr(ImageFont, 'truetype', lambda *a: SimpleNamespace(getname=lambda: ('Fixture', '')))
    monkeypatch.setattr(audio_render.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=0))
    monkeypatch.setattr(audio_render, '_receipt', lambda *a, **details: details)

    def write(lines, bare=False):
        result = audio_render.subtitle_final_video(source, lines, tmp_path / 'final_subtitled.mp4')
        srt = Path(result['subtitle_srt_path']).read_text(encoding='utf-8')
        ass = Path(result['subtitle_ass_path']).read_text(encoding='utf-8-sig')
        cues = []
        for index, block in enumerate(srt.strip().split('\n\n'), 1):
            number, timing, *rows = block.splitlines()
            assert int(number) == index
            start, end = map(seconds, timing.split(' --> '))
            cues.append((start, end, rows))
        events = [line.split(',', 9) for line in ass.splitlines() if line.startswith('Dialogue:')]
        assert len(events) == len(cues)
        for (start, end, rows), event in zip(cues, events):
            assert seconds(event[1]) == pytest.approx(start, abs=.005001)
            assert seconds(event[2]) == pytest.approx(end, abs=.005001)
            # Retain the existing ASS override escaping policy.
            assert event[9].split(r'\N') == [row.replace('\\', '＼').replace('{', '｛').replace('}', '｝')
                                                  for row in rows]
            assert start < end
            assert 1 <= len(rows) <= 2
            assert bare or all(any(char.isalnum() for char in row) for row in rows)
        assert ''.join(''.join(rows) for _, _, rows in cues) == ''.join(
            ' '.join(line['text'].split()) for line in lines)
        return cues

    return write


@pytest.mark.parametrize('prefix', ['', '前' * 36])
def test_evidence_tail_merges_without_timing_gap(subtitles, prefix):
    text = prefix + EVIDENCE
    cues = subtitles([dict(text=text, start_s=59, end_s=67.960)])
    assert len(cues) == (2 if prefix else 1)
    assert cues[0][0] == 59 and cues[-1][1] == 67.960
    assert cues[-1][2] == [EVIDENCE[:18], EVIDENCE[18:]]
    for previous, following in zip(cues, cues[1:]):
        assert previous[1] == following[0]
    if prefix:
        assert cues[0][1] == pytest.approx(59 + 8.96 * 36 / len(text), abs=.000501)


EVIDENCE_LINE = '第一，东西收了钱不给配齐；第二，设备坏了照样开门营业；第三，我去反映问题，你们还互相推脱。'


def test_leading_closing_punctuation_joins_the_previous_cue(subtitles):
    cues = subtitles([dict(text=EVIDENCE, start_s=0, end_s=8),
                      dict(text=EVIDENCE_LINE, start_s=13, end_s=22.520)])
    assert cues[1][2] == ['第一，东西收了钱不给配齐；第二，设备',
                          '坏了照样开门营业；第三，我去反映问题，']
    assert cues[2][2] == ['你们还互相推脱。']
    assert cues[2][1] == 22.520
    for previous, following in zip(cues[1:], cues[2:]):
        assert previous[1] == following[0]


def test_leading_closing_punctuation_joins_the_previous_row(subtitles):
    text = '前' * 18 + '。' + '后' * 20
    cues = subtitles([dict(text=text, start_s=0, end_s=4)])
    assert cues[0][2] == ['前' * 18 + '。', '后' * 17]
    assert cues[1][2] == ['后' * 3]
    assert cues[0][1] == cues[1][0]


def test_opening_punctuation_moves_to_the_following_chunk(subtitles):
    text = '前' * 35 + '「你们」都好。'
    cues = subtitles([dict(text=text, start_s=0, end_s=4)])
    assert cues[0][2] == ['前' * 18, '前' * 17]
    assert cues[1][2] == ['「你们」都好。']
    assert cues[0][1] == cues[1][0]


@pytest.mark.parametrize('length', [18, 36, 72])
@pytest.mark.parametrize('tail', ['。', '……', '！」', '...', '!?', '\"\'', '—–--',
                                  ' \t？！ \n ', string.punctuation, '。—…」' * 30, '～＋＝｜＄＾｀'],
                         ids=['period', 'ellipsis', 'closing-quote', 'dots', 'ascii', 'quotes',
                              'dashes', 'whitespace', 'all-ascii-punctuation', 'long-tail', 'fullwidth'])
def test_punctuation_tail_adds_no_cue_or_row(subtitles, length, tail):
    cues = subtitles([dict(text='文' * length + tail, start_s=1, end_s=21)])
    assert len(cues) == (length + 35) // 36
    assert cues[-1][1] == 21
    assert len(cues[-1][2]) == (1 if length == 18 else 2)
    for _, _, rows in cues:
        assert all(row.count('文') <= 18 for row in rows)
    assert all(a[1] == b[0] for a, b in zip(cues, cues[1:]))


def test_leading_fragments_attach_to_next_cue_of_the_same_line(subtitles):
    leading = '「—' * 36
    cues = subtitles([dict(text='上一句。', start_s=0, end_s=1),
                      dict(text=leading + '字' * 72, start_s=2, end_s=14)])
    assert cues[0] == (0, 1, ['上一句。'])
    assert cues[1] == (2, 11, [leading + '字' * 18, '字' * 18])
    assert cues[2] == (11, 14, ['字' * 18, '字' * 18])


def test_middle_punctuation_extends_previous_cue(subtitles):
    middle = '…—' * 36
    cues = subtitles([dict(text='甲' * 36 + middle + '乙' * 36, start_s=0, end_s=16)])
    assert cues == [(0, 12, ['甲' * 18, '甲' * 18 + middle]),
                    (12, 16, ['乙' * 18, '乙' * 18])]


@pytest.mark.parametrize('text, expected', [
    ('短句。', [(2, 12, ['短句。'])]),
    ('  普通\t句子，\n继续。  ', [(2, 12, ['普通 句子， 继续。'])]),
    ('abcdefghijklmnopqrstuvwxyz0123456789ABCD',
     [(2, 11, ['abcdefghijklmnopqr', 'stuvwxyz0123456789']), (11, 12, ['ABCD'])]),
])
def test_ordinary_lines_keep_existing_text_layout_and_times(subtitles, text, expected):
    assert subtitles([dict(text=text, start_s=2, end_s=12)]) == expected


def test_trailing_fragment_does_not_merge_across_lines(subtitles):
    cues = subtitles([dict(text='字' * 36 + '。', start_s=0, end_s=2),
                      dict(text='下一句。', start_s=2, end_s=3)])
    assert cues == [(0, 2, ['字' * 18, '字' * 18 + '。']), (2, 3, ['下一句。'])]


def test_punctuation_only_line_keeps_one_cue(subtitles):
    cues = subtitles([dict(text='你好。', start_s=0, end_s=1), dict(text='……！」', start_s=1, end_s=2)], bare=True)
    assert cues == [(0, 1, ['你好。']), (1, 2, ['……！」'])]


def test_blank_line_is_rejected(subtitles):
    with pytest.raises(ValueError, match='final_subtitle_invalid_speech_interval'):
        subtitles([dict(text=' \t\n ', start_s=0, end_s=1)])


def test_recorded_deliverables_are_not_rewritten(subtitles, tmp_path):
    paths = [tmp_path / ('final_subtitled' + suffix) for suffix in ('.mp4', '.srt', '.ass')]
    for path in paths:
        path.write_bytes(b'recorded deliverable')
    with pytest.raises(ValueError, match='audio_render_output_already_exists'):
        subtitles([dict(text=EVIDENCE, start_s=0, end_s=1)])
    assert all(path.read_bytes() == b'recorded deliverable' for path in paths)


def test_opening_quote_never_adds_a_third_row(subtitles):
    text = '字' * 35 + '「' + '字' * 36 + '」'
    cues = subtitles([dict(text=text, start_s=0, end_s=4)])
    assert all(len(rows) <= 2 for _, _, rows in cues)
