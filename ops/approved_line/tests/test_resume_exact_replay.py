import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from omni_story.library import resource_guard
from omni_story.library.material_browser import MaterialBrowser


class Stop(Exception):
    pass


class ResumeExactReplay(unittest.TestCase):
    def test_replay_only_stops_before_any_new_paid_request(self):
        with mock.patch.dict(os.environ, {'OMNI_REPLAY_ONLY': '1'}):
            with self.assertRaises(resource_guard.ResourceLimit) as e:
                resource_guard.before_request()
        self.assertIn('replay_only_new_paid_request', str(e.exception))
        with mock.patch.dict(os.environ, {'OMNI_REPLAY_ONLY': ''}):
            resource_guard.before_request()

    def test_resume_first_decision_matches_a_cold_start(self):
        """Ancestor watches are not preloaded: the first prompt equals the recorded first prompt."""
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp) / 'parent'
            ledger = dict(calls=[dict(name='material_scene_browser_blind_vl_aaaaaaaaaaaaaaaa', status='received')])
            state = SimpleNamespace(output=Path(tmp) / 'new')
            d = SimpleNamespace(root=state.output / 'autonomous_material', state=state)
            b = MaterialBrowser(d)
            def scan():
                b.catalog = [dict(window_id='vl_aaaaaaaaaaaaaaaa', source_sha256='s')]
                b.by_id = {r['window_id']: r for r in b.catalog}
                b.scans = []
            b.scan = scan
            b.goal = lambda: {}
            watched = []
            b.watch = lambda w: watched.append(w)
            prompts = []
            def call(stage, prompt, validator):
                prompts.append(prompt); raise Stop
            d.call = call
            with mock.patch('omni_story.library.pipeline._material_ledgers', return_value=iter([(parent, ledger)])):
                with self.assertRaises(Stop):
                    b.run()
            self.assertEqual(watched, [])
            self.assertTrue(prompts[0].startswith('当前watched为空'))


if __name__ == '__main__':
    unittest.main()
