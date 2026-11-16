import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from PIL import Image
from omni_story.library.material_evidence import VisualIdentityRegistry
from omni_story.library.media import sha256_file
from omni_story.library.scene_casting import cast_lookalikes


def ref(folder, name, colour, appearance):
    path = Path(folder) / (name + '.jpg'); Image.new('RGB', (64, 64), colour).save(path)
    return dict(path=str(path), sha256=sha256_file(path), appearance=appearance)


class CastLookalikes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); t = self.tmp.name
        self.prompts = []
        def call(stage, prompt, validator, media, image=False):
            self.prompts.append((stage, prompt))
            same_look = 'pink armour' in prompt and prompt.count('pink armour') >= 2
            return validator(dict(appearance_a='a', appearance_b='b', distinguishable=not same_look, evidence='e'))
        self.registry = VisualIdentityRegistry(t, call)
        self.registry.root.mkdir(parents=True, exist_ok=True)
        self.registry.references = {'a_boss': ref(t, 'boss', 'pink', 'pink armour, beaded headband, back view'),
                                    'a_hench': ref(t, 'hench', 'salmon', 'pink armour, beaded headband, front'),
                                    'a_cat': ref(t, 'cat', 'white', 'white cat with blue bow')}

    def tearDown(self):
        self.tmp.cleanup()

    def test_lookalike_performers_cannot_play_two_roles(self):
        story = SimpleNamespace(identities=self.registry)
        clashes = cast_lookalikes(story, {'boss': 'a_boss', 'henchman': 'a_hench'})
        self.assertEqual(clashes[0]['actor_ids'], ['a_boss', 'a_hench'])
        self.assertEqual(sorted(clashes[0]['roles']), ['boss', 'henchman'])
        self.assertIn('一眼分清', self.prompts[0][1])

    def test_distinct_performers_pass_with_one_stable_check_per_pair(self):
        story = SimpleNamespace(identities=self.registry)
        self.assertEqual(cast_lookalikes(story, {'boss': 'a_boss', 'chief': 'a_cat'}), [])
        first = self.prompts[-1][0]
        cast_lookalikes(story, {'chief': 'a_cat', 'boss': 'a_boss'})
        self.assertEqual(self.prompts[-1][0], first)  # same pair, same stage name: replayable

    def test_missing_reference_is_not_guessed(self):
        story = SimpleNamespace(identities=self.registry)
        self.assertEqual(cast_lookalikes(story, {'boss': 'a_boss', 'x': 'a_unknown'}), [])
        self.assertEqual(self.prompts, [])


class GuardPermitsLookalikeStage(unittest.TestCase):
    def test_transport_guard_allows_the_exact_stage_name(self):
        # 17 (bf64c94c4da3): the new stage was refused by the MCP stage allow-list
        # (library_mcp_opencode_stage_duplicate_or_not_permitted) before any send.
        import shutil, subprocess
        from omni_story.library import scene_casting
        node = shutil.which('node')
        if node is None:
            self.skipTest('node unavailable')
        source = Path(scene_casting.__file__).with_name('mcp_opencode_guard.mjs').read_text()
        pattern = next(l for l in source.splitlines() if l.startswith('const SCENE_STAGE'))
        names = []
        with tempfile.TemporaryDirectory() as t:
            reg = VisualIdentityRegistry(t, lambda stage, prompt, validator, media, image=False:
                names.append(stage) or validator(dict(appearance_a='a', appearance_b='b', distinguishable=True, evidence='e')))
            reg.root.mkdir(parents=True, exist_ok=True)
            reg.distinguishable(ref(t, 'l', 'red', 'x'), ref(t, 'r', 'blue', 'y'))
        code = pattern + "\nconst n=%r;if(!SCENE_STAGE.test(n)||SCENE_STAGE.test(n+'0'))process.exit(1);" % names[0]
        subprocess.run([node, '--input-type=module', '-e', code], check=True)


if __name__ == '__main__':
    unittest.main()
