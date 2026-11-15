from types import SimpleNamespace
from unittest.mock import Mock
import unittest
from omni_story.library.scene_casting import direct_cast
from omni_story.library.state import LibraryStopped

class CandidateSwitchTests(unittest.TestCase):
    def story(self, selected):
        story=SimpleNamespace(feedback=[],route=SimpleNamespace(lines=[],scenes=[]),roles=[],actors={},shots=[],cast=None,
            active_candidate=dict(scene_group_id='old'),browser_result=dict(candidates=[]),active_ranges={})
        story.browser=SimpleNamespace(visual={'watched':{}},run=Mock(return_value=dict(candidates=[dict(scene_group_id=selected)])))
        count=[]
        def call(stage,prompt,valid):
            if count:raise StopIteration('next casting decision reached')
            count.append(1);return valid(dict(action='expand',reason='original voice insufficient'))
        story.call=call
        story.use_candidate=lambda rank:setattr(story,'active_candidate',story.browser_result['candidates'][rank])
        return story
    def test_can_switch_to_already_watched_candidate_before_lock(self):
        story=self.story('different')
        with self.assertRaises(StopIteration):direct_cast(story)
        self.assertEqual(story.active_candidate['scene_group_id'],'different')
        feedback=story.browser.run.call_args.kwargs['feedback']
        self.assertNotIn('user_rule',feedback)
        self.assertIsNone(feedback['locked_cast'])
    def test_same_candidate_without_evidence_still_stops(self):
        with self.assertRaisesRegex(LibraryStopped,'no_new_video_evidence'):direct_cast(self.story('old'))

    def test_exact_unique_appearance_ids_are_normalized_without_new_inference(self):
        from omni_story.library.scene_casting import normalize_source_turns,validate_source_turns
        value=dict(turns=[dict(start_s=0,end_s=1,visible=['armored person'],speaking=['armored person'],basis='mouth_motion',evidence='visible motion')])
        actors=dict(actor_a='armored person',actor_b='small animal')
        result=normalize_source_turns(value,actors)
        validate_source_turns(result,actors,1)
        self.assertEqual(result['turns'][0]['speaking'],['actor_a'])
        self.assertEqual(value['turns'][0]['speaking'],['armored person'])
        with self.assertRaises(ValueError):
            validate_source_turns(normalize_source_turns(value,dict(actor_a='armored person',actor_b='armored person')),actors,1)

if __name__=='__main__':unittest.main()
