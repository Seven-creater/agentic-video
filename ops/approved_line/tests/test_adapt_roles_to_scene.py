import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from omni_story.library.scene_casting import adapt_roles_to_scene, explain_cast_errors
from omni_story.library.state import json_sha


def contract(lines):
    c = dict(policy='reference_story_v1', visual_policy='continuous_dialogue_adaptation_v1', lines=lines)
    c['sha256'] = json_sha({k: v for k, v in c.items() if k not in ('policy', 'sha256')})
    return c


class Story(SimpleNamespace):
    @property
    def roles(self):
        return list(dict.fromkeys(r for s in self.route.scenes for r in s['roles']))


def story(tmp):
    lines = [dict(line_id=1, role='前台', text='请问您找谁'), dict(line_id=2, role='大妈', text='我找那个不要脸的狐狸精出来给我说清楚'),
             dict(line_id=3, role='同事', text='阿姨这里是公司'), dict(line_id=4, role='白衬衫女', text='我要去上班了阿姨请您回去吧'),
             dict(line_id=5, role='大妈', text='你给我站住今天不把话说清楚谁也别想走')]
    arts = {}
    st = SimpleNamespace(output=tmp, set_artifact=lambda k, v: arts.__setitem__(k, v))
    route = SimpleNamespace(lines=list(lines), scenes=[dict(scene_id='scene_1', line_ids=[1, 2, 3, 4, 5],
                                                            roles=['前台', '大妈', '同事', '白衬衫女'], voice_only_roles=[])])
    return Story(contract=contract(lines), route=route, state=st, artifacts=arts)


class AdaptRolesToScene(unittest.TestCase):
    def test_secondary_roles_are_dropped_to_fit_the_people_on_screen(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = story(tmp)
            adapt_roles_to_scene(s, ['前台', '同事'], '前台与同事是插曲，作用并入大妈直接上门质问')
            self.assertEqual(s.roles, ['大妈', '白衬衫女'])
            self.assertEqual(sorted(s.contract['optional_reference_line_ids']), [1, 3])
            self.assertEqual([r['line_id'] for r in s.route.lines], [2, 4, 5])
            self.assertTrue((Path(tmp) / 'reference_contract.json').exists())
            self.assertEqual(s.artifacts['cast_roles_adapted_to_scene']['remaining_roles'], ['大妈', '白衬衫女'])

    def test_leads_cannot_be_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = story(tmp)
            with self.assertRaises(ValueError):
                adapt_roles_to_scene(s, ['大妈'], 'x')

    def test_too_few_people_now_points_to_adapting_the_story(self):
        with self.assertRaises(ValueError) as e:
            explain_cast_errors(dict(cast={}), ['前台', '大妈', '同事', '白衬衫女'], {}, ['a', 'b', 'c'], ['scene_1'])
        self.assertIn('adapt_roles', str(e.exception))


if __name__ == '__main__':
    unittest.main()
