import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from omni_story.library.joint_dialogue import anchor_view
from omni_story.library import scene_casting


def shot(slot, visible, start, end, dub=None, speaking=()):
    return dict(slot_id=slot, window_id='w', visible=list(visible), speaking=list(speaking),
                dub_speaker_ids=list(dub if dub is not None else visible), source_start_s=start, source_end_s=end)


class AnchorView(unittest.TestCase):
    def test_shared_dub_shot_needs_a_checked_focus(self):
        s = shot('s1', ['cat', 'bear'], 0, 2)
        self.assertFalse(anchor_view(s, 'cat', {'cat', 'bear'}))
        self.assertTrue(anchor_view(dict(s, focus_ids=['cat']), 'cat', {'cat', 'bear'}))
        self.assertFalse(anchor_view(dict(s, focus_ids=['cat']), 'bear', {'cat', 'bear'}))

    def test_solo_and_real_speech_unchanged(self):
        self.assertTrue(anchor_view(shot('s2', ['hamster'], 0, 2), 'hamster', {'cat', 'hamster'}))
        self.assertTrue(anchor_view(shot('s3', ['cat', 'bear'], 0, 2, dub=[], speaking=['cat']), 'cat', {'cat', 'bear'}))


@unittest.skipIf(shutil.which('ffmpeg') is None, 'ffmpeg unavailable')
class FocusAnchors(unittest.TestCase):
    def test_each_shared_shot_gets_one_answer_for_all_its_people(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / 'p.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=gray:s=320x180:d=6', str(video)], check=True)
            shots = [shot('s1', ['cat', 'bear', 'hamster'], 0, 2), shot('s2', ['cat', 'bear'], 2, 5),
                     shot('s3', ['hamster'], 5, 6)]
            calls = []
            def call(stage, prompt, validator, media, image=False):
                rows = json.loads(prompt.rsplit('\n', 1)[1])['shots']
                calls.append([sorted(r['people']) for r in rows])
                # s1 -> none, s2 -> cat (the only answer for that frame; no second opinion)
                answer = [dict(label=r['label'], focus='cat' if 'hamster' not in r['people'] else 'none', evidence='构图')
                          for r in rows]
                return validator(dict(shots=answer))
            story = SimpleNamespace(shots=shots, root=Path(tmp), call=call,
                                    observations={'w': dict(proxy=dict(path=str(video)))}, windows={'w': dict(source_start_s=0)},
                                    actors={a: dict(appearance=a) for a in ('cat', 'bear', 'hamster')})
            scene_casting.focus_anchors(story, ['cat', 'bear', 'hamster'])
            self.assertEqual(len(calls), 1)                                   # one board, one answer per shot
            self.assertEqual(calls[0], [['bear', 'cat', 'hamster'], ['bear', 'cat']])
            self.assertEqual(shots[1]['focus_ids'], ['cat'])
            self.assertEqual(shots[0]['focus_ids'], [])
            scene_casting.focus_anchors(story, ['cat', 'bear', 'hamster'])
            self.assertEqual(len(calls), 1)                                   # not asked again

    def test_focus_must_be_someone_in_that_shot(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / 'p.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=gray:s=320x180:d=4', str(video)], check=True)
            shots = [shot('s2', ['cat', 'bear'], 0, 3)]
            def call(stage, prompt, validator, media, image=False):
                with self.assertRaises(ValueError):
                    validator(dict(shots=[dict(label='1', focus='hamster', evidence='x')]))
                return validator(dict(shots=[dict(label='1', focus='none', evidence='并排')]))
            story = SimpleNamespace(shots=shots, root=Path(tmp), call=call,
                                    observations={'w': dict(proxy=dict(path=str(video)))}, windows={'w': dict(source_start_s=0)},
                                    actors={a: dict(appearance=a) for a in ('cat', 'bear', 'hamster')})
            scene_casting.focus_anchors(story, ['cat', 'bear', 'hamster'])
            self.assertEqual(shots[0]['focus_ids'], [])

    def test_guard_allows_focus_stage(self):
        node = shutil.which('node')
        if node is None:
            self.skipTest('node unavailable')
        source = Path(scene_casting.__file__).with_name('mcp_opencode_guard.mjs').read_text()
        pattern = next(l for l in source.splitlines() if l.startswith('const SCENE_STAGE'))
        code = pattern + "\nif(!SCENE_STAGE.test('material_scene_discovery_castfocus_0123456789abcdef'))process.exit(1);"
        subprocess.run([node, '--input-type=module', '-e', code], check=True)


if __name__ == '__main__':
    unittest.main()
