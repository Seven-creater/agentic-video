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

CORE_LABEL_PROMPT='''你在准备核心主旨理解评测，尚未看到被测程序答案。观看实际reference和query声画。
theme_hypothesis只来自名称检索的待核验假设；与实际视频不符就明确否定，不靠片名或外部剧情补全。
逐模板独立归纳核心观点、价值取向、态度或情感；不要求都讲正面道理。
elements给3至5个原子主旨要素，关注主要矛盾、态度选择、表达含义；不要用谁做了什么或服装、道具、数字代替。
reference_core_supported表示reference是否完整、清楚地支持一个可迁移主旨；same_mechanism表示query是否表达同一主旨且立场一致。
reference前缀截断由prefix_cut_source_s预先固定，不按答案挑切点；reference_cut_packet_s返回该点在包内的累计时间。
prefix_information取explicit/implied/ambiguous，分别表示这个前缀已点题、可推断、不能唯一推断。
prefix_acceptable_themes列出仅据前缀合理的主旨推测，不能要求猜到不可见动作或唯一结局。
core_evidence逐项引用真实声画的传输包秒数及其如何支撑主旨，拒绝只有表层模仿而没有内容的样本。
编写同一文化节背景的8段、至少600汉字合成长故事，嵌入同主旨情节；同时有表层相同但价值相反、表层不同但主旨相同的对照，不能标记答案或使用原人物名。
story_target_indices只指向与主旨同向的连续段落，0起算。
bgm_question只标query中实际没有可辨识对白、能听到背景音乐的2至12秒区间；没有就null。不能把混合对白当纯BGM，歌曲演唱可说明为音乐。
音频只记录实际听到的内容，不能从画面推测音乐。所有时间用传输包累计秒数，不自行减偏移。
只返回JSON：{"reference_core_supported":true,"theme_summary":"主旨及态度","same_mechanism":true,
"elements":["抽象主旨要素"],"core_evidence":[{"element_index":0,"start_packet_s":0,"end_packet_s":1,"observation":"实际依据"}],
"reference_cut_packet_s":1,"prefix_information":"implied","prefix_acceptable_themes":["合理推测"],
"audio_kind":"speech_music","bgm_question":null,"story_paragraphs":["8段"],"story_target_indices":[2,3],"limitations":[]}。
bgm_question存在时为{"start_packet_s":0,"end_packet_s":1,"stage":"template_music_identity","ambiguous":false}。'''

ELEMENTS_PROMPT = '''按照人工要素吻合方法评估原创作模块的真实理解输出。视频就是该模块理解的同一条reference，绝不与其他版本的人物外观比对。
每个要素返回matched/partial/missing/contradicted。matched和partial必须逐字引用候选实际说出的内容；不能因视频存在某要素而替候选补写答案。
每个判断说明理由并引用原视频内证据时间；只按已提供的理解输入能力解释未知项。speech_mode是新创作策略，不等同于原视频有无对白。
只输出JSON：{"elements":[{"index":0,"status":"missing","quote":"","reason":"原因","evidence":[{"start_s":0,"end_s":1,"observation":"真实音画"}]}],"limitations":[]}。
必须覆盖每个预设要素。未写出的理解记missing，不捏造引用。'''

FORECAST_PROMPT = '''评估遮挡后续预测题。候选只看过视频前缀和另一条参考的原理解；你观看完整query核验隐藏后半段。
只匹配实际隐藏后续与候选预测，不因替换角色或场景扣分。按语义匹配，每个预测最多匹配一个答案事件，每个答案事件最多匹配一个预测。
返回0起算的索引配对及实际视频证据，不能补写候选没预测的内容。
只输出JSON：{"matches":[{"prediction_index":0,"gold_index":0,"start_s":0,"end_s":1,"observation":"实际隐藏后续"}],"limitations":[]}。'''

