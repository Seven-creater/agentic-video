
from omni_story.library.selected_edit import anchor_errors, blind_review


LINES=[dict(line_id='a',start_s=0,end_s=1),dict(line_id='b',start_s=1,end_s=2)]
BEATS=[dict(line='a',shot=None,reserve=False),dict(line='b',shot='expression',reserve=True)]


def test_a_reserved_reaction_lands_on_its_trigger():
    assert not anchor_errors([dict(at=0,shot='wide'),dict(at=.9,shot='expression')],BEATS,LINES,60)
    assert anchor_errors([dict(at=0,shot='expression'),dict(at=.8,shot='wide')],BEATS,LINES,60)
    assert anchor_errors([dict(at=0,shot='expression'),dict(at=.5,shot='wide'),dict(at=1,shot='expression')],BEATS,LINES,60)


def test_blind_observer_only_receives_picture_and_time_windows(tmp_path,monkeypatch):
    import omni_story.library.selected_edit as edit
    picture=tmp_path/'rough_picture.mp4'
    def ff(*args):
        assert args[1]==picture and '-an' in args and 'ass=' not in str(args)
    monkeypatch.setattr(edit,'ff',ff)
    def ask(stage,prompt,validate,media):
        assert 'expression' not in prompt and '机密台词' not in prompt
        return validate(dict(observations=[dict(id=l['line_id'],seen='手指向桌面') for l in LINES]))
    lines=[dict(l,text='机密台词') for l in LINES]
    assert len(blind_review(tmp_path,'test',picture,lines,BEATS,ask)['observations'])==2


def test_shared_and_reserved_assignments_cannot_contradict_each_other():
    import pytest
    from omni_story.library.selected_edit import check_beats
    lines=[dict(l,scene_id='scene',role='A') for l in LINES]
    args=lines,{'hand':{'speaking':[]}}, {'scene':['hand']},{'scene':{'A':{'role_id':'actor'}}}
    beats=[dict(line=l['line_id'],shot='hand',observation='递出物品',reserve=(i==0)) for i,l in enumerate(lines)]
    with pytest.raises(ValueError,match='reserved AND reused'):check_beats(dict(anchors=beats),*args)
    beats[1].update(shot=None,observation='缺少可见证据')
    assert check_beats(dict(anchors=beats),*args)['anchors']==beats


def test_received_timeline_checks_original_input_and_reply(tmp_path):
    import json
    import pytest
    from omni_story.library.selected_edit import received_timeline
    from omni_story.library.state import json_sha, write_json

    original = {'arguments': {'prompt': 'original'}, 'media_sha256': 'video'}
    timeline = {'cuts': [{'shot': 'S1', 'tail': ''}]}
    reply = {'result': {'content': [{'type': 'text', 'text': json.dumps(timeline)}]}}
    directory = tmp_path / 'calls' / 'call1'
    directory.mkdir(parents=True)
    write_json(directory / 'request.json', original)
    write_json(directory / 'response.json', reply)
    write_json(directory / 'protocol_failure.json', {'error': 'blank tail'})
    write_json(tmp_path / 'library_state.json', {'calls': [
        dict(id='call1', name='material_scene_selected_edit_plan', status='received',
             request_sha256=json_sha(original), response_sha256=json_sha(reply))]})
    def retrieve(prompt):
        return received_timeline(tmp_path, 'plan', prompt, 'video', lambda value: value)
    assert retrieve('original') == (timeline, 'call1')
    with pytest.raises(ValueError, match='current input'):
        retrieve('different request')
    write_json(directory / 'response.json', {})
    with pytest.raises(ValueError, match='receipt changed'):
        retrieve('original')


def test_review_may_cover_extra_real_lines_without_losing_requested_evidence():
    import pytest
    from omni_story.library.selected_edit import review_rows
    rows = [dict(line=key, reason='observed') for key in ('a', 'b', 'c')]
    review = {'checks': rows}
    assert review_rows(review, ['c','a'], {'a','b','c'}) == [rows[2], rows[0]]
    assert review['all_checks'] == rows
    for invalid in (rows[:2], rows + [rows[1]], rows + [dict(line='unknown')]):
        with pytest.raises(ValueError):
            review_rows(dict(checks=invalid), ['c','a'], {'a','b','c'})


def test_refinement_can_replace_a_weak_shot_without_unlocking_the_reaction():
    from omni_story.library.selected_edit import refined_anchors
    anchors=[dict(line='a',shot='stall',reserve=False), BEATS[1]]
    checks=[dict(line=key,status='weak') for key in ('a','b')]
    cuts=[dict(at=0,shot='customer'),dict(at=1,shot='expression')]
    revised=refined_anchors(anchors,checks,cuts,LINES,60)
    assert anchor_errors(cuts,anchors,LINES,60) and not anchor_errors(cuts,revised,LINES,60)
    assert anchors[0]['shot']=='stall' and revised[1]==anchors[1]
    assert refined_anchors(anchors,[dict(line='a',status='visible')],cuts,LINES,60)==anchors
    assert anchor_errors([dict(at=0,shot='expression'),dict(at=1,shot='customer')],revised,LINES,60)


