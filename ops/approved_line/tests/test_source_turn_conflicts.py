from copy import deepcopy
import json
from pathlib import Path

import pytest

from omni_story.library.scene_casting import normalize_source_turns,validate_source_turns,speech_disputes


def turn(visible,speaking,basis='mouth_motion'):
    return dict(start_s=0,end_s=1,visible=visible,speaking=speaking,basis=basis,evidence='provider observation')


def test_real_received_contradictions_do_not_forge_visible_people_or_source_speech():
    fixture=json.loads(Path(__file__).with_name('source_visibility_conflict_fixture.json').read_text())
    raw=fixture['raw'];before=deepcopy(raw)
    normalized=normalize_source_turns(raw,fixture['actors'])
    validate_source_turns(normalized,fixture['actors'],fixture['duration'])
    assert raw==before
    conflicts=[t for t in normalized['turns'] if t.get('source_claim_conflict')]
    assert len(conflicts)==2
    for t in conflicts:
        assert t['visible']==[] and t['speaking']==[] and t['basis']=='uncertain'
        assert t['heard_actor_hypotheses']==t['source_claim_conflict']['claimed_speaking']
    assert normalize_source_turns(normalized,fixture['actors'])==normalized


@pytest.mark.parametrize('basis',['none','uncertain','mouth_motion','synchronous_gesture'])
def test_unattributed_audio_always_triggers_independent_visual_recheck(basis):
    normalized=normalize_source_turns(dict(turns=[turn([],['a'],basis)]),dict(a='known appearance'))
    validate_source_turns(normalized,dict(a='known appearance'),1)
    assert normalized['turns'][0]['speaking']==[]
    assert normalized['turns'][0]['basis']=='uncertain'
    shot=dict(source_start_s=10,source_end_s=11,speaking=[],visible=[])
    assert speech_disputes([shot],normalized['turns'],10)==[dict(span_id='d1',start_s=0,end_s=1)]


def test_unknown_positive_speaker_remains_rejected_without_alias_guessing():
    value=normalize_source_turns(dict(turns=[turn([],['unknown'])]),dict(a='known appearance'))
    with pytest.raises(ValueError,match='actually visible known actor'):
        validate_source_turns(value,dict(a='known appearance'),1)


def test_overlapping_known_speakers_are_uncertain_instead_of_arbitrarily_selecting_one():
    value=normalize_source_turns(dict(turns=[turn(['a','b'],['a','b'])]),dict(a='one',b='two'))
    validate_source_turns(value,dict(a='one',b='two'),1)
    assert value['turns'][0]['speaking']==[] and value['turns'][0]['heard_actor_hypotheses']==['a','b']


def test_visible_single_positive_speaker_and_true_silence_are_preserved():
    for row in (turn(['a'],['a']),turn(['a'],[],'none')):
        raw=dict(turns=[row]);result=normalize_source_turns(raw,dict(a='one'))
        assert result==raw
        assert speech_disputes([dict(source_start_s=0,source_end_s=1,speaking=row['speaking'],visible=['a'])],result['turns'],0)==[]
