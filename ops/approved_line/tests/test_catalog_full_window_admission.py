import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from omni_story.library.material_browser import MaterialBrowser

EXCLUDED = (20.0, 22.0)  # e.g. a title card inside the source


def fake_discovery(tmp):
    windows = {'ok': (0.0, 17.0), 'crossing': (15.0, 32.0)}
    # the crossing window still has one surviving record that avoids the excluded range
    records = {'ok': [dict(observation_id='o_ok', source_sha256='s', source_start_s=1.0, source_end_s=5.0)],
               'crossing': [dict(observation_id='o_x', source_sha256='s', source_start_s=24.0, source_end_s=30.0)]}
    def allowed(r):
        return not (r['source_start_s'] < EXCLUDED[1] and r['source_end_s'] > EXCLUDED[0])
    store = {'o_ok': dict(window={}), 'o_x': dict(window={})}
    route = SimpleNamespace(store=SimpleNamespace(get=store.get), _allowed_record=allowed, config={}, scenes=[])
    load = lambda wid: dict(source_sha256='s', source_start_s=windows[wid][0], source_end_s=windows[wid][1])
    return SimpleNamespace(root=Path(tmp), records=records, route=route, load_window=load)


class CatalogFullWindowAdmission(unittest.TestCase):
    def test_window_crossing_excluded_range_is_not_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = MaterialBrowser(fake_discovery(tmp))
            self.assertEqual([r['window_id'] for r in b.catalog_rows()], ['ok'])
            self.assertFalse(b.window_allowed('crossing'))
            self.assertTrue(b.window_allowed('ok'))


if __name__ == '__main__':
    unittest.main()
