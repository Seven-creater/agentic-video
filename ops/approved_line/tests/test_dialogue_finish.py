from copy import deepcopy
import pytest

from omni_story.library.dialogue_finish import remove_micro_tails, verified_masks
from omni_story.library.final_picture import ground_visual_notes


def segment(ident, slot, start, end, line='l1'):
    return dict(segment_id=ident,slot_id=slot,scene_id='scene',line_id=line,
        start_s=start,end_s=end,speed=1,window_id='w',source_id='src',source_in_s=10,source_out_s=12)


def test_micro_tail_removed_without_changing_audio_clock_source_or_opening():
    rows=[segment('a','first',0,2),segment('b','flash',2,2+2/30),segment('c','next',2+2/30,4,'l2')]
    original=deepcopy(rows);lines=[dict(line_id='l1'),dict(line_id='l2')]
    receipt=remove_micro_tails(rows,lines)
    assert len(receipt['changes'])==1 and [r['segment_id'] for r in rows]==['a','c']
    assert rows[0]['source_in_s']==original[0]['source_in_s']
    assert rows[0]['source_out_s']==original[0]['source_out_s']
    assert rows[0]['end_s']==original[1]['end_s'] and rows[-1]['end_s']==4
    assert rows[0]['speed']==pytest.approx(2/(2+2/30))
    assert lines[0]['segment_ids']==['a']


def test_does_not_delete_whole_short_line_or_heavily_slow_an_intro():
    rows=[segment('a','first',0,.1),segment('b','tail',.1,.4),segment('c','short',.4,.6,'l2')]
    before=deepcopy(rows)
    assert not remove_micro_tails(rows,[])['changes']
    assert rows==before


def test_verified_masks_remove_redundant_refinement_and_merge_without_shrinking():
    masks=[dict(start_s=0,end_s=20,x=.2,y=.8,w=.4,h=.15),dict(start_s=0,end_s=20,x=.4,y=.85,w=.4,h=.1)]
    proxies={'w':dict(source_masks=masks,proxy=dict(sha256='sha',source_id='src',source_start_s=0,
        source_end_s=20,spec=dict(subtitle_masks=masks)))}
    observations={'w':dict(subtitle_blind=True,evidence_sha256='sha')}
    rows=[segment('a','first',0,2)];rows[0]['source_subtitle_treatments']=[dict(y=.6,h=.4)]
    assert verified_masks(rows,proxies,observations)
    selected=rows[0]['source_subtitle_treatments']
    assert len(selected)==1 and selected[0]['y']==.8
    assert abs(selected[0]['x']+selected[0]['w']-.8)<1e-9
    assert abs(selected[0]['y']+selected[0]['h']-.95)<1e-9
    observations['w']['subtitle_blind']=False;before=deepcopy(rows)
    assert verified_masks(rows,proxies,observations) is None and rows==before


def test_visual_facts_cannot_be_invented_by_audio_reviewer():
    observed=dict(facts=[dict(fact_id='picture_1',observation='Two figures face one another')])
    verdict=dict(visual_notes=['A scale and magnet appear'],issues=[])
    ground_visual_notes(verdict,observed)
    assert not verdict['picture_grounding']['passed'] and verdict['repair_at']=='selection'
    verdict=dict(visual_notes=['picture_1'])
    ground_visual_notes(verdict,observed)
    assert verdict['picture_grounding']['passed']
    assert verdict['visual_notes']==['Two figures face one another']


