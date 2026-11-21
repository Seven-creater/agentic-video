from types import SimpleNamespace
import pytest
from omni_story.library import multiscene_pick
from omni_story.library.scene_casting import candidate_ranges
from omni_story.library.story_dialogue import StoryDialogue


def part(ref,src,w,a,b):
    return dict(reference=ref,source=src,window_ids=[w],entry='arrives',exit='leaves',
                transition='arrival follows departure',usable_segments=[
                dict(window_id=w,source_sha256=src,source_start_s=a,source_end_s=b)])


def test_two_sources_with_same_clock_both_survive():
    a,b=part('a','x','w1',10,14),part('b','y','w2',10,14)
    plan=multiscene_pick.compose([a,b])
    assert candidate_ranges(plan)=={'w1':[(10,14)],'w2':[(10,14)]}


def test_overlapping_source_cannot_be_a_second_scene():
    with pytest.raises(ValueError,match='reused'):
        multiscene_pick.compose([part('a','x','w1',10,14),part('b','x','w2',13,19)])


def test_two_scenes_sharing_a_window_cannot_borrow_each_others_frames():
    a,b=part('a','x','w',10,14),part('b','x','w',16,20)
    shot=dict(slot_id='s',window_id='w',source_start_s=10,source_end_s=20,events=[])
    story=SimpleNamespace(active_candidate=multiscene_pick.compose([a,b]),shots=[shot])
    picked=StoryDialogue.selection_shots(story,{'scene_id':'b'},['w'])
    assert [(s['source_start_s'],s['source_end_s']) for s in picked]==[(16,20)]
    with pytest.raises(ValueError):StoryDialogue.selection_shots(story,{'scene_id':'b'},['foreign'])


def test_dialogue_then_silent_scene_requires_same_visible_actor_but_no_mouth_motion():
    story=SimpleNamespace(route=SimpleNamespace(scenes=[dict(scene_id='second',roles=['buyer'],line_ids=[])],lines=[]),
          active_candidate=multiscene_pick.compose([part('first','x','a',0,4),part('second','x','b',8,12)]),
          active_ranges={'a':[(0,4)],'b':[(8,12)]},
          shots=[dict(slot_id='s',window_id='b',source_sha256='x',source_start_s=8,source_end_s=12,
                      visible=['same'],speaking=[],dub_speaker_ids=[],loopable=False,events=[])])
    value=dict(scene_windows={'second':['b']},cast={'buyer':'same'})
    StoryDialogue.validate_selection(story,value)
    value['cast']['buyer']='other'
    with pytest.raises(ValueError,match='出镜'):StoryDialogue.validate_selection(story,value)


def test_dialogue_adaptation_cannot_drop_a_scene_or_its_recurring_cast(tmp_path):
    from omni_story.library import reference_story
    from omni_story.library.scene_casting import adapt_roles_to_scene

    lines=[dict(line_id=i,role=role,text=text) for i,role,text in [
        (1,'sender','main dialogue '*12),(2,'receiver','main answer '*12),
        (3,'companion','hint'),(4,'sender','follow-up'),(5,'companion','answer')]]
    scenes=[dict(scene_id=sid,roles=roles,line_ids=ids,setting=place,interaction=action)
            for sid,roles,ids,place,action in [
                ('setup',['sender','receiver'],[1,2],'hall','talk standing'),
                ('memory',['sender','receiver','companion'],[3],'party','recall'),
                ('ending',['sender','companion'],[4,5],'corridor','walk together')]]
    story=StoryDialogue.__new__(StoryDialogue)
    story.route=SimpleNamespace(scenes=scenes,lines=lines,config={})
    story.contract=reference_story.make_contract({},scenes,lines,visual_policy='continuous_dialogue_adaptation_v1')
    story.requirements_planned=False
    story.browser_result={'candidates':[]}
    story.root=tmp_path
    story.state=SimpleNamespace(output=tmp_path)
    for exhausted in (False,True):
        with pytest.raises(ValueError,match='companion.*memory'):
            adapt_roles_to_scene(story,['companion'],'not enough actors',picture_led=exhausted)
        assert len(story.route.scenes)==3 and len(story.route.lines)==5
    answer=dict(omission_reason='keep the story',scenes=[dict(scene_id=s['scene_id'],
        relationship='friends',dramatic_progression='question and answer',setting_type='same bench') for s in scenes])

    def call(name,prompt,valid):
        assert '任何有对白的场景都不能把台词全省去' in prompt
        with pytest.raises(ValueError,match='companion.*memory'):
            valid(dict(answer,optional_roles=['companion']))
        return valid(dict(answer,optional_roles=[]))

    story.call=call
    story.retrieve()
    assert [(s['scene_id'],s['setting'],s['interaction']) for s in story.route.scenes]==[
        ('setup','hall','talk standing'),('memory','party','recall'),('ending','corridor','walk together')]
    assert story.route.scenes[1]['roles']==['sender','receiver','companion']
    assert [s['line_ids'] for s in story.route.scenes]==[[1,2],[3],[4,5]]


