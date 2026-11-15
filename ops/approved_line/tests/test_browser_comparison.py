from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from omni_story.library.material_browser import MaterialBrowser

class BrowserComparisonTests(unittest.TestCase):
    def test_single_candidate_triggers_only_two_new_watch_rounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=SimpleNamespace(root=Path(tmp))
            b=MaterialBrowser(d)
            b.catalog=[dict(window_id=w,source_sha256='source') for w in ['a','b','c']]
            b.by_id={r['window_id']:r for r in b.catalog}
            b.scans=[];b.catalog_pages=[];b.visual={'a':dict(subtitle_blind=True)}
            b.goal=lambda:{}
            def watch(w):b.visual[w]=dict(subtitle_blind=True);return b.visual[w]
            b.watch=watch
            b.audit_continuity=lambda ids:None
            b.grounded_candidate=lambda s:s
            b.validate_finish=lambda v:v
            calls=[]
            def call(stage,prompt,validator):
                calls.append(prompt)
                if prompt.startswith('当前仅一组候选'):
                    fresh=next(w for w in ['b','c'] if w not in b.visual)
                    return validator(dict(action='watch',window_ids=[fresh]))
                return validator(dict(action='finish',candidates=[dict(window_ids=['a'],scene_group_id='scene')],only_one_candidate_reason='new scenes unsuitable',selection_reason='observed comparison'))
            d.call=call
            result=b.run()
            self.assertEqual(result['comparison_audit']['supplemented_rounds'],2)
            self.assertEqual(len(b.visual),3)
            restored=MaterialBrowser(d)
            self.assertEqual(len(restored.comparison_progress['rounds']),2)
            self.assertTrue(all(r['completed'] for r in restored.comparison_progress['rounds']))

    def test_new_resume_directory_inherits_budget_from_pinned_ancestry(self):
        import json
        from unittest.mock import patch
        from omni_story.library.state import json_sha
        with tempfile.TemporaryDirectory() as tmp:
            parent=Path(tmp)/'parent'
            old=parent/'autonomous_material/browser';old.mkdir(parents=True)
            progress=dict(goal_sha256=json_sha({}),active=True,rounds=[dict(window_ids=['w'],completed=True)]*2)
            (old/'comparison_progress.json').write_text(json.dumps(progress))
            state=SimpleNamespace(output=Path(tmp)/'new')
            d=SimpleNamespace(root=state.output/'autonomous_material',state=state)
            b=MaterialBrowser(d);b.goal=lambda:{}
            with patch('omni_story.library.pipeline._material_ledgers',return_value=iter([(parent,{})])):
                b.restore_comparison_progress()
            self.assertEqual(len(b.comparison_progress['rounds']),2)

    def comparison_fixture(self):
        b=MaterialBrowser.__new__(MaterialBrowser)
        b.visual={w:dict(subtitle_blind=True) for w in ('a','b')}
        b.by_id={w:dict(source_sha256='source') for w in ('a','b')}
        b.scene_groups={g:dict(segments=[dict(window_id=w)]) for g,w in (('one','a'),('two','b'))}
        b.audited_sets={frozenset(['a']),frozenset(['b'])}
        def candidate(group,windows):
            return dict(scene_group_id=group,window_ids=windows,visual_alone='visible interaction',
                editing_can_add='cuts',dubbing_can_add='dialogue',limitations='uncertain details',
                continuity_evidence='independent observation')
        value=dict(candidates=[candidate('one',['a']),candidate('two',['b'])],
            selection_reason='Compare two separate observed scenes')
        return b,value

    def test_separate_observed_scene_groups_can_be_ranked_as_multiple_candidates(self):
        b,value=self.comparison_fixture()
        self.assertEqual(b.validate_finish(value),value)

    def test_one_candidate_cannot_merge_different_observed_groups(self):
        b,value=self.comparison_fixture();value['candidates'][0]['window_ids']=['a','b']
        with self.assertRaisesRegex(ValueError,r'candidates\[0\].*does not equal one independently observed scene group'):
            b.validate_finish(value)
    def test_unwatched_and_readable_windows_are_named(self):
        b,value=self.comparison_fixture()
        b.visual['b']=dict(subtitle_blind=False)
        value['candidates'][1]['window_ids']=['b']
        value['candidates'].append(dict(value['candidates'][0],window_ids=['zz'],scene_group_id=None))
        with self.assertRaisesRegex(ValueError,r"candidates\[1\].*readable dialogue text \['b'\]"):
            b.validate_finish(value)

    def test_wrong_group_error_lists_exact_groups_to_use(self):
        b,value=self.comparison_fixture();value['candidates'][0]['window_ids']=['a','b']
        with self.assertRaises(ValueError) as ctx:b.validate_finish(value)
        self.assertIn('"scene_group_id":"one"',ctx.exception.args[0].replace(' ',''))
        self.assertIn('"scene_group_id":"two"',ctx.exception.args[0].replace(' ',''))

    def test_missing_field_names_candidate_and_field(self):
        b,value=self.comparison_fixture();value['candidates'][1]['limitations']=''
        with self.assertRaisesRegex(ValueError,r'candidates\[1\] needs a non-empty string field limitations'):
            b.validate_finish(value)


if __name__=='__main__':unittest.main()

