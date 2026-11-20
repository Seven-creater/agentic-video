import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from omni_story.library.material_browser import MaterialBrowser
from omni_story.library.state import LibraryStopped, ProviderRefused


def browser(tmp):
    state = SimpleNamespace(output=Path(tmp) / 'task', set_artifact=lambda k, v: None)
    d = SimpleNamespace(root=Path(tmp), state=state)
    b = MaterialBrowser(d)
    b.catalog = [dict(window_id=w, source_sha256='s', start_s=str(i * 15), end_s=str(i * 15 + 17)) for i, w in enumerate(['a', 'b'])]
    b.by_id = {r['window_id']: r for r in b.catalog}
    return d, b


class ProviderRefusal(unittest.TestCase):
    def test_refused_window_is_skipped_and_kept_out_of_continuity(self):
        with tempfile.TemporaryDirectory() as tmp:
            d, b = browser(tmp)
            def _watch(w):
                if w == 'b':
                    raise LibraryStopped('provider_request_rejected:glm_040_material_scene_browser_blind_b:400:1210')
                b.visual[w] = dict(window_id=w); return b.visual[w]
            b._watch = _watch
            with mock.patch('omni_story.library.pipeline._material_ledgers', return_value=[]):
                self.assertIn('provider_refused', b.watch('b'))
                b.watch('a')
            self.assertIn('b', b.unwatchable)
            self.assertNotIn('b', b.visual)
            self.assertIsNone(MaterialBrowser.audit_continuity(b, ['b']))   # nothing watchable left to audit

    def test_other_failures_still_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            d, b = browser(tmp)
            b._watch = lambda w: (_ for _ in ()).throw(LibraryStopped('opencode_vision_job_failed_no_retry:glm_1'))
            with mock.patch('omni_story.library.pipeline._material_ledgers', return_value=[]):
                with self.assertRaises(LibraryStopped):
                    b.watch('a')

    def test_resume_does_not_resend_a_recorded_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            d, b = browser(tmp)
            parent = Path(tmp) / 'parent'
            ev = parent / 'calls/glm_040_x/agent'; ev.mkdir(parents=True)
            (ev / 'provider_rejection.json').write_text(json.dumps(dict(http_status=400, provider_code='1210', category='provider_request_rejected')))
            ledger = dict(calls=[dict(id='glm_040_x', name='material_scene_browser_blind_b', status='failed_known')])
            b._watch = lambda w: self.fail('a recorded refusal must not be sent again')
            with mock.patch('omni_story.library.pipeline._material_ledgers', return_value=[(parent, ledger)]):
                self.assertEqual(b.watch('b')['provider_refused']['provider_code'], '1210')

    def test_resume_drops_window_after_recorded_portrait_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, b = browser(tmp)
            parent = Path(tmp) / 'parent'
            ev = parent / 'calls/glm_020_portrait/agent'; ev.mkdir(parents=True)
            (ev / 'provider_rejection.json').write_text(json.dumps(dict(
                http_status=400, provider_code='1301', category='provider_content_rejected')))
            ledger = dict(calls=[dict(id='glm_020_portrait', name='material_scene_discovery_portraits_b',
                                      status='failed_known')])
            b._watch = lambda w: self.fail('a refused portrait window must not be sent again')
            with mock.patch('omni_story.library.pipeline._material_ledgers', return_value=[(parent, ledger)]):
                self.assertEqual(b.watch('b')['provider_refused']['provider_code'], '1301')

    def test_portrait_refusal_skips_the_second_request_for_the_same_window(self):
        from omni_story.library.autonomous_material import Discovery
        from omni_story.library.creative_portraits import observe_portraits

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            window = dict(window_id='b', source_sha256='s', source_start_s=1, source_end_s=3)
            refuse = mock.Mock(side_effect=ProviderRefused('portrait', 'glm_020', 'provider_content_rejected'))
            discovery = SimpleNamespace(root=root, observations={}, load_window=lambda wid:window,
                route=SimpleNamespace(sources={'s':{}}), inspection_proxy=lambda *args:{'path':'clip.mp4'},
                identity_window=lambda w,p,s:observe_portraits(w,p,s,root,refuse), state=mock.Mock())
            with mock.patch('omni_story.library.scene_coverage.detect_shots', return_value=[]):
                result = Discovery.inspect(discovery,'b')
                self.assertEqual(Discovery.inspect(discovery,'b'),result)
            self.assertEqual(refuse.call_count,1)
            self.assertEqual(result['shots'],[])
            self.assertIn('provider_content_rejected',discovery.observations['b']['provider_refused'])


