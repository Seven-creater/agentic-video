"""A non-visual source basis (e.g. off_screen_voice) is a heard hypothesis, never visible speech."""
import json
import unittest
from pathlib import Path
from omni_story.library.contracts import parse_model_json
from omni_story.library.scene_casting import normalize_source_turns, validate_source_turns

FIXTURE=json.loads(Path(__file__).with_name('source_offscreen_basis_fixture.json').read_text())


class OffscreenBasisTests(unittest.TestCase):
    def test_real_offscreen_voice_reply_is_kept_as_hypothesis(self):
        people=FIXTURE['people'];raw=parse_model_json(FIXTURE['reply'])
        value=validate_source_turns(normalize_source_turns(raw,people),people,17.0)
        off=[t for t in value['turns'] if t.get('source_basis_original')=='off_screen_voice']
        self.assertEqual(len(off),1)
        self.assertEqual(off[0]['speaking'],[]);self.assertEqual(off[0]['basis'],'uncertain')
        self.assertEqual(off[0]['heard_actor_hypotheses'],['actor_5969e99f69d3'])
        self.assertTrue(all(len(t['speaking'])<=1 and set(t['speaking'])<=set(t['visible']) for t in value['turns']))
        self.assertEqual(raw['turns'][-1]['basis'],'off_screen_voice')  # received reply not rewritten

    def turn(self, **k):
        row=dict(start_s=0,end_s=1,visible=['a'],speaking=['b'],basis='off_screen_voice',evidence='heard')
        row.update(k);return dict(turns=[row],limitations=[])

    def test_unknown_speaker_id_is_still_rejected(self):
        value=normalize_source_turns(self.turn(speaking=['ghost']),{'a':'x','b':'y'})
        with self.assertRaises(ValueError):validate_source_turns(value,{'a':'x','b':'y'},1)

    def test_visible_mouth_motion_speaker_unchanged(self):
        value=normalize_source_turns(self.turn(speaking=['a'],basis='mouth_motion'),{'a':'x','b':'y'})
        self.assertEqual(validate_source_turns(value,{'a':'x','b':'y'},1)['turns'][0]['speaking'],['a'])


if __name__=='__main__':unittest.main()