def test_browser_picks_once_and_stops_with_the_failed_review(tmp_path,monkeypatch):
    import json
    from omni_story.library.material_browser import MaterialBrowser
    from omni_story.library import scene_pick
    from omni_story.library.state import LibraryStopped
    refs=[dict(scene_id='a',roles=['X'],line_ids=[1]),dict(scene_id='b',roles=['X'],line_ids=[])]
    data=dict(characters={'C1':{'appearance':'round ears','entity_id':'global'}},scenes={s:dict(record=s,window_ids=[s],characters=['C1'],source_start_s=0,source_end_s=4) for s in ['s1','s2','s3']})
    ranks=[]
    def call(stage,prompt,valid,*args):
        if 'page' in stage:
            assert 'round ears' in prompt
            return valid(dict(options={'a':['s1'],'b':['s2','s3']}))
        assert 'round ears' in prompt
        ranks.append(stage)
        rows=[dict(reference=r,source=s,entry='in',exit='out',transition='transition',takes=[dict(window=s,start_s=0,end_s=4)])
              for r,s in zip(('a','b'),('s1','s2'))]
        with pytest.raises(ValueError,match='恰好一行'):valid(dict(scenes=rows+rows[1:],cast_hints={'X':'C1'}))
        with pytest.raises(ValueError):valid(dict(scenes=rows,cast_hints={'X':'unknown'}))
        return valid(dict(scenes=rows,cast_hints={'X':'C1'}))
    browser=SimpleNamespace(d=SimpleNamespace(route=SimpleNamespace(scenes=refs,config={}),seen=set(),call=call),
        catalog_rows=lambda:[dict(window_id=s,summary='talk') for s in data['scenes']],isolated_page_call=lambda:None,root=tmp_path)
    browser.d.records={s:[{'observation_id':s}] for s in data['scenes']}
    observed=dict(roles=[dict(role_id='A',entity_id='global',identity_confirmed=True)],
                  events=[dict(local_start_s=0,local_end_s=4,role_ids=['A'],actor_id='A')])
    browser.d.route.store=SimpleNamespace(get=lambda _:dict(window=dict(source_start_s=0,source_end_s=4,observation=observed)))
    monkeypatch.setattr(scene_pick,'load',lambda root:data)
    def ground(browser,scene):
        wid=scene['window_ids'][0]
        return dict(window_ids=[wid],usable_segments=[dict(window_id=wid,source_sha256=wid,source_start_s=0,source_end_s=4)])
    monkeypatch.setattr(multiscene_pick,'ground',ground)
    def review(browser,rows,refs,i,hints,appearances):
        raise multiscene_pick.PictureMismatch(['b 缺人物画面'],dict(scenes=[]),{},tmp_path/'sequence.mp4')
    monkeypatch.setattr(multiscene_pick,'review_sequence',review)
    with pytest.raises(LibraryStopped,match='multiscene_pick_failed:picture_mismatch:b 缺人物画面'):
        MaterialBrowser.run(browser)
    assert ranks==['material_scene_sequence_rank_1']
    failure=json.loads((tmp_path/'scene_pick_failure.json').read_text())
    assert failure['problems']==['b 缺人物画面'] and [r['source'] for r in failure['choice']['scenes']]==['s1','s2']
    with pytest.raises(LibraryStopped,match='followup_feedback'):
        MaterialBrowser.run(browser,feedback=dict(reason='need more footage'))
    assert ranks==['material_scene_sequence_rank_1']


