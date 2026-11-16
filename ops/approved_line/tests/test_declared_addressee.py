import unittest
from unittest import mock
from omni_story.library import joint_dialogue as jd

POLICY = 'continuous_dialogue_adaptation_v1'


def scene(policy=POLICY):
    shot = dict(slot_id='s1', visible=['a_boss', 'a_chief'], speaking=['a_boss'], listeners=['a_chief'],
                source_start_s=0, source_end_s=2)
    return dict(scene_id='scene_1', visual_policy=policy, template='t',
                role_bindings={'boss': dict(role_id='a_boss'), 'chief': dict(role_id='a_chief'),
                               'henchman': dict(role_id='a_hench')},
                reference_lines=[dict(line_id=1, role='boss', text='find him', addressee_roles=['henchman'], on_screen=True)],
                coverage_shots=[shot])


def row(**kw):
    r = dict(line_id='l1', scene_id='scene_1', role='boss', reference_line_ids=[1], text='find him',
             shot_ids=['s1'], visual_reason='boss speaks to chief')
    r.update(kw); return r


class DeclaredAddressee(unittest.TestCase):
    def test_reference_relation_without_declaration(self):
        self.assertEqual(jd.line_addressees(scene(), row()), ['henchman'])

    def test_writer_can_readdress_to_the_person_actually_faced(self):
        self.assertEqual(jd.line_addressees(scene(), row(addressee_roles=['chief'])), ['chief'])

    def test_strict_route_keeps_reference_relation(self):
        self.assertEqual(jd.line_addressees(scene(None), row(addressee_roles=['chief'])), ['henchman'])

    def test_script_rejects_unbound_or_self_addressee(self):
        lock = dict(scenes=[scene()])
        jd.validate_script(dict(lines=[row(addressee_roles=['chief'])], visual_scenes=[]), lock)
        for bad in (['boss'], ['村民'], 'chief'):
            with self.assertRaises(ValueError) as e:
                jd.validate_script(dict(lines=[row(addressee_roles=bad)], visual_scenes=[]), lock)
            self.assertIn("['chief', 'henchman']", str(e.exception))

    def test_shot_review_judges_the_declared_relation(self):
        seen = {}
        def call(state, glm, name, prompt, validator):
            seen['prompt'] = prompt
            return dict(lines=[dict(line_id='l1', options=[dict(shot_id='s1', reason='ok')])], pause_shots=[])
        line = dict(row(addressee_roles=['chief']), voiced_s=1.0)
        with mock.patch('omni_story.library.pipeline._material_call', call):
            jd.review_shot_uses(scene(), [line], None, None, 'n')
        self.assertIn('"addressee_roles": ["chief"]', seen['prompt'])
        self.assertEqual(line['addressee_roles'], ['chief'])

    def test_writer_prompt_offers_the_field_only_for_adaptation(self):
        self.assertIn('addressee_roles', jd.ADDRESSEE_GUIDE)
        self.assertIn('不能让画面里朝甲说话', jd.ADDRESSEE_GUIDE)


if __name__ == '__main__':
    unittest.main()
