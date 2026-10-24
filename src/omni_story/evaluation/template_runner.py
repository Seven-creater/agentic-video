"""First template experiment: sparse visual understanding, independent AV judge.

This evaluates understanding only. It does not claim to generate a film or to
measure audio understanding from sampled images.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

from ..library.audio_review import review_audio_video
from ..library.contracts import parse_model_json
from ..library.media import sha256_file
from ..library.state import file_lock, json_sha, write_json
from .template_metrics import validate_judgment, compile_packet_judgment
from . import template_protocol as protocol

UNDERSTAND = '''只根据按视频分组、按时间顺序排列的稀疏画面，总结这些例子共同的可迁移表达结构。
输入只有抽帧，没有声音，不能声称听到音乐或对白，也不能把不可见动作说成已观察到。
不知道的内容明确记录。不要从文件名或外部剧情补造答案。
只返回JSON：{"mechanism":"共同机制","events":["事件或句式节点"],
"claims":[{"id":"c1","text":"可核验的具体主张"}],"limitations":["观察限制"]}。
最多20个主张；不要复述每个画面代替模板归纳。'''

JUDGE_V1 = '''你是独立模板理解评审。候选方法名称和生成时自评均不可见。
根据实际视频、冻结的要素表及候选的主张评分。视频是多个独立未见实例顺次拼接，边界见映射。
评分对象是候选是否正确理解了模板，不是原视频拍得好不好。输入端仅提供稀疏画面、没有音频：
不得把“未知音频”当作伪造；但预先要求的声音理解项仍应如实记为未体现，不能事后删除。
每个冻结要素必须给0/1/2整数分：缺失或错误/部分理解/明确理解。每个非零分必须引用
实际视频的asset_id、原视频自身start_s/end_s及observation；禁止引用拼接视频的累计时间。
order必须覆盖每个预定义先后关系（键为before>after），遗漏事件不能算顺序正确。
claims必须逐个核对候选claim id，取supported/unsupported/unknown；无法确认填unknown。
仅输出JSON：{"elements":{"元素ID":{"score":0,"evidence":[]}},
"order":{"前ID>后ID":false},"claims":{"c1":"unknown"},"limitations":[]}。'''

JUDGE = '''你是独立模板理解评审，评分对象是候选文字对模板的理解，而不是视频本身。
只根据候选明确表达的机制、事件和关系评分；不能因为视频含有某个要素就给没有表达它的候选加分。
视频由多个未见实例顺序拼接，映射列出各实例asset_id和其在拼接视频中的起止时间。
所有start_s/end_s直接使用你看到的拼接视频累计秒数，不要自行减去片段起点；程序负责换算。
每个证据区间必须位于所选asset_id的packet_start_s/packet_end_s之间；不要跨越两个实例。
引用整段时使用映射中的精确边界，不把结束时间向上取整。
顶层time_base固定为packet_seconds。每个冻结要素给0/1/2整数分：缺失或错误/部分理解/明确理解。
非零分附实际画面或字幕证据，包含asset_id、start_s、end_s、observation。
order覆盖所有预定义关系（before>after）；候选遗漏事件时不能算顺序正确。
claims逐个核对，取supported/unsupported/unknown。候选对support示例中特定人物、道具或场景的描述，
无法从query验证就记unknown，不能仅因query换了人物就判unsupported。
只有候选明确声称通用结构，而实际query与它矛盾时才判unsupported。
理解器只看到稀疏画面、没有声音，不能把其未描述声音当作伪造，也不能默认它听到了对白。
仅返回JSON：{"time_base":"packet_seconds","elements":{"元素ID":{"score":0,"evidence":[]}},
"order":{"前ID>后ID":false},"claims":{"c1":"unknown"},"limitations":[]}。'''


def _case(args):
    manifest = protocol.load(args.manifest)
    if args.method not in manifest['methods']:
        raise ValueError('template_evaluation_unplanned_method')
    template = next((t for t in manifest['templates'] if t['template_id'] == args.template_id), None)
    if template is None:
        raise ValueError('template_evaluation_unplanned_template')
    if not os.environ.get('HUNTER_RECEIPTS_DIR') or not os.environ.get('OMNI_EVAL_BUDGET_FILE'):
        raise ValueError('template_evaluation_requires_server_runtime_and_shared_budget')
    return manifest, template


def _claims(value):
    claims = value.get('claims')
    if (not isinstance(value.get('mechanism'), str) or not value['mechanism'].strip() or
            not isinstance(value.get('events'), list) or not value['events'] or
            not isinstance(value.get('limitations'), list) or not isinstance(claims, list) or
            not 1 <= len(claims) <= 20):
        raise ValueError('template_understanding_contract_invalid')
    ids = []
    for claim in claims:
        ids.append(protocol.safe_id(claim['id']))
        if not isinstance(claim.get('text'), str) or not claim['text'].strip():
            raise ValueError('template_understanding_claim_text_required')
    if len(set(ids)) != len(ids):
        raise ValueError('template_understanding_duplicate_claim_ids')
    return ids


def understand(args):
    from hunter import glm
    manifest, template = _case(args)
    inputs = protocol.learner_inputs(template, args.method)
    identity = dict(protocol_sha256=manifest['protocol_sha256'], template_id=args.template_id,
                    method=args.method, model='glm-5.3-flash', input_scope='sampled_visual_no_audio',
                    support=[a['sha256'] for a in inputs], prompt_sha256=json_sha(UNDERSTAND))
    args.output.mkdir(parents=True, exist_ok=True)
    with file_lock(args.output / '.understand.lock'):
        binding = args.output / 'understanding_input.json'
        if binding.exists() and protocol.read(binding) != identity:
            raise ValueError('template_understanding_output_already_bound')
        write_json(binding, identity)
        frames, groups = [], []
        for asset in inputs:
            found = glm.extract_frames(Path(asset['path']), n=6,
                                       workdir=args.output / 'frames' / asset['sha256'])
            if not found:
                raise ValueError('template_understanding_frame_extraction_failed')
            groups.append(dict(asset_id=asset['asset_id'], frame_start=len(frames), frame_count=len(found)))
            frames.extend(found)
        text = glm.ask('glm-5.3-flash', UNDERSTAND + '\n' + json.dumps(groups), frames, max_tokens=8192)
        value = parse_model_json(text)
        _claims(value)
        prediction = dict(identity=identity, understanding=value)
        path = args.output / 'prediction.json'
        if path.exists() and protocol.read(path) != prediction:
            raise ValueError('template_understanding_received_prediction_changed')
        write_json(path, prediction)
        return dict(status='understood', prediction=str(path), input_scope=identity['input_scope'])


def _packet(template, folder, *, max_duration_s=180):
    inputs = [a for a in template['assets'] if a['asset_id'] in template['query']]
    if max_duration_s not in {180,240}:
        raise ValueError('template_judge_packet_unsupported_duration_policy')
    if sum(a['duration_s'] for a in inputs) > max_duration_s:
        raise ValueError('template_judge_packet_over_180_seconds_use_new_frozen_protocol')
    folder.mkdir(parents=True, exist_ok=True)
    path, receipt = folder / 'query.mp4', folder / 'packet.json'
    identity = dict(policy='template_query_packet_640x360_v1', inputs=inputs)
    if max_duration_s!=180:identity['max_duration_s']=max_duration_s
    if receipt.exists():
        saved = protocol.read(receipt)
        if saved['input'] != identity or sha256_file(path) != saved['sha256']:
            raise ValueError('template_judge_packet_changed')
        return path, saved
    if (folder / 'receipt').exists():
        raise ValueError('template_judge_receipt_present_do_not_rebuild_packet')
    mapping, segments, offset = [], [], 0.0
    for index, asset in enumerate(inputs):
        segment = folder / f'segment_{index:02d}.mp4'
        command = ['ffmpeg', '-nostdin', '-v', 'error', '-y', '-threads', '2', '-i', asset['path']]
        if not asset['has_audio_track']:
            command += ['-f', 'lavfi', '-i', 'anullsrc=r=24000:cl=mono']
        command += ['-map', '0:v:0', '-map', '0:a:0' if asset['has_audio_track'] else '1:a:0',
            '-t', str(asset['duration_s']), '-vf',
            'scale=640:360:force_original_aspect_ratio=decrease,pad=640:360:(ow-iw)/2:(oh-ih)/2,setsar=1',
            '-r', '15', '-c:v', 'libx264', '-preset', 'veryfast', '-b:v', '300k', '-threads', '2',
            '-c:a', 'aac', '-b:a', '48k', '-ar', '24000', '-ac', '1', str(segment)]
        subprocess.run(command, check=True, capture_output=True, timeout=600)
        from ..library.media import probe_media
        duration = probe_media(segment)['duration_s']
        mapping.append(dict(asset_id=asset['asset_id'], packet_start_s=offset,
                            packet_end_s=offset + duration, source_duration_s=asset['duration_s'],
                            original_has_audio_track=asset['has_audio_track']))
        offset += duration
        segments.append(segment)
    listing = folder / 'segments.txt'
    listing.write_text(''.join("file '" + p.name + "'\n" for p in segments))
    temporary = folder / 'building_query.mp4'
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-f', 'concat', '-safe', '1',
                    '-i', str(listing), '-c', 'copy', str(temporary)],
                   check=True, capture_output=True, timeout=120)
    temporary.replace(path)
    saved = dict(input=identity, mapping=mapping, sha256=sha256_file(path))
    write_json(receipt, saved)
    for segment in segments:
        segment.unlink()
    return path, saved


def judge(args):
    manifest, template = _case(args)
    prediction = protocol.read(args.prediction)
    identity = prediction['identity']
    if any(identity.get(k) != v for k, v in dict(protocol_sha256=manifest['protocol_sha256'],
            template_id=args.template_id, method=args.method, input_scope='sampled_visual_no_audio',
            support=[a['sha256'] for a in protocol.learner_inputs(template, args.method)],
            prompt_sha256=json_sha(UNDERSTAND)).items()):
        raise ValueError('template_judge_prediction_binding_changed')
    claims = _claims(prediction['understanding'])
    # All output destinations resolve to one paid operation directory. Changing
    # --output cannot replay an unresolved judge request.
    query_shas = {a['sha256'] for a in template['assets'] if a['asset_id'] in template['query']}
    key = json_sha(dict(query_sha256s=sorted(query_shas), candidate=prediction['understanding'],
                       rubric=template['rubric'], judge_prompt_sha256=json_sha(JUDGE)))
    root = Path(os.environ['HUNTER_RECEIPTS_DIR']) / 'independent_judge'
    directory = root / key
    directory.mkdir(parents=True, exist_ok=True)
    with file_lock(root / '.judge.lock', wait=True):
        for state_path in root.glob('*/receipt/state.json'):
            if protocol.read(state_path).get('status') in {'submitted', 'uncertain'}:
                old = protocol.read(state_path.parent.parent / 'packet.json')
                if query_shas & {a['sha256'] for a in old['input']['inputs']}:
                    raise RuntimeError('template_judge_unknown_query_media_no_replay')
        video, packet = _packet(template, directory)
        prompt = JUDGE + '\n' + json.dumps(dict(mapping=packet['mapping'], rubric=template['rubric'],
            candidate=prediction['understanding']), ensure_ascii=False)
        credentials = protocol.read(args.home / 'credentials.json')
        # Reuse the existing actual-audio/video client, transport receipts,
        # unknown guard and shared-budget reservation.
        previous = os.environ.get('DASHSCOPE_API_KEY')
        os.environ['DASHSCOPE_API_KEY'] = credentials['DASHSCOPE_API_KEY']
        try:
            receipt = review_audio_video(video, directory / 'receipt', prompt=prompt,
                                         allow_temporary_upload=True)
        finally:
            if previous is None:
                os.environ.pop('DASHSCOPE_API_KEY', None)
            else:
                os.environ['DASHSCOPE_API_KEY'] = previous
        raw = receipt['parsed'] or {}
        judgment = validate_judgment(template, compile_packet_judgment(raw, packet['mapping']), claims=claims)
        row = dict(protocol_sha256=manifest['protocol_sha256'], template_id=args.template_id,
            method=args.method, status='completed', input_scope='sampled_visual_no_audio',
            claim_ids=claims, judgment=judgment, prediction_sha256=sha256_file(args.prediction),
            receipt_path=str(directory / 'receipt/state.json'),
            receipt_sha256=sha256_file(directory / 'receipt/state.json'),
            response_sha256=sha256_file(directory / 'receipt/response.json'),
            evidence_transform='packet_seconds_to_source_v1', raw_judgment_sha256=json_sha(raw),
            packet_sha256=packet['sha256'], reviewer='independent_actual_audio_video_request')
        write_json(args.output / 'record.json', row)
        return row


def verify_record(row):
    """Offline report verification; never send a request to validate a score."""
    if row.get('judgment') is None:
        return
    path = Path(row['receipt_path'])
    state, request = protocol.read(path), protocol.read(path.with_name('request.json'))
    response_path = path.with_name('response.json')
    response = protocol.read(response_path)
    if (row.get('reviewer') != 'independent_actual_audio_video_request' or
            sha256_file(path) != row['receipt_sha256'] or state['status'] != 'received' or
            json_sha(request) != state['request_sha256'] or
            sha256_file(response_path) != state['response_sha256'] or
            state['response_sha256'] != row['response_sha256'] or response['http_status'] != 200 or
            state['video_sha256'] != row['packet_sha256'] or
            not any(request['prompt'].startswith(prompt + '\n') for prompt in (JUDGE, JUDGE_V1))):
        raise ValueError('template_report_model_receipt_binding_changed')
    parsed = json.loads(json.loads(response['body_text'])['choices'][0]['message']['content'])
    if row.get('evidence_transform') == 'packet_seconds_to_source_v1':
        if not request['prompt'].startswith(JUDGE + '\n') or json_sha(parsed) != row['raw_judgment_sha256']:
            raise ValueError('template_report_raw_judgment_changed')
        bound = json.loads(request['prompt'].rsplit('\n', 1)[1])
        parsed = compile_packet_judgment(parsed, bound['mapping'])
    elif not request['prompt'].startswith(JUDGE_V1 + '\n'):
        raise ValueError('template_report_time_transform_missing')
    if parsed != row['judgment']:
        raise ValueError('template_report_score_differs_from_received_response')