def test_window_roster_counts_only_people_seen_in_the_usable_seconds():
    roles=[dict(role_id=r,entity_id=r,identity_confirmed=True) for r in ('cat','armor')]
    timed=dict(roles=roles,events=[dict(local_start_s=0,local_end_s=6,role_ids=['cat'],actor_id='cat'),
                                   dict(local_start_s=10,local_end_s=16,role_ids=['armor'],actor_id='armor')])
    stored={'timed':timed,'untimed':dict(roles=roles)}
    data=dict(characters={'C2':{'entity_id':'cat'},'C5':{'entity_id':'armor'}},
              scenes={'s':dict(window_ids=list(stored),source_start_s=109.4,source_end_s=117)})
    browser=SimpleNamespace(by_id={w:{'summary':'x'} for w in stored},d=SimpleNamespace(records={w:[{'observation_id':w}] for w in stored},
        route=SimpleNamespace(store=SimpleNamespace(get=lambda w:dict(window=dict(source_start_s=100,source_end_s=117,observation=stored[w]))))))
    rows={r['window']:r for r in multiscene_pick.window_options(browser,data,{'s'})['s']}
    assert rows['timed']['usable']==[9.4,17] and rows['timed']['actors']==['C5']
    assert rows['untimed']['actors']==['C2','C5']


def test_review_clock_beyond_a_clip_is_a_format_error_not_a_missing_person(tmp_path,monkeypatch):
    import subprocess
    rows=[part('a','one','w1',0,16)]
    refs=[dict(scene_id='a',roles=['boss'])]
    def scene(at):
        return dict(reference='a',ok=True,people=[dict(role='boss',actor='C5',clip='S1W1',at_s=at,evidence='armor sits')],exchanges=[])
    def call(stage,prompt,valid,media):
        with pytest.raises(ValueError,match='S1W1.*16'):valid(dict(scenes=[scene(19)],joins=[]))
        return valid(dict(scenes=[scene(3)],joins=[]))
    monkeypatch.setattr(subprocess,'run',lambda *args,**kwargs:None)
    browser=SimpleNamespace(root=tmp_path,d=SimpleNamespace(call=call),proxies={'w1':{'proxy':{'source_start_s':0,'path':'w1.mp4'}}})
    assert multiscene_pick.review_sequence(browser,rows,refs,1,{'boss':'C5'},{'C5':'armor'})==[]


def test_join_review_uses_animation_evidence_and_adapted_transition(tmp_path,monkeypatch):
    import subprocess

    rows=[part('a','one','w1',0,2),part('b','two','w2',4,6)]
    refs=[dict(scene_id='a',roles=['human'],place='literal airport'),
          dict(scene_id='b',roles=['human'],place='literal plane')]
    report={'scenes':[dict(reference=s,ok=True,people=[dict(role='human',actor='C1',clip='S'+str(i)+'W1',
        at_s=1,evidence='white round head turns')],exchanges=[]) for i,s in enumerate(('a','b'),1)],'joins':[{'to':'b','ok':True,'evidence':'The white character leaves the store and enters the carriage.'}]}

    def call(stage,prompt,valid,media):
        assert '改编所选的动画素材' in prompt
        assert '没有参考片的真人、服饰、地点或道具不能作为否决理由' in prompt
        assert 'literal airport' not in prompt and 'white round head' in prompt
        assert 'S2W1' in prompt
        valid(report)
        return report

    monkeypatch.setattr(subprocess,'run',lambda *args,**kwargs:None)
    browser=SimpleNamespace(root=tmp_path,d=SimpleNamespace(call=call),
        proxies={w:{'proxy':{'source_start_s':0,'path':w+'.mp4'}} for w in ('w1','w2')})
    assert multiscene_pick.review_sequence(browser,rows,refs,1,{'human':'C1'},{'C1':'white round head'})==report['joins']