def test_guard_continues_only_after_a_settled_portrait_content_refusal(tmp_path):
    import base64, hashlib, subprocess
    from omni_story.library import material_browser, prompts
    from omni_story.library.state import LibraryState

    provider = 'official_vision_mcp_in_opencode'
    config = dict(provider=provider, transport='opencode_cli_official_mcp_v1',
                  progress_policy='finite_stages_no_retry_v1', vision_model='glm-5.3-flash',
                  agent_model='zhipuai-coding-plan/glm-5.3-flash', workflow='reference_rough_skill_v1',
                  material_flow='scene_v1', creative_audio=True, creative_style='picture_aware',
                  visual_library_root=str(tmp_path/'library'))
    guard = Path(material_browser.__file__).with_name('mcp_opencode_guard.mjs')
    script = """
import fs from 'node:fs';
const {opencodeRequestLimit}=await import(process.argv[1]);
const root=process.argv[2],p=JSON.parse(fs.readFileSync(root+'/guard_payload.json','utf8'));
try { console.log(String(opencodeRequestLimit(root,p.job,p.body))); }
catch(e) { console.log(e.message); }
"""
    bad = 'library_mcp_opencode_current_job_or_previous_outcome_invalid'
    for label, event, expected in [('known','response','Infinity'),('unknown','unknown_result',bad),
                                   ('other','other_response',bad),('no_dispatch',None,bad)]:
        root = tmp_path / label
        state = LibraryState(root,dict(configuration=dict(config, guard_fixture=label)),max_requests=None)
        def request(name, contents):
            clip = root / (name + '.mp4'); clip.write_bytes(contents)
            return dict(tool='analyze_video', arguments=dict(video_source=str(clip),prompt=name),
                        media_sha256=hashlib.sha256(contents).hexdigest(),provider=provider,
                        policy_version=prompts.POLICY_VERSION)
        first,_ = state.begin_call('material_scene_discovery_portraits_vl_0123456789abcdef',request('first',b'first'))
        state.fail_call(first,'recorded failure',uncertain=False)
        records = []
        if event is not None:
            records = [dict(type='request',seq=1,job_id=first['id']),
                       dict(type='response' if event=='other_response' else event,seq=1,job_id=first['id'],
                            status=400,body=json.dumps({'error':{'code':'1210' if event=='other_response' else '1301'}}))]
        (root/'mcp_http.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in records))
        second,_ = state.begin_call('material_scene_discovery_portraits_vl_fedcba9876543210',request('second',b'second'))
        body = dict(model='glm-5.3-flash',stream=False,messages=[dict(role='user',content=[
            dict(type='video_url',video_url=dict(url='data:video/mp4;base64,'+base64.b64encode(b'second').decode())),
            dict(type='text',text='second')])])
        (root/'guard_payload.json').write_text(json.dumps(dict(job=dict(job_id=second['id']),body=body)))
        result = subprocess.run(['node','--input-type=module','-e',script,guard.as_uri(),str(root)],
                                capture_output=True,text=True,timeout=15,check=True)
        assert result.stdout.strip()==expected, (label,result.stdout)
        if label == 'known':
            saved_path = root/'library_state.json'
            saved = json.loads(saved_path.read_text())
            saved['max_requests'] = 20
            cfg = saved['input_lock']['configuration']
            cfg.update(independent_final_visual_v1=True,quick_preview=dict(output=str(root)))
            saved_path.write_text(json.dumps(saved))
            def run_guard():
                return subprocess.run(['node','--input-type=module','-e',script,guard.as_uri(),str(root)],
                                      capture_output=True,text=True,timeout=15,check=True).stdout.strip()
            assert run_guard() == '20'
            cfg['quick_preview']['output'] = str(root.parent)
            saved_path.write_text(json.dumps(saved))
            assert run_guard().endswith('configuration_or_budget_changed')



if __name__ == '__main__':
    unittest.main()
