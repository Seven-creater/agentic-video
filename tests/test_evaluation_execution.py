"""Safety/meaning checks: real local processes and media, no paid requests."""
import json
import os
import threading
from pathlib import Path
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch

from omni_story.evaluation.budget import reserve_request, stop_new_requests
from omni_story.evaluation.judge import validate_assessment
from omni_story.evaluation.report import QUALITY_DIMENSIONS
from omni_story.evaluation.runner import model_declined_selection, model_plan_rejected, worker_command, reference_policy_refusal, reject_reference
from omni_story.evaluation.collect import production_delivered
from omni_story.library.state import LibraryState, LibraryStopped, file_lock, json_sha


class ExecutionTests(unittest.TestCase):
    def test_received_field_merge_preserves_old_facts_and_excludes_unbound_intervals(self):
        from copy import deepcopy
        from omni_story.library.reference_repair import merge_missing_fields
        original=dict(reference_sha256='a'*64,theme='original',visible_evidence=[
            dict(start_s=0,end_s=1,observed_fact='seen',supports='meaning')])
        repair=deepcopy(original);repair['theme']='rewritten'
        repair.update(strategy=dict(adaptation_mode='reinterpret',speech_mode='none',timing_driver='action'),
            speech_form='no speech',visual_form=dict(description='action',evidence=[
                dict(start_s=0,end_s=1,observed_fact='seen',unbound_role='invented'),
                dict(start_s=0,end_s=3,observed_fact='unsupported'),dict(start_s=False,end_s=1,observed_fact='seen')]))
        frozen=deepcopy(repair);merged,proof=merge_missing_fields(original,repair)
        self.assertEqual(merged['theme'],'original')
        self.assertEqual(merged['visible_evidence'],original['visible_evidence'])
        self.assertEqual(merged['visual_form']['evidence'],[dict(start_s=0,end_s=1,observed_fact='seen')])
        self.assertEqual(proof['excluded_unbound_visual_evidence'],repair['visual_form']['evidence'][1:])
        self.assertEqual(repair,frozen)

    def test_portrait_packet_keeps_original_audio_fact_and_rejects_modified_cache(self):
        import shutil, subprocess
        from omni_story.evaluation.judge import packet
        from omni_story.library.media import sha256_file
        if not shutil.which('ffmpeg'):self.skipTest('actual FFmpeg required')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);ref=root/'ref.mp4';video=root/'portrait.mp4';target=root/'assessment'
            for path,size in ((ref,'320x180'),(video,'90x160')):
                subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','color=c=blue:s='+size+':r=30',
                    '-t','0.4','-an','-c:v','libx264','-threads','1',str(path)],check=True,capture_output=True)
            original=sha256_file(video)
            target.mkdir();(target/'reference_then_output.mp4').write_bytes(b'')
            path,proof=packet(ref,video,target)
            self.assertTrue(path.stat().st_size>0)
            self.assertFalse(proof['output_has_audio_track'])
            self.assertEqual(proof['output_canvas'],dict(width=90,height=160))
            self.assertEqual(sha256_file(video),original)
            self.assertEqual(len(list(target.glob('unbound_packet_*.mp4'))),1)
            proof['output_has_audio_track']=True
            (target/'packet.json').write_text(json.dumps(proof))
            with self.assertRaisesRegex(ValueError,'packet_changed'):packet(ref,video,target)
            proof['output_has_audio_track']=False
            proof['output_canvas']['width']=999
            (target/'packet.json').write_text(json.dumps(proof))
            with self.assertRaisesRegex(ValueError,'packet_changed'):packet(ref,video,target)

    def test_collect_remeasures_cached_technical_facts_instead_of_trusting_booleans(self):
        import shutil, subprocess
        from omni_story.evaluation.collect import collect
        from omni_story.library.media import sha256_file
        if not shutil.which('ffmpeg'):self.skipTest('actual FFmpeg required')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);folder=root/'runs/c/full';folder.mkdir(parents=True)
            video=folder/'silent.mp4'
            subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','color=c=blue:s=1280x720:r=30',
                '-t','0.4','-an','-c:v','libx264','-threads','1',str(video)],check=True,capture_output=True)
            (folder/'.omni-server').mkdir();(folder/'.omni-server/job.json').write_text('{}')
            (folder/'chain_result.json').write_text(json.dumps(dict(candidate_video=str(video),candidate_sha256=sha256_file(video))))
            manifest=dict(planned_runs=[dict(run_id='exp/c/full',case_id='c',method_id='full')],
                cases=[dict(case_id='c',reference=dict(sha256='a'*64))],
                output_spec=dict(width=1280,height=720,fps=30,duration_min_s=.1,duration_max_s=10))
            with patch('omni_story.evaluation.collect.server_jobs.status',return_value=dict(state='succeeded')):
                self.assertFalse(collect(manifest,root)[0]['technical_complete'])
                original=(folder/'technical_check.json').read_text();proof=json.loads(original)
                proof['result']['checks']={key:True for key in proof['result']['checks']}
                proof['result']['technical_complete']=True
                (folder/'technical_check.json').write_text(json.dumps(proof))
                with self.assertRaisesRegex(ValueError,'technical_proof_modified'):collect(manifest,root)
                (folder/'technical_check.json').write_text(original)
                (folder/'chain_result.json').write_text(json.dumps(dict(status='needs_material_reselection',
                    final_video=str(video),final_sha256=sha256_file(video),content_delivery_ready=False)))
                evaluation=folder/'external_evaluation';evaluation.mkdir()
                (evaluation/'judgment.json').write_text('{}')
                receipt=evaluation/'receipt.json';receipt.write_text('{}')
                judgment=dict(artifact_kind='final',reviewer_model='fixture',shares_model_with_internal=True,
                    receipt_path=str(receipt),scores={k:5 for k in QUALITY_DIMENSIONS},severe_defects=[],quality_pass=True)
                with patch('omni_story.evaluation.judge.load_judgment',return_value=judgment):
                    row=collect(manifest,root)[0]
                self.assertEqual(row['quality_scope'],'candidate_full_video')
                self.assertFalse(row['production_delivered'])
                self.assertFalse(row['technical_complete'])

    def test_input_refusal_is_a_bound_zero_request_preflight_failure_for_the_other_method(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);origin=root/'baseline';other=root/'full'
            state=LibraryState(origin,dict(reference_sha256='a'*64))
            call,_=state.begin_call('reference',dict(tool='analyze_video',media_sha256='b'*64))
            state.fail_call(call,'SDK retry blocked',uncertain=False)
            (origin/'chain_failure.json').write_text(json.dumps(dict(type='LibraryStopped')))
            events=[dict(type='request',job_id=call['id'],seq=1),dict(type='response',job_id=call['id'],seq=1,
                status=400,body=json.dumps(dict(error=dict(code='1301',message='Input refused'),contentFilter=[dict(role='user')])))]
            journal=origin/'mcp_http.jsonl';journal.write_text(''.join(json.dumps(e)+'\n' for e in events))
            proof=reference_policy_refusal(origin,'a'*64)
            self.assertIsNotNone(proof)
            self.assertIsNone(reference_policy_refusal(origin,'c'*64))
            with self.assertRaises(SystemExit):reject_reference(other,proof)
            self.assertEqual(reference_policy_refusal(other,'a'*64),proof)
            self.assertFalse((other/'library_state.json').exists())
            self.assertFalse((other/'mcp_http.jsonl').exists())
            events[-1]['body']=json.dumps(dict(error=dict(code='1301',message='Output refused'),contentFilter=[dict(role='assistant')]))
            journal.write_text(''.join(json.dumps(e)+'\n' for e in events))
            self.assertIsNone(reference_policy_refusal(origin,'a'*64))
            output_proof=reference_policy_refusal(origin,'a'*64,role='assistant')
            self.assertIsNotNone(output_proof)
            with self.assertRaisesRegex(RuntimeError,'evidence_changed'):reject_reference(root/'not_an_input_refusal',output_proof)
            state._reload();state.data['calls'][0]['status']='uncertain';state._save()
            self.assertIsNone(reference_policy_refusal(origin,'a'*64))
            self.assertIsNone(reference_policy_refusal(other,'a'*64))

    def test_provider_http_error_is_not_hidden_by_the_sdk_retry_guard_message(self):
        from omni_story.library.opencode_provider import _diagnostic_error
        reply=dict(error='Network error: library_mcp_retry_or_budget_blocked')
        events=[dict(type='response',status=400,body=json.dumps(dict(error=dict(code='1301',message='Input refused'))))]
        self.assertEqual(_diagnostic_error(reply,events),'provider_http_400_code_1301:Input refused')
        self.assertEqual(_diagnostic_error(reply,[dict(type='response',status=None,body='bad')]),reply['error'])

    def test_received_plan_rejection_can_admit_peers_only_with_bound_complete_receipts(self):
        from omni_story.library.pipeline import CodexMCP, ModelProtocolRejected
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'run'
            state=LibraryState(root, {'reference': 'fixed'})
            media=root/'view.jpg';media.write_bytes(b'fixed model-input bytes')
            class ReceivedOnlyMCP(CodexMCP):
                def __init__(self):
                    self.state=state;self.output=root
                def _submit(self,name,request,*,repair_of=None):
                    call,_=state.begin_call(name,request,repair_of=repair_of)
                    response=dict(result=dict(content=[dict(type='text',text=json.dumps(dict(segments=[])))]))
                    state.complete_call(call,response);state._reload()
                    return next(c for c in state.data['calls'] if c['id']==call['id']),response
            def reject_plan(value):
                raise ValueError('plan/required_fields_missing')
            with self.assertRaises(ModelProtocolRejected) as caught:
                ReceivedOnlyMCP().call('plan_0','fixed prompt',media,reject_plan,image=True,
                    scope=dict(kind='complete_file',source_sha256='a'*64,source_start_s=0,source_end_s=1))
            calls=state.data['calls']
            self.assertEqual(len(calls),2)
            self.assertTrue(all(c['status']=='received' for c in calls))
            failure=dict(type=type(caught.exception).__name__,error=str(caught.exception))
            (root/'chain_failure.json').write_text(json.dumps(failure))
            self.assertTrue(model_plan_rejected(root))
            ledger=root/'library_state.json'
            calls[-1]['status']='uncertain';ledger.write_text(json.dumps(dict(calls=calls)))
            self.assertFalse(model_plan_rejected(root))
            calls[-1]['status']='received';ledger.write_text(json.dumps(dict(calls=calls)))
            (root/'chain_failure.json').write_text(json.dumps(dict(failure,type='ValueError')))
            self.assertFalse(model_plan_rejected(root))
            (root/'chain_failure.json').write_text(json.dumps(failure))
            (root/'calls'/calls[-1]['id']/'response.json').write_text(json.dumps(dict(result={})))
            self.assertFalse(model_plan_rejected(root))

    def test_received_malformed_plan_is_failed_without_blocking_peers_or_reposting(self):
        from omni_story.library.pipeline import CodexMCP, ModelProtocolRejected
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);state=LibraryState(root, {'reference':'fixed'})
            media=root/'view.jpg';media.write_bytes(b'bound bytes')
            class Replies(CodexMCP):
                def __init__(self):self.state=state;self.output=root
                def _submit(self,name,request,*,repair_of=None):
                    call,_=state.begin_call(name,request,repair_of=repair_of)
                    text='{} trailing' if repair_of is None else '{"segments":[]}'
                    reply=dict(result=dict(content=[dict(type='text',text=text)]))
                    state.complete_call(call,reply);state._reload()
                    return next(c for c in state.data['calls'] if c['id']==call['id']),reply
            with self.assertRaises(ModelProtocolRejected) as error:
                Replies().call('plan_1','bound prompt',media,
                    lambda value:(_ for _ in ()).throw(ValueError('plan:range_not_supported')),image=True)
            (root/'chain_failure.json').write_text(json.dumps(dict(type='ModelProtocolRejected',error=str(error.exception))))
            self.assertTrue(model_plan_rejected(root))
            proof=root/'calls/glm_001_plan_1/protocol_failure.json'
            value=json.loads(proof.read_text());value['error']='different error';proof.write_text(json.dumps(value))
            self.assertFalse(model_plan_rejected(root))
            self.assertEqual(state.data['request_count'],2)

    def test_received_empty_selection_fails_case_without_stopping_peers_but_unknown_still_stops(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proof = dict(round=0, requested_window_count=0, watched_window_count=0, new_observed_window_count=0)
            path = root/'proof.json';path.write_text(json.dumps(proof))
            (root/'chain_failure.json').write_text(json.dumps(dict(error='no_watched_window_available_for_planning')))
            (root/'search_0.json').write_text(json.dumps(dict(windows=[], observation_ids=[], retrieval_queries=[], slots=[])))
            state = dict(calls=[dict(status='received')], artifacts=dict(rough_no_observed_footage=[dict(path=str(path),sha256=json_sha(proof))]))
            (root/'library_state.json').write_text(json.dumps(state))
            self.assertTrue(model_declined_selection(root))
            state['calls'][0]['status'] = 'uncertain'
            (root/'library_state.json').write_text(json.dumps(state))
            self.assertFalse(model_declined_selection(root))
            state['calls'][0]['status'] = 'received'
            (root/'library_state.json').write_text(json.dumps(state))
            proof['watched_window_count'] = 1
            path.write_text(json.dumps(proof))
            self.assertFalse(model_declined_selection(root))

    def test_registry_contention_waits_without_weakening_task_or_duplicate_guards(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root/'registry.json'
            entered = threading.Event()
            def register():
                entered.set()
                return LibraryState(root/'run', {'reference': 'a'}, registry_path=registry)
            with ThreadPoolExecutor(max_workers=1) as pool:
                with file_lock(registry.with_suffix('.lock')):
                    future = pool.submit(register)
                    self.assertTrue(entered.wait(1))
                    self.assertFalse(future.done())
                state = future.result(timeout=3)
            self.assertEqual(state.data['request_count'], 0)
            with file_lock(state.lock_path):
                with self.assertRaisesRegex(LibraryStopped, 'library_state_is_locked'):
                    with file_lock(state.lock_path):
                        pass
            with self.assertRaisesRegex(LibraryStopped, 'same_inputs_already_registered'):
                LibraryState(root/'sibling', {'reference': 'a'}, registry_path=registry)

    def test_currency_reservation_reconciles_received_tts_but_keeps_unknown_exposure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            budget = root/'request_budget.json'
            run_id = 'exp/case_01/full'
            saved = dict(experiment_id='exp', max_model_calls=20, per_run_max_model_calls=20,
                generation_call_limit=20, deadline_unix=time.time()+30, requests=[],
                cost_control=dict(experiment_root=str(root), spend_envelope_cny=1.1, per_operation_reserve_cny=1))
            budget.write_text(json.dumps(saved))
            with patch.dict(os.environ, {'OMNI_EVAL_BUDGET_FILE': str(budget), 'OMNI_EVAL_RUN_ID': run_id}):
                reserve_request()
                binding = dict(payload=dict(input=dict(text='中文')))
                state = dict(schema_version='tts_line_v1', model='qwen3-tts-flash', status='received',
                    text='中文', request_count=1, binding=binding, request_sha256=json_sha(binding))
                folder = root/'runs/case_01/full/tts/first';folder.mkdir(parents=True)
                (folder/'state.json').write_text(json.dumps(state))
                reserve_request()  # First reservation reconciles to a small bound, not a slot refund.
                other = root/'runs/case_01/full/tts/second';other.mkdir(parents=True)
                (other/'state.json').write_text(json.dumps(dict(state, status='uncertain')))
                with self.assertRaisesRegex(RuntimeError, 'currency_envelope_exhausted'):
                    reserve_request()
            final = json.loads(budget.read_text())
            self.assertEqual(len(final['requests']), 2)
            self.assertEqual(final['cost_snapshot']['unresolved_reserved_operations'], 1)
            self.assertGreater(float(final['cost_snapshot']['upper_cny']), 1)

    def test_parallel_currency_reservations_cannot_oversubscribe_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            budget = Path(directory)/'budget.json'
            budget.write_text(json.dumps(dict(experiment_id='exp', max_model_calls=20,
                per_run_max_model_calls=20, generation_call_limit=20, deadline_unix=time.time()+30, requests=[],
                cost_control=dict(experiment_root=directory, spend_envelope_cny=3, per_operation_reserve_cny=1))))
            with patch.dict(os.environ, {'OMNI_EVAL_BUDGET_FILE':str(budget),'OMNI_EVAL_RUN_ID':'exp/case/full'}):
                def reserve(_):
                    try:
                        reserve_request()
                        return True
                    except RuntimeError:
                        return False
                with ThreadPoolExecutor(max_workers=5) as pool:
                    self.assertEqual(sum(pool.map(reserve, range(10))), 3)
            self.assertEqual(len(json.loads(budget.read_text())['requests']), 3)

    def test_production_rejected_candidate_cannot_be_delivered_and_baseline_has_its_own_gate(self):
        visual = dict(status='pass', final_sha256='a'*64)
        baseline = dict(status='rough_audio_ready', joint_quality_gate=False,
            content_delivery_ready=True, final_sha256='a'*64, final_visual_review=visual)
        self.assertTrue(production_delivered(baseline, 'baseline_single_pass'))
        self.assertFalse(production_delivered(dict(baseline, status='needs_visual_revision'), 'baseline_single_pass'))
        self.assertFalse(production_delivered(dict(baseline, final_sha256='b'*64), 'baseline_single_pass'))
        full = dict(baseline, status='rough_to_skill_completed', joint_quality_gate=True,
            finecut=dict(creative_audio=dict(status='delivered', content_delivery_ready=True,
                joint_quality_gate=True, technical_delivery_verified=True)))
        self.assertTrue(production_delivered(full, 'full'))
        for changes in (dict(status='needs_material_reselection'), dict(joint_quality_gate=False),
                        dict(content_delivery_ready=False)):
            self.assertFalse(production_delivered(dict(full, **changes), 'full'))

    def test_parallel_reservations_are_serialized_and_never_exceed_cap(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'budget.json'
            path.write_text(json.dumps(dict(max_model_calls=12, per_run_max_model_calls=12,
                generation_call_limit=12, deadline_unix=time.time()+30, requests=[])))
            with patch.dict(os.environ, {'OMNI_EVAL_BUDGET_FILE': str(path), 'OMNI_EVAL_RUN_ID': 'a'}):
                def reserve(_):
                    try:
                        reserve_request()
                        return True
                    except RuntimeError:
                        return False
                with ThreadPoolExecutor(max_workers=5) as pool:
                    results = list(pool.map(reserve, range(20)))
            self.assertEqual(sum(results), 12)
            rows = json.loads(path.read_text())['requests']
            self.assertEqual([r['sequence'] for r in rows], list(range(1, 13)))

    def test_stop_keeps_original_reservations_and_deadline_blocks_send(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'budget.json'
            path.write_text(json.dumps(dict(max_model_calls=4, per_run_max_model_calls=4,
                generation_call_limit=3, deadline_unix=time.time()+30, requests=[])))
            with patch.dict(os.environ, {'OMNI_EVAL_BUDGET_FILE': str(path), 'OMNI_EVAL_RUN_ID': 'a'}):
                reserve_request()
                stop_new_requests(path, 'uncertain_child')
                with self.assertRaisesRegex(RuntimeError, 'stopped_before_request'):
                    reserve_request()
            saved = json.loads(path.read_text())
            self.assertEqual(len(saved['requests']), 1)
            saved.pop('stop_reason')
            saved['deadline_unix'] = time.time()-1
            path.write_text(json.dumps(saved))
            with patch.dict(os.environ, {'OMNI_EVAL_BUDGET_FILE': str(path), 'OMNI_EVAL_RUN_ID': 'a'}):
                with self.assertRaisesRegex(RuntimeError, 'stopped_before_request'):
                    reserve_request()

    def test_generation_reserve_does_not_consume_independent_evaluation_allowance(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'budget.json'
            path.write_text(json.dumps(dict(max_model_calls=2, per_run_max_model_calls=2,
                generation_call_limit=1, deadline_unix=time.time()+30, requests=[])))
            with patch.dict(os.environ, {'OMNI_EVAL_BUDGET_FILE': str(path), 'OMNI_EVAL_RUN_ID': 'a'}):
                reserve_request()
                with self.assertRaisesRegex(RuntimeError, 'reserved_for_independent_judge'):
                    reserve_request()
                with patch.dict(os.environ, {'OMNI_EVAL_REQUEST_SCOPE': 'independent_evaluation'}):
                    reserve_request()
            self.assertEqual(len(json.loads(path.read_text())['requests']), 2)

    def test_baseline_and_full_use_identical_inputs_and_only_differ_in_finecut(self):
        manifest = dict(cases=[dict(case_id='c1', reference=dict(path='/ref.mp4'))],
                        output_spec=dict(width=1280,height=720,fps=30),
                        library=dict(media_root='/movies', visual_root='/visual'))
        run = dict(case_id='c1', method_id='full')
        full = worker_command('/home', manifest, run, '/output', asr_model_dir='/local/asr')
        baseline = worker_command('/home', manifest, dict(run, method_id='baseline_single_pass'), '/output',
                                  asr_model_dir='/local/asr')
        self.assertEqual(baseline, full+['--stop-after-rough'])
        self.assertEqual(full[full.index('--asr-model-dir')+1], '/local/asr')
        self.assertEqual(json.loads(full[full.index('--output-spec')+1]),manifest['output_spec'])

    def test_judge_needs_actual_evidence_and_severe_errors_override_high_scores(self):
        assessment = dict(scores={k: 5 for k in QUALITY_DIMENSIONS},
                          evidence={k: [dict(start_s=0, end_s=1, observation='actual observed evidence')]
                                    for k in QUALITY_DIMENSIONS}, severe_defects=[], limitations=[])
        self.assertTrue(validate_assessment(assessment)['quality_pass'])
        self.assertFalse(validate_assessment(dict(assessment, severe_defects=['持续严重字幕遮挡']))['quality_pass'])
        with self.assertRaises(ValueError):
            validate_assessment(dict(assessment, evidence={}))


if __name__ == '__main__':
    unittest.main()
