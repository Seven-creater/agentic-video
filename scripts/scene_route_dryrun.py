#!/usr/bin/env python3
"""Offline scene_v1 playback: real index/ASR/replies, scripted model and fake media delivery.

Defaults to both the single-scene case and a synthetic two-scene split. Every
generated observation/check is labelled synthetic; it is never written to the
library or offered as a new real visual receipt.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import itertools
import json
import math
from pathlib import Path
import tempfile
import wave
from unittest.mock import patch

import numpy as np

from omni_story.library import audio_render, contracts, dialogue_cut, material_index, render, speech_slots, tts, visual_audio
from omni_story.library.media import sha256_file
from omni_story.library.scene_route import SceneRoute, transcript_lines, window_roles
from omni_story.library.state import LibraryState, json_sha


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def reply(path):
    parsed = Path(path).with_name('parsed.json')
    if parsed.exists():
        return read(parsed)
    response = read(path)
    return contracts.parse_model_json('\n'.join(c['text'] for c in response['result']['content'] if c.get('type') == 'text'))


def synthetic_window(candidate, source):
    """Reconstruct a task-only observation from real index records, without video."""
    records, roles = candidate['records'], window_roles(candidate, dict(characters=[]))
    first = records[0]
    start, end = first['window_source_start_s'], first['window_source_end_s']
    observation = dict(window_id=candidate['window_id'], source_id=source['source_id'], roles=[
        dict(r, state='离线合成观察：来自索引角色表', identity_evidence='synthetic_index_playback',
             identity_confirmed=r.get('identity_confirmed', False)) for r in roles.values()],
        events=[dict(local_start_s=r['source_start_s']-start, local_end_s=r['source_end_s']-start,
                     role_ids=[p['role_id'] for p in r.get('roles', [])], observed_fact=r.get('action') or '索引场景记录')
                for r in records], usable_ranges=[], uncertainties=['离线合成，不代表重新看过画面'])
    return dict(window_id=candidate['window_id'], source_id=source['source_id'], source_sha256=source['sha256'],
                source_start_s=start, source_end_s=end, duration_s=end-start, status='watched',
                source_visual_policy='silent_subtitle_masked', source_subtitle_masks=[], audio_present=False,
                observation=observation, synthetic=True)


def fake_clip(source, start, end, cache, **kwargs):
    assert end-start <= 12 and kwargs['pad_s'] == 0
    path = Path(cache) / (json_sha([source['sha256'], start, end])[:20] + '.fake')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('synthetic clip; no ffmpeg', encoding='utf-8')
    return dict(path=str(path), sha256=sha256_file(path), source_sha256=source['sha256'],
                source_start_s=start, source_end_s=end, duration_s=end-start)


def fake_join(clips, cache):
    assert sum(c['duration_s'] for c in clips) <= 12.001
    return clips[0]['path']


def fake_vad(source, start, end, cache):
    return [[t, min(t+3, end-start)] for t in range(0, math.ceil(end-start), 4) if end-start-t >= .6]


def fake_cuts(source, start, end, cache):
    return list(range(10, math.ceil(end-start), 10))


def scripted_split(lines, duration, count):
    if count == 1:
        return dict(scenes=[dict(scene_id='scene_1', start_s=0, end_s=duration,
            setting='店内柜台、桌前与货架边', roles=['顾客', '店员', '店长'],
            interaction='顾客与店员当面对话争执，店长收尾交代，正反打', line_ids=[r['line_id'] for r in lines])])
    # Choose an actual ASR gap; no line is cut in half or assigned twice.
    left, right = min(((a, b) for a, b in zip(lines, lines[1:]) if a['end_s'] <= b['start_s']),
                      key=lambda pair: abs(pair[0]['end_s']-94))
    boundary = (left['end_s'] + right['start_s']) / 2
    return dict(scenes=[
        dict(scene_id='scene_1', start_s=0, end_s=boundary, setting='店内柜台桌前',
             roles=['顾客', '店员'], interaction='店内当面争执、正反打对话',
             line_ids=[r['line_id'] for r in lines if r['end_s'] <= boundary]),
        dict(scene_id='scene_2', start_s=boundary, end_s=duration, setting='店内柜台桌前',
             roles=['顾客', '店员'], interaction='店内收尾，人物反应与当面对话',
             line_ids=[r['line_id'] for r in lines if r['start_s'] >= boundary])])


def scripted_selection(route):
    # Backtrack over the REAL top-five lists. Same story role keeps its actor;
    # unique roles use distinct actors where possible. No candidate is injected.
    scenes = [s for s in route.scenes if route._shared_previous(s['scene_id']) is None]
    def choose(index, actors, result):
        if index == len(scenes):
            return result
        scene = scenes[index]
        sid = scene['scene_id']
        old_wid = 'vl_ec75745efa3c6658'
        ordered = sorted(route.candidates[sid], key=lambda c: (c['window_id'] in {x['window_id'] for x in result},
            c['window_id'] != old_wid, -c['score']))
        for candidate in ordered:
            roles = window_roles(candidate, route.cast)
            combinations = itertools.permutations(roles, len(scene['roles']))
            if candidate['window_id'] == old_wid:
                preferred = dict(顾客='D', 店员='B', 店长='E')
                combinations = sorted(combinations, key=lambda rids: sum(
                    rid != preferred[story] for story, rid in zip(scene['roles'], rids)))
            for rids in combinations:
                picked = {story: roles[rid]['actor'] for story, rid in zip(scene['roles'], rids)}
                if len(set(picked.values())) != len(picked) or any(story in actors and actors[story] != actor for story, actor in picked.items()):
                    continue
                found = choose(index+1, dict(actors, **picked), result + [dict(scene_id=sid,
                    window_id=candidate['window_id'], role_bindings=dict(zip(scene['roles'], rids)))])
                if found:
                    return found
        return None
    result = choose(0, {}, [])
    if not result:
        raise ValueError('scripted fake cannot satisfy same-actor selection from real top five')
    return dict(scenes=result)


class ScriptedModel:
    def __init__(self, state, split, data, template):
        self.state, self.split, self.data, self.template = state, split, data, template
        self.route, self.windows = None, None
        self.check_sources = dict(real=0, synthetic=0)
        self.old_lock = read(data/'mat18/material_lock.json')
        self.real_checks = []
        for path in sorted((data/'mat18/calls').glob('*material_check*')):
            value = reply(path/'response.json')
            self.real_checks.extend(value.get('scenes', []))

    def call_text(self, name, prompt, validator):
        return self.call(name, prompt, None, validator)

    def call(self, name, prompt, media, validator, **kwargs):
        if name == 'material_scene_split':
            value = deepcopy(self.split)
            value['lines'] = []
            for scene in value['scenes']:
                scene.update(dialogue_id='offline_dialogue', template='离线合成：按角色逐轮问答，不删句、不转述')
                for i, ident in enumerate(scene['line_ids']):
                    raw = next(r for r in self.route.lines if r['line_id'] == ident)
                    value['lines'].append(dict(line_id=ident, role=scene['roles'][i % len(scene['roles'])],
                        text=raw['text'], function='离线合成问答，非真实说话人识别'))
        elif name == 'material_scene_select':
            value = scripted_selection(self.route)
            for scene in self.route.scenes:
                sid = scene['scene_id']
                print(f'  候选 {sid} ({scene["setting"]}) [bm25，向量不加载模型]')
                for index, c in enumerate(self.route.candidates[sid], 1):
                    print(f'    {index}. {c["window_id"]} score={c["score"]:.6f}')
        elif name.startswith('material_scene_check_'):
            context = json.loads(prompt.split('\n', 1)[1])
            rows = []
            for choice in context['choices']:
                wid = choice['window_id']
                ranges = [[e['source_start_s'], e['source_end_s']] for e in context['entries'] if e['window_id'] == wid]
                roles, reused = {}, False
                for story in choice['role_bindings']:
                    # Reuse only matching role/actor/window and intersected
                    # recorded evidence. Other replies are explicitly synthetic.
                    bound = self.route._bound(choice)[story]
                    real = wid in self.old_lock['window_ids'] and dict(window_id=wid, role_id=bound['role_id']) in \
                        self.old_lock.get('cast_bindings', {}).get(story, {}).get('bindings', [])
                    evidence = [e for c in self.old_lock.get('scene_checks', []) if c.get('kind') == 'identity' and
                        bound['actor'] in c['condition'] for e in c.get('evidence', []) if e.get('verdict') == 'satisfied'] if real else []
                    visible = [[max(a, x), min(b, y)] for e in evidence for a, b in e.get('evidence_source_times', [])
                               for x, y in ranges if max(a, x) < min(b, y)]
                    speech = [[max(a['source_start_s'], x), min(a['source_end_s'], y)]
                              for a in self.old_lock.get('speech_ranges', {}).get(story, []) for x, y in ranges
                              if real and max(a['source_start_s'], x) < min(a['source_end_s'], y)]
                    # A recorded visible role can be silent; don't turn its
                    # identity evidence into invented recorded mouth movement.
                    speech = [[max(a, x), min(b, y)] for a, b in speech for x, y in visible if max(a, x) < min(b, y)]
                    origin = 'real' if visible else 'synthetic'
                    reused = reused or bool(visible)
                    self.check_sources[origin] += 1
                    roles[story] = dict(present=True, visible_ranges=visible or ranges,
                                        speech_ranges=speech if visible else ranges, origin=origin)
                rows.append(dict(window_id=wid, roles=roles, setting_match=True, single_location=True,
                    setting_reason='离线脚本：假定候选地点与参考场景一致', scene=(self.real_checks[0]['text'] if reused else
                    '离线合成确认：' + self.route._candidate(choice)['records'][0].get('window_summary', '人物当面对话'))))
            value = dict(windows=rows)
        elif name.startswith('material_scene_slots_'):
            context = json.loads(prompt.split('\n', 1)[1])
            roles = [hint['label'] for hint in context['characters']]
            value = dict(slots=[dict(candidate_id=e['candidate_id'], speaker=roles[i % len(roles)], gender='unclear',
                mouth_visible=True, clear=True, loopable=True, mouth_start_s=e['source_start_s'], mouth_end_s=e['source_end_s'],
                origin='synthetic_offline_reply') for i, e in enumerate(context['entries'])])
        elif name.startswith('material_scene_edges_'):
            assert kwargs['image'] is True
            context = json.loads(prompt.split('\n', 1)[1])
            value = dict(talking=[True]*len(context['cells']), speaker_ok=True,
                fixed_view=[True]*(len(context['cells'])-1), in_place=[True]*(len(context['cells'])-1),
                static_range=[context['cells'][0]['time_s'], context['cells'][-1]['time_s']+1/6], note='synthetic_offline_reply')
        elif name.startswith('material_scene_reactions_'):
            assert kwargs['image'] is True
            context = json.loads(prompt.split('\n', 1)[1])
            value = dict(listeners=[c['label'] for c in context['characters']],
                camera_still=True, static_range=[context['cells'][0]['time_s'], context['cells'][-1]['time_s']+1/6],
                note='synthetic_offline_reply')
        elif name == 'dialogue_rewrite':
            context = json.loads(prompt.split('\n', 1)[1])
            value = dict(text=context['line']['text'][:context['max_chars']])
        elif name == 'dialogue_script':
            context = json.loads(prompt.split('\n', 1)[1])
            lines, reference_roles = [], {}
            for scene in context['scenes']:
                slots = scene['slots']
                roles = [r['role'] for r in scene['reference_lines']]
                reference_roles[scene['scene_id']] = roles
                for i, raw in enumerate(scene['reference_lines']):
                    slot = next(p for p in slots if p['role'] == raw['role'])
                    text = '离线验证问答'[:slot['max_chars']]
                    lines.append(dict(line_id=f'l{len(lines)}', scene_id=scene['scene_id'],
                        slot_id=slot['slot_id'], role=slot['role'], text=text, facts=[0],
                        reference_line_ids=[raw['line_id']]))
            value = dict(reference_roles=reference_roles, facts=['离线合成台词，仅验证链路'], lines=lines)
        else:
            raise AssertionError('unexpected stage: ' + name)
        call, _ = self.state.begin_call(name, dict(prompt=prompt, media=str(media) if media else None))
        self.state.complete_call(call, dict(result=dict(content=[dict(type='text', text=json.dumps(value, ensure_ascii=False))])))
        validator(value)
        print(f'  {name}: PASS')
        return value


def run(data, count, plan_reply):
    print(f'\n=== scene_v1 离线回放：{count} 场景 ===')
    index = material_index.MaterialIndex.load(data/'material_index_v1/index')
    assert len(index.records) == 4943, f'expected real 4943-record index, got {len(index.records)}'
    asr, reference = read(data/'mat18/reference_asr.json'), read(data/'mat18/reference_reading.json')
    duration = asr['source_end_s']
    split = scripted_split(transcript_lines(asr), duration, count)
    template = reply(data/f'mat18/calls/{plan_reply}/response.json')
    catalog = {}
    for record in index.records:
        sha = record['source_sha256']
        source = catalog.setdefault(sha, dict(source_id=record['source_id'], sha256=sha,
            duration_s=0, audio_stream_index=1, path='/synthetic/offline-only'))
        source['duration_s'] = max(source['duration_s'], record['window_source_end_s'])
    with tempfile.TemporaryDirectory(prefix='scene-route-dryrun-') as scratch:
        root = Path(scratch)
        config = dict(material_flow='scene_v1', material_index_root=str(data/'material_index_v1/index'),
                      visual_library_root=str(data/'visual_library/three_films_v2'),
                      creative_audio=True, creative_style='picture_aware')
        state = LibraryState(root, dict(reference_sha256=reference['reference_sha256'], configuration=config),
                             max_requests=80, registry_path=root/'registry.json')
        model = ScriptedModel(state, split, data, template)
        route = SceneRoute(state, model, None, dict(sources=list(catalog.values())), reference,
            min(180, 1.25*duration), root/'cache', asr=asr, reference_duration_s=duration, index=index,
            clip_maker=fake_clip, joiner=fake_join, window_loader=synthetic_window, vad=fake_vad, cut_detector=fake_cuts)
        model.route = route
        def sheet(source, slot, cache, **kwargs):
            path = Path(cache)/'synthetic_edges.jpg'
            path.write_bytes(b'synthetic offline image')
            return path, dict(slot_id=slot['slot_id'], cells=[dict(cell=1, time_s=slot['source_start_s']),
                dict(cell=2, time_s=slot['source_end_s']-1/6)])
        with patch.object(speech_slots, 'edge_sheet', sheet):
            windows, lock = route.run()
        if count == 2:
            assert len(lock['scenes']) == 1  # Two descriptions of one conversation share one location.
        model.windows = windows
        slots = read(root/'speech_slots.json')
        assert all(s['speech_slots'] and s['candidates'] and s['replies'] for s in slots['scenes'])
        assert all(c['origin'] == 'vad' for s in slots['scenes'] for c in s['candidates'])
        assert all(sum(e['source_end_s']-e['source_start_s'] for e in r['entries']) <= 12.001
                   for s in slots['scenes'] for r in s['replies'])
        print(f'  speech_slots: PASS (synthetic VAD/picture) slots={sum(len(s["speech_slots"]) for s in slots["scenes"])}')
        print(f'  material_lock: PASS scenes={len(lock["scenes"])} windows={len(windows)} mismatches={len(lock["mismatches"])}')
        tts_calls, reviews = [], []
        def synthesize(text, voice, output, **kwargs):
            tts_calls.append(voice)
            seconds = [1.1, 1.4, 2.2, 3.0, 4.5][(len(tts_calls)-1) % 5]
            tone = .2*np.cos(2*np.pi*220*np.arange(round(seconds*48000))/48000)
            samples = np.concatenate([np.zeros(9600), tone, np.zeros(14400)])
            path = Path(output)/kwargs['line_id']/'audio.wav'
            path.parent.mkdir(parents=True, exist_ok=True)
            audio_render._wav(path, np.column_stack([samples, samples]))
            return dict(audio_path=str(path), audio_sha256=sha256_file(path), duration_s=len(samples)/48000)
        def receipt(path):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'synthetic offline video')
            return dict(path=str(path), sha256=sha256_file(path))
        def picture(catalog, plan, output, **kwargs):
            assert kwargs.pop('burn_captions') is False
            compiled = render.compile_library_plan(catalog, plan, **kwargs)
            value = receipt(output/'picture.mp4')
            return dict(rendered_path=value['path'], sha256=value['sha256'],
                        measured_duration_s=compiled['duration_s'])
        def review(*args):
            reviews.append(args)
            return dict(theme_alignment='matches', speech_clear=True, repair_at='none', missing_core_meaning=[])
        def samples(path):
            with wave.open(str(path)) as wav:
                return np.frombuffer(wav.readframes(wav.getnframes()), dtype='<i2').reshape(-1, 2)/32768
        with patch.object(tts, 'synthesize_line', synthesize), \
             patch.object(audio_render, '_samples', samples), \
             patch.object(render, 'render_library_video', picture), \
             patch.object(audio_render, 'mix_speech', lambda video, lines, output: receipt(output)), \
             patch.object(audio_render, 'subtitle_final_video', lambda video, lines, output: receipt(output)), \
             patch.object(visual_audio, 'review_final_video', review):
            result = dialogue_cut.run(lock, windows, reference, contracts.reference_strategy(reference, creative_audio=True),
                dict(sources=list(catalog.values())), state, model, root/'cache', root)
        assert result['status'] == 'delivered' and len(reviews) == 1
        assert read(root/'run_outcome.json')['outcome'] == 'delivered'
        assert all(s['speed'] == 1 and not s.get('freeze_tail_s') for s in result['timeline'])
        from loop_audit import audit_timeline
        issues, repeats = audit_timeline(result['timeline'], slots)
        assert not issues, issues
        assert all('segment_ids' in line for line in result['lines'])
        print(f'  loop_audit: PASS repeated_rows={repeats} violations=0')
        reactions = sum(s['kind'] == 'reaction' for s in result['timeline'])
        print(f'  dialogue_cut: PASS lines={len(result["lines"])} reactions={reactions} AV_reviews={len(reviews)}')
        print(f'  SUMMARY scenes={count} records={len(index.records)} asr_lines={len(route.lines)} '
              f'reference_s={duration:.3f} segments={len(result["timeline"])} lines={len(result["lines"])} '
              f'video_s={result["timeline"][-1]["end_s"]:.3f} status={result["status"]} '
              f'calls={state.usage()["requests"]} checks={route.check_calls} '
              f'check_origins={model.check_sources} mismatches={len(route.mismatches)}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--scenes', choices=('1', '2', 'both'), default='both')
    parser.add_argument('--plan-reply', choices=('glm_011_plan_0', 'glm_012_plan_0_repair', 'glm_013_plan_0_repair2'),
                        default='glm_013_plan_0_repair2')
    args = parser.parse_args()
    for count in (1, 2) if args.scenes == 'both' else (int(args.scenes),):
        run(args.data, count, args.plan_reply)


if __name__ == '__main__':
    main()
