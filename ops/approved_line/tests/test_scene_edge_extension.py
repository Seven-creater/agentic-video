from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from omni_story.library.material_browser import MaterialBrowser, EDGE_EXTENSION_LIMIT, CONTINUITY_POLICY


def browser(tmp, groups):
    b=MaterialBrowser(SimpleNamespace(root=Path(tmp)))
    # Real library geometry: 17s slices overlapping by 2s; one slice from another source.
    b.catalog=[dict(window_id='p',source_sha256='s',start_s=1663.0,end_s=1680.0),
               dict(window_id='a',source_sha256='s',start_s=1678.0,end_s=1695.0),
               dict(window_id='b',source_sha256='s',start_s=1693.0,end_s=1710.0),
               dict(window_id='x',source_sha256='other',start_s=1693.0,end_s=1710.0)]
    b.by_id={r['window_id']:r for r in b.catalog}
    b.proxies={w:dict(proxy=dict(media_duration_s=17.0)) for w in b.by_id}
    b.scene_groups={g:dict(scene_group_id=g,segments=s) for g,s in groups.items()}
    b.audited_sets={frozenset(['a'])}
    return b


class SceneEdgeExtensionTests(unittest.TestCase):
    def test_group_ending_at_slice_edge_reviews_following_slice(self):
        with tempfile.TemporaryDirectory() as tmp:
            b=browser(tmp,{'g':[dict(window_id='a',start_s=9,end_s=16)]})
            out=b.edge_extensions([dict(scene_group_id='g')])
            self.assertEqual(out,[dict(scene_group_id='g',side='after',added_window_id='b',window_ids=['a','b'],continuity_policy=CONTINUITY_POLICY)])

    def test_group_inside_slice_is_not_extended(self):
        with tempfile.TemporaryDirectory() as tmp:
            b=browser(tmp,{'g':[dict(window_id='a',start_s=3,end_s=12)]})
            self.assertEqual(b.edge_extensions([dict(scene_group_id='g')]),[])

    def test_group_starting_at_slice_edge_reviews_preceding_slice(self):
        with tempfile.TemporaryDirectory() as tmp:
            b=browser(tmp,{'g':[dict(window_id='a',start_s=0,end_s=8)]})
            out=b.edge_extensions([dict(scene_group_id='g')])
            self.assertEqual([(e['side'],e['window_ids']) for e in out],[('before',['p','a'])])

    def test_other_source_is_never_a_continuation(self):
        with tempfile.TemporaryDirectory() as tmp:
            b=browser(tmp,{'g':[dict(window_id='b',start_s=5,end_s=17)]})
            self.assertEqual(b.edge_extensions([dict(scene_group_id='g')]),[])

    def test_recorded_extension_replays_without_budget_and_new_ones_respect_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            b=browser(tmp,{'g':[dict(window_id='a',start_s=9,end_s=16)]})
            filler=[dict(window_ids=[str(i)],continuity_policy=CONTINUITY_POLICY) for i in range(EDGE_EXTENSION_LIMIT-1)]
            b.comparison_progress['edge_extensions']=filler+[dict(window_ids=['a','b'],continuity_policy=CONTINUITY_POLICY)]
            out=b.edge_extensions([dict(scene_group_id='g')])
            self.assertEqual([(e['window_ids'],e.get('replay')) for e in out],[(['a','b'],True)])
            b.comparison_progress['edge_extensions']=[dict(window_ids=[str(i)],continuity_policy=CONTINUITY_POLICY) for i in range(EDGE_EXTENSION_LIMIT)]
            self.assertEqual(b.edge_extensions([dict(scene_group_id='g')]),[])
            b.audited_sets.add(frozenset(['a','b']))
            b.comparison_progress['edge_extensions']=[dict(window_ids=['a','b'],continuity_policy=CONTINUITY_POLICY)]
            self.assertEqual(b.edge_extensions([dict(scene_group_id='g')]),[])

    def test_resume_replays_recorded_extension_once_without_new_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            b=browser(tmp,{'g':[dict(window_id='a',start_s=9,end_s=16)]})
            d=b.d
            b.scans=[];b.catalog_pages=[];b.goal=lambda:{}
            b.visual={w:dict(subtitle_blind=True) for w in ('p','a','b','x')}
            recorded=dict(scene_group_id='g',side='after',added_window_id='b',window_ids=['a','b'],continuity_policy=CONTINUITY_POLICY)
            b.comparison_progress=dict(rounds=[dict(window_ids=['p'],completed=True)]*2,active=True,edge_extensions=[recorded])
            audited=[]
            def audit(ids):
                audited.append(ids);b.audited_sets.add(frozenset(ids))
                b.scene_groups['joined']=dict(scene_group_id='joined',segments=[
                    dict(window_id='a',start_s=9,end_s=17),dict(window_id='b',start_s=0,end_s=8)])
            b.watch=lambda w:b.visual[w];b.audit_continuity=audit
            b.grounded_candidate=lambda s:s;b.validate_finish=lambda v:v
            choices=iter(['g','joined'])
            d.call=lambda stage,prompt,validator:validator(dict(action='finish',
                candidates=[dict(window_ids=['a'],scene_group_id=next(choices))],only_one_candidate_reason='x',selection_reason='y'))
            result=b.run()
            self.assertEqual(audited,[['a','b']])
            self.assertEqual(result['candidates'][0]['scene_group_id'],'joined')
            self.assertEqual(b.comparison_progress['edge_extensions'],[recorded])

    def test_only_first_ranked_candidate_spends_extension_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            b=browser(tmp,{'top':[dict(window_id='b',start_s=3,end_s=12)],'g':[dict(window_id='a',start_s=9,end_s=16)]})
            self.assertEqual(b.edge_extensions([dict(scene_group_id='top'),dict(scene_group_id='g')]),[])

    def test_extensions_under_an_older_grouping_definition_do_not_spend_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            b=browser(tmp,{'g':[dict(window_id='a',start_s=9,end_s=16)]})
            b.comparison_progress['edge_extensions']=[dict(window_ids=['a','b'])]+[dict(window_ids=[str(i)]) for i in range(EDGE_EXTENSION_LIMIT)]
            self.assertEqual([e['window_ids'] for e in b.edge_extensions([dict(scene_group_id='g')])],[['a','b']])

    def test_finish_on_edge_group_reviews_neighbor_then_decides_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            b=browser(tmp,{'g':[dict(window_id='a',start_s=9,end_s=16)]})
            d=b.d
            b.scans=[];b.catalog_pages=[];b.goal=lambda:{}
            b.visual={w:dict(subtitle_blind=True) for w in ('p','a','x')}
            b.comparison_progress=dict(rounds=[dict(window_ids=['p'],completed=True)]*2,active=True)
            watched,audited=[],[]
            def watch(w):watched.append(w);b.visual[w]=dict(subtitle_blind=True);return b.visual[w]
            def audit(ids):
                audited.append(ids);b.audited_sets.add(frozenset(ids))
                b.scene_groups['joined']=dict(scene_group_id='joined',segments=[
                    dict(window_id='a',start_s=9,end_s=17),dict(window_id='b',start_s=0,end_s=8)])
            b.watch=watch;b.audit_continuity=audit
            b.grounded_candidate=lambda s:s;b.validate_finish=lambda v:v
            choices=iter(['g','joined'])
            def call(stage,prompt,validator):
                return validator(dict(action='finish',candidates=[dict(window_ids=['a'],scene_group_id=next(choices))],
                    only_one_candidate_reason='others unsuitable',selection_reason='observed'))
            d.call=call
            result=b.run()
            self.assertEqual(watched,['b'])
            self.assertEqual(audited,[['a','b']])
            self.assertEqual(result['candidates'][0]['scene_group_id'],'joined')
            self.assertEqual(len(MaterialBrowser(d).comparison_progress['edge_extensions']),1)


if __name__=='__main__':unittest.main()
