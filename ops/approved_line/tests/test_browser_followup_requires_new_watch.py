from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from omni_story.library.material_browser import MaterialBrowser


def browser(tmp):
    d = SimpleNamespace(root=Path(tmp))
    b = MaterialBrowser(d)
    b.catalog = [dict(window_id=w, source_sha256='source') for w in ['a', 'b', 'c']]
    b.by_id = {r['window_id']: r for r in b.catalog}
    b.scans = []; b.catalog_pages = []
    b.visual = {'a': dict(subtitle_blind=True), 'b': dict(subtitle_blind=True)}
    b.goal = lambda: {}
    def watch(w):
        b.visual[w] = dict(subtitle_blind=True); return b.visual[w]
    b.watch = watch
    b.audit_continuity = lambda ids: None
    b.grounded_candidate = lambda s: s
    b.validate_finish = lambda v: v
    b.edge_extensions = lambda candidates: []
    return d, b


OLD = dict(action='finish', candidates=[dict(window_ids=['a'], scene_group_id='g1'), dict(window_ids=['b'], scene_group_id='g2')],
           selection_reason='compared')


class FollowupRequiresNewWatch(unittest.TestCase):
    def test_followup_finish_without_new_watch_is_rejected_with_the_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            d, b = browser(tmp)
            errors = []
            def call(stage, prompt, validator):
                # resume: a received reply failing this new rule must get a bounded correction
                self.assertTrue(getattr(validator, 'revalidated_protocol_repair', False))
                try:
                    return validator(dict(OLD))
                except ValueError as exc:
                    errors.append(str(exc))
                    return validator(dict(action='watch', window_ids=['c']))
            d.call = call
            b.run(feedback=dict(reason='需要5位同场人物'))
            self.assertEqual(len(errors), 1)
            self.assertIn('需要5位同场人物', errors[0])
            self.assertIn('c', b.visual)

    def test_second_refusal_to_look_further_is_a_material_gap_stop(self):
        from omni_story.library.state import LibraryStopped
        with tempfile.TemporaryDirectory() as tmp:
            d, b = browser(tmp)
            artifacts = {}
            d.state = SimpleNamespace(set_artifact=lambda k, v: artifacts.__setitem__(k, v), _reload=lambda: None,
                                      data=dict(calls=[]), input_lock=dict(configuration={}), output=tmp)
            def call(stage, prompt, validator):
                for _ in range(3):  # original reply plus protocol repairs, all refusing to watch
                    try:
                        return validator(dict(OLD))
                    except ValueError:
                        continue
            d.call = call
            with self.assertRaises(LibraryStopped) as e:
                b.run(feedback=dict(reason='需要5位同场人物'))
            self.assertIn('material_gap', str(e.exception))
            self.assertEqual(artifacts['browser_followup_material_gap']['refusals'], 2)

    def test_first_run_without_followup_may_finish_on_watched_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            d, b = browser(tmp)
            d.call = lambda stage, prompt, validator: validator(dict(OLD))
            self.assertEqual(b.run()['candidates'][0]['scene_group_id'], 'g1')


if __name__ == '__main__':
    unittest.main()