def test_page_resume_pins_the_matching_child_ledger(tmp_path,monkeypatch):
    import json
    from omni_story.library import media, opencode_provider, pipeline, state
    from omni_story.library.material_browser import MaterialBrowser

    prior=tmp_path/'prior'
    current=tmp_path/'current'
    relative='autonomous_material/browser/scene_pick_pages/page_002/library_state.json'
    ledger=prior/relative
    ledger.parent.mkdir(parents=True)
    ledger.write_text(json.dumps({'calls':[{'status':'received'}]}))
    config={'resume_from':{'ledger':{}}}
    d=SimpleNamespace(state=SimpleNamespace(output=current,input_lock={'configuration':config},registry_path=None),
        glm=SimpleNamespace(provider='official_vision_mcp_in_opencode',package_root=None,executable=None,exclusions=[],timeout_s=0))
    browser=SimpleNamespace(d=d,root=current/'autonomous_material/browser')
    monkeypatch.setattr(pipeline,'_material_ledgers',lambda _:[(current,{}),(prior,{})])
    monkeypatch.setattr(state,'LibraryState',lambda folder,lock,**kwargs:lock)
    monkeypatch.setattr(opencode_provider,'OpenCodeMCP',lambda *args,**kwargs:None)
    monkeypatch.setattr(pipeline,'_material_call',lambda lock,*args:lock['configuration']['resume_from']['ledger'])

    def validate(reply):
        return reply

    call=MaterialBrowser.isolated_page_call(browser)
    assert call(1,'material_scene_sequence_page_2','same question',validate)=={
        'path':str(ledger),'sha256':media.sha256_file(ledger)}
    ledger.write_text(json.dumps({'calls':[{'status':'submitted'}]}))
    with pytest.raises(state.LibraryStopped,match='page_outcome_unknown'):
        call(1,'material_scene_sequence_page_2','same question',validate)


def test_actual_pair_must_change_partner_and_preserve_the_shared_listener():
    refs=[dict(scene_id=s,roles=[r,'listener'],turns=[dict(role=r,addressee_roles=['listener'])])
          for s,r in [('start','first'),('end','explainer')]]
    hints={'first':'blue','listener':'white','explainer':'armor'}
    clips={s:dict(reference=s,duration=3) for s in ('start','end')}
    scenes=[]
    for ref in refs:
        sid=ref['scene_id']
        roles=ref['roles']
        fact=dict(clip=sid,at_s=1,evidence='turns to the listener')
        scenes.append(dict(reference=sid,ok=True,people=[dict(role=r,actor=hints[r],**fact) for r in roles],
            exchanges=[dict(speaker=hints[roles[0]],listener='white',**fact)]))
    report=dict(scenes=scenes)
    assert multiscene_pick.check_performances(report,refs,clips,hints)==[]
    scenes[0]['people'].append(dict(scenes[0]['people'][0],at_s=1.7))
    assert multiscene_pick.check_performances(report,refs,clips,hints)==[]
    scenes[1]['people'][0]['actor']='blue'
    assert multiscene_pick.check_performances(report,refs,clips,hints)
    scenes[1]['people'][0]['actor']='armor'
    scenes[1]['exchanges'][0]['at_s']=4
    assert multiscene_pick.check_performances(report,refs,clips,hints)
    scenes[1]['exchanges'][0].update(at_s=1,speaker='white',listener='armor')
    assert multiscene_pick.check_performances(report,refs,clips,hints)


def test_picked_seconds_intersect_only_the_retained_window():
    scene=dict(scene_id='chosen',source_start_s=101,source_end_s=113,window_ids=['win'],
               takes={'win':[(103,109)]})
    b=SimpleNamespace(by_id={'win':{'source_sha256':'sha'}},visual={'win':{}},
        audit_continuity=lambda _:dict(media_sha256='audit',result={'groups':[{'id':'group'}]}),
        usable_group_windows=lambda g:['win'],grounded_candidate=lambda _:dict(usable_segments=[
            dict(window_id='win',source_start_s=106,source_end_s=115)]))
    result=multiscene_pick.ground(b,scene)
    assert result['usable_segments']==[dict(window_id='win',source_sha256='sha',source_start_s=106,source_end_s=109)]


