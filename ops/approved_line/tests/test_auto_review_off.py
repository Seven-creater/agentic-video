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

    def test_visible_dialogue_uses_reviewed_edit_and_scheduled_fallback_keeps_its_switch(self):
        from omni_story.library import approved_dialogue_preview
        from importlib.util import find_spec
        body = inspect.getsource(approved_dialogue_preview.deliver)
        self.assertIn('speaker_mouth=True', body)
        legacy = Path(find_spec('omni_story.library.scheduled_preview').origin)
        self.assertIn('auto_review=False', legacy.read_text())


if __name__=='__main__':unittest.main()
