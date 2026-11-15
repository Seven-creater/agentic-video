from copy import deepcopy
import pytest
from omni_story.library.material_browser import normalize_overview_selection
from omni_story.library.contracts import normalized_view

A='vl_0123456789abcdef';B='vl_abcdef0123456789';UNKNOWN='vl_0000000000000000'

@pytest.mark.parametrize('items',[[A,B],[A+' (actual picture explanation)',B+'（另一项说明）'],
 [{'id':A,'reason':'observed detail'},{'window_id':B,'time':'0–2s'}],
 [{'id':A,'window_id':A},B],[]])
def test_representation_does_not_change_selection_order_or_raw_reply(items):
 raw=dict(worth_watching=items,notes='Only proposals; actual video still needs watching')
 before=deepcopy(raw)
 result=normalized_view(raw,lambda v:normalize_overview_selection(v,[A,B]))
 assert result['worth_watching']==([A,B] if len(items)==2 else [])
 assert raw==before
 assert result['notes']==raw['notes']
 twice=normalized_view(result,lambda v:normalize_overview_selection(v,[A,B]))
 assert twice==result

@pytest.mark.parametrize('item',[UNKNOWN,UNKNOWN+' (explanation)',{'id':UNKNOWN},
 {'id':A,'window_id':B},A+' or '+B,'maybe '+A,{'reason':A},123,A[:8]])
def test_unknown_ambiguous_and_partial_ids_are_not_repaired_by_guessing(item):
 with pytest.raises(ValueError,match='only actual overview IDs'):
  normalize_overview_selection(dict(worth_watching=[item],notes='uncertain'),[A,B])


def test_short_visual_labels_map_exactly_to_source_ids_and_remain_idempotent():
 aliases={'R1':A,'R2':B}
 raw=dict(worth_watching=[{'id':'R2','window_id':B,'reason':'observed'},'R1 (image detail)'],notes='proposal only')
 before=deepcopy(raw);result=normalized_view(raw,lambda v:normalize_overview_selection(v,aliases))
 assert result['worth_watching']==[B,A] and raw==before
 assert normalized_view(result,lambda v:normalize_overview_selection(v,aliases))==result
 with pytest.raises(ValueError):normalize_overview_selection(dict(worth_watching=['R3'],notes=''),aliases)
 with pytest.raises(ValueError):normalize_overview_selection(dict(worth_watching=[{'id':'R1','window_id':B}],notes=''),aliases)


def test_real_received_long_id_ocr_errors_are_never_guessed():
 from pathlib import Path
 import json
 fixture=json.loads(Path(__file__).with_name('overview_received_fixtures.json').read_text())
 for case in fixture['cases']:
  raw=deepcopy(case['received']);before=deepcopy(raw)
  with pytest.raises(ValueError,match='only actual overview IDs'):
   normalized_view(raw,lambda v:normalize_overview_selection(v,fixture['known_ids']))
  assert raw==before
