from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
from omni_story.library import dialogue_identity_review as identity

class ReferencePairTests(unittest.TestCase):
    def run_review(self, overrides=None, voices=None):
        clips=[dict(clip_id='s'+str(i),start_s=i*4,end_s=i*4+d) for i,d in enumerate((3,2,1,.8),1)]
        mapping={'s'+str(i):'line'+str(i) for i in range(1,5)}
        speakers=voices or dict(s1='a',s2='b',s3='a',s4='b');seen=[]
        def compare(media,folder,by_id,pair,reviewer):
            a,b=pair;seen.append(pair)
            return dict(a=a,b=b,first_text='heard '+a,second_text='heard '+b,
                verdict=(overrides or {}).get(pair,'same' if speakers[a]==speakers[b] else 'different'),evidence='independent acoustic comparison')
        with TemporaryDirectory() as tmp,patch.object(identity,'_compare_voice_pair',side_effect=compare):
            result=identity.review_voice_reference_pairs(Path('media'),Path(tmp),clips,mapping,['line1','line2'],None,max_workers=1)
        return {r['line_id']:r for r in result['observations']},seen
    def test_each_target_compared_directly_against_both_neutral_references(self):
        rows,seen=self.run_review()
        self.assertEqual(len(seen),5)
        self.assertEqual(rows['line3']['voice_id'],'s1')
        self.assertEqual(rows['line4']['voice_id'],'s2')
        self.assertEqual(rows['line3']['heard_text'],'heard s3')
        self.assertEqual(rows['line4']['heard_text'],'heard s4')
    def test_unknown_third_voice_is_not_forced_into_known_groups(self):
        rows,_=self.run_review(voices=dict(s1='a',s2='b',s3='c',s4='b'))
        self.assertIsNone(rows['line3']['voice_id'])
    def test_uncertainty_is_retained_despite_one_positive_match(self):
        rows,_=self.run_review(overrides={('s2','s3'):'uncertain'})
        self.assertIsNone(rows['line3']['voice_id'])
    def test_two_positive_reference_matches_remain_ambiguous(self):
        rows,_=self.run_review(overrides={('s2','s3'):'same'})
        self.assertIsNone(rows['line3']['voice_id'])

    def boundary_compare(self, first, second):
        calls=[]
        def labelled(media,folder,clips,**kwargs):
            folder.mkdir(parents=True,exist_ok=True);calls.append(kwargs)
            return folder/'media.mp4',[dict(clip_id='s1',start_s=2,end_s=4),dict(clip_id='s2',start_s=6,end_s=7)]
        replies=iter([first,second])
        def review(*args,**kwargs):return dict(http_status=200,parsed=next(replies))
        with TemporaryDirectory() as tmp,patch.object(identity,'labelled_audio_batch',side_effect=labelled):
            result=identity._compare_voice_pair(Path('media'),Path(tmp),
                dict(s1=dict(start_s=0,end_s=2),s2=dict(start_s=2,end_s=3)),('s1','s2'),review)
        return result,calls
    def test_empty_transcript_gets_one_explicit_boundary_measurement(self):
        result,calls=self.boundary_compare(dict(first_text='reference plus target',second_text='',verdict='uncertain',evidence='silent second'),
            dict(first_text='reference',second_text='target',verdict='same',evidence='same timbre'))
        self.assertEqual(result['second_text'],'target')
        self.assertEqual(calls,[{},dict(gap_s=2.0,label_lead_s=2.0)])
        self.assertIn('boundary_repair',result)
    def test_boundary_measurement_does_not_force_uncertain_voice_to_match(self):
        result,calls=self.boundary_compare(dict(first_text='reference',second_text='',verdict='uncertain',evidence='missing'),
            dict(first_text='reference',second_text='target',verdict='uncertain',evidence='too short'))
        self.assertEqual(result['verdict'],'uncertain')
        self.assertEqual(len(calls),2)

if __name__=='__main__':unittest.main()