def test_partition_cannot_silently_omit_visible_beats():
    from omni_story.library.reference_units import require_picture_beats
    with pytest.raises(ValueError,match='picture_beats'):
        require_picture_beats([{'scene_id':'recollection','roles':['one','other']}])
    require_picture_beats([dict(scene_id='recollection',picture_beats=[
        dict(kind='insert',roles=['one'],action='turns back'),dict(kind='insert',roles=['other'],action='smiles')])])


def test_cutaway_does_not_force_the_previous_partner_into_the_next_conversation():
    ref=dict(scene_id='ending',roles=['one','common','new'],turns=[
        dict(role='new',addressee_roles=['common'])],picture_beats=[
        dict(kind='insert',roles=['one'],action='remembers'),
        dict(kind='exchange',roles=['common','new'],action='explains')])
    units=multiscene_pick.performance_units([ref])
    assert [(r['roles'],r['has_dialogue']) for r in units]==[(['one'],False),(['common','new'],True)]
    rows=[part(units[0]['scene_id'],'memory','w1',0,4),part(units[1]['scene_id'],'road','w2',0,8)]
    plan=multiscene_pick.compose(rows)
    multiscene_pick.group_units(plan,units)
    assert len(plan['scene_sequence'])==1 and plan['scene_sequence'][0]['reference']=='ending'
    assert [p['source'] for p in plan['scene_sequence'][0]['picture_units']]==['memory','road']
    assert candidate_ranges(plan['scene_sequence'][0])=={'w1':[(0,4)],'w2':[(0,8)]}


def test_library_portraits_catch_two_ids_for_the_same_visible_partner(tmp_path):
    import hashlib,json
    from PIL import Image
    area=tmp_path/'library'; (area/'observations').mkdir(parents=True)
    photo=tmp_path/'p.jpg';Image.new('RGB',(10,10),'white').save(photo)
    digest=hashlib.sha256(photo.read_bytes()).hexdigest()
    ids={'lid1':'C1','lid2':'C2','lid3':'C3'}
    observed=dict(window={'entity_exemplars':[dict(entity_id=e,path=str(photo),sha256=digest) for e in ids]})
    (area/'observations'/(hashlib.sha256(b'w').hexdigest()+'.json')).write_text(json.dumps(observed))
    data=dict(folder=str(area/'scene_pick'),scenes={'one':{'characters':list(ids.values())}},
              characters={c:dict(entity_id=e,appearance='appearance') for e,c in ids.items()})
    def call(name,prompt,valid,path,*,image):
        assert image and path.endswith('.jpg')
        with pytest.raises(ValueError,match='无法区分'):valid(dict(lookalikes=[['C1','C2']],casts=[dict(a='C1',b='C2')]))
        return valid(dict(lookalikes=[['C1','C2']],casts=[{'第一组':dict(a='C1',b='C3')},dict(a='C1',b='C2')]))
    b=SimpleNamespace(root=tmp_path,d=SimpleNamespace(call=call))
    looks=multiscene_pick.visual_cast_options(b,data,[dict(scene_id='x',roles=['a','b'])],
        {'x':['one']},{'one':[dict(window='w',actors=list(ids.values()))]})
    assert looks['casts']==[dict(a='C1',b='C3')]


def test_pair_must_act_in_the_story_seconds_and_not_just_appear_on_a_title_card():
    roles=[dict(role_id=r,entity_id=r,identity_confirmed=True) for r in 'abc']
    observed=dict(roles=roles,story_segments=[
        dict(kind='title',local_start_s=0,local_end_s=2),dict(kind='story',local_start_s=2,local_end_s=8)],
        events=[dict(local_start_s=a,local_end_s=b,role_ids=[actor,other],actor_id=actor,recipient_id=other)
                for a,b,actor,other in [(0,2,'a','c'),(2,5,'a','b')]])
    moments=multiscene_pick.observed_moments(observed,{r:r for r in 'abc'},0,8)
    window=dict(actors=list('abc'),moments=moments)
    ref=dict(roles=['lead','partner'],turns=[dict(role='lead',addressee_roles=['partner'])])
    assert multiscene_pick.supports_performance([window],ref,dict(lead='a',partner='b'))
    assert not multiscene_pick.supports_performance([window],ref,dict(lead='a',partner='c'))
    assert moments[0]['at']==[2,5]


