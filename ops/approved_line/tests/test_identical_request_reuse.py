import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from omni_story.library.pipeline import _identical_received_reply
from omni_story.library.state import json_sha


def ledger(root, prompt, media, text, status='received'):
    folder = Path(root) / 'calls' / 'glm_004_material_scene_discovery_identitypair_4'
    folder.mkdir(parents=True)
    request = dict(tool='analyze_image', arguments=dict(prompt=prompt, image_source='pair_4.jpg'), media_sha256=media)
    response = dict(status='complete', result=dict(content=[dict(type='text', text=text)]))
    (folder / 'request.json').write_text(json.dumps(request))
    (folder / 'response.json').write_text(json.dumps(response))
    call = dict(id=folder.name, name='material_scene_discovery_identitypair_4', status=status,
                request_sha256=json_sha(request), response_sha256=json_sha(response))
    artifacts = {}
    return SimpleNamespace(output=str(root), data=dict(calls=[call]), _reload=lambda: None,
                           input_lock=dict(configuration={}), set_artifact=lambda k, v: artifacts.__setitem__(k, v),
                           artifacts=artifacts)


ANSWER = '{"appearance_a":"a","appearance_b":"b","same_person":true,"evidence":"e"}'


class IdenticalRequestReuse(unittest.TestCase):
    def test_same_prompt_and_media_reuses_received_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = ledger(tmp, 'compare', 'm1', ANSWER)
            value = _identical_received_reply(state, 'material_scene_discovery_identitypair_11', 'compare', 'm1', lambda v: v)
            self.assertTrue(value['same_person'])
            self.assertEqual(state.artifacts['material_identical_request_reuse']['call_id'],
                             'glm_004_material_scene_discovery_identitypair_4')

    def test_different_media_or_prompt_or_failed_call_is_not_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = ledger(tmp, 'compare', 'm1', ANSWER)
            self.assertIsNone(_identical_received_reply(state, 'x', 'compare', 'm2', lambda v: v))
            self.assertIsNone(_identical_received_reply(state, 'x', 'other prompt', 'm1', lambda v: v))
        with tempfile.TemporaryDirectory() as tmp:
            state = ledger(tmp, 'compare', 'm1', ANSWER, status='failed_known')
            self.assertIsNone(_identical_received_reply(state, 'x', 'compare', 'm1', lambda v: v))

    def test_answer_failing_current_validator_is_not_reused(self):
        def strict(v):
            raise ValueError('new rule')
        with tempfile.TemporaryDirectory() as tmp:
            state = ledger(tmp, 'compare', 'm1', ANSWER)
            self.assertIsNone(_identical_received_reply(state, 'x', 'compare', 'm1', strict))


class IdenticalRequestRepairedAnswer(unittest.TestCase):
    def test_accepted_repair_of_the_identical_request_is_reused(self):
        # 21 (80c0227b86bf): entities_3's first reply failed validation, entities_3_repair was
        # accepted; a later identical request must reuse the repair, not be resent.
        with tempfile.TemporaryDirectory() as tmp:
            state = ledger(tmp, 'compare', 'm1', '{"broken": true}')
            first = state.data['calls'][0]
            folder = Path(tmp) / 'calls' / 'glm_005_material_scene_discovery_identitypair_4_repair'
            folder.mkdir(parents=True)
            request = dict(tool='analyze_image', arguments=dict(prompt='compare\n上次输出未通过本地协议校验。fix'),
                           media_sha256='m1')
            response = dict(status='complete', result=dict(content=[dict(type='text', text=ANSWER)]))
            (folder / 'request.json').write_text(json.dumps(request))
            (folder / 'response.json').write_text(json.dumps(response))
            state.data['calls'].append(dict(id=folder.name, name=first['name'] + '_repair', status='received',
                                            request_sha256=json_sha(request), response_sha256=json_sha(response)))
            def needs_evidence(v):
                if 'evidence' not in v:
                    raise ValueError('missing evidence')
                return v
            value = _identical_received_reply(state, 'material_scene_discovery_identitypair_9', 'compare', 'm1', needs_evidence)
            self.assertTrue(value['same_person'])
            reuse = state.artifacts['material_identical_request_reuse']
            self.assertEqual(reuse['call_id'], folder.name)
            self.assertEqual(reuse['identical_call_id'], first['id'])


if __name__ == '__main__':
    unittest.main()
