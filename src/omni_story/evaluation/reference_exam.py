"""Four meeting-derived exams. The judge never substitutes for the subject."""
from __future__ import annotations

import json
import os
from pathlib import Path

from ..library.audio_review import review_audio_video
from ..library.state import json_sha, write_json, file_lock
from ..library.media import sha256_file
from . import reference_protocol as p
from .template_runner import _packet

LABEL_PROMPT = '''你在准备测评题，尚未看到任何被测模型答案。观看实际音画，给出待人工确认的题目草案。
视频包包含reference与query，角色及累计秒数见mapping。判断它们是否具有相同的可迁移表达机制。
不要要求人物、场景或画风相同。elements只标reference的3至6个主旨、行动、变化、结果要素。
为query选择一个关键后续尚未发生的截断点cut_packet_s；future_events列出截断后实际发生的2至5个关键事件，按时间排序。
音乐判断必须听实际声音；纯BGM合法，不能因无对白扣分。标注音轨类型及query中一个有辨识度的音乐片段对应的剧情阶段；无对应关系写null。
为长篇合成故事写一个8段、总计至少600汉字的章节，背景统一为某城市筹备文化节；章节中间嵌入与reference机制对应的情节，并有两个表面相似但机制不同的干扰情节。
不要使用参考人物名、模板名或“目标/正确/干扰”等提示答案的文字。每段尽量完整，story_target_indices仅指真正对应的连续段落，0起算。
只输出JSON：{"same_mechanism":true,"elements":["要素"],"cut_packet_s":0,
"future_events":["实际后续事件"],"audio_kind":"music_only|speech_music|speech|silent|unknown",
"bgm_question":null,"story_paragraphs":["8段长故事"],"story_target_indices":[2,3],"limitations":[]}。
bgm_question若存在，结构为{"start_packet_s":0,"end_packet_s":1,"stage":"剧情阶段","ambiguous":false}。
所有时间直接使用传输包累计秒数，不自行换算。'''

ELEMENTS_PROMPT = '''按照人工要素吻合方法评估原创作模块的真实理解输出。视频就是该模块理解的同一条reference，绝不与其他版本的人物外观比对。
每个要素返回matched/partial/missing/contradicted。matched和partial必须逐字引用候选实际说出的内容；不能因视频存在某要素而替候选补写答案。
每个判断说明理由并引用原视频内证据时间；只按已提供的理解输入能力解释未知项。speech_mode是新创作策略，不等同于原视频有无对白。
只输出JSON：{"elements":[{"index":0,"status":"missing","quote":"","reason":"原因","evidence":[{"start_s":0,"end_s":1,"observation":"真实音画"}]}],"limitations":[]}。
必须覆盖每个预设要素。未写出的理解记missing，不捏造引用。'''

FORECAST_PROMPT = '''评估遮挡后续预测题。候选只看过视频前缀和另一条参考的原理解；你观看完整query核验隐藏后半段。
只匹配实际隐藏后续与候选预测，不因替换角色或场景扣分。按语义匹配，每个预测最多匹配一个答案事件，每个答案事件最多匹配一个预测。
返回0起算的索引配对及实际视频证据，不能补写候选没预测的内容。
只输出JSON：{"matches":[{"prediction_index":0,"gold_index":0,"start_s":0,"end_s":1,"observation":"实际隐藏后续"}],"limitations":[]}。'''


def paid_review(asset_list, role_ids, prompt, data, directory):
    """Original request/response receipts remain in the shared financial tree."""
    if not os.environ.get('OMNI_EVAL_BUDGET_FILE') or not os.environ.get('HUNTER_RECEIPTS_DIR'):
        raise ValueError('reference_exam_shared_budget_and_receipts_required')
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    template = dict(assets=asset_list, query=[a['asset_id'] for a in asset_list])
    registry_root = Path(os.environ['HUNTER_RECEIPTS_DIR'])
    registry_path = registry_root / 'production_exam_media_registry.json'
    sources = {a['sha256'] for a in asset_list}
    with file_lock(registry_path.with_suffix('.lock'), wait=True):
        registry = p.read(registry_path) if registry_path.exists() else {}
        for old in registry.values():
            state = Path(old['directory'])/'receipt/state.json'
            if (sources.intersection(old['sources']) and
                    ((state.exists() and p.read(state)['status'] in {'submitted','uncertain'}) or
                     (not state.exists() and old['directory']!=str(root.resolve())))):
                raise RuntimeError('reference_exam_unknown_source_no_replay')
        key = str(root.resolve())
        identity = dict(directory=key, sources=sorted(sources), prompt_sha256=json_sha(dict(prompt=prompt,data=data)))
        if key in registry and registry[key] != identity:
            raise ValueError('reference_exam_operation_changed')
        registry[key] = identity
        write_json(registry_path,registry)
    with file_lock(root / '.operation.lock', wait=True):
        media, packet = _packet(template, root / 'media')
        request = dict(mapping=packet['mapping'], roles=role_ids, **data)
        # review_audio_video itself binds the prompt and media before payment.
        result = review_audio_video(media, root / 'receipt',
            prompt=prompt + '\n' + json.dumps(request, ensure_ascii=False), allow_temporary_upload=True)
        if result['http_status'] != 200 or not isinstance(result['parsed'], dict):
            raise ValueError('reference_exam_invalid_received_reply')
        return result['parsed'], dict(path=str(root / 'receipt/state.json'),
            state_sha256=sha256_file(root / 'receipt/state.json'),
            response_sha256=sha256_file(root / 'receipt/response.json'),
            packet_sha256=packet['sha256'], packet=packet)