def test_insert_reserves_unique_frames_from_an_approved_performance():
    main=part('dialogue','source','w1',100,112)
    old=part('memory','old','w2',8,11)
    end=part('ending','source','w3',120,130)
    clips={'S1W1':dict(reference='dialogue',window_id='w1',source_start_s=100,duration=12)}
    fixes=[dict(reference='memory',entry='face',exit='reaction',transition='recall',
                takes=[dict(clip='S1W1',start_s=4,end_s=6)])]
    rows=multiscene_pick.reserve_insert_segments([main,old,end],fixes,clips)
    assert candidate_ranges(rows[0])=={'w1':[(100,104),(106,112)]}
    assert candidate_ranges(rows[1])=={'w1':[(104,106)]}
    assert candidate_ranges(rows[2])=={'w3':[(120,130)]}
    multiscene_pick.compose(rows)
    assert main['usable_segments'][0]['source_end_s']==112


def test_fresh_insert_menu_preserves_approved_and_rejected_seconds_and_varies_sources():
    from omni_story.library.insert_search import candidates
    def window(wid,roles):
        return dict(window=wid,origin=100,moments=[
            dict(actors=roles,actor=roles[0],at=[0,6],action='turn'),
            dict(actors=roles,actor=roles[-1],at=[6,10],action='look')])
    windows={'first':[window('w1',['a'])],'memory':[window('w2',['b'])],
             'other':[window('w3',['c'])]}
    blocked=[dict(source_sha256='f',source_start_s=101,source_end_s=108)]
    menu=candidates(windows, list(windows), ['a','b'], blocked, {'w1':'f','w2':'g','w3':'h'})
    assert {row['source'] for row in menu.values()}=={'first','memory'}
    for row in menu.values():
        if row['source']=='first':assert row['end_s']<=1 or row['start_s']>=8


def test_insert_search_keeps_shortlist_by_reference(tmp_path,monkeypatch):
    from omni_story.library import multiscene_pick as picks, insert_search
    refs=[dict(scene_id='one',roles=['X']),dict(scene_id='two',roles=['X'],kind='insert')]
    rows=[dict(reference=r['scene_id'],usable_segments=[],window_ids=[]) for r in refs]
    reports=[dict(reference='one',ok=True),dict(reference='two',ok=False)]
    clips={'S1':dict(reference='one')}
    failure=picks.PictureMismatch(['bad'],dict(scenes=reports),clips,tmp_path/'comparison.mp4')
    shortlist={'one':['candidate1'],'two':['candidate2']}
    monkeypatch.setattr(picks,'check_performances',lambda report,*args: [] if report['scenes'][0]['ok'] else ['bad'])
    browser=SimpleNamespace(d=SimpleNamespace(call=lambda *args:dict(action='search')))
    def replace(browser, rows, refs, bad, hints, pool, windows, options, index, failure):
        assert options is shortlist and bad=={'two'}
        return rows
    monkeypatch.setattr(insert_search,'replace',replace)
    monkeypatch.setattr(picks,'review_sequence',lambda *args:[])
    result=picks.repair_inserts(browser,rows,refs,1,failure,{'X':'actor'},{},{},{},shortlist)
    assert result['scene_sequence']==rows


def test_visual_library_repair_ground_and_review_for_composite_scene_id(tmp_path,monkeypatch):
    import re
    from omni_story.library import insert_search, multiscene_pick
    windows={'s':[dict(window='w',origin=10,moments=[
        dict(actors=['a'],actor='a',at=[0,5],action='react')])]}
    old=dict(source_sha256='f',source_start_s=10,source_end_s=12,window_id='w')
    rows=[dict(reference='dialogue',source='s',usable_segments=[old],window_ids=['w']),
          dict(reference='memory.p1',source='s',usable_segments=[],window_ids=['w'])]
    failure=multiscene_pick.PictureMismatch(['wrong'],dict(scenes=[]),{},None)
    def call(stage,prompt,valid,*args,**kwargs):
        assert re.fullmatch(r'[A-Za-z0-9_-]{1,80}',stage)
        return valid(dict(takes=[dict(clip='V1',start_s=2,end_s=4)],entry='turn',exit='reaction',transition='recall'))
    browser=SimpleNamespace(root=tmp_path,d=SimpleNamespace(call=call),by_id={'w':{'source_sha256':'f'}})
    monkeypatch.setattr(insert_search,'visual_menu',lambda *args:None)
    def ground(browser,choice):
        a,b=choice['takes']['w'][0]
        return dict(usable_segments=[dict(old,source_start_s=a,source_end_s=b)])
    monkeypatch.setattr(multiscene_pick,'ground',ground)
    result=insert_search.replace(browser,rows,[dict(scene_id='memory.p1',roles=['role'])],{'memory.p1'},
        {'role':'a'},{'s':{}},windows,{'memory.p1':['s']},2,failure)
    assert result[0] is rows[0] and result[1]['usable_segments'][0]['source_start_s']==12