def test_voiced_claim_cannot_upgrade_an_unclear_picture():
    from omni_story.library.selected_edit import reconcile_picture_review
    review={'checks':[dict(line='a',status='visible',reason='caption'),dict(line='b',status='audio_only',reason='no prop')]}
    evidence={'a':dict(status='unclear',seen='several heads'), 'b':dict(status='clear',seen='a reaction')}
    final=reconcile_picture_review(review,evidence)
    assert [r['status'] for r in final['checks']]==['weak','audio_only']
    assert final['checks'][0]['reason']=='several heads' and review['checks'][0]['status']=='visible'


def test_out_of_scene_walk_is_excluded_only_where_it_was_identified():
    import pytest
    from omni_story.library.selected_edit import continuity_options
    permissible={'shop':['stay','leave'], 'street':['walk_home']}
    assert continuity_options(permissible, {'leave':'seller carries a box away'}) == {
        'shop':['stay'], 'street':['walk_home']}
    with pytest.raises(ValueError, match='no usable'):
        continuity_options(permissible, {'walk_home':'no source left'})


def test_speaker_handoff_requires_both_the_actual_cut_and_the_incoming_actor():
    from omni_story.library.selected_edit import speaker_picture_errors

    lines = [dict(line, scene_id='stall', role=role) for line, role in zip(LINES, ('seller', 'buyer'))]
    actors = {'stall': {'seller': 'person1', 'buyer': 'person2'}}
    shots = {'seller': {'visible': ['person1']}, 'buyer': {'visible': ['person2']}}
    cuts = [{'at': 0, 'shot': 'seller', 'scene': 'stall'}, {'at': 1, 'shot': 'buyer', 'scene': 'stall'}]
    assert speaker_picture_errors(cuts, shots, actors, lines, 60) == []
    assert speaker_picture_errors(cuts[:1], shots, actors, lines, 60)
    assert speaker_picture_errors([cuts[0], dict(cuts[1], at=.8)], shots, actors, lines, 60)
    assert speaker_picture_errors([cuts[0], dict(cuts[1], shot='seller')], shots, actors, lines, 60)
    reaction = dict(cuts[0], at=1.5)
    assert speaker_picture_errors([*cuts, reaction], shots, actors, lines, 60) == []


def test_locked_object_insert_can_have_an_event_without_a_visible_speaker():
    from omni_story.library.selected_edit import allowed_shots

    shots = {k: dict(visible=[], speaking=[], events=[dict(subject_ids=ids)])
             for k, ids in [('scale', []), ('unbound', ['stranger'])]}
    assert allowed_shots(shots, {'stall': list(shots)}, {'stall': {'buyer': {'role_id': 'buyer'}}}) == {
        'stall': ['scale']}


def test_only_out_of_location_walks_are_blocked_by_dialogue_policy():
    from omni_story.library.selected_edit import dialogue_walkaways

    shots = {'market': {'action': '摊主抱箱在集市中走动'}, 'customer': {'action': '顾客走近摊位'},
             'outside_dialogue': {'action': '角色在市集中走动'}}
    permitted = {'stall': ['market', 'customer'], 'travel': ['outside_dialogue']}
    assert dialogue_walkaways(shots, permitted, [{'scene_id': 'stall', 'role': '摊主'}]) == {
        'market': shots['market']['action']}


def test_the_incoming_actor_in_a_wide_shot_can_still_be_in_the_background():
    from omni_story.library.selected_edit import handoff_focus_edits

    lines = [dict(line, scene_id='stall', role=role) for line, role in zip(LINES, ('seller', 'buyer'))]
    actors = {'stall': {'seller': 'person1', 'buyer': 'person2'}}
    shots = {'wide': dict(visible=['person1','person2'], clear_face=['person1'], speaking=[]),
             'buyer': dict(visible=['person2'], clear_face=['person2'], speaking=[])}
    cuts = [{'at': 0, 'shot': 'wide', 'scene': 'stall'}, {'at': 1, 'shot': 'wide', 'scene': 'stall'}]
    edits = handoff_focus_edits(cuts, shots, actors, lines, {'stall':list(shots)})
    assert [(r['line'],r['alternatives']) for r in edits] == [('b',['buyer'])]
    cuts[1]['shot']='buyer'
    assert not handoff_focus_edits(cuts, shots, actors, lines, {'stall':list(shots)})


def test_a_travel_closeup_is_checked_with_the_preceding_motion():
    from omni_story.library.selected_edit import movement_context_candidates

    def shot(start, end, actor='same', source='source'):
        return dict(source_start_s=start,source_end_s=end,visible=[actor],source_sha256=source)

    shots={'walk':shot(1,3),'close':shot(3.3,4),'detail':shot(4,5),
           'unrelated':shot(4,5,actor='other'),'late_answer':shot(8,9),'other_episode':shot(3.3,4,source='new')}
    assert movement_context_candidates(shots, {'scene': list(shots)}, {'walk':'visible travel'}) == {
        'walk':['close','detail']}


