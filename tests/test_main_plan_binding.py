"""The explicit author edit cannot silently change timings, dialogue or evidence."""
from copy import deepcopy

import pytest

from omni_story.library.main_plan_binding import apply_binding_split
from omni_story.library.state import LibraryStopped


def test_main_authoring_cannot_relabel_the_original_rejected_model_result(tmp_path):
    from omni_story.library.main_plan_binding import unparsed_received_plan
    reply=dict(result=dict(content=[dict(type='text',text='{"cast":[]}')]))
    assert unparsed_received_plan(tmp_path,reply)=={'cast':[]}
    assert not (tmp_path/'parsed.json').exists()
    (tmp_path/'parsed.json').write_text('{"cast":[]}')
    with pytest.raises(LibraryStopped,match='original_rejection_must_remain'):
        unparsed_received_plan(tmp_path,reply)


def test_explicit_identity_split_preserves_all_other_plan_content_and_source():
    source=dict(segments=[dict(source_in_s=10,source_out_s=15)],audio_draft=[dict(text='Unchanged words')],
        cast=[dict(cast_id='hero',entity_id='older_entity',voice='Ethan',bindings=[dict(window_id='old',role_id='A'),dict(window_id='new',role_id='A')])])
    baseline=deepcopy(source)
    windows=[dict(window_id='new',status='watched',observation=dict(roles=[dict(role_id='A',entity_id='separate_received_entity',identity_confirmed=True)]))]
    patch=dict(operation='split_one_unsupported_cast_binding',cast_id='hero',new_cast_id='musician',binding=dict(window_id='new',role_id='A'))
    repaired=apply_binding_split(source,windows,patch)
    assert source==baseline and repaired['segments']==source['segments'] and repaired['audio_draft']==source['audio_draft']
    assert repaired['cast'][0]['bindings']==[dict(window_id='old',role_id='A')]
    assert repaired['cast'][1]==dict(cast_id='musician',entity_id='separate_received_entity',voice='Ethan',bindings=[patch['binding']])
    windows[0]['observation']['roles'][0]['identity_confirmed']=False
    with pytest.raises(LibraryStopped,match='unsupported_identity_split'):
        apply_binding_split(source,windows,patch)
    windows[0]['observation']['roles'][0].update(identity_confirmed=True,entity_id='older_entity')
    with pytest.raises(LibraryStopped,match='unsupported_identity_split'):
        apply_binding_split(source,windows,patch)


def test_explicit_role_metadata_removal_keeps_clip_and_dialogue_and_rejects_supported_role():
    source=dict(cast=[dict(cast_id='hero',entity_id='old',voice='Ethan',bindings=[dict(window_id='new',role_id='A')])],
        segments=[dict(segment_id='s',window_id='new',source_in_s=11,source_out_s=12,role_ids=['A','B'],reason='old')],
        audio_draft=[dict(text='Unchanged')])
    windows=[dict(window_id='new',status='watched',source_start_s=10,observation=dict(roles=[dict(role_id='A',entity_id='new',identity_confirmed=True)],
        events=[dict(local_start_s=0,local_end_s=3,role_ids=['B'])]))]
    patch=dict(operation='split_one_unsupported_cast_binding',cast_id='hero',new_cast_id='solo',binding=dict(window_id='new',role_id='A'),
        segment_role_removals=[dict(segment_id='s',role_id='A',reason='Visible reaction')])
    result=apply_binding_split(source,windows,patch)
    assert result['segments'][0]['role_ids']==['B'] and result['segments'][0]['source_in_s']==11
    assert result['audio_draft']==source['audio_draft'] and source['segments'][0]['role_ids']==['A','B']
    patch['segment_role_removals'][0]['role_id']='B'
    with pytest.raises(LibraryStopped,match='role_removal_not_supported'):
        apply_binding_split(source,windows,patch)