ELEMENT_IDS_PROMPT = '''评测原创作模块的参考理解，严格区分两个输入：
gold_rubric是待人工确认的要素标签，不是候选的回答；candidate_units才是被测模块实际输出的原句。
先看同一条真实reference核验标签：标签本身错误或不能确认时标为disputed，不把标签错误归罪于候选。
然后只评candidate_units是否表达了有效要素，返回候选原句ID，不抄写或改写引文，程序会从原输出提取。
不能因视频存在某要素就给未表达的候选加分；不确定的假设不算明确理解。speech_mode属于新创作策略，不等于参考的实际声音。
每个gold_id恰好返回一次。relation是matched/partial/missing/contradicted；有效标签的matched/partial/contradicted必须选择真实candidate_ids。
每个判断附原视频时间及实际音画观察。只输出JSON：
{"items":[{"gold_id":"g000","gold_validity":"valid","relation":"missing","candidate_ids":[],
"reason":"比较候选原句与标准的理由","evidence":[{"start_s":0,"end_s":1,"observation":"实际音画"}]}],"limitations":[]}。
gold_validity仅取valid/disputed；引用ID只能来自candidate_units，禁止把g开头的标准ID当作候选ID。'''

CORE_ELEMENT_PROMPT=ELEMENT_IDS_PROMPT+'''
本轮测核心主旨、价值取向和情感。每个模板有自己的要素，不统一套用某种道德标准。
换角色、行业、道具不扣分；候选有叙事细节也不扣分，但只有细节、没有表达对应含义不能算matched。
明确表达了同一态度选择或含义才完整命中；不能替候选把剧情自动升华为它没说出的主旨。
单纯笼统的“感人、搞笑、反转”不足以说明具体主旨；同一情节但价值相反属于contradicted。
这是对原输出的评测，不把搜索含义强加给不支持该主题的实际视频。'''

PREFIX_CORE_PROMPT=CORE_ELEMENT_PROMPT+'''
候选仅见到reference的真实前缀，没有整片理解或模板名称；它在推测整片主旨，而不是声称已看到后续。
结合visible_prefix_s、prefix_information及acceptable_prefix_themes判断推测的合理性。
信息不足时，合理多解和明确的不确定性不可判为编造；额外返回inference_validity，取supported/plausible/insufficient/contradicted。
其余items格式保持一致。主旨吻合与前缀信息充足程度分别记录，不按后续动作复刻打分。
最终JSON顶层必须同时有items、inference_validity、limitations：
{"items":[{"gold_id":"g000","gold_validity":"valid","relation":"missing","candidate_ids":[],
"reason":"候选与主旨的比较","evidence":[{"start_s":0,"end_s":1,"observation":"实际画面依据"}]}],
"inference_validity":"insufficient","limitations":[]}。
inference_validity不能省略；按实际证据选择，示例值不代表本题答案。'''

FORECAST_IDS_PROMPT = '''评估遮挡后续预测题，观看完整query，但只评价隐藏后续。
predictions中的p编号才是候选预测；gold_events中的g编号是待人工确认的实际后续标签，二者不能互换。
按语义一对一匹配，不因替换人物、场景扣分；不能用正确标签补写候选没有预测的内容。
只有候选预测、标签和实际隐藏音画三者吻合才返回配对，明确不匹配就不返回。
证据时间使用本次视频包的累计时间。跨越隐藏边界的事件由程序只取隐藏部分。
只输出JSON：{"matches":[{"prediction_id":"p000","gold_id":"g000","start_s":0,"end_s":1,"observation":"实际隐藏音画"}],"limitations":[]}。'''


def compile_forecast_ids(raw, answer, case, packet):
    if isinstance(raw,list):raw=dict(matches=raw)
    bound=next(x for x in packet['mapping'] if x['asset_id']==case['query']['asset_id'])
    predictions={f'p{i:03d}':i for i in range(len(answer['events']))}
    gold={f'g{i:03d}':i for i in range(len(case['labels']['future_events']))}
    matches=[]
    for row in raw['matches']:
        if row['prediction_id'] not in predictions or row['gold_id'] not in gold:
            raise ValueError('reference_exam_forecast_event_identity_invalid')
        start,end=row['start_s'],row['end_s']
        if not bound['packet_start_s']<=start<end<=bound['packet_end_s']:
            raise ValueError('reference_exam_forecast_packet_evidence_invalid')
        start=max(start-bound['packet_start_s'],case['labels']['cut_s'])
        end=min(end-bound['packet_start_s'],case['query']['duration_s'])
        if not start<end:
            raise ValueError('reference_exam_forecast_no_hidden_evidence')
        matches.append(dict(prediction_index=predictions[row['prediction_id']],gold_index=gold[row['gold_id']],
            start_s=start,end_s=end,observation=row['observation']))
    return dict(matches=matches,limitations=raw.get('limitations',[]))


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
        media, packet = _packet(template, root / 'media',max_duration_s=240 if prompt==CORE_LABEL_PROMPT else 180)
        request = dict(mapping=packet['mapping'], roles=role_ids, **data)
        # review_audio_video itself binds the prompt and media before payment.
        result = review_audio_video(media, root / 'receipt',
            prompt=prompt + '\n' + json.dumps(request, ensure_ascii=False), allow_temporary_upload=True)
        parsed=parse_received_review(result,prompt)
        return parsed, dict(path=str(root / 'receipt/state.json'),
            state_sha256=sha256_file(root / 'receipt/state.json'),
            response_sha256=sha256_file(root / 'receipt/response.json'),
            packet_sha256=packet['sha256'], packet=packet)


