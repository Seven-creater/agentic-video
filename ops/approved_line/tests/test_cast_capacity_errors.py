import unittest
from types import SimpleNamespace
from omni_story.library.scene_casting import cast_capacity, explain_cast_errors

ROLES = ['樵夫', '白狐', '红狐', '青衣精怪', '老铁匠']
ACTORS = {a: dict(entity_kind='individual') for a in ('a1', 'a2', 'a3', 'a4', 'a5')}
ACTORS['crowd'] = dict(entity_kind='group')
SCENES = ['scene_1', 'scene_3']


def finish(cast, windows=None):
    return dict(action='finish', cast=cast, scene_windows=windows or {s: ['w1'] for s in SCENES})


class CastCapacity(unittest.TestCase):
    def test_counts_only_visible_individuals_in_active_shots(self):
        story = SimpleNamespace(actors=ACTORS, shots=[dict(visible=['a1', 'crowd']), dict(visible=['a2', 'a1'])])
        self.assertEqual(cast_capacity(story), ['a1', 'a2'])

    def test_too_few_actors_says_finish_impossible_and_how_to_continue(self):
        cast = dict(zip(ROLES[:4], ['a1', 'a2', 'a3', 'a4']))
        with self.assertRaises(ValueError) as e:
            explain_cast_errors(finish(cast), ROLES, ACTORS, ['a1', 'a2', 'a3', 'a4'], SCENES)
        self.assertIn('不能finish', str(e.exception))
        self.assertIn('candidate', str(e.exception))
        with self.assertRaises(ValueError) as e:
            explain_cast_errors(finish(cast), ROLES, ACTORS, ['a1', 'a2', 'a3', 'a4'], SCENES, locked=True)
        self.assertNotIn('candidate', str(e.exception))
        self.assertIn('expand', str(e.exception))

    def test_missing_role_is_named(self):
        cast = dict(zip(ROLES[:4], ['a1', 'a2', 'a3', 'a4']))
        with self.assertRaises(ValueError) as e:
            explain_cast_errors(finish(cast), ROLES, ACTORS, list(ACTORS)[:5], SCENES)
        self.assertIn('老铁匠', str(e.exception))

    def test_shared_actor_is_named(self):
        cast = dict(zip(ROLES, ['a1', 'a2', 'a3', 'a4', 'a4']))
        with self.assertRaises(ValueError) as e:
            explain_cast_errors(finish(cast), ROLES, ACTORS, list(ACTORS)[:5], SCENES)
        self.assertIn('a4', str(e.exception))
        self.assertIn('共用', str(e.exception))

    def test_missing_scene_windows_named(self):
        cast = dict(zip(ROLES, ['a1', 'a2', 'a3', 'a4', 'a5']))
        with self.assertRaises(ValueError) as e:
            explain_cast_errors(finish(cast, {'scene_1': ['w1']}), ROLES, ACTORS, list(ACTORS)[:5], SCENES)
        self.assertIn('scene_3', str(e.exception))

    def test_valid_finish_passes(self):
        cast = dict(zip(ROLES, ['a1', 'a2', 'a3', 'a4', 'a5']))
        self.assertIsNone(explain_cast_errors(finish(cast), ROLES, ACTORS, list(ACTORS)[:5], SCENES))


if __name__ == '__main__':
    unittest.main()