def test_picture_repairs_leave_unreported_cuts_timing_and_scene_untouched():
    import pytest
    from omni_story.library.edit_review import apply_cut_replacements

    original = [{'at': 0, 'scene': 'one', 'shot': 'first', 'offset': 0},
                {'at': 2, 'scene': 'one', 'shot': 'walking', 'offset': 0},
                {'at': 4, 'scene': 'two', 'shot': 'travel', 'offset': 0}]
    changes = {'replacements': {'2': {'shot': 'stopped', 'offset': .2, 'reason': 'same position'}},
               'deferred': {}}
    repaired = apply_cut_replacements(original, {'2'}, changes)
    assert repaired[0] == original[0] and repaired[2] == original[2]
    assert repaired[1] == dict(original[1], shot='stopped', offset=.2, reason='same position')
    with pytest.raises(ValueError, match='picture'):
        apply_cut_replacements(original, {'2'}, {'replacements': {'2': {'at': 2.1, 'reason': 'shift'}}})
    with pytest.raises(ValueError, match='no unrelated'):
        apply_cut_replacements(original, {'2'}, {'replacements': {'3': {'reason': 'unrelated'}}})


def test_automatic_review_covers_rendered_cuts_outside_model_story_anchors(tmp_path):
    import pytest
    from omni_story.library.edit_review import audit_render

    cuts = [{'at': t, 'scene': 'scene', 'shot': k} for t, k in [(0, 'wide'), (12, 'insert'), (38, 'speaker')]]
    checked = []
    def ff(*args):
        args[-1].touch()
    def ask(stage, prompt, validate, media):
        assert media.is_file()
        checked.append(stage)
        if stage == 'review_0':
            assert 'insert' in prompt
            with pytest.raises(ValueError, match='visible cut'):
                validate({'issues': [{'cut': '9', 'kind': 'continuity', 'seen': 'imagined'}]})
            return validate({'issues': [{'cut': 2, 'kind': 'continuity', 'seen': 'position jumps'}]})
        return validate({'issues': []})

    output = audit_render(tmp_path, 'review', tmp_path/'render.mp4', cuts, [], 45*30, 30, [], ask, ff)
    assert checked == ['review_0', 'review_1080']
    assert output[0]['interval'] == dict(id='2', scene='scene', shot='insert', start=360, end=1140)


def test_verified_subtitle_mask_accepts_older_proxy_bound_digest_only():
    from omni_story.library.selected_edit import mask_source_matches

    assert mask_source_matches({'proxy': {'source_sha256': 'locked'}}, 'locked')
    assert not mask_source_matches({'proxy': {'source_sha256': 'locked'}, 'source_sha256': 'other'}, 'locked')
    assert not mask_source_matches({'proxy': {}}, 'locked')


def test_new_hard_cut_claim_requires_actual_change_at_its_claimed_position():
    from omni_story.library.edit_review import verify_claim

    interval = {'start': 2, 'end': 5}
    changed = [False, False, True, True, True, False]
    before = [0, 0, 20, 0, 0, 60]
    after = [0, 0, 20, 0, 1, 55]
    claim = dict(verdict='introduced', evidence_type='hard_cut', reason='hard jump')
    assert verify_claim(dict(claim, frame=5, position='inside'), interval, changed, before, after)['verdict'] == 'wrong_location'
    assert verify_claim(dict(claim, frame=4, position='inside'), interval, changed, before, after)['verdict'] == 'not_observed'
    assert verify_claim(dict(claim, frame=5, position='end'), interval, changed, before, after)['verdict'] == 'preexisting'
    after[4] = 32
    assert verify_claim(dict(claim, frame=4, position='inside'), interval, changed, before, after)['verdict'] == 'introduced'


def test_relative_review_skips_identical_neighbor_context_and_requires_proven_improvement(tmp_path, monkeypatch):
    import omni_story.library.edit_review as review

    cuts = [{'at': t, 'scene': 'one', 'shot': str(t)} for t in (0, 1, 2, 3)]
    first = list(range(120))
    second = first.copy()
    second[:25] = list(range(900,925))
    monkeypatch.setattr(review, 'rendered_frames', lambda p: first if p.name == 'before' else second)
    monkeypatch.setattr(review, 'picture_steps', lambda p: [0]*120)
    monkeypatch.setattr(review, '_paired_page', lambda *args: tmp_path/'pair.mp4')
    original = [{'cut':'1','kind':'framing','seen':'cropped face'}]
    after = [{'cut':'3','kind':'timing','seen':'a reported expression jump'}]
    def ask(stage,prompt,check,media):
        assert 'a reported expression jump' not in prompt
        return check(dict(reviews={'before_1':dict(verdict='resolved',frame=10,position='inside',
            evidence_type='visible_content',before_seen='face cropped',after_seen='full face',reason='face now readable')}))
    evidence = review.adjudicate_repairs(tmp_path,tmp_path/'before',tmp_path/'after',
        cuts,cuts,original,after,120,30,ask,None)
    assert evidence['accepted'] and not evidence['new_defects']
    assert evidence['checks'][1]['verdict']=='unchanged'
    assert evidence['changed_frames']==25