def test_final_edit_must_play_the_verified_insert_people_in_retained_seconds():
    from omni_story.library.selected_edit import insert_role_guides,missing_insert_roles
    def shot(w,a,b,actor):
        return dict(window_id=w,source_sha256='film',source_start_s=a,source_end_s=b,visible=[actor])
    table={'A':shot('one',20,26,'a'),'B':shot('two',70,74,'b')}
    unit=dict(reference='memory',kind='insert',roles=['first','listener'],
              usable_segments=[shot('one',22,24,'a'),shot('two',70,74,'b')])
    guides=insert_role_guides([dict(reference='room',picture_units=[unit])],table,{'room':['A','B']},
                              {'room':{'first':'a','listener':'b'}})
    cuts=[dict(scene='room',shot='A',at=0,offset=0,speed=1),dict(scene='room',shot='B',at=1,offset=1,speed=1)]
    assert [row[1] for row in missing_insert_roles(guides,cuts,table,90)]==['first']
    cuts[0]['offset']=2.2
    assert not missing_insert_roles(guides,cuts,table,90)


def test_solo_in_flashback_does_not_hide_unverified_focus_in_first_dialogue():
    from omni_story.library.scene_casting import focus_candidates
    def shot(key,who):
        return dict(slot_id=key,window_id=key,source_start_s=0,source_end_s=1,
                    speaking=[],dub_speaker_ids=who,visible=who)
    table=[shot('first',['A','B']),shot('memory',['A']),shot('later',['B'])]
    def unit(key,kind):
        return dict(kind=kind,usable_segments=[dict(window_id=key,source_start_s=0,source_end_s=1)])
    seq=[unit('first','exchange'),dict(picture_units=[unit('memory','insert'),unit('later','exchange')])]
    assert set(focus_candidates(table,['A','B'],seq))=={'first'}
    assert not focus_candidates(table,['A','B'],[])


def test_insert_cannot_play_over_first_onscreen_words_of_incoming_partner():
    from omni_story.library.selected_edit import incoming_voice_guides, missed_incoming_voices
    span=dict(window_id='w',source_sha256='x',source_start_s=2,source_end_s=7)
    seq=[dict(reference='one',roles=['a','b']),dict(reference='two',picture_units=[
        dict(reference='flash',kind='insert',roles=['a','b']),
        dict(reference='explain',kind='exchange',roles=['b','c'],usable_segments=[span])])]
    shots={'S':dict(span,visible=['actorC'])}
    lines=[dict(scene_id='two',role='c',start_s=4,on_screen=True)]
    guides=incoming_voice_guides(seq,shots,{'two':['S']},{'two':{'c':'actorC'}},lines)
    cuts=[dict(at=0,scene='one',offset=0,speed=1,shot='X'),dict(at=4.5,scene='two',shot='S',offset=0,speed=1)]
    assert missed_incoming_voices(guides,cuts)
    cuts[-1]['at']=4
    assert not missed_incoming_voices(guides,cuts)
    lines[0]['on_screen']=False
    assert not incoming_voice_guides(seq,shots,{'two':['S']},{'two':{'c':'actorC'}},lines)