def prepare_labels(case, directory):
    assets = [case['reference'], case['query']]
    raw, receipt = paid_review(assets, dict(reference=assets[0]['asset_id'], query=assets[1]['asset_id']),
                              LABEL_PROMPT, {}, directory)
    bound = next(x for x in receipt['packet']['mapping'] if x['asset_id'] == assets[1]['asset_id'])
    cut_value=raw.get('cut_packet_s')
    cut = cut_value-bound['packet_start_s'] if type(cut_value) in (int,float) else None
    labels={k:raw.get(k,[]) for k in ('elements','future_events','story_paragraphs','story_target_indices','limitations')}
    labels.update(same_mechanism=raw.get('same_mechanism'),audio_kind=raw.get('audio_kind','unknown'))
    labels.update(cut_s=cut, provenance='model_assisted_unconfirmed', receipt=receipt)
    if not isinstance(labels['elements'],list) or not labels['elements'] or any(not isinstance(x,str) or not x.strip() for x in labels['elements']):
        labels.update(elements=[],elements_preparation_error='reference element labels missing or invalid')
    if (cut is None or not 0 < cut < assets[1]['duration_s'] or type(labels['same_mechanism']) is not bool or
            not isinstance(labels['future_events'],list) or not labels['future_events'] or any(not isinstance(x,str) for x in labels['future_events'])):
        labels['forecast_preparation_error']='query cut or future event labels invalid; no guessed time correction'
    paragraphs=labels['story_paragraphs'];targets=labels['story_target_indices']
    if (not isinstance(paragraphs,list) or len(paragraphs)<8 or
            any(not isinstance(x,str) for x in paragraphs) or sum(len(x) for x in paragraphs)<600 or
            not isinstance(targets,list) or not targets or any(type(i) is not int or not 0<=i<len(paragraphs) for i in targets)):
        labels['story_preparation_error']='story length or target range invalid'
    bgm = raw.get('bgm_question')
    if bgm is not None:
        try:
            bgm = normalize_bgm_question(bgm,bound)
        except (ValueError,KeyError,TypeError):
            bgm = None
            labels['bgm_preparation_error']='Out-of-bounds BGM interval retained in original response; only this question is rejected.'
    labels['bgm_question'] = bgm
    return labels


def normalize_bgm_question(value,bound):
    start,end=value['start_packet_s'],value['end_packet_s']
    if not bound['packet_start_s']<=start<end<=bound['packet_end_s']:
        raise ValueError('reference_exam_bgm_range_invalid')
    start-=bound['packet_start_s']
    end=min(end-bound['packet_start_s'],bound['source_duration_s'])
    if not 0<=start<end:
        raise ValueError('reference_exam_bgm_padding_only')
    return dict(start_s=start,end_s=end,stage=value['stage'],ambiguous=value['ambiguous'])


