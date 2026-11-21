from omni_story.library.source_activity import activity_hits, shot_activity_ranges, source_window


def test_activity_scan_uses_played_source_frames_including_aliases_repeats_and_holds():
    shot = dict(source_sha256='film',source_start_s=100,source_end_s=106)
    shots = dict(wide=shot,close=shot,other=dict(shot,source_sha256='other'))
    cuts = [dict(at=i,scene='a',shot=k,offset=offset,speed=speed,tail='hold')
            for i,k,offset,speed in ((0,'wide',0,1),(1,'close',2,1),(2,'wide',2.5,.5),
                                     (3,'wide',5.5,1),(4,'other',2,1))]
    trace = dict(seed_cut='original',scene='a',source_sha256='film',first=3060,end=3090)
    assert list(activity_hits(cuts,shots,150,30,[trace])) == ['2','3']
    assert source_window(shot,cuts[3],90,30)==(3165,3180)
    assert source_window(shot,dict(offset=4,speed=1.5,tail='loop'),200,30)==(3120,3180)
    assert activity_hits([dict(cuts[0],at=0,offset=1)],shots,30,30,[trace]) == {}


def test_planned_exclusion_is_scoped_but_reviewer_finds_the_same_action_in_other_scenes():
    clips = dict(a=dict(source_sha256='x',source_start_s=9,source_end_s=11),
                 b=dict(source_sha256='x',source_start_s=10.5,source_end_s=13))
    trace = dict(seed_cut='1',scene='stationary',source_sha256='x',first=300,end=360)
    scenes = dict(stationary=['a'],walking=['b'])
    assert shot_activity_ranges([trace],clips,scenes,30,scene_only=True)=={'a':[[300,330]]}
    assert shot_activity_ranges([trace],clips,scenes,30)=={'a':[[300,330]],'b':[[315,360]]}


def test_source_trace_follows_a_crop_across_pages_until_a_positive_stop(tmp_path, monkeypatch):
    import json
    import shutil
    import pytest
    from omni_story.library import source_activity
    from omni_story.library.selected_edit import ff

    if not shutil.which('ffmpeg'):
        pytest.skip('ffmpeg is required for continuous source review')
    video = tmp_path/'source.mp4'
    ff('-f','lavfi','-i','color=c=blue:s=96x64:r=30','-t',24,'-c:v','libx264',video)
    monkeypatch.setattr(source_activity,'_probe',lambda *_: {'format':{'duration':'24'}})
    stop_checks = []
    def ask(stage, prompt, validate, media):
        data = json.loads(prompt[prompt.index('{"anchor_row"'):])
        assert media.stat().st_size
        if stage.endswith('_stop'):
            stop_checks.append(stage)
            value = (dict(status='continues',evidence='Only upper body visible at the edge')
                     if len(stop_checks)==1 else
                     dict(status='ended',stopped_row=2,proof='fixed_position',evidence='Feet remain at the counter'))
        else:
            anchor = data['anchor_row']
            value = dict(status='seen',first=1,last=anchor,before='approaching',during='travel',after='close-up')
        return validate(value)
    trace = source_activity.trace_activity(tmp_path,'check',dict(cut='7',seen='walking',interval=dict(start=0,end=60)),
        dict(scene='talk',speed=1,offset=0),dict(source_sha256='x',source_start_s=5,source_end_s=7),
        {'x':video},30,ask,ff)
    assert len(stop_checks)==2 and trace['end']==18*30


def test_crop_opinion_cannot_clear_known_departure_in_same_exchange(tmp_path):
    from omni_story.library.source_activity import review_activity_uses

    shot = dict(source_sha256='x',source_start_s=0,source_end_s=1)
    cuts = [dict(at=i,scene=s,shot='head',offset=0,speed=1) for i,s in enumerate(('stall','stall','road'))]
    trace = dict(scene='stall',source_sha256='x',first=0,end=30,seed_cut='4',seen='Seller leaving with a box')
    lines = [dict(start_s=i,end_s=i+1,role='seller',text=t) for i,t in enumerate(('How much?', 'We leave now.', 'Walk with me.'))]
    def ask(stage,prompt,validate,media):
        return validate({'cuts':{'1':{'fit':'fits','why':'Face works as a reaction'},
                                 '2':{'fit':'fits','why':'Leaves in this beat','story_change':'We leave now.'},
                                 '3':{'fit':'fits','why':'Walking scene'}}})
    findings=review_activity_uses(tmp_path,'uses',tmp_path/'film.mp4',cuts,{'head':shot},90,30,lines,[],[trace],ask,lambda *a:None)
    assert [(r['cut'],r['kind']) for r in findings]==[('1','action_conflict')]
