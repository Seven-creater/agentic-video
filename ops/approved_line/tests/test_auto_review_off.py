"""User policy: the fast lane delivers without the automatic final review."""
import inspect
import unittest
from pathlib import Path
from omni_story.library import dialogue_cut


class AutoReviewOffTests(unittest.TestCase):
    def test_deliver_has_review_switch_defaulting_on(self):
        self.assertIs(inspect.signature(dialogue_cut.deliver).parameters['auto_review'].default,True)

    def test_review_off_path_returns_before_any_model_review(self):
        source=inspect.getsource(dialogue_cut.deliver)
        off=source.index('if not auto_review:');review=source.index('visual_audio.review_final_video')
        self.assertLess(off,review)
        block=source[off:review]
        self.assertIn("quality_status='auto_review_off'",block)
        self.assertIn('apply_schedule_quality',block)  # collapsed-plan guard kept
        self.assertIn('return result',block)

    def test_fast_lane_runner_turns_review_off(self):
        runner=Path(__file__).with_name('quick_preview_runner.py').read_text()
        self.assertIn('auto_review=False',runner)


if __name__=='__main__':unittest.main()
