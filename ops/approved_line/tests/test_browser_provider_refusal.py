import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from omni_story.library.material_browser import MaterialBrowser
from omni_story.library.state import LibraryStopped


def browser(tmp):
    state = SimpleNamespace(output=Path(tmp) / 'task', set_artifact=lambda k, v: None)
    d = SimpleNamespace(root=Path(tmp), state=state)
    b = MaterialBrowser(d)
    b.catalog = [dict(window_id=w, source_sha256='s', start_s=str(i * 15), end_s=str(i * 15 + 17)) for i, w in enumerate(['a', 'b'])]
    b.by_id = {r['window_id']: r for r in b.catalog}
    return d, b


class ProviderRefusal(unittest.TestCase):
    def test_refused_window_is_skipped_and_kept_out_of_continuity(self):
        with tempfile.TemporaryDirectory() as tmp:
            d, b = browser(tmp)
            def _watch(w):
                if w == 'b':
                    raise LibraryStopped('provider_request_rejected:glm_040_material_scene_browser_blind_b:400:1210')
                b.visual[w] = dict(window_id=w); return b.visual[w]
            b._watch = _watch
            with mock.patch('omni_story.library.pipeline._material_ledgers', return_value=[]):
                self.assertIn('provider_refused', b.watch('b'))
                b.watch('a')
            self.assertIn('b', b.unwatchable)
            self.assertNotIn('b', b.visual)
            self.assertIsNone(MaterialBrowser.audit_continuity(b, ['b']))   # nothing watchable left to audit

    def test_other_failures_still_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            d, b = browser(tmp)
            b._watch = lambda w: (_ for _ in ()).throw(LibraryStopped('opencode_vision_job_failed_no_retry:glm_1'))
            with mock.patch('omni_story.library.pipeline._material_ledgers', return_value=[]):
                with self.assertRaises(LibraryStopped):
                    b.watch('a')

    def test_resume_does_not_resend_a_recorded_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            d, b = browser(tmp)
            parent = Path(tmp) / 'parent'
            ev = parent / 'calls/glm_040_x/agent'; ev.mkdir(parents=True)
            (ev / 'provider_rejection.json').write_text(json.dumps(dict(http_status=400, provider_code='1210', category='provider_request_rejected')))
            ledger = dict(calls=[dict(id='glm_040_x', name='material_scene_browser_blind_b', status='failed_known')])
            b._watch = lambda w: self.fail('a recorded refusal must not be sent again')
            with mock.patch('omni_story.library.pipeline._material_ledgers', return_value=[(parent, ledger)]):
                self.assertEqual(b.watch('b')['provider_refused']['provider_code'], '1210')


if __name__ == '__main__':
    unittest.main()