def test_adapted_cast_review_uses_directions_and_actual_frame_numbers_without_source_costumes():
    import json
    from omni_story.library.scene_casting import adapted_cast_context
    roles={'red-haired performer':'rabbit','listener':'white'}
    def shot(t):return dict(window_id='w',source_start_s=t,source_end_s=t+1)
    scene=dict(reference='first',usable_segments=[shot(9)],performance_review=dict(ok=True,people=[
        dict(role='red-haired performer',actor='C1'),dict(role='listener',actor='C2')],
        exchanges=[dict(speaker='C1',listener='C2')]))
    aliases,units=adapted_cast_context([scene],roles,[shot(1),shot(9)])
    assert units[0]['frame_numbers']==[2] and units[0]['exchange_directions']==[[aliases['red-haired performer'],aliases['listener']]]
    assert 'red-haired' not in json.dumps(units)


def test_new_voice_gate_resolves_rewritten_dub_ids_and_merged_lines():
    from omni_story.library.selected_edit import timed_script_visibility
    refs=[dict(scene_id='room',reference_lines=[dict(line_id=7,role='c',on_screen=True),
         dict(line_id=8,role='c',on_screen=False)])]
    timed=[dict(line_id='l6',scene_id='room',role='c',start_s=12.7)]
    script=[dict(line_id='l6',scene_id='room',reference_line_ids=[7,8])]
    assert timed_script_visibility(timed,script,refs)==[dict(timed[0],on_screen=True)]


def test_first_voice_can_get_separate_approval_for_a_short_preverified_focus(monkeypatch):
    from omni_story.library import joint_dialogue as joint, pipeline
    shots=[dict(slot_id='focus',speaking=[],visible=['b','c'],dub_speaker_ids=['c'],focus_ids=['c']),
           dict(slot_id='listener',speaking=[],visible=['b'],dub_speaker_ids=['b'])]
    scene=dict(coverage_shots=shots,role_bindings={'B':{'role_id':'b'},'C':{'role_id':'c'}})
    def ask(state,glm,stage,prompt,valid):
        assert '0.8' in prompt
        with pytest.raises(ValueError):valid(dict(options=[dict(shot_id='listener',reason='wrong actor')]))
        return valid(dict(options=[dict(shot_id='focus',reason='points at the listener')]))
    monkeypatch.setattr(pipeline,'_material_call',ask)
    approved=joint.review_short_intro(scene,dict(role='C',text='look',addressee_roles=['B']),None,None,'stage')
    assert [o['shot_id'] for o in approved]==['focus']


def test_reference_exchange_rechecks_the_real_line_without_prior_speaker(tmp_path):
    import hashlib
    import subprocess
    from omni_story.library.reference_dialogue import review_exchange_speakers

    path = tmp_path/'reference.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=red:s=96x54:r=12:d=2',
                    '-c:v','libx264','-threads','1',str(path)],check=True)
    source = dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    rows = [dict(line_id=i,start_s=start,end_s=end,text=text,kind=('non_speech' if i == 6 else 'speech'),
                 role='leaver',on_screen=visible) for i,start,end,text,visible in
            [(4,.1,.7,'Invite',False),(5,.8,1.2,'Answer',True),(6,1.3,1.8,'Narration',False)]]
    def watch(name, prompt, validate, media, *, image=False):
        if image:
            assert name == 'material_scene_turn_lineframes_1'
            assert 'Narration' not in prompt and 'previous' not in prompt
            confirmed=dict(tag='D1',speaker='stayer',listeners=['leaver'],frames=['D1.1','D1.3'],cue='mouth moves')
            unknown=dict(tag='D2',speaker=None,listeners=[],frames=[],cue='no visible speaker')
            with pytest.raises(ValueError):
                validate(dict(rows=[dict(confirmed,listeners=['stayer']),unknown]))
            with pytest.raises(ValueError):
                validate(dict(rows=[dict(confirmed,frames=['D1.1','D2.1']),unknown]))
            assert validate(dict(rows=[confirmed,dict(unknown,speaker='null')]))['rows'][1]['speaker'] is None
            return validate(dict(rows=[confirmed,unknown]))
        assert name == 'material_scene_turn_utterance_1' and Path(media).stat().st_size > 0
        return validate(dict(speaker='stayer',cue='the opening mouth closes'))
    from pathlib import Path
    result = review_exchange_speakers(source,rows,dict(leaver='long hair',stayer='short hair'),tmp_path,watch)
    assert list(result) == [4,5]
    assert result[4]['speaker'] == 'stayer' and result[5]['speaker'] is None
