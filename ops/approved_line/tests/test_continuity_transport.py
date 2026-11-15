from pathlib import Path
import pytest
from omni_story.library.material_browser import continuity_transport
from omni_story.library.state import LibraryStopped
import omni_story.library.media as media


def test_large_montage_reencoded_without_truncation_or_audio(tmp_path,monkeypatch):
    original=tmp_path/'continuity.mp4'
    with original.open('wb') as f:f.truncate(9_452_811)
    commands=[]
    def probe(path):return dict(duration_s=68,streams=[dict(codec_type='video',width=800)])
    def run(command):
        commands.append(command);Path(command[-1]).write_bytes(b'bounded_proxy')
    monkeypatch.setattr(media,'probe_media',probe);monkeypatch.setattr(media,'_run',run)
    target,measured=continuity_transport(original,68)
    assert target!=original and original.stat().st_size==9_452_811
    assert target.stat().st_size<8_000_000 and measured['duration_s']==68
    command=commands[0]
    assert '-t' not in command and '-ss' not in command and '-an' in command
    assert command[command.index('-vf')+1]=='scale=640:-2,fps=8'


def test_small_proxy_bytes_and_request_can_be_reused(tmp_path,monkeypatch):
    original=tmp_path/'continuity.mp4';original.write_bytes(b'original')
    monkeypatch.setattr(media,'probe_media',lambda path:dict(duration_s=34,streams=[]))
    monkeypatch.setattr(media,'_run',lambda command:pytest.fail('no re-encode needed'))
    assert continuity_transport(original,34)[0]==original


def test_duration_mapping_error_is_not_hidden_by_compression(tmp_path,monkeypatch):
    original=tmp_path/'continuity.mp4';original.write_bytes(b'original')
    monkeypatch.setattr(media,'probe_media',lambda path:dict(duration_s=10,streams=[]))
    with pytest.raises(LibraryStopped,match='mapping_or_size_invalid'):continuity_transport(original,34)
