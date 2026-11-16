import unittest
from types import SimpleNamespace
from unittest import mock
from omni_story.library import autonomous_material, scene_coverage
from omni_story.library.autonomous_material import Discovery


class Stop(Exception):
    pass


def discovery(captured):
    d = object.__new__(Discovery)
    d.load_window = lambda wid: dict(source_sha256='s', source_start_s=0.0, source_end_s=3.0)
    d.route = SimpleNamespace(sources={'s': dict(sha256='s')})
    d.inspection_proxy = lambda window, source: dict(path='proxy.mp4')
    d.identity_window = lambda window, proxy, source: window
    d.identities = SimpleNamespace(bind=lambda w, protected: {'A1': dict(role_id='r1', entity_kind='person',
                                   appearance='white bear', evidence='face', entity_ids=[], local_role_ids=[])})
    d.reconcile_actors = lambda: None
    d.inspection_instructions = lambda: ''
    d.cast = None; d.allow_static_reuse = False; d.root = __import__('pathlib').Path('.')
    def call(stage, prompt, validator, media):
        captured.update(prompt=prompt, validator=validator); raise Stop
    d.call = call
    return d


def seg(**kw):
    row = dict(cut_id='c1', start_s=0, end_s=1, kind='story', visible=['A1'], speaking=[], clear_face=[],
               speech_evidence='', addressee_ids=[], addressee_evidence='', place='snow', action='stands',
               events=[dict(action='stands', subject_ids=['A1'], addressee_ids=[])])
    row.update(kw); return row


class ObserveActionableErrors(unittest.TestCase):
    def setUp(self):
        self.captured = {}
        with mock.patch.object(scene_coverage, 'detect_shots', lambda *a: [1.0, 2.0]):
            with self.assertRaises(Stop):
                discovery(self.captured).inspect('w1')
        self.validate = self.captured['validator']

    def reply(self, *segments):
        return dict(confirmed_actors=[dict(actor_id='A1', appearance='white bear', evidence='face')], segments=list(segments))

    def test_prompt_states_single_cut_and_confirmed_only_rules(self):
        self.assertIn('不能写c1-c6这样的区间', self.captured['prompt'])
        self.assertIn('只能取自本回复confirmed_actors', self.captured['prompt'])

    def test_cut_range_error_says_one_segment_per_cut(self):
        with self.assertRaises(ValueError) as e:
            self.validate(self.reply(seg(cut_id='c1-c3', end_s=3)))
        self.assertIn("'c1-c3'", str(e.exception)); self.assertIn('c1..c3', str(e.exception))

    def test_unconfirmed_label_error_names_value_and_allowed_ids(self):
        with self.assertRaises(ValueError) as e:
            self.validate(self.reply(seg(visible=['A1', '蓝帽白色圆生物(未确认ID)'])))
        msg = str(e.exception)
        self.assertIn('蓝帽白色圆生物(未确认ID)', msg); self.assertIn("['A1']", msg); self.assertIn('uncertainties', msg)

    def test_valid_reply_passes(self):
        self.assertEqual(len(self.validate(self.reply(seg(), seg(cut_id='c2', start_s=1, end_s=2)))['segments']), 2)


if __name__ == '__main__':
    unittest.main()
