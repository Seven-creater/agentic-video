"""Scene grouping definition, fixed source subtitle band and early speaker-anchor check."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from omni_story.library import joint_dialogue
from omni_story.library.material_browser import (CONTINUITY_POLICY, SUBTITLE_BAND_POLICY,
    continuity_request_key, source_subtitle_band)
from omni_story.library.state import LibraryStopped
from omni_story.library.story_dialogue import StoryDialogue

SHA='16ca73de7eecb6c9c1aeeb35bffec347f1c161ad3e8922d6344dffee8b430f68'


class SubtitleBandTests(unittest.TestCase):
    def write(self, folder, **override):
        value=dict(policy=SUBTITLE_BAND_POLICY,source_sha256=SHA,region=dict(x=.08,y=.75,w=.84,h=.22))
        value.update(override)
        (Path(folder)/f'{SHA}.json').write_text(json.dumps(value))

    def test_recorded_band_is_returned_with_its_receipt(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{'OMNI_SUBTITLE_BAND_DIR':tmp}):
            self.write(tmp)
            band=source_subtitle_band(SHA)
            self.assertEqual(band['region'],dict(x=.08,y=.75,w=.84,h=.22))
            self.assertEqual(len(band['sha256']),64)

    def test_missing_band_keeps_per_window_localization_only(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{'OMNI_SUBTITLE_BAND_DIR':tmp}):
            self.assertIsNone(source_subtitle_band(SHA))
        with patch.dict(os.environ,{},clear=True):
            self.assertIsNone(source_subtitle_band(SHA))

    def test_band_for_another_source_or_outside_frame_stops(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{'OMNI_SUBTITLE_BAND_DIR':tmp}):
            self.write(tmp,source_sha256='other')
            with self.assertRaises(LibraryStopped):source_subtitle_band(SHA)
            self.write(tmp,region=dict(x=.1,y=.9,w=.8,h=.2))
            with self.assertRaises(LibraryStopped):source_subtitle_band(SHA)


class ContinuityPolicyTests(unittest.TestCase):
    def test_new_grouping_definition_never_reuses_old_continuity_answers(self):
        self.assertEqual(CONTINUITY_POLICY,'narrative_scene_intervals_v3')
        self.assertRegex(continuity_request_key(['a','b']),r'^[0-9a-f]{16}$')


def shot(window, visible, dub, speaking=()):
    return dict(window_id=window,visible=list(visible),dub_speaker_ids=list(dub),speaking=list(speaking),
                source_sha256='src',source_start_s=0,source_end_s=2,loopable=True)


class AnchorTests(unittest.TestCase):
    def test_anchor_view_requires_identifiable_new_voice(self):
        both=shot('w',['knight','bear'],['knight','bear'])
        alone=shot('w',['knight','cat'],['knight','cat'])
        source=shot('w',['knight','bear'],[],['knight'])
        self.assertFalse(joint_dialogue.anchor_view(both,'knight',{'knight','bear'}))
        self.assertTrue(joint_dialogue.anchor_view(alone,'knight',{'knight','bear'}))  # silent companion is not a speaker
        self.assertTrue(joint_dialogue.anchor_view(source,'knight',{'knight','bear'}))

    def story(self, shots):
        scene=dict(scene_id='scene_1',roles=['摊主','顾客'],line_ids=[1,2])
        lines=[dict(line_id=1,role='顾客',text='这个多少钱'),dict(line_id=2,role='摊主',text='两块钱一斤')]
        return SimpleNamespace(route=SimpleNamespace(scenes=[scene],lines=lines),shots=shots,active_ranges={'w':[[0,9]]})

    def test_cast_without_seller_anchor_is_sent_back_before_voices(self):
        shots=[shot('w',['knight','bear'],['knight','bear']),shot('w',['bear'],['bear'])]
        value=dict(cast={'摊主':'knight','顾客':'bear'},scene_windows={'scene_1':['w']})
        with self.assertRaisesRegex(ValueError,'摊主.*expand'):
            StoryDialogue.validate_selection(self.story(shots),value)

    def test_cast_with_each_speaker_identifiable_passes_anchor_check(self):
        shots=[shot('w',['knight','bear'],['knight','bear']),shot('w',['bear'],['bear']),shot('w',['knight'],['knight'])]
        value=dict(cast={'摊主':'knight','顾客':'bear'},scene_windows={'scene_1':['w']})
        StoryDialogue.validate_selection(self.story(shots),value)


class ContinuityValidationTests(unittest.TestCase):
    def mapping(self, second_source_start):
        return [dict(window_id='a',start_s=0,end_s=17,media_duration_s=17,source_sha256='s',source_start_s=1678,source_end_s=1695),
                dict(window_id='b',start_s=17,end_s=34,media_duration_s=17,source_sha256='s',
                     source_start_s=second_source_start,source_end_s=second_source_start+17)]

    def group(self, **fields):
        value=dict(segments=[dict(window_id='a',start_s=2,end_s=17),dict(window_id='b',start_s=0,end_s=17)],
                   setting='摊位',participants='三人',evidence='连续',limitations='无明显限制')
        value.update(fields);return value

    def test_missing_group_field_names_the_field(self):
        from omni_story.library.material_browser import validate_continuity_groups
        bad=self.group();del bad['limitations']
        with self.assertRaisesRegex(ValueError,r"groups\[0\] lacks .*limitations"):
            validate_continuity_groups(dict(groups=[bad],issues=['x']),self.mapping(1693))

    def test_source_adjacent_windows_may_form_one_scene(self):
        from omni_story.library.material_browser import validate_continuity_groups
        out=validate_continuity_groups(dict(groups=[self.group()],issues=[]),self.mapping(1693))
        self.assertEqual(len(out['groups'][0]['segments']),2)

    def test_group_across_unseen_source_gap_is_rejected(self):
        from omni_story.library.material_browser import validate_continuity_groups
        with self.assertRaisesRegex(ValueError,'not adjacent in the source'):
            validate_continuity_groups(dict(groups=[self.group()],issues=[]),self.mapping(1738))


class BandFallbackTests(unittest.TestCase):
    def browser(self, tmp, readable_first):
        from omni_story.library.material_browser import MaterialBrowser
        calls=[]
        def call(stage,prompt,validator,media):
            calls.append((stage,media))
            readable=readable_first and stage.startswith('material_scene_browser_blind_')
            return validator(dict(visible_facts=['画面'],interaction=[],uncertainties=[],occlusion_limits=[],
                                  readable_dialogue_text=readable))
        b=MaterialBrowser(SimpleNamespace(root=Path(tmp),call=call))
        b.goal=lambda:{}  # watching is question-led since d9bca80a9598; this fixture has no route
        plain=dict(proxy=dict(path='plain.mp4',sha256='p'*64,source_start_s=0,source_end_s=17),
                   source_masks=[dict(x=.3,y=.82,w=.4,h=.12)],source_sha256=SHA,subtitle_band=None)
        b.proxy=lambda wid:b.proxies.setdefault(wid,plain)
        b.banded_proxy=lambda wid,band:dict(plain,proxy=dict(plain['proxy'],path='band.mp4',sha256='b'*64),
            source_masks=plain['source_masks']+[dict(band['region'])],subtitle_band=band)
        return b,calls

    def band_env(self, tmp):
        (Path(tmp)/f'{SHA}.json').write_text(json.dumps(dict(policy=SUBTITLE_BAND_POLICY,source_sha256=SHA,
            region=dict(x=.08,y=.75,w=.84,h=.22))))
        return patch.dict(os.environ,{'OMNI_SUBTITLE_BAND_DIR':tmp})

    def test_clean_window_keeps_narrow_masks_and_one_watch(self):
        with tempfile.TemporaryDirectory() as tmp, self.band_env(tmp):
            b,calls=self.browser(tmp,readable_first=False)
            seen=b.watch('vl_0123456789abcdef')
            self.assertEqual([c[0] for c in calls],['material_scene_browser_blind_vl_0123456789abcdef'])
            self.assertTrue(seen['subtitle_blind']);self.assertIsNone(b.proxies['vl_0123456789abcdef']['subtitle_band'])

    def test_leaking_window_is_rewatched_with_band_and_later_steps_use_it(self):
        with tempfile.TemporaryDirectory() as tmp, self.band_env(tmp):
            b,calls=self.browser(tmp,readable_first=True)
            seen=b.watch('vl_0123456789abcdef')
            self.assertEqual([c[0] for c in calls],['material_scene_browser_blind_vl_0123456789abcdef',
                                                    'material_scene_browser_blindband_vl_0123456789abcdef'])
            self.assertEqual(calls[1][1],'band.mp4')
            self.assertTrue(seen['subtitle_blind']);self.assertEqual(seen['evidence_sha256'],'b'*64)
            self.assertTrue(seen['subtitle_band_fallback']['first_observation']['readable_dialogue_text'])
            self.assertEqual(b.proxies['vl_0123456789abcdef']['proxy']['path'],'band.mp4')

    def test_without_recorded_band_leak_stays_visible_as_before(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{},clear=True):
            b,calls=self.browser(tmp,readable_first=True)
            seen=b.watch('vl_0123456789abcdef')
            self.assertEqual(len(calls),1);self.assertFalse(seen['subtitle_blind'])


def test_actual_mcp_guard_accepts_blindband_and_still_rejects_unknown(tmp_path):
    import base64,hashlib,subprocess
    from omni_story.library import material_browser,prompts
    from omni_story.library.state import LibraryState
    provider='official_vision_mcp_in_opencode'
    config=dict(provider=provider,transport='opencode_cli_official_mcp_v1',progress_policy='finite_stages_no_retry_v1',
        vision_model='glm-5.3-flash',agent_model='zhipuai-coding-plan/glm-5.3-flash',workflow='reference_rough_skill_v1',
        material_flow='scene_v1',creative_audio=True,creative_style='picture_aware',visual_library_root=str(tmp_path/'library'))
    guard=Path(material_browser.__file__).with_name('mcp_opencode_guard.mjs')
    script="""
