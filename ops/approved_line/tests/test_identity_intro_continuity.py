import unittest
from omni_story.library.dialogue_identity_review import combine_identity_observations,verify_observations

class IntroContinuityTests(unittest.TestCase):
    def check(self, later='p1', permission=True, concealed=False):
        catalog=[dict(person_id=p) for p in ('p1','p2')]
        expected=[dict(line_id='a',role='speaker',person_id='p1',visual_anchor=True,allows_stylized_intro=permission,
            allows_occluded_intro=permission,reference_offscreen=False,voice_only=False),
            dict(line_id='b',role='speaker',person_id='p1',visual_anchor=False,reference_offscreen=False,voice_only=False)]
        sound=dict(observations=[dict(line_id=k,voice_id='voice',heard_text='speech',evidence='same timbre') for k in ('a','b')])
        picture=dict(observations=[dict(line_id='a',visibility='none',visible_speaker_ids=[],opening_visible_actor_ids=['p1'],
            opening_stylized_actor_ids=[] if concealed else ['p1'],opening_concealed_mouth_ids=['p1'] if concealed else [],evidence='unique clear person'),
            dict(line_id='b',visibility='speaker',visible_speaker_ids=[later],evidence='visible mouth motion')])
        observed=combine_identity_observations(sound,picture,catalog,expected)
        return observed,verify_observations(observed,catalog,expected)
    def test_later_same_person_speech_keeps_valid_stylized_intro(self):
        observed,verdict=self.check()
        self.assertEqual(observed['observations'][0]['basis'],'stylized_intro')
        self.assertTrue(verdict['passed'])
    def test_later_same_person_speech_keeps_valid_concealed_intro(self):
        observed,verdict=self.check(concealed=True)
        self.assertEqual(observed['observations'][0]['basis'],'occluded_intro')
        self.assertTrue(verdict['passed'])
    def test_permission_is_still_required(self):
        self.assertFalse(self.check(permission=False)[1]['passed'])
    def test_different_person_later_is_not_silently_reassigned(self):
        self.assertFalse(self.check(later='p2')[1]['passed'])

if __name__=='__main__':unittest.main()
