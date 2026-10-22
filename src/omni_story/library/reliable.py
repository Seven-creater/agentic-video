"""Reference editing with executable clip IDs, durable stages and local fallback.

Quality judgments never grant engineering completion. Unknown paid operations
remain in their original ledgers; recovery only reuses received work or does
local work. Both capture and file inputs use this production execution path.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

import numpy as np

from .media import inventory_sources, prepare_window, probe_media, sha256_file
from .pipeline import _received_library_window, _http_evidence, _outcome_unknown
from .opencode_provider import OpenCodeMCP, PROVIDER
from .state import LibraryState, LibraryStopped, file_lock, json_sha, write_json
from .visual_library import _projection, _terms
from .render import render_library_video

POLICY = 'reliable_v1'
SPEC = dict(width=1280, height=720, fps=30, duration_min_s=10, duration_max_s=180)
# These examples are also the key/type contract consumed by validators.
REFERENCE_EXAMPLE = dict(theme='可迁移的表达目标', queries=['动作或人物互动'], speech_mode='none')
PLAN_EXAMPLE = dict(clip_ids=['清单中的完整clip_id'], speech=[])
REFERENCE_PROMPT = ('只依据实际参考画面概括可迁移的表达目标，提供2至5个可在异源电影库检索的具体动作、互动、情绪短语。'
    '不要要求原人物、场地或服饰复刻；不声称听见此静音代理的声音。只输出JSON，字段和类型严格如下，'
    'speech_mode可选none/narration/dialogue。无明确新配音需求选none。\n' +
    json.dumps(REFERENCE_EXAMPLE, ensure_ascii=False))


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def now():
    return datetime.now(timezone.utc).isoformat()


def code_sha():
    root = Path(__file__).parents[1]
    return json_sha({str(p.relative_to(root)): sha256_file(p) for p in sorted(root.rglob('*'))
                     if p.suffix in {'.py', '.mjs', '.md', '.json'} and '__pycache__' not in p.parts})


def validate_reference(value):
    if (not isinstance(value, dict) or set(value) != set(REFERENCE_EXAMPLE) or
            not isinstance(value['theme'], str) or not value['theme'].strip() or
            not isinstance(value['queries'], list) or not 1 <= len(value['queries']) <= 8 or
            any(not isinstance(q, str) or not q.strip() or len(q) > 100 for q in value['queries']) or
            value['speech_mode'] not in {'none', 'narration', 'dialogue'}):
        raise ValueError('reliable_reference_contract')


def validate_choice(value, candidates):
    ids = {c['clip_id'] for c in candidates}
    if (not isinstance(value, dict) or set(value) != set(PLAN_EXAMPLE) or
            not isinstance(value['clip_ids'], list) or not 1 <= len(value['clip_ids']) <= 32 or
            any(not isinstance(i, str) or i not in ids for i in value['clip_ids']) or
            len(value['clip_ids']) != len(set(value['clip_ids'])) or
            not isinstance(value['speech'], list) or len(value['speech']) > 3):
        raise ValueError('reliable_plan_choose_unique_supplied_clip_ids')
    spoken = set()
    for line in value['speech']:
        if (not isinstance(line, dict) or set(line) != {'clip_id', 'text'} or
                line['clip_id'] not in value['clip_ids'] or line['clip_id'] in spoken or
                not isinstance(line['text'], str) or not 0 < len(line['text'].strip()) <= 24):
            raise ValueError('reliable_speech_clip_and_short_text_required')
        spoken.add(line['clip_id'])


def overlap(scope, sha, start, end):
    return (scope.get('source_sha256') == sha and
            start < scope.get('source_end_s', math.inf) and scope.get('source_start_s', 0) < end)


def candidates_from_records(records, catalog, queries, visual_root, exclusions=()):
    """Search facts/conditional uses, then verify original received provenance."""
    sources = {s['sha256']: s for s in catalog['sources']}
    wanted = {t for t in _terms(queries) if len(t) > 1}
    ranked, rejected = [], []
    for path in sorted(Path(records).glob('*.json')):
        record = read(path)
        card, facts, uses = _projection(record)
        hits = wanted.intersection(set(facts) | set(uses))
        if not hits or card['source_sha256'] not in sources:
            continue
        ranked.append((len(hits), card['observation_id'], path, hits))
    ranked.sort(key=lambda r: (-r[0], r[1]))
    result = []
    for score, _, path, hits in ranked:
        record = read(path)
        card, _, _ = _projection(record)
        source = sources[record['window']['source_sha256']]
        try:
            window = _received_library_window(record, visual_root, source=source)
        except (ValueError, KeyError, OSError, LibraryStopped) as error:
            rejected.append(dict(record=str(path), error=str(error)))
            continue
        for i, usable in enumerate(window['observation']['usable_ranges']):
            start = window['source_start_s'] + usable['local_in_s']
            end = min(window['source_start_s'] + usable['local_out_s'], start + 8)
            if end - start < 1 or any(overlap(x.get('scope', x), source['sha256'], start, end) for x in exclusions):
                continue
            if any(c['source_id'] == source['source_id'] and c['source_in_s'] < end and
                   start < c['source_out_s'] for c in result):
                continue
            result.append(dict(clip_id=card['observation_id'] + '_' + str(i),
                source_id=source['source_id'], source_sha256=source['sha256'],
                source_path=source['path'],
                window_id=window['window_id'], source_in_s=start, source_out_s=end,
                summary=card['summary'], matched_terms=sorted(hits),
                match_score=score, evidence_path=str(path), evidence_sha256=sha256_file(path),
                evidence=record['evidence'], source_subtitle_masks=window.get('source_subtitle_masks', [])))
            if len(result) == 48:
                return result, rejected
    return result, rejected


def compile_choice(choice, candidates, target_s, spec=SPEC):
    validate_choice(choice, candidates)
    mapping = {c['clip_id']: c for c in candidates}
    ordered = [mapping[i] for i in choice['clip_ids']]
    quantized = lambda c: math.floor((c['source_out_s'] - c['source_in_s']) * spec['fps'] + 1e-7) / spec['fps']
    duration = sum(quantized(c) for c in ordered)
    extended = duration < spec['duration_min_s']
    for c in candidates:
        if duration >= spec['duration_min_s']:
            break
        if c['clip_id'] not in choice['clip_ids']:
            ordered.append(c)
            duration += quantized(c)
    if duration < spec['duration_min_s']:
        raise LibraryStopped('reliable_insufficient_verified_material')
    target = max(spec['duration_min_s'], min(target_s, duration, spec['duration_max_s']))
    segments, cursor = [], 0
    for c in ordered:
        frames = min(math.floor((c['source_out_s'] - c['source_in_s']) * spec['fps'] + 1e-7), round(target * spec['fps']) - cursor)
        if frames <= 0:
            break
        row = dict(segment_id='seg_' + str(len(segments)), clip_id=c['clip_id'],
            source_id=c['source_id'], window_id=c['window_id'], source_in_s=c['source_in_s'],
            source_out_s=c['source_in_s'] + frames / spec['fps'], speed=1, framing='fit',
            source_subtitle_masks=c['source_subtitle_masks'], output_in_s=cursor / spec['fps'],
            output_out_s=(cursor + frames) / spec['fps'], evidence_path=c['evidence_path'],
            evidence_sha256=c['evidence_sha256'], source_sha256=c['source_sha256'], source_path=c['source_path'])
        segments.append(row)
        cursor += frames
    return dict(segments=segments, audio_mode='silent', duration_s=cursor / spec['fps'],
                speech=choice['speech'], extended_to_minimum=extended)


class ReliableMCP(OpenCodeMCP):
    """Retry only complete, known transient failures; never ambiguous outcomes."""
    def received_media(self, name, prompt, source_sha256, duration_s):
        """Use the original proxy bytes: a fresh encode need not be identical."""
        from .opencode_provider import bound_media_scope
        selected = None
        for source in self.state.input_lock['configuration'].get('received_sources', []):
            ledger = Path(source['path'])
            if sha256_file(ledger) != source['sha256']:
                raise LibraryStopped('reliable_received_ledger_changed')
            for call in read(ledger)['calls']:
                request = read(ledger.parent / 'calls' / call['id'] / 'request.json')
                if json_sha(request) != call['request_sha256']:
                    raise LibraryStopped('reliable_received_request_changed')
                scope = bound_media_scope(request)
                if (scope['source_sha256'] == source_sha256 and scope['source_start_s'] == 0 and
                        abs(scope['source_end_s'] - duration_s) < 1e-6):
                    if 'provider_http_400_code_1301:' in call.get('error', ''):
                        raise LibraryStopped('reliable_provider_refused')
                    if call['status'] in {'submitted','uncertain'}:
                        raise LibraryStopped('reliable_parent_not_received_no_replay')
                    if (call['status'] == 'received' and call['name'] == name and
                            request['tool'] == 'analyze_video' and request.get('provider') == self.provider and
                            request.get('policy_version') == self.state.data['policy_version'] and
                            request['arguments']['prompt'] == prompt):
                        selected = Path(request['arguments']['video_source'])
        return selected

    def _received(self, name, request, validator):
        """Reuse exact, hash-bound responses across immutable release versions."""
        from .contracts import parse_model_json
        from .opencode_provider import bound_media_scope, _matching_request
        scope = bound_media_scope(request)
        for source in self.state.input_lock['configuration'].get('received_sources', []):
            ledger = Path(source['path'])
            if sha256_file(ledger) != source['sha256']:
                raise LibraryStopped('reliable_received_ledger_changed')
            old = read(ledger)
            for call in old['calls']:
                if call['name'] != name:
                    continue
                folder = ledger.parent / 'calls' / call['id']
                previous = read(folder / 'request.json')
                if json_sha(previous) != call['request_sha256']:
                    raise LibraryStopped('reliable_received_request_changed')
                if _matching_request(previous, bound_media_scope(previous)) != _matching_request(request, scope):
                    continue
                if call['status'] != 'received':
                    if 'provider_http_400_code_1301:' in call.get('error', ''):
                        raise LibraryStopped('reliable_provider_refused')
                    raise LibraryStopped('reliable_parent_not_received_no_replay')
                reply = read(folder / 'response.json')
                if json_sha(reply) != call['response_sha256']:
                    raise LibraryStopped('reliable_received_response_changed')
                value = parse_model_json('\n'.join(x['text'] for x in reply['result']['content'] if x.get('type') == 'text'))
                validator(value)
                self.state.set_artifact('received_reuse', dict(source_ledger=source,
                    source_call_id=call['id'], source_request_sha256=call['request_sha256'],
                    source_response_sha256=call['response_sha256'], stage=name, new_paid_request=False))
                return value
        return None

    def call(self, name, prompt, media, validator):
        from .contracts import parse_model_json
        media = Path(media).resolve(strict=True)
        if media.stat().st_size >= 8_000_000:
            raise ValueError('official_mcp_media_limit_8mb')
        lineage = read(media.parent / 'lineage.json')
        request = dict(tool='analyze_video', arguments=dict(video_source=str(media), prompt=prompt),
            media_sha256=sha256_file(media), provider=self.provider, policy_version=self.state.data['policy_version'],
            observation_scope={k: lineage[k] for k in ('kind','source_sha256','source_start_s','source_end_s')})
        self._exclude(request, request['observation_scope'])
        reused = self._received(name, request, validator)
        if reused is not None:
            return reused
        parent = None
        for repair in range(2):
            stage = name + ('_repair' if repair else '')
            self.state._reload()
            prior = next((c for c in self.state.data['calls'] if c['name'] == stage), None)
            if prior:
                request = read(self.output / 'calls' / prior['id'] / 'request.json')
                if json_sha(request) != prior['request_sha256']:
                    raise LibraryStopped('reliable_saved_request_changed')
            call, reply = self._submit(stage, request, repair_of=parent)
            text = '\n'.join(x['text'] for x in reply['result']['content'] if x.get('type') == 'text')
            try:
                value = parse_model_json(text)
                validator(value)
                parsed = self.output / 'calls' / call['id'] / 'parsed.json'
                if parsed.exists() and read(parsed) != value:
                    raise LibraryStopped('reliable_parsed_response_changed')
                if not parsed.exists():
                    write_json(parsed,value)
                return value
            except (ValueError,KeyError,TypeError) as error:
                failure = self.output / 'calls' / call['id'] / 'protocol_failure.json'
                if not failure.exists():
                    write_json(failure,dict(error=str(error),model_text=text))
                if repair:
                    raise LibraryStopped('reliable_one_protocol_repair_exhausted') from error
                parent = call
                request = dict(request, arguments=dict(request['arguments'], prompt=prompt +
                    '\n仅修复JSON结构和非法ID，不补造事实。上次错误：' + str(error) + '\n上次响应：' + text))
        raise AssertionError('unreachable')

    def _submit(self, name, request, *, repair_of=None):
        first_digest = json_sha(request)
        for attempt in range(3):
            current = request if not attempt else dict(request, reliable_retry_of=first_digest, reliable_attempt=attempt)
            try:
                return super()._submit(name if not attempt else name + '_retry' + str(attempt), current,
                                       repair_of=repair_of if not attempt else None)
            except LibraryStopped:
                self.state._reload()
                call = next((c for c in self.state.data['calls'] if c['request_sha256'] == json_sha(current)), None)
                events = _http_evidence(self.output, call['id']) if call else []
                if (attempt == 2 or not call or call['status'] != 'failed_known' or _outcome_unknown(events) or
                        not any(e.get('type') == 'response' and e.get('status') in {429, 500, 502, 503, 504}
                                for e in events)):
                    raise
                time.sleep(2 ** attempt)
        raise AssertionError('unreachable')


class Checkpoints:
    def __init__(self, output, identity):
        self.root = Path(output)
        self.path = self.root / 'reliable_state.json'
        if self.path.exists():
            self.data = read(self.path)
            if self.data['input'] != identity:
                raise LibraryStopped('reliable_input_or_code_changed')
        else:
            self.data = dict(policy=POLICY, input=identity, created_at=now(), stages={}, recoveries=0)
            write_json(self.path, self.data)

    def stage(self, name, operation):
        previous = self.data['stages'].get(name)
        if previous and previous['status'] == 'complete':
            for p, digest in previous['files'].items():
                if sha256_file(p) != digest:
                    raise LibraryStopped('reliable_checkpoint_artifact_changed:' + p)
            return previous['value']
        if previous:
            self.data['recoveries'] += 1
        self.data['stages'][name] = dict(status='running', started_at=now())
        write_json(self.path, self.data)
        value, files = operation()
        self.data['stages'][name].update(status='complete', finished_at=now(), value=value,
                                       files={str(p): sha256_file(p) for p in files})
        write_json(self.path, self.data)
        return value


def audio_measure(path, *, start=0, duration=None):
    args = ['ffmpeg', '-nostdin', '-v', 'error', '-ss', str(start), '-i', str(path)]
    if duration is not None:
        args += ['-t', str(duration)]
    p = subprocess.run([*args, '-map', '0:a:0', '-vn', '-ac', '1', '-ar', '8000',
                        '-f', 'f32le', '-'], capture_output=True, timeout=300)
    if p.returncode:
        raise LibraryStopped('reliable_audio_decode_failed')
    samples = np.frombuffer(p.stdout, dtype='<f4')
    if not len(samples) or not np.isfinite(samples).all():
        raise LibraryStopped('reliable_audio_empty_or_nonfinite')
    peak = float(np.max(np.abs(samples)))
    active_s = float(np.count_nonzero(np.abs(samples) > .001) / 8000)
    return dict(peak_db=20 * math.log10(max(peak, 1e-12)), active_s=active_s,
                duration_s=len(samples) / 8000, audible=peak > .001 and active_s >= .1)


def audio_digest(path):
    p = subprocess.run(['ffmpeg','-nostdin','-v','error','-i',str(path),'-map','0:a:0',
        '-c:a','copy','-f','hash','-hash','sha256','-'],capture_output=True,timeout=300)
    if p.returncode:
        raise LibraryStopped('reliable_audio_identity_failed')
    return p.stdout.decode().strip()


def synthesize_registered(text, output, line_id, registry_path=None, excluded=()):
    from .tts import synthesize_line, MODEL
    payload = dict(model=MODEL,input=dict(text=text,voice='Cherry',language_type='Chinese'))
    key = json_sha(payload)
    if key in excluded:
        raise LibraryStopped('reliable_historical_unknown_voice_no_replay')
    if registry_path is None:
        return synthesize_line(text,'Cherry',output,line_id=line_id)
    registry_path = Path(registry_path)
    with file_lock(registry_path.with_suffix('.lock'),wait=True):
        registry = read(registry_path) if registry_path.exists() else {}
        current = dict(output=str(Path(output).resolve()),line_id=line_id)
        previous = registry.get(key)
        if previous and previous != current:
            saved = Path(previous['output']) / previous['line_id'] / 'state.json'
            if not saved.is_file() or read(saved).get('status') != 'received':
                raise LibraryStopped('reliable_shared_unknown_voice_no_replay')
            current = previous
        elif not previous:
            registry[key] = current
            write_json(registry_path,registry)
    return synthesize_line(text,'Cherry',current['output'],line_id=current['line_id'])


def verify_delivery(video, plan, spec=SPEC, speech=(), mix_receipt=None):
    measured = probe_media(video)
    stream = next(s for s in measured['streams'] if s['index'] == measured['video_stream_index'])
    duration = measured['duration_s']
    checks = dict(width=stream['width'] == spec['width'], height=stream['height'] == spec['height'],
        fps=abs(float(Fraction(stream['avg_frame_rate'])) - spec['fps']) < .01,
        duration=spec['duration_min_s'] <= duration <= spec['duration_max_s'] + .075,
        timeline=abs(duration - plan['duration_s']) <= .075,
        audio=any(s.get('codec_type') == 'audio' for s in measured['streams']))
    decoded = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-i', str(video),
        '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'], capture_output=True, timeout=600)
    checks['fully_decoded'] = decoded.returncode == 0 and not decoded.stderr.strip()
    sound = audio_measure(video) if checks['audio'] else None
    checks['audible'] = bool(sound and sound['audible'])
    speech_checks = [audio_measure(video, start=l['start_s'], duration=l['end_s'] - l['start_s']) for l in speech]
    checks['speech_intervals'] = all(x['audible'] for x in speech_checks)
    if speech:
        if mix_receipt is None:
            raise LibraryStopped('reliable_speech_requires_mix_provenance')
        receipt = read(mix_receipt['path'])
        voice = receipt['audio_artifacts']['voice']
        expected = {line['id']: line for line in speech}
        checks['speech_provenance'] = (sha256_file(mix_receipt['path']) == mix_receipt['sha256'] and
            receipt['operation'] == 'mix_speech' and sha256_file(voice['path']) == voice['sha256'] and
            sha256_file(receipt['path']) == receipt['sha256'] and
            audio_digest(video) == audio_digest(receipt['path']) and
            len(receipt['lines']) == len(speech) and all(
                line['id'] in expected and line['audio_sha256'] == expected[line['id']]['audio_sha256'] and
                sha256_file(line['audio_path']) == line['audio_sha256'] and
                abs(line['start_s'] - expected[line['id']]['start_s']) < .001 and
                audio_measure(voice['path'],start=line['start_s'],duration=line['duration_s'])['audible']
                for line in receipt['lines']))
    checks['provenance'] = all(sha256_file(p) == digest for p, digest in {
        **{s['evidence_path']: s['evidence_sha256'] for s in plan['segments']},
        **{s['source_path']: s['source_sha256'] for s in plan['segments']}}.items())
    pixels = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(video), '-an',
        '-vf', 'fps=2,scale=32:18,format=gray', '-f', 'rawvideo', '-'],
        capture_output=True, timeout=300)
    frames = np.frombuffer(pixels.stdout, dtype=np.uint8)
    checks['nonblank_picture'] = bool(pixels.returncode == 0 and len(frames) and
        np.mean(np.mean(frames.reshape(-1, 32 * 18), axis=1) > 20) >= .1)
    result = dict(technical_complete=all(checks.values()), checks=checks, sound=sound,
                  speech_intervals=speech_checks, sha256=sha256_file(video), duration_s=duration)
    if not result['technical_complete']:
        raise LibraryStopped('reliable_technical_rejected:' + json.dumps(checks, sort_keys=True))
    return result


def _call_or_fallback(mcp, stage, prompt, media, validator, fallback, reasons):
    try:
        return mcp.call(stage, prompt, media, validator)
    except (LibraryStopped, ValueError, RuntimeError) as error:
        if str(error) == 'reliable_provider_refused':
            raise
        mcp.state._reload()
        if any(c['status'] in {'submitted', 'uncertain'} for c in mcp.state.data['calls']):
            reasons.append(stage + ':unknown_isolated_no_replay')
        else:
            # Inspect actual responses; do not require the legacy chain's
            # failure report or mistake the proxy SHA for the original SHA.
            for call in mcp.state.data['calls']:
                if call['status'] != 'failed_known':
                    continue
                for event in _http_evidence(mcp.output,call['id']):
                    if event.get('type') == 'response' and event.get('status') == 400:
                        try:
                            refused = str(json.loads(event['body']).get('error',{}).get('code')) == '1301'
                        except (ValueError,TypeError):
                            refused = False
                        if refused:
                            raise LibraryStopped('reliable_provider_refused') from error
            reasons.append(stage + ':' + str(error)[:300])
        if fallback is None:
            raise
        return deepcopy(fallback)


def execute(reference, library, visual_library, output, *, model_factory, configuration,
            title='', exclusions=(), music=None, spec=None, registry_path=None,
            tts_registry_path=None, unknown_tts_payloads=()):
    reference, output = Path(reference).resolve(strict=True), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    spec = dict(spec or SPEC)
    identity = dict(reference=str(reference), reference_sha256=sha256_file(reference),
        library=str(Path(library).resolve()), visual_library=str(Path(visual_library).resolve()),
        title=title, configuration=configuration, code_sha256=code_sha(), spec=spec,
        music_sha256=sha256_file(music) if music else None, exclusions_sha256=json_sha(list(exclusions)))
    with file_lock(output / '.reliable.lock', wait=True):
        checkpoint = Checkpoints(output, identity)
        result_path = output / 'reliable_result.json'
        if result_path.exists():
            result = read(result_path)
            if sha256_file(result['final_video']) != result['final_sha256']:
                raise LibraryStopped('reliable_final_artifact_changed')
            return result
        reasons = []
        def persist(name, value):
            path = output / (name + '.json')
            write_json(path, value)
            return value, [path]
        try:
            catalog = checkpoint.stage('inventory', lambda: persist('catalog', inventory_sources(library, output / 'inventory')))
            ref = probe_media(reference)
            for item in exclusions:
                if overlap(item.get('scope', item), identity['reference_sha256'], 0, ref['duration_s']):
                    raise LibraryStopped('reliable_reference_historical_unknown_no_replay')
            state = LibraryState(output / 'model', dict(reference_sha256=identity['reference_sha256'],
                configuration=configuration), max_requests=20, registry_path=registry_path)
            mcp = model_factory(state)
            def reading():
                media = (mcp.received_media('reference', REFERENCE_PROMPT, identity['reference_sha256'], ref['duration_s'])
                    if isinstance(mcp, ReliableMCP) else None)
                if media is None:
                    media = Path(prepare_window(inventory_sources(reference, output / 'reference_catalog')['sources'][0],
                        0, ref['duration_s'], output / 'cache', max_bytes=3500000, include_audio=False)['path'])
                fallback = dict(theme=title, queries=[title], speech_mode='none') if title.strip() else None
                value = _call_or_fallback(mcp, 'reference', REFERENCE_PROMPT, media, validate_reference, fallback, reasons)
                return persist('reference_reading', dict(value=value, degraded_reasons=list(reasons),
                    media_path=str(media), media_sha256=sha256_file(media)))
            reading_saved = checkpoint.stage('reference', reading)
            reading_value = reading_saved['value']
            reasons.extend(r for r in reading_saved['degraded_reasons'] if r not in reasons)
            def retrieval():
                candidates, rejected = candidates_from_records(Path(visual_library) / 'observations', catalog,
                    reading_value['queries'], visual_library, exclusions)
                if not candidates:
                    raise LibraryStopped('reliable_no_related_verified_material')
                return persist('retrieval', dict(candidates=candidates, rejected=rejected))
            candidates = checkpoint.stage('retrieval', retrieval)['candidates']
            def planning():
                fallback = dict(clip_ids=[c['clip_id'] for c in candidates[:16]], speech=[])
                context = dict(reference=reading_value, clips=[{k: c[k] for k in
                    ('clip_id', 'summary', 'matched_terms', 'source_in_s', 'source_out_s')} for c in candidates])
                prompt = ('为参考表达目标选择并排序相关片段，只引用给出的完整clip_id，不生成时间、角色身份或新动作。'
                    'speech最多3句，每句不超过24字，只能绑定已选择的clip_id；没有新配音需要时为空数组。'
                    '只返回JSON，完整字段结构如下：\n' + json.dumps(PLAN_EXAMPLE, ensure_ascii=False) + '\n' +
                    json.dumps(context, ensure_ascii=False))
                state._reload()
                if any(c['status'] in {'submitted', 'uncertain'} for c in state.data['calls']):
                    choice = fallback
                    reasons.append('plan:local_after_unknown')
                else:
                    # The reference proxy is a received input, not a new observation of library footage.
                    media = Path(reading_saved['media_path'])
                    if sha256_file(media) != reading_saved['media_sha256']:
                        raise LibraryStopped('reliable_reference_proxy_changed')
                    def check(value):
                        validate_choice(value,candidates)
                        if reading_value['speech_mode'] == 'none' and value['speech']:
                            raise ValueError('reliable_no_speech_reference_requires_empty_speech')
                    choice = _call_or_fallback(mcp, 'plan', prompt, media, check, fallback, reasons)
                plan = compile_choice(choice, candidates, min(ref['duration_s'], 60), spec)
                if plan['extended_to_minimum']:
                    reasons.append('plan:extended_from_verified_candidates_to_minimum_duration')
                return persist('plan', dict(plan=plan, degraded_reasons=list(reasons)))
            planned = checkpoint.stage('plan', planning)
            plan = planned['plan']
            reasons = list(dict.fromkeys([*reasons, *planned['degraded_reasons']]))
            def render():
                result = render_library_video(catalog, plan, output / 'render', fps=spec['fps'],
                    width=spec['width'], height=spec['height'], max_duration_s=spec['duration_max_s'], burn_captions=False)
                return result, [Path(result['rendered_path']), Path(result['manifest_path'])]
            rendered = checkpoint.stage('picture', render)
            def sound():
                from .audio_render import mix_speech, subtitle_final_video
                # Only uncommitted local products are disposable. Paid WAVs and
                # received mix receipts remain immutable across recovery.
                for name in ('voiced.mp4','subtitled.mp4','soundtrack.mp4'):
                    path = output / name
                    if not path.with_suffix(path.suffix + '.receipt.json').exists():
                        path.unlink(missing_ok=True)
                        stem = path.parent / (path.stem + '_audio')
                        if stem.is_dir() and not stem.is_symlink():
                            shutil.rmtree(stem)
                lines, failures = [], []
                segments = {s['clip_id']: s for s in plan['segments']}
                for i, line in enumerate(plan['speech']):
                    segment = segments.get(line['clip_id'])
                    if segment is None:
                        continue
                    try:
                        received = synthesize_registered(line['text'], output / 'tts', 'line_' + str(i),
                            tts_registry_path,unknown_tts_payloads)
                        length = received['duration_s']
                        if length > segment['output_out_s'] - segment['output_in_s']:
                            raise LibraryStopped('reliable_speech_exceeds_clip_window')
                        lines.append(dict(id='line_' + str(i), text=line['text'], audio_path=received['audio_path'],
                            audio_sha256=received['audio_sha256'], start_s=segment['output_in_s'],
                            end_s=segment['output_in_s'] + length))
                    except (LibraryStopped, RuntimeError, ValueError) as error:
                        failures.append(str(error)[:300])
                        break
                if lines and not failures:
                    mixed = mix_speech(rendered['rendered_path'], lines, output / 'voiced.mp4', music_path=music)
                    sub = subtitle_final_video(mixed['path'], lines, output / 'subtitled.mp4')
                    return persist('sound', dict(path=sub['path'], sha256=sub['sha256'], speech=lines,
                        mix_receipt=dict(path=mixed['receipt_path'],sha256=sha256_file(mixed['receipt_path'])),
                        mode='new_speech', degraded_reasons=[]))
                if plan['speech']:
                    reasons.append('audio:new_speech_fallback:' + ';'.join(failures))
                # Use the complete received soundtrack honestly, never call a dialogue mix pure BGM.
                soundtrack = Path(music) if music else reference
                try:
                    available_sound = audio_measure(soundtrack)['audible']
                except LibraryStopped:
                    available_sound = False
                if available_sound:
                    mixed = mix_speech(rendered['rendered_path'], [], output / 'soundtrack.mp4', music_path=soundtrack)
                    if audio_measure(mixed['path'])['audible']:
                        return persist('sound', dict(path=mixed['path'], sha256=mixed['sha256'], speech=[],
                            mode='configured_music' if music else 'reference_original_soundtrack', degraded_reasons=list(reasons)))
                source_plan = dict(plan, audio_mode='source')
                source = render_library_video(catalog, source_plan, output / 'source_sound', fps=spec['fps'],
                    width=spec['width'], height=spec['height'], max_duration_s=spec['duration_max_s'], burn_captions=False)
                if not audio_measure(source['rendered_path'])['audible']:
                    raise LibraryStopped('reliable_no_audible_sound_source')
                reasons.append('audio:selected_source_soundtrack_fallback')
                return persist('sound', dict(path=source['rendered_path'], sha256=source['sha256'], speech=[],
                    mode='selected_source_soundtrack', degraded_reasons=list(reasons)))
            audio = checkpoint.stage('sound', sound)
            if sha256_file(audio['path']) != audio['sha256']:
                raise LibraryStopped('reliable_sound_artifact_changed')
            reasons = list(dict.fromkeys([*reasons, *audio['degraded_reasons']]))
            def publish():
                source, temporary, final = Path(audio['path']), output / 'delivery.part.mp4', output / 'final.mp4'
                if temporary.exists():
                    temporary.unlink()
                os.link(source, temporary)
                verified = verify_delivery(temporary, plan, spec, audio['speech'], audio.get('mix_receipt'))
                os.replace(temporary, final)
                return persist('verification', dict(**verified, path=str(final)))
            verified = checkpoint.stage('delivery', publish)
            result = dict(policy=POLICY, engineering_status='completed_degraded' if reasons else 'completed',
                quality_status='unassessed', joint_quality_gate=False, degraded_reasons=reasons,
                final_video=verified['path'], final_sha256=verified['sha256'], technical=verified,
                audio_mode=audio['mode'], plan_path=str(output / 'plan.json'), input=identity,
                audio_provenance=audio,
                automatic_recoveries=checkpoint.data['recoveries'], manual_interventions=0, finished_at=now())
            write_json(result_path, result)
            return result
        except Exception as error:
            from .resource_guard import ResourceLimit
            failure = dict(error=str(error), type=type(error).__name__,
                engineering_status='blocked' if isinstance(error, LibraryStopped) else 'failed',
                quality_status='unassessed', degraded_reasons=reasons, failed_at=now(),
                retryable_local=isinstance(error,(OSError,subprocess.TimeoutExpired,ResourceLimit)) or
                    str(error).startswith(('library_media_tool_failed:','audio_render_ffmpeg_failed:')))
            write_json(output / 'failures' / (json_sha(failure)+'.json'),failure)
            write_json(output / 'reliable_failure.json', failure)
            raise