def parse_received_review(result,prompt):
    parsed=result['parsed']
    # The shared production reviewer intentionally exposes only object roots.
    # Read its preserved, received reply here; do not alter that frozen parser.
    if (prompt==FORECAST_IDS_PROMPT and parsed is None and
            result.get('parse_error')=='reply_json_object_required'):
        parsed=json.loads(result['reply'])
    valid=isinstance(parsed,dict) or (prompt==FORECAST_IDS_PROMPT and isinstance(parsed,list))
    if result['http_status']!=200 or not valid:
        raise ValueError('reference_exam_invalid_received_reply')
    return parsed


def prepare_labels(case, directory):
    assets = [case['reference'], case['query']]
    core=case.get('evaluation_target')=='core_meaning_v2'
    prompt=CORE_LABEL_PROMPT if core else LABEL_PROMPT
    raw, receipt = paid_review(assets, dict(reference=assets[0]['asset_id'], query=assets[1]['asset_id']),
                              prompt,dict(theme_hypothesis=case['curation']['theme_hypothesis'],
                                  prefix_cut_source_s=case.get('prefix_cut_s')) if core else {},directory)
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
    if core:
        labels.pop('forecast_preparation_error',None)
        reference=next(x for x in receipt['packet']['mapping'] if x['asset_id']==assets[0]['asset_id'])
        value=raw.get('reference_cut_packet_s')
        labels.update(theme_summary=raw.get('theme_summary'),core_evidence=raw.get('core_evidence',[]),
            reference_core_supported=raw.get('reference_core_supported'),prefix_information=raw.get('prefix_information'),
            prefix_acceptable_themes=raw.get('prefix_acceptable_themes',[]),
            prefix_cut_s=case.get('prefix_cut_s',value-reference['packet_start_s'] if type(value) in (int,float) else None))
        if labels['reference_core_supported'] is not True:
            labels['preparation_error']=dict(status='blocked_question',error='Actual reference did not verify a clear transferable core meaning.')
        if (type(labels['prefix_cut_s']) not in (int,float) or not 0<labels['prefix_cut_s']<assets[0]['duration_s'] or
                labels['prefix_information'] not in {'explicit','implied','ambiguous'} or not labels['prefix_acceptable_themes']):
            labels['forecast_preparation_error']='Reference prefix or reasonable core meaning alternatives invalid.'
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


def candidate_units(value):
    found=[]
    def walk(item,path):
        if isinstance(item,str) and item.strip():found.append(dict(path=path,text=item))
        elif isinstance(item,list):
            for index,child in enumerate(item):walk(child,path+'/'+str(index))
        elif isinstance(item,dict):
            for key in sorted(item):
                if not key.endswith('sha256'):walk(item[key],path+'/'+key)
    walk(value,'')
    return [dict(id=f'u{index:03d}',**row) for index,row in enumerate(found)]


