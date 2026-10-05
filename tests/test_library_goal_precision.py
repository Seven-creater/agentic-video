"""Executable EDL progress and segment-internal review contracts."""
from copy import deepcopy
import pytest
from omni_story.library.goal_feedback_continuation import substantive_edl, _validate_economy


def plan():
    return {'fps':30,'width':90,'height':160,'audio_mode':'silent','segments':[
        {'segment_id':'original','slot_id':'slot','source_id':'fixture','window_id':'window',
         'source_in_s':1,'source_out_s':2,'speed':1,'reason':'model rationale'}]}


def catalog():
    return {'sources':[{'source_id':'fixture','sha256':'a'*64,'path':'synthetic_not_opened.mkv',
                         'duration_s':6,'audio_stream_index':1}]}


def test_labels_and_ignored_audio_fields_do_not_fake_editing_progress():
    old=plan(); new=deepcopy(old)
    new['segments'][0].update(segment_id='renamed',slot_id='other',reason='different explanation',
        framing='fit',look='none')
    new['reference_audio']={'reason':'ignored in silent mode','start_s':2,'end_s':3}
    assert substantive_edl(catalog(),old)==substantive_edl(catalog(),new)
    new['segments'][0]['look']='grayscale'
    assert substantive_edl(catalog(),old)!=substantive_edl(catalog(),new)


def economy():
    timing={'sha256':'a'*64,'measured_duration_s':5,'provenance':[
        {'segment_id':'segment','output_in_s':0,'output_out_s':5}]}
    value={'video_sha256':'a'*64,'economy_status':'pass','narrative_readability':'pass',
        'segment_checks':[{'segment_id':'segment','status':'necessary','reason':'fixture',
            'output_evidence':[{'start_s':0,'end_s':5,'observed_fact':'fixture'}]}],
        'microcut_checks':[{'segment_id':'segment','status':'necessary','reason':'fixture',
            'necessary_intervals':[{'start_s':0,'end_s':5,'observed_fact':'fixture'}],
            'removable_intervals':[]}],'limitations':[]}
    return value,timing


def test_a_highlight_does_not_establish_economy_of_the_unreviewed_remainder():
    value,timing=economy()
    assert _validate_economy(value,timing)==value
    value['microcut_checks'][0]['necessary_intervals'][0]['end_s']=1
    with pytest.raises(ValueError,match='unreviewed_segment_tail'):
        _validate_economy(value,timing)
    value['economy_status']='partial'
    assert _validate_economy(value,timing)==value


@pytest.mark.parametrize('bad',['bad',None,{},[{}]])
def test_malformed_microcut_checks_are_format_errors(bad):
    value,timing=economy(); value['microcut_checks']=[bad]
    with pytest.raises((ValueError,TypeError)):
        _validate_economy(value,timing)


def test_nonobject_instant_is_repairable_format_error():
    value,timing=economy(); value['microcut_checks'][0]['necessary_intervals']=[None]
    with pytest.raises(ValueError,match='evidence_object_required'):
        _validate_economy(value,timing)
