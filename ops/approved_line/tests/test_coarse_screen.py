import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from PIL import Image
from omni_story.library import material_browser as mb
from omni_story.library.material_browser import MaterialBrowser, validate_coarse, validate_goal_fit


def browser(tmp, starts):
    d = SimpleNamespace(root=Path(tmp))
    b = MaterialBrowser(d)
    b.catalog = [dict(window_id=f'w{i}', source_sha256='s', start_s=str(t), end_s=str(t + 17)) for i, t in enumerate(starts)]
    b.by_id = {r['window_id']: r for r in b.catalog}
    b.scans = [dict(promising=[r['window_id'] for r in b.catalog], possible=[], unlikely=[])]
    b.goal = lambda: {'dialogue_functions': ['boss questions worker']}
    def frames(wid, folder):
        out = []
        for k in range(4):
            p = folder / f'{wid}_{k}.jpg'; Image.new('RGB', (240, 135), 'gray').save(p); out.append((p, k * 4.0))
        return out, {'region': {}}
    b.coarse_frames = frames
    b.watched = []
    b.watch = lambda w: b.watched.append(w)
    b.audited = []
    b.audit_continuity = lambda ids: b.audited.append(list(ids))
    return d, b


class CoarseScreen(unittest.TestCase):
    def test_every_promising_window_is_glanced_and_distinct_regions_are_watched(self):
        # w0,w1 adjacent (same region); w2 far; w3..w9 spread; w5 judged 'no'
        starts = [0, 17, 600, 1200, 1800, 2400, 3000, 3600, 4200, 4800]
        with tempfile.TemporaryDirectory() as tmp:
            d, b = browser(tmp, starts)
            stages = []
            def call(stage, prompt, validator, media, image=False):
                stages.append(stage)
                import json
                labels = json.loads(prompt.rsplit('\n', 1)[1])['labels']
                rows = [dict(label=l, fit='strong', score=3, can_carry='问答', reason='两人对视') for l in labels]
                if stage.endswith('_1'):
                    rows[5]['fit'] = 'no'  # w5
                return validator(dict(rows=rows))
            d.call = call
            result = b.coarse_screen()
            self.assertEqual(stages, ['material_scene_browser_coarse_1', 'material_scene_browser_coarse_2'])
            self.assertEqual(len(result['verdicts']), 10)
            # w0 and w1 touch: one scene segment, watched whole and checked for continuity
            self.assertIn(['w0', 'w1'], [g['window_ids'] for g in result['segments']])
            self.assertEqual(b.audited, [['w0', 'w1']])
            self.assertNotIn('w5', result['shortlist'])          # judged unusable
            self.assertEqual(len(result['segments']), 8)         # eight scenes, not eight windows
            self.assertEqual(sorted(result['shortlist']), ['w0', 'w1', 'w2', 'w3', 'w4', 'w6', 'w7', 'w8', 'w9'])
            self.assertEqual(b.watched, result['shortlist'])

    def test_long_scene_is_watched_around_its_best_window_within_budget(self):
        starts = [i * 17 for i in range(6)] + [1000 + i * 17 for i in range(20)]
        with tempfile.TemporaryDirectory() as tmp:
            d, b = browser(tmp, starts)
            def call(stage, prompt, validator, media, image=False):
                import json
                labels = json.loads(prompt.rsplit('\n', 1)[1])['labels']
                rows = [dict(label=l, fit='possible', score=3, can_carry='问答', reason='r') for l in labels]
                if stage == 'material_scene_browser_coarse_1':
                    rows[4]['fit'] = 'strong'   # w4, inside the first six-window scene
                return validator(dict(rows=rows))
            d.call = call
            result = b.coarse_screen()
            first = next(g for g in result['segments'] if g['window_ids'][0] == 'w0')
            self.assertEqual(first['window_ids'], [f'w{i}' for i in range(6)])
            self.assertEqual(first['watch_ids'], ['w3', 'w4', 'w5'])   # three contiguous around the strongest
            self.assertLessEqual(len(result['shortlist']), 16)
            self.assertTrue(all(len(g['watch_ids']) <= 3 for g in result['segments']))

    def test_neighbours_of_a_good_window_are_glanced_so_the_scene_is_whole(self):
        # Real catalog geometry: 17 s windows starting every 15 s (2 s overlap).
        # w0..w2 one scene but only w1 was promising in the catalog; w3 far away.
        starts = [0, 15, 30, 900]
        with tempfile.TemporaryDirectory() as tmp:
            d, b = browser(tmp, starts)
            b.scans = [dict(promising=['w1', 'w3'], possible=['w0'], unlikely=['w2'])]
            seen = []
            def call(stage, prompt, validator, media, image=False):
                import json
                labels = json.loads(prompt.rsplit('\n', 1)[1])['labels']
                rows = [dict(label=l, fit='strong', score=4, can_carry='问答', reason='r') for l in labels]
                seen.append(len(labels))
                return validator(dict(rows=rows))
            d.call = call
            result = b.coarse_screen()
            self.assertEqual(seen, [2, 2])                        # promising sheet, then w0/w2 neighbours
            self.assertEqual(result['verdicts']['w2']['via'], 'neighbor_hop_1')
            self.assertEqual(result['segments'][0]['window_ids'] if result['segments'][0]['window_ids'][0] == 'w0'
                             else result['segments'][1]['window_ids'], ['w0', 'w1', 'w2'])

    def test_overlapping_windows_continue_but_distant_ones_do_not(self):
        from omni_story.library.material_browser import continues
        w = lambda a: dict(source_sha256='s', start_s=str(a), end_s=str(a + 17))
        self.assertTrue(continues(w(0), w(15)))       # 2 s overlap (catalog stride)
        self.assertTrue(continues(w(0), w(17.8)))     # small gap
        self.assertFalse(continues(w(0), w(30)))      # skips a window
        self.assertFalse(continues(w(15), w(0)))      # direction matters
        self.assertFalse(continues(w(0), dict(w(15), source_sha256='t')))

    def test_ranking_follows_scores_not_catalog_order(self):
        starts = [i * 1000 for i in range(10)]
        with tempfile.TemporaryDirectory() as tmp:
            d, b = browser(tmp, starts)
            def call(stage, prompt, validator, media, image=False):
                import json
                labels = json.loads(prompt.rsplit('\n', 1)[1])['labels']
                rows = [dict(label=l, fit='strong', score=3, can_carry='c', reason='r') for l in labels]
                if stage.endswith('_2'):
                    rows[-1]['score'] = 5                         # w9, last in the catalog
                return validator(dict(rows=rows))
            d.call = call
            self.assertEqual(b.coarse_screen()['segments'][0]['window_ids'], ['w9'])

    def test_sheet_answers_must_cover_every_label(self):
        with self.assertRaises(ValueError) as e:
            validate_coarse(dict(rows=[dict(label='A', fit='strong', score=4, can_carry='x', reason='y')]), ['A', 'B'])
        self.assertIn("缺少['B']", str(e.exception))
        with self.assertRaises(ValueError):
            validate_coarse(dict(rows=[dict(label='A', fit='strong', reason='y')]), ['A'])
        validate_coarse(dict(rows=[dict(label='A', fit='no', reason='标题卡')]), ['A'])

    def test_question_column_must_cite_seconds_and_a_listed_fact(self):
        base = dict(visible_facts=['两人隔桌对视'])
        validate_goal_fit(dict(base, goal_fit=[dict(need='质问', start_s=0, end_s=3, fact_index=0, reason='r')]), 17)
        for bad in (dict(need='质问', start_s=5, end_s=30, fact_index=0), dict(need='质问', start_s=0, end_s=3, fact_index=4)):
            with self.assertRaises(ValueError):
                validate_goal_fit(dict(base, goal_fit=[bad]), 17)
        validate_goal_fit(dict(base, goal_fit=[]), 17)

    def test_transport_guard_allows_coarse_stage(self):
        node = shutil.which('node')
        if node is None:
            self.skipTest('node unavailable')
        source = Path(mb.__file__).with_name('mcp_opencode_guard.mjs').read_text()
        pattern = next(l for l in source.splitlines() if l.startswith('const SCENE_STAGE'))
        code = pattern + "\nif(!SCENE_STAGE.test('material_scene_browser_coarse_12')||SCENE_STAGE.test('material_scene_browser_coarse_x'))process.exit(1);"
        subprocess.run([node, '--input-type=module', '-e', code], check=True)


if __name__ == '__main__':
    unittest.main()
