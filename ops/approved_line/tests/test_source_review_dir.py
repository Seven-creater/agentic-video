import json
import tempfile
import unittest
from pathlib import Path
from omni_story.library.scene_casting import source_review_dir


def record(folder, prompt):
    folder.mkdir(parents=True)
    (folder / 'state.json').write_text(json.dumps(dict(status='received')))
    (folder / 'request.json').write_text(json.dumps(dict(prompt=prompt)))


class SourceReviewDir(unittest.TestCase):
    def test_first_question_uses_legacy_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(source_review_dir(Path(tmp), 'q1'), Path(tmp) / 'review')

    def test_same_question_reuses_paid_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            record(Path(tmp) / 'review', 'q1')
            self.assertEqual(source_review_dir(Path(tmp), 'q1'), Path(tmp) / 'review')

    def test_new_question_after_reinspection_gets_own_stable_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            record(Path(tmp) / 'review', 'q1')
            new = source_review_dir(Path(tmp), 'q2 with a newly confirmed person')
            self.assertNotEqual(new, Path(tmp) / 'review')
            self.assertEqual(new, source_review_dir(Path(tmp), 'q2 with a newly confirmed person'))
            self.assertEqual(json.loads((Path(tmp) / 'review/request.json').read_text())['prompt'], 'q1')

    def test_damaged_record_stays_on_guarded_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'review').mkdir()
            (Path(tmp) / 'review/state.json').write_text('{}')
            self.assertEqual(source_review_dir(Path(tmp), 'q1'), Path(tmp) / 'review')


if __name__ == '__main__':
    unittest.main()
