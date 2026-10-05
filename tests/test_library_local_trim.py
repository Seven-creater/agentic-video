from copy import deepcopy
import pytest
from omni_story.library import local_trim


PARENT = {'segment_id':'s1','slot_id':'slot','source_id':'movie','window_id':'w',
          'source_in_s':10,'source_out_s':16,'speed':1,'freeze_tail_s':0}


def proposal():
    return {'parent_segment_id':'s1','kept_slices':[
        {'source_in_s':10,'source_out_s':12,'speed':1,'freeze_tail_s':0,'visible_information':'synthetic state',
         'essential_interval':{'source_start_s':10,'source_end_s':12,'min_readable_s':2},'reason':'required state'},
        {'source_in_s':14,'source_out_s':16,'speed':2,'freeze_tail_s':0,'visible_information':'synthetic change',
         'essential_interval':{'source_start_s':14,'source_end_s':16,'min_readable_s':1},'reason':'distinct change'}],
        'deletion_checks':[{'source_start_s':10,'source_end_s':12,'decision':'keep','information_lost':'state','reason':'needed'},
            {'source_start_s':12,'source_end_s':14,'decision':'omit','information_lost':'repeated state','reason':'redundant'},
            {'source_start_s':14,'source_end_s':16,'decision':'keep','information_lost':'change','reason':'needed'}],
        'obligation_status':'preserved','limitations':[],'uncertainties':[]}


def test_noncontiguous_model_cuts_deletion_partition_and_exposure():
    value = proposal(); before = deepcopy(value)
    assert local_trim.validate(value,PARENT,32) is value
    assert value == before
    rows = [{'parent_segment':PARENT,'parent_segment_id':'s1','proposal':value}]
    plan = {'segments':[{**PARENT,**{k:c[k] for k in ('source_in_s','source_out_s','speed','freeze_tail_s')}}
                        for c in value['kept_slices']]}
    assert local_trim.enforce_final(plan,rows) is plan
    assert local_trim.unchanged(rows) is False


@pytest.mark.parametrize('mutate,reason',[
    (lambda v:v['kept_slices'][0].update(source_in_s=9),'cut_range'),
    (lambda v:v['kept_slices'][1]['essential_interval'].update(min_readable_s=2),'insufficient_exposure'),
    (lambda v:v['deletion_checks'][1].update(source_start_s=13),'partition_gap'),
    (lambda v:v['deletion_checks'].pop(),'partition_tail'),
    (lambda v:v['deletion_checks'][1].update(decision='keep'),'deletion_cut_conflict'),
    (lambda v:v.pop('uncertainties'),'uncertainties_required'),
    (lambda v:v.update(kept_slices=[]),'preserved_without_picture')])
def test_invalid_local_decision_remains_rejected_without_defaults(mutate,reason):
    value = proposal(); mutate(value); original=deepcopy(value)
    with pytest.raises(ValueError,match=reason): local_trim.validate(value,PARENT,32)
    assert value == original


def test_final_same_seconds_in_wrong_movie_are_rejected():
    rows=[{'parent_segment':PARENT,'proposal':proposal()}]
    c=proposal()['kept_slices'][0]
    plan={'segments':[{**PARENT,**c,'source_id':'another_movie'}]}
    with pytest.raises(ValueError,match='final_slice_not_selected'):local_trim.enforce_final(plan,rows)


def test_whole_parent_no_change_is_not_progress():
    p=proposal(); p['kept_slices']=[{k:PARENT[k] for k in ('source_in_s','source_out_s','speed','freeze_tail_s')}]
    assert local_trim.unchanged([{'parent_segment':PARENT,'proposal':p}])


def test_final_cannot_repeat_one_local_cut_under_two_ids():
    rows=[{'parent_segment':PARENT,'proposal':proposal()}]
    s={**PARENT,**proposal()['kept_slices'][0]}
    with pytest.raises(ValueError,match='final_slice_not_selected'):
        local_trim.enforce_final({'segments':[s,{**s,'segment_id':'alias'}]},rows)


def test_bad_partition_object_is_a_bounded_format_error():
    value=proposal(); value['deletion_checks'].append(None)
    with pytest.raises(ValueError,match='deletion_interval_numbers'):
        local_trim.validate(value,PARENT,32)


def test_only_related_model_windows_enter_assembly_without_rewriting_archive():
    context={k:{} for k in ('reference','editing_reference','catalog','render_capabilities')}
    context.update(reference_duration_s=22,reference_audio_stream_index=0,audio_semantics_policy='unknown',
        known_exhausted_slice_inputs=[],watched_windows=[{'window_id':'w'},{'window_id':'other'}],
        actual_feedback={'large':'old failed hypotheses'},source_cut_navigation={'large':'all original candidates'})
    before=deepcopy(context)
    reduced=local_trim.compact_context(context,[{'parent_segment':PARENT,'proposal':proposal()}])
    assert reduced['watched_windows']==[{'window_id':'w'}]
    assert 'actual_feedback' not in reduced and 'source_cut_navigation' not in reduced
    assert context==before