import fs from 'node:fs';
const {opencodeRequestLimit}=await import(process.argv[1]);
const root=process.argv[2],p=JSON.parse(fs.readFileSync(root+'/guard_payload.json','utf8'));
try {console.log(String(opencodeRequestLimit(root,p.job,p.body)));}
catch(e) {console.log(e.message);}
"""
    for stage,expected in (('material_scene_browser_blindband_vl_0123456789abcdef','Infinity'),
                           ('material_scene_browser_blindwide_vl_0123456789abcdef','library_mcp_opencode_stage_duplicate_or_not_permitted')):
        root=tmp_path/stage;state=LibraryState(root,dict(configuration=dict(config,guard_fixture=stage)),max_requests=None)
        media=root/'clip.mp4';media.write_bytes(b'exact official transport fixture bytes')
        prompt='Observe muted footage.'
        request=dict(tool='analyze_video',arguments=dict(video_source=str(media),prompt=prompt),
            media_sha256=hashlib.sha256(media.read_bytes()).hexdigest(),provider=provider,policy_version=prompts.POLICY_VERSION)
        call,_=state.begin_call(stage,request)
        body=dict(model='glm-5.3-flash',stream=False,messages=[dict(role='user',content=[
            dict(type='video_url',video_url=dict(url='data:video/mp4;base64,'+base64.b64encode(media.read_bytes()).decode())),
            dict(type='text',text=prompt)])])
        (root/'guard_payload.json').write_text(json.dumps(dict(job=dict(job_id=call['id']),body=body)))
        result=subprocess.run(['node','--input-type=module','-e',script,guard.as_uri(),str(root)],capture_output=True,text=True,timeout=15)
        assert result.returncode==0,result.stderr
        assert result.stdout.strip()==expected,(stage,result.stdout)


if __name__=='__main__':unittest.main()