def strings(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [s for item in value for s in strings(item)]
    if isinstance(value, dict):
        return [s for item in value.values() for s in strings(item)]
    return []


def validate_elements(raw, case, understanding):
    rows = raw['elements']
    if (not isinstance(rows, list) or len(rows) != len(case['labels']['elements']) or
            {r['index'] for r in rows} != set(range(len(rows)))):
        raise ValueError('reference_exam_missing_elements')
    for row in rows:
        if row['status'] not in {'matched', 'partial', 'missing', 'contradicted'}:
            raise ValueError('reference_exam_element_status_invalid')
        if row['status'] in {'matched', 'partial'} and (not row['quote'] or
                not any(row['quote'] in text for text in strings(understanding))):
            raise ValueError('reference_exam_judge_invented_candidate_quote')
        if row['status'] in {'matched','partial'} and not row['evidence']:
            raise ValueError('reference_exam_actual_video_evidence_required')
        for evidence in row['evidence']:
            if not 0 <= evidence['start_s'] < evidence['end_s'] <= case['reference']['duration_s']:
                raise ValueError('reference_exam_element_evidence_outside_reference')
    counts = {status: sum(row['status'] == status for row in rows)
              for status in ('matched', 'partial', 'missing', 'contradicted')}
    return dict(full_match_rate=counts['matched'] / len(rows), counts=counts)


def forecast_metrics(raw, answer, case):
    pairs = [(r['prediction_index'], r['gold_index']) for r in raw['matches']]
    if len({a for a, _ in pairs}) != len(pairs) or len({b for _, b in pairs}) != len(pairs):
        raise ValueError('reference_exam_duplicate_event_match')
    for row in raw['matches']:
        if (type(row['prediction_index']) is not int or type(row['gold_index']) is not int or
                not 0 <= row['prediction_index'] < len(answer['events']) or
                not 0 <= row['gold_index'] < len(case['labels']['future_events']) or
                not case['labels']['cut_s'] <= row['start_s'] < row['end_s'] <= case['query']['duration_s']):
            raise ValueError('reference_exam_forecast_evidence_invalid')
    found, predicted, expected = len(pairs), len(answer['events']), len(case['labels']['future_events'])
    precision, recall = found / predicted if predicted else 0, found / expected
    by_gold = {gold: pred for pred, gold in pairs}
    edges = max(expected - 1, 0)
    correct = sum(i in by_gold and i+1 in by_gold and by_gold[i] < by_gold[i+1] for i in range(edges))
    return dict(precision=precision, recall=recall, event_f1=2*precision*recall/(precision+recall) if precision+recall else 0,
                order_accuracy=correct/edges if edges else None)


def story_metrics(answer, manifest, case):
    paragraphs = {r['id']: r['text'] for r in p.story_text(manifest)}
    chosen, quotes = answer['paragraph_ids'], answer['quotations']
    if (not isinstance(chosen, list) or not chosen or len(set(chosen)) != len(chosen) or
            len(chosen) != len(quotes) or any(i not in paragraphs for i in chosen) or
            any(not q or q not in paragraphs[i] for i, q in zip(chosen, quotes))):
        raise ValueError('reference_exam_story_quote_or_identity_invalid')
    gold = p.story_gold(manifest, case['id'])
    return dict(hit_at_1=float(chosen[0] in gold), exact_range=float(set(chosen) == gold),
                paragraph_precision=len(set(chosen)&gold)/len(chosen),
                paragraph_recall=len(set(chosen)&gold)/len(gold))


def ask_probe(question, directory, *, frames=()):
    # This is an exam answer, not a new reference understanding. The original
    # producer prompt and its validated output are never changed.
    from hunter import glm
    from ..library.contracts import parse_model_json
    if not os.environ.get('OMNI_EVAL_BUDGET_FILE') or not os.environ.get('HUNTER_RECEIPTS_DIR'):
        raise ValueError('reference_exam_shared_budget_and_receipts_required')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    identity = dict(question=question, image_sha256s=[sha256_file(f) for f in frames],
                    model='glm-5.3-flash', scope='exam_answer_not_reference_understanding')
    path = directory / 'answer.json'
    with file_lock(directory / '.answer.lock', wait=True):
        if path.exists():
            saved = p.read(path)
            if saved['identity'] != identity:
                raise ValueError('reference_exam_answer_binding_changed')
            verify_probe(saved)
            return saved['answer']
        prompt = '仅输出JSON。\n' + json.dumps(question, ensure_ascii=False)
        text = glm.ask('glm-5.3-flash', prompt,
                       list(frames), max_tokens=8192)
        answer = parse_model_json(text)
        receipts = []
        blocks = [dict(type='text',text=prompt), *[glm._image_block(f) for f in frames]]
        for state_path in Path(os.environ['HUNTER_RECEIPTS_DIR']).glob('*/library_state.json'):
            state = p.read(state_path)
            if state['input_lock']['request']['payload'].get('messages') != [dict(role='user',content=blocks)]:
                continue
            for call in state['calls']:
                if call['status']=='received':
                    folder=state_path.parent/'calls'/call['id']
                    receipts.append(dict(path=str(folder/'response.json'),sha256=sha256_file(folder/'response.json'),
                                         response_sha256=call['response_sha256']))
        if len(receipts)!=1:
            raise ValueError('reference_exam_unique_original_probe_receipt_required')
        saved=dict(identity=identity, answer=answer, receipt=receipts[0])
        verify_probe(saved)
        write_json(path, saved)
        return answer


def verify_probe(saved):
    from ..library.contracts import parse_model_json
    receipt=saved['receipt']; path=Path(receipt['path'])
    response=p.read(path)
    if sha256_file(path)!=receipt['sha256'] or json_sha(response)!=receipt['response_sha256']:
        raise ValueError('reference_exam_probe_receipt_changed')
    body=json.loads(response['body_text'])
    text='\n'.join(x['text'] for x in body.get('content',[]) if x.get('type')=='text')
    if response['http_status']!=200 or parse_model_json(text)!=saved['answer']:
        raise ValueError('reference_exam_answer_differs_from_received_response')