def compile_element_ids(raw,understanding,count,*,require_inference=False):
    if require_inference and 'inference_validity' not in raw:
        raise ValueError('reference_exam_prefix_inference_status_required')
    units={u['id']:u for u in candidate_units(understanding)}
    items=raw.get('items',[])
    if len(items)!=count or {i['gold_id'] for i in items}!={f'g{i:03d}' for i in range(count)}:
        raise ValueError('reference_exam_all_gold_ids_required')
    rows=[]
    for item in items:
        ids=item['candidate_ids'];validity=item['gold_validity'];relation=item['relation']
        if (validity not in {'valid','disputed'} or relation not in {'matched','partial','missing','contradicted'} or
                not isinstance(ids,list) or len(set(ids))!=len(ids) or any(i not in units for i in ids)):
            raise ValueError('reference_exam_candidate_unit_identity_invalid')
        if validity=='valid' and relation!='missing' and not ids:
            raise ValueError('reference_exam_actual_candidate_unit_required')
        rows.append(dict(index=int(item['gold_id'][1:]),status=relation if validity=='valid' else 'label_disputed',
            quote=units[ids[0]]['text'] if ids else '',candidate_ids=ids,
            quotes=[units[i]['text'] for i in ids],reason=item['reason'],evidence=item['evidence']))
    value=dict(elements=rows,limitations=raw.get('limitations',[]),candidate_units_compiled=True)
    if 'inference_validity' in raw:
        if raw['inference_validity'] not in {'supported','plausible','insufficient','contradicted'}:
            raise ValueError('reference_exam_prefix_inference_status_invalid')
        value['inference_validity']=raw['inference_validity']
    return value


def validate_elements(raw, case, understanding):
    rows = raw['elements']
    if (not isinstance(rows, list) or len(rows) != len(case['labels']['elements']) or
            {r['index'] for r in rows} != set(range(len(rows)))):
        raise ValueError('reference_exam_missing_elements')
    for row in rows:
        allowed={'matched','partial','missing','contradicted'}
        if raw.get('candidate_units_compiled'):allowed.add('label_disputed')
        if row['status'] not in allowed:
            raise ValueError('reference_exam_element_status_invalid')
        if row['status'] in {'matched', 'partial'} and (not row['quote'] or
                not any(row['quote'] in text for text in strings(understanding))):
            raise ValueError('reference_exam_judge_invented_candidate_quote')
        if row['status'] in {'matched','partial','label_disputed'} and not row['evidence']:
            raise ValueError('reference_exam_actual_video_evidence_required')
        for evidence in row['evidence']:
            if not 0 <= evidence['start_s'] < evidence['end_s'] <= case['reference']['duration_s']:
                raise ValueError('reference_exam_element_evidence_outside_reference')
    statuses=['matched','partial','missing','contradicted']
    if raw.get('candidate_units_compiled'):statuses.append('label_disputed')
    counts = {status: sum(row['status'] == status for row in rows) for status in statuses}
    if not raw.get('candidate_units_compiled'):
        return dict(full_match_rate=counts['matched']/len(rows),counts=counts)
    valid=len(rows)-counts['label_disputed']
    return dict(full_match_rate=counts['matched']/valid if valid else None,
        full_match_rate_all_planned=counts['matched']/len(rows),label_verification_coverage=valid/len(rows),counts=counts)


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


def story_metrics(answer, manifest, case, *, compile_quotes=False):
    source=p.story_inputs(manifest,case['id']) if case.get('evaluation_target')=='core_meaning_v2' else p.story_text(manifest)
    paragraphs = {r['id']: r['text'] for r in source}
    chosen, quotes = answer['paragraph_ids'], answer.get('quotations',[])
    if (not isinstance(chosen, list) or not chosen or len(set(chosen)) != len(chosen) or
            any(i not in paragraphs for i in chosen)):
        raise ValueError('reference_exam_story_quote_or_identity_invalid')
    exact_quotes=len(quotes)==len(chosen) and all(q and q in paragraphs[i] for i,q in zip(chosen,quotes))
    if not compile_quotes and not exact_quotes:
        raise ValueError('reference_exam_story_quote_or_identity_invalid')
    gold = p.story_gold(manifest, case['id'])
    result=dict(hit_at_1=float(chosen[0] in gold), exact_range=float(set(chosen) == gold),
                paragraph_precision=len(set(chosen)&gold)/len(chosen),
                paragraph_recall=len(set(chosen)&gold)/len(gold))
    if compile_quotes:result['raw_quotation_exact']=float(exact_quotes)
    return result


def compiled_story_excerpt(answer,manifest):
    by_id={row['id']:row['text'] for row in p.story_text(manifest)}
    return [dict(paragraph_id=i,text=by_id[i]) for i in answer['paragraph_ids']]


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
