import unittest
from unittest import mock
from omni_story.library import joint_dialogue as jd


def scene(sid, roles):
    return dict(scene_id=sid, visual_policy='continuous_dialogue_adaptation_v1', template='t',
                role_bindings={r: dict(role_id='a_' + r, appearance=r + ' look') for r in roles},
                reference_lines=[dict(line_id=1, role=roles[0], text='find him', addressee_roles=[roles[1]], on_screen=True)],
                coverage_shots=[dict(slot_id='s1', visible=['a_' + roles[0], 'a_chief'], speaking=['a_' + roles[0]],
                                     listeners=[], source_start_s=0, source_end_s=2)])


class ReviewFilmCast(unittest.TestCase):
    def test_roles_from_other_scenes_are_known_people(self):
        lock = dict(scenes=[scene('scene_2', ['boss', 'chief']), scene('scene_4', ['boss', 'henchman'])])
        self.assertEqual(jd.film_cast(lock, lock['scenes'][1]), {'chief': dict(role_id='a_chief', appearance='chief look')})
        self.assertEqual(jd.film_cast(dict(scenes=[lock['scenes'][0]]), lock['scenes'][0]), {})

    def test_review_is_told_that_facing_a_known_role_is_another_person(self):
        lock = dict(scenes=[scene('scene_2', ['boss', 'chief']), scene('scene_4', ['boss', 'henchman'])])
        seen = {}
        def call(state, glm, name, prompt, validator):
            seen['prompt'] = prompt
            return dict(lines=[dict(line_id='l1', options=[dict(shot_id='s1', reason='ok')])], pause_shots=[])
        line = dict(line_id='l1', scene_id='scene_4', role='boss', reference_line_ids=[1], text='find him',
                    shot_ids=['s1'], visual_reason='r', voiced_s=1.0)
        with mock.patch('omni_story.library.pipeline._material_call', call):
            jd.review_shot_uses(lock['scenes'][1], [line], None, None, 'n', jd.film_cast(lock, lock['scenes'][1]))
        self.assertIn('"film_cast": {"chief"', seen['prompt'])
        self.assertIn('不能当作朝本句addressee_roles说话', seen['prompt'])

    def test_single_scene_prompt_is_unchanged(self):
        sc = scene('scene_1', ['boss', 'chief'])
        self.assertEqual(jd.review_prompt(sc, [], [], {}), jd.review_prompt(sc, [], [], {}, {}))
        self.assertNotIn('film_cast', jd.review_prompt(sc, [], [], {}))


if __name__ == '__main__':
    unittest.main()
