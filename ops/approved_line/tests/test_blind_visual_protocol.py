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
            validate_visual(self.base([dict(second='0',desc='a')]))


if __name__=='__main__':unittest.main()
