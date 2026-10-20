"""Blind whole-video assessment, separate from the editor's internal reviews."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import subprocess
import uuid

from ..library.audio_review import review_audio_video
from ..library.hot_batch import _unknown
from ..library.media import probe_media, sha256_file
from ..library.state import json_sha, write_json
from .report import QUALITY_DIMENSIONS


RUBRIC = '''你是独立视频评审。只根据实际听到看到的参考与输出评分，不知道编辑方法，不提供剧本或内部自评分。
输入视频前段是参考，后段是待评作品，时间分界见下面JSON。参考为等比适配，参考周围补黑不属于作品缺陷；作品保留全画布。
不要以原电影剧情判断再创作错误，也不能用对白替画面补造关键物理动作。评价参考表达意图的迁移，不要求重现原人物、地点和故事。
对五维打1–5整数分：reference_intent参考主旨与形式；narrative_coherence人物身份/关系及叙事或非叙事结构；audio_visual_alignment真实声音与画面支撑；editing_rhythm剪辑节奏/重复/过渡；viewing_experience画幅/字幕/混音/可观看性。
统一锚点：1严重不成立，2明显缺失影响理解，3基本成立有可接受瑕疵，4表现清晰且稳定，5表达准确且有突出完成度。
只有实际观察到影响核心理解或观看的缺陷放severe_defects：如持续黑屏、不可懂的声音、错误主体身份、关键声画矛盾、字幕严重遮挡/重叠、结尾无关片尾。
每维必须提供实际声画证据，引用作品自身时间戳（减去参考时长），不能编造精确时间；无法确认须说明，不能推测通过。
每个维度evidence必须为非空数组，每项为{"start_s":0,"end_s":1,"observation":"实际作品中的声画证据"}，秒数必须是作品自身时间、落在作品时长内。不能确认时不要编造证据，说明limitations。
只输出JSON {"scores":{"reference_intent":1,"narrative_coherence":1,"audio_visual_alignment":1,"editing_rhythm":1,"viewing_experience":1},"severe_defects":[],"evidence":{"reference_intent":[{"start_s":0,"end_s":1,"observation":"..."}],"narrative_coherence":[],"audio_visual_alignment":[],"editing_rhythm":[],"viewing_experience":[]},"limitations":[]}。'''


def validate_assessment(value, *, duration=None):
    scores, evidence = value.get('scores'), value.get('evidence')
    if (not isinstance(scores, dict) or set(scores) != set(QUALITY_DIMENSIONS) or
            any(type(v) is not int or not 1 <= v <= 5 for v in scores.values()) or
            not isinstance(evidence, dict) or any(not isinstance(evidence.get(k), list) or
                not evidence[k] for k in QUALITY_DIMENSIONS)):
        raise ValueError('independent_judge_requires_five_scores_and_actual_evidence')
    for key in QUALITY_DIMENSIONS:
        for item in evidence[key]:
            if (not isinstance(item, dict) or any(type(item.get(k)) not in (int, float) or
                    not math.isfinite(item[k]) for k in ('start_s', 'end_s')) or
                    not 0 <= item['start_s'] < item['end_s'] or
                    (duration is not None and item['end_s'] > duration) or
                    not isinstance(item.get('observation'), str) or not item['observation'].strip()):
                raise ValueError('independent_judge_evidence_time_outside_actual_output')
    defects = value.get('severe_defects')
    if not isinstance(defects, list) or any(not isinstance(v, str) or not v.strip() for v in defects):
        raise ValueError('independent_judge_severe_defects_required')
    limitations = value.get('limitations')
    if not isinstance(limitations, list) or any(not isinstance(v, str) for v in limitations):
        raise ValueError('independent_judge_explicit_limitations_required')
    return dict(value, quality_pass=(not limitations and not defects and min(scores.values()) >= 3 and
                                    sum(scores.values())/len(scores) >= 3.5))


def load_judgment(path, reference_sha, video_sha):
    path = Path(path)
    value = json.loads(path.read_text())
    if (value['reference_sha256'] != reference_sha or value['video_sha256'] != video_sha or
            sha256_file(value['receipt_path']) != value['receipt_sha256'] or
            sha256_file(value['response_path']) != value['response_sha256'] or
            sha256_file(value['packet_path']) != value['packet_sha256']):
        raise ValueError('independent_judge_evidence_binding_changed')
    state = json.loads(Path(value['receipt_path']).read_text())
    response = json.loads(Path(value['response_path']).read_text())
    request = json.loads(Path(value['request_path']).read_text())
    binding = json.loads(Path(value['packet_path']).with_name('packet.json').read_text())
    if (state['status'] != 'received' or state['response_sha256'] != value['response_sha256'] or
            json_sha(request) != state['request_sha256'] or sha256_file(value['request_path']) != value['request_sha256'] or
            request['video_sha256'] != value['packet_sha256'] or
            binding['input'] != dict(reference_sha256=reference_sha, video_sha256=video_sha) or
            binding['sha256'] != value['packet_sha256'] or
            hashlib.sha256(request['prompt'].encode()).hexdigest() != request['prompt_sha256'] or
            request['prompt'] != value['prompt'] or value['rubric_sha256'] != json_sha(RUBRIC) or
            not request['prompt'].startswith(RUBRIC+'\n') or response['http_status'] != 200):
        raise ValueError('independent_judge_response_not_received')
    parsed = json.loads(json.loads(response['body_text'])['choices'][0]['message']['content'])
    assessment = validate_assessment(parsed, duration=binding['output_duration_s'])
    for key in ('scores', 'severe_defects', 'quality_pass', 'evidence'):
        if value[key] != assessment[key]:
            raise ValueError('independent_judge_assessment_changed')
    return value


def packet(reference, video, directory):
    directory.mkdir(parents=True, exist_ok=True)
    identity = dict(reference_sha256=sha256_file(reference), video_sha256=sha256_file(video))
    path, binding = directory/'reference_then_output.mp4', directory/'packet.json'
    ref, final = probe_media(reference), probe_media(video)
    has_audio = [any(s.get('codec_type') == 'audio' for s in item['streams']) for item in (ref, final)]
    output_stream = next(stream for stream in final['streams'] if stream['index'] == final['video_stream_index'])
    facts = dict(reference_duration_s=ref['duration_s'], output_duration_s=final['duration_s'],
                 reference_has_audio_track=has_audio[0], output_has_audio_track=has_audio[1],
                 output_canvas=dict(width=output_stream['width'], height=output_stream['height']))
    if binding.exists():
        saved = json.loads(binding.read_text())
        if (saved['input'] != identity or sha256_file(path) != saved['sha256'] or
                any(type(saved.get(key)) is not type(value) or saved[key] != value for key, value in facts.items())):
            raise ValueError('independent_judge_packet_changed')
        return path, saved
    if (directory/'receipt').exists():
        raise ValueError('independent_judge_unbound_packet_with_receipt_no_rebuild')
    if path.exists():
        path.rename(directory/('unbound_packet_'+uuid.uuid4().hex+'.mp4'))
    building = directory/('building_packet_'+uuid.uuid4().hex+'.mp4')
    args = ['ffmpeg', '-nostdin', '-v', 'error', '-n', '-i', str(reference), '-i', str(video)]
    audio_inputs, next_input = [], 2
    for index, item in enumerate((ref, final)):
        if has_audio[index]:
            audio_inputs.append(f'{index}:a:0')
        else:
            args += ['-f', 'lavfi', '-t', str(item['duration_s']), '-i', 'anullsrc=r=48000:cl=stereo']
            audio_inputs.append(f'{next_input}:a:0')
            next_input += 1
    filters = ('[0:v:0]scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,'
        'setsar=1,fps=30,setpts=PTS-STARTPTS[v0];'
        '[1:v:0]scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,'
        'setsar=1,fps=30,setpts=PTS-STARTPTS[v1];'
        f'[{audio_inputs[0]}]aresample=48000,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS[a0];'
        f'[{audio_inputs[1]}]aresample=48000,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS[a1];'
        '[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]')
    try:
        subprocess.run(args+['-filter_complex', filters, '-map', '[v]', '-map', '[a]', '-c:v', 'libx264',
            '-preset', 'fast', '-crf', '20', '-c:a', 'aac', '-b:a', '128k', str(building)],
            capture_output=True, check=True, timeout=900)
    except subprocess.CalledProcessError as error:
        write_json(directory/'packet_failure.json', dict(input=identity, partial_path=str(building),
            stderr=error.stderr.decode(errors='replace')[-4000:]))
        raise
    duration = probe_media(building)['duration_s']
    if abs(duration-ref['duration_s']-final['duration_s']) > .2:
        raise ValueError('independent_judge_packet_duration_changed')
    building.rename(path)
    saved = dict(input=identity, sha256=sha256_file(path), **facts,
                 canvas_policy='whole_canvas_aspect_fit_padding_no_crop; original_spec_checked_separately',
                 audio_policy='original_tracks_reencoded_aac; missing_audio_remains_transport_silence_no_generated_content')
    write_json(binding, saved)
    return path, saved


def evaluate(manifest, root, run_id, *, home):
    from .runner import environment, registered_experiment
    from ..library import server_jobs
    from ..library.hot_batch import unknown_guard_active
    root = Path(root).resolve()
    if home is None:
        raise ValueError('independent_judge_requires_registered_server_experiment')
    registered = registered_experiment(manifest, home, root)
    budget = json.loads((root/'request_budget.json').read_text())
    if (budget['experiment_id'] != manifest['experiment_id'] or
            budget['manifest_sha256'] != registered['manifest_sha256'] or
            budget['batch_job_id'] != registered['job_id'] or unknown_guard_active(home)):
        raise ValueError('independent_judge_budget_or_unknown_guard_binding_changed')
    run = next(row for row in manifest['planned_runs'] if row['run_id'] == run_id)
    case = next(row for row in manifest['cases'] if row['case_id'] == run['case_id'])
    folder = root/'runs'/run['case_id']/run['method_id']
    if server_jobs.status(folder)['state'] != 'succeeded':
        raise ValueError('independent_judge_requires_settled_successful_worker')
    if _unknown(root, terminal=True):
        raise RuntimeError('independent_judge_unsettled_experiment_no_new_request')
    result = json.loads((folder/'chain_result.json').read_text())
    from .collect import production_delivered
    artifact_kind = 'final' if production_delivered(result, run['method_id']) else 'candidate'
    artifact = result.get('final_video') or result.get('candidate_video')
    artifact_sha = result.get('final_sha256') or result.get('candidate_sha256')
    if not artifact or not artifact_sha:
        raise ValueError('independent_judge_no_bound_final_or_candidate')
    reference, video = Path(case['reference']['path']), Path(artifact)
    if sha256_file(reference) != case['reference']['sha256'] or sha256_file(video) != artifact_sha:
        raise ValueError('independent_judge_input_changed')
    directory = folder/'external_evaluation'
    target = directory/'judgment.json'
    if target.exists():
        return load_judgment(target, case['reference']['sha256'], artifact_sha)
    if home is None or manifest['budget']['max_cost_cny'] <= 0:
        raise ValueError('independent_judge_requires_server_credentials_and_nonzero_budget')
    media, provenance = packet(reference, video, directory)
    credentials = json.loads((Path(home)/'credentials.json').read_text())
    prompt = RUBRIC+'\n'+json.dumps(dict(reference_end_s=provenance['reference_duration_s'],
        output_duration_s=provenance['output_duration_s'], output_spec=manifest['output_spec'],
        output_original_canvas=provenance.get('output_canvas'),
        transport_canvas_policy='The whole work is aspect-fitted without cropping. Transport padding is not an original-work defect; evaluate padding within the original canvas separately.',
        output_has_audio_track=provenance.get('output_has_audio_track', True),
        missing_audio_policy='If false, the work has no audio track. Transport silence is not generated sound and does not establish audio quality.'), ensure_ascii=False)
    with environment(dict(DASHSCOPE_API_KEY=credentials['DASHSCOPE_API_KEY'],
            OMNI_EVAL_BUDGET_FILE=root/'request_budget.json', OMNI_EVAL_RUN_ID=run_id,
            OMNI_EVAL_REQUEST_SCOPE='independent_evaluation')):
        receipt = review_audio_video(media, directory/'receipt', prompt=prompt, allow_temporary_upload=True)
    assessment = validate_assessment(receipt['parsed'] or {}, duration=provenance['output_duration_s'])
    judgment = dict(**assessment, reference_sha256=case['reference']['sha256'], video_sha256=artifact_sha,
        artifact_kind=artifact_kind, evaluator_source_sha256=sha256_file(__file__),
        reviewer_model=json.loads((directory/'receipt/request.json').read_text())['configuration']['model'],
        shares_model_with_internal=True, independence_scope='separate_request_method_blind; not_independent_model_family',
        packet_path=str(media), packet_sha256=sha256_file(media), rubric_sha256=json_sha(RUBRIC), prompt=prompt,
        request_path=str(directory/'receipt/request.json'), request_sha256=sha256_file(directory/'receipt/request.json'),
        receipt_path=str(directory/'receipt/state.json'), receipt_sha256=sha256_file(directory/'receipt/state.json'),
        response_path=str(directory/'receipt/response.json'), response_sha256=sha256_file(directory/'receipt/response.json'))
    write_json(target, judgment)
    return judgment
