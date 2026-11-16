"""Silent-watch replies: lossless flattening of timed facts, actionable errors otherwise."""
import json
import re
import unittest
from pathlib import Path
from omni_story.library.material_browser import validate_visual

FIXTURE=Path(__file__).with_name('blind_timed_facts_fixture.json')


def parse(text):
    text=re.sub(r'^```(?:json)?\s*|\s*```$','',text.strip())
    return json.loads(text)


class BlindVisualProtocolTests(unittest.TestCase):
    def test_real_timed_fact_replies_are_flattened_without_loss(self):
        for row in json.loads(FIXTURE.read_text()):
            raw=parse(row['model_text']);original=json.loads(json.dumps(raw))
            value=validate_visual(raw)
            self.assertTrue(all(isinstance(x,str) for x in value['visible_facts']))
            self.assertEqual(len(value['visible_facts']),len(original['visible_facts']))
            for flat,item in zip(value['visible_facts'],original['visible_facts']):
                self.assertTrue(flat.endswith(item['desc'].strip()))
                self.assertTrue(flat.startswith(f"{item['second']:g}秒："))
            self.assertIn('visible_facts',value['normalized_fields'])

    def base(self, facts):
        return dict(visible_facts=facts,interaction=[],uncertainties=[],occlusion_limits=[],readable_dialogue_text=False)

    def test_plain_strings_unchanged(self):
        value=validate_visual(self.base(['两人对视']))
        self.assertEqual(value['visible_facts'],['两人对视']);self.assertNotIn('normalized_fields',value)

    def test_unknown_object_shape_gets_actionable_error(self):
        with self.assertRaisesRegex(ValueError,r'visible_facts\[0\] must be a plain string'):
            validate_visual(self.base([dict(second=0,desc='a',who='b')]))
        with self.assertRaisesRegex(ValueError,r'visible_facts\[0\] must be a plain string'):
            # string times ("0-2秒") are flattened losslessly since f2c03a82; a list time is still unclear
            validate_visual(self.base([dict(second=[0],desc='a')]))


class NumberedFactShapes(unittest.TestCase):
    def test_numbered_and_differently_named_facts_flatten_without_loss(self):
        # 21 (f2c03a82): with the goal_fit column replies numbered their facts in these shapes.
        from omni_story.library.material_browser import validate_visual, validate_goal_fit
        value=dict(visible_facts=[dict(fact='两人进店',index=0,time='0-2秒'),dict(content='白猫张嘴',second=3),
                                  dict(desc='仓鼠吃面',idx=2,time='5'),dict(description='熊冒汗',fact_index=3,time_s=7.5),
                                  dict(fact='对视',id=4,s=9)],
                   interaction=[dict(description='猫朝仓鼠逼近',second=4)],uncertainties=[],occlusion_limits=[],
                   readable_dialogue_text=False,
                   goal_fit=[dict(need='定格',start_s=16,end_s=16,fact_index=4,reason='瞬间特写')])
        validate_goal_fit(validate_visual(value),17)
        self.assertEqual(value['visible_facts'],['0-2秒：两人进店','3秒：白猫张嘴','5秒：仓鼠吃面','7.5秒：熊冒汗','9秒：对视'])
        self.assertEqual(value['interaction'],['4秒：猫朝仓鼠逼近'])

    def test_non_integer_index_or_unknown_key_still_rejected(self):
        from omni_story.library.material_browser import validate_visual
        for bad in (dict(fact='x',index='a'),dict(fact='x',mood='y')):
            with self.assertRaises(ValueError):
                validate_visual(dict(visible_facts=[bad],interaction=[],uncertainties=[],occlusion_limits=[],readable_dialogue_text=False))


if __name__=='__main__':unittest.main()
