"""Injected rough-to-fine skill stage; historical trial authorization is untouched."""
from __future__ import annotations

from copy import deepcopy
import math
import re
from pathlib import Path
from shutil import copyfile

from . import visual_story_trial as trial
from .media import probe_media, sha256_file
from .render import compile_library_plan, render_library_video
from .state import LibraryStopped, json_sha, write_json

POLICY = 'rough_to_story_finecut_v1'
PREFIX = 'chain_e2e_v1_fine_'
FPS, WIDTH, HEIGHT = 30, 1280, 720


def require(value, reason):
    if not value:
        raise LibraryStopped('story_finecut:' + reason)


def strings(value, field):
    if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value):
        raise ValueError('story_finecut:' + field + '_string_array_required')


class StoryFinecut(trial.Trial):
    """Reuse CPU helpers with externally owned ledger, MCP and reference evidence."""

    def __init__(self, state, mcp, reference_context, parent_source, reference_source, base):
        self.state, self.mcp = state, mcp
        self.adaptive = getattr(state, 'input_lock', {}).get('configuration', {}).get('workflow') == 'reference_rough_skill_v1'
        self.output = Path(state.output).resolve()
        self.base = Path(base).resolve()
        require(self.base.is_relative_to(self.output), 'stage_directory_outside_task')
        self.base.mkdir(parents=True, exist_ok=True)
        for source in (parent_source, reference_source):
            require(Path(source['path']).is_file() and sha256_file(source['path']) == source['sha256'],
                    'source_bytes_changed')
            metadata = probe_media(source['path'])
            require(abs(metadata['duration_s'] - source['duration_s']) <= .075,
                    'actual_source_duration_changed')
            require(type(source.get('video_stream_index')) is int and
                    source['video_stream_index'] == metadata['video_stream_index'],
                    'actual_video_stream_changed')
        reference_audio = reference_source.get('audio_stream_index')
        if reference_audio is None and self.adaptive:
            require(not any(row.get('codec_type') == 'audio' for row in metadata['streams']),
                    'actual_reference_audio_stream_changed')
        else:
            require(reference_audio is not None, 'reference_audio_required')
            require(any(row.get('codec_type') == 'audio' and row['index'] == reference_audio
                        for row in metadata['streams']), 'actual_reference_audio_stream_changed')
        self.auth = dict(parent=deepcopy(parent_source), reference=deepcopy(reference_source),
                         target_duration_s=reference_source['duration_s'], target_tolerance_s=2.0)
        if self.adaptive:
            self.auth['unbounded_inspections'] = True
        # A longer reference cannot require a shorter rough to be padded out.
        if parent_source['duration_s'] < reference_source['duration_s']:
            self.auth.update(duration_policy='do_not_lengthen_rough',
                             target_duration_s=parent_source['duration_s'])
        self.reference = deepcopy(reference_context)
        reference = self.reference.get('reference', self.reference)
        require(reference.get('reference_sha256') == reference_source['sha256'], 'reference_context_changed')
        self.state._reload()
        binding_path = self.base / 'input.json'
        binding = dict(policy=POLICY, parent=parent_source, reference=reference_source,
                       reference_context_sha256=json_sha(reference_context), target_tolerance_s=2.0,
                       max_local_queries=4, max_query_duration_s=6, max_requested_frames=36,
                       max_revisions=1, stage_prefix=PREFIX)
        if self.auth.get('duration_policy'):
            binding['duration_policy'] = self.auth['duration_policy']
        if self.adaptive:
            binding.update(max_local_queries=None, max_revisions=None, progress_driven=True)
        if binding_path.exists():
            saved = trial.read(binding_path)
            require({k: v for k, v in saved.items() if k != 'initial_request_count'} == binding,
                    'stage_input_changed')
            self.initial_request_count = saved['initial_request_count']
        else:
            self.initial_request_count = self.state.data['request_count']
            trial.write_once(binding_path, {**binding, 'initial_request_count': self.initial_request_count})
        self.knowledge_files = []
        packaged = Path(__file__).with_name('resources') / 'visual_story_finecut'
        knowledge = []
        for relative in ('SKILL.md', 'references/decision-cards.md'):
            raw = (packaged / relative).read_bytes()
            target = self.base / 'knowledge' / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                require(target.read_bytes() == raw, 'knowledge_snapshot_changed')
            else:
                target.write_bytes(raw)
            self.knowledge_files.append(dict(name=relative, sha256=sha256_file(target), path=str(target)))
            knowledge.append(raw.decode('utf-8'))
        self.knowledge = '\n\n'.join(knowledge)
        trial.write_once(self.base / 'knowledge.json', self.knowledge_files)
        layout_path = self.base / 'parent_layout.json'
        self.roi = trial.read(layout_path) if layout_path.exists() else trial.write_once(layout_path, trial.layout(parent_source))
        self.full = trial.proxy(parent_source, 0, parent_source['duration_s'], self.base,
                                roi=self.roi['crop'], label='full_parent')
        self.render_source = dict(source_id='chain_parent', path=self.full['path'], sha256=self.full['sha256'],
                                  duration_s=parent_source['duration_s'], video_stream_index=0,
                                  audio_stream_index=None)
        self.inspections = []
        self.selected_facts = {}

    def status(self, stage, **details):
        self.state._reload()
        write_json(self.base / 'progress.json', dict(stage=stage,
                   cumulative_calls=self.state.data['request_count'], **details))

    def call(self, name, prompt, media, scope, validator, *, image=False, purpose):
        require(name.startswith(PREFIX), 'stage_prefix_required')
        path = Path(media).resolve(strict=True)
        self.state.set_artifact(name + '_input', dict(stage=name,
            tool='analyze_image' if image else 'analyze_video', media_path=str(path),
            media_sha256=sha256_file(path), scope=scope, purpose=purpose))
        self.status(name)
        # Request settlement, one format repair and unknown no-replay stay with the injected MCP.
        return self.mcp.call(name, prompt, path, validator, image=image, scope=scope)

    def observation_check(self, value):
        trial.observe_check(value, self.auth)
        strings(value.get('limitations'), 'observation_limitations')
        ids = [row['id'] for row in value['story']]
        if len(ids) != len(set(ids)):
            raise ValueError('story_finecut:duplicate_story_id')
        for row in value.get('inspect', []):
            if row['target'] == 'reference' and row['start_s'] == 0 and row['end_s'] >= self.auth['reference']['duration_s'] - .001:
                raise ValueError('story_finecut:full_reference_repost_forbidden_use_seed')

    @staticmethod
    def facts_check(value, start, end, *, sparse=False):
        trial.facts_check(value)
        strings(value['uncertainties'], 'observation_uncertainties')
        for row in value['facts']:
            if not isinstance(row, dict) or not isinstance(row.get('description'), str) or not row['description'].strip():
                raise ValueError('story_finecut:fact_description_required')
            basis = row.get('basis')
            parts = re.split(r'[+和]', basis) if isinstance(basis, str) else basis
            if (not isinstance(parts, list) or not parts or
                    any(part not in {'picture', 'text', 'inference'} for part in parts)):
                raise ValueError('story_finecut:fact_basis_required')
            if sparse:
                trial.number(row.get('time_s'), start, end)
            else:
                trial.number(row.get('start_s'), start, end)
                trial.number(row.get('end_s'), start, end)
                if row['end_s'] < row['start_s']:
                    raise ValueError('story_finecut:fact_interval_reversed')

    def inspect(self, items):
        trial.inspect_check(items, self.auth)
        from .microclip_frames import extract_grid, frame_catalog
        for row in items:
            key = json_sha(row)
            require(not any(r['request_sha256'] == key for r in self.inspections),
                    'repeated_inspection_no_new_evidence')
            index, target = len(self.inspections), row['target']
            source = self.auth[target]
            start, end, step = row['start_s'], row['end_s'], row.get('step_s', .1)
            clip = trial.proxy(source, start, end, self.base,
                               roi=self.roi['crop'] if target == 'parent' else None, label='model_local')
            prompt = ('独立观察真实正常速度静音短片，原时间=本地时间+' + str(start) +
                '。问题只用于定位缺项，不是答案：' + row['question'] +
                '\n只输出JSON {"facts":[{"start_s":原时间,"end_s":原时间,"description":"实际可见事实",'
                '"basis":"picture或text或inference"}],"uncertainties":["未知事项文字"]}。'
                'uncertainties必须为string[]，没有未知时用[]；区分画面、文字和推断。')
            facts = self.call(f'{PREFIX}inspect_{index}', prompt, clip['path'],
                self.source_scope(target, start, end), lambda value: self.facts_check(value, start, end),
                purpose='model_selected_normal_speed_local_observation')
            sheets, cursor, page, requested_frames = [], start, 0, 0
            actual_end = frame_catalog(source['path'])['source']['source_end_s']
            while cursor < end - 1e-8:
                stop = min(end, cursor + step * 6, actual_end)
                require(stop > cursor, 'frame_range_not_available')
                count = min(6, max(1, math.ceil((stop - cursor) / step - 1e-7)))
                requested_frames += count
                require(requested_frames <= 36, 'frame_request_limit_exceeded')
                grid = extract_grid(source['path'], cursor, stop,
                    self.base / 'frames' / f'inspect_{index}_{page}',
                    count=count, max_cell_width=480)
                prompt = ('真实PTS抽样帧，不是连续播放；图间未展示的结果保持未知。问题：' + row['question'] +
                    '\n只输出JSON {"facts":[{"time_s":图中真实时间,"description":"可见事实",'
                    '"basis":"picture或text或inference"}],"uncertainties":["未知事项文字"]}。'
                    'uncertainties必须为string[]，没有未知时用[]。')
                facts_page = self.call(f'{PREFIX}detail_{index}_{page}', prompt, grid['grid']['path'],
                    self.source_scope(target, cursor, stop, 'sparse_contact_sheet'),
                    lambda value: self.facts_check(value, cursor, stop, sparse=True),
                    image=True, purpose='model_selected_real_PTS_frames')
                sheets.append(dict(grid_manifest=grid['manifest_path'], facts=facts_page))
                cursor, page = stop, page + 1
            self.inspections.append(dict(request=row, request_sha256=key, continuous=facts, frame_sheets=sheets))
        trial.write_once(self.base / 'inspection_results.json', self.inspections)

    def plan_prompt(self, observed, *, revision=None):
        context = dict(reference=self.reference, observed=observed, local_observations=self.inspections,
            source=dict(source_id=self.render_source['source_id'], duration_s=self.render_source['duration_s']),
            target_duration_s=self.auth['target_duration_s'], tolerance_s=2.0,
            max_duration_s=self.auth['reference']['duration_s'] + 1 / FPS)
        if self.auth.get('duration_policy') == 'do_not_lengthen_rough':
            context.update(duration_policy='do_not_lengthen_rough',
                max_duration_s=self.auth['parent']['duration_s'],
                duration_instruction='粗剪已短于参考；按信息贡献精炼，不为接近参考时长新增等待、重复素材或拖长。没有最低时长要求。')
        if revision:
            context['actual_output_feedback'] = revision
        template = {'segments': [{'segment_id': 'clip_1', 'source_id': self.render_source['source_id'],
            'window_id': 'full_parent_observed', 'source_in_s': 0.0, 'source_out_s': 1.0,
            'speed': 1.0, 'freeze_tail_s': 0.0, 'look': 'none', 'framing': 'fit',
            'contribution_to': ['原story的ID'], 'key_change': '实际关键变化', 'reason': '操作改善什么',
            'relation_to_next': '相邻片的关系'}], 'limitations': []}
        revision_rule = ('\n本次仅按actual_output_feedback中实际输出的问题局部修订；保持完整段落顺序、'
                         'segment_id和contribution_to，问题区间之外的片段操作不变。' if revision else '')
        prompt = self.knowledge + '\n\n' + trial.json.dumps(context, ensure_ascii=False) + revision_rule + (
            '\n自主制定完整精剪表；先省略冗余，再按可读性决定局部变速/停留。接近实际参考长度即可，'
            '不追求最短、不要求每种技巧都用。只能使用已观察父片，保留story贡献，不新增解释字幕，'
            '不能靠电影常识补结果。speed为0.5–2，freeze_tail_s为0–2秒，最多24段。'
            '源时间不等于输出时间；多个片段可共同表达一个贡献。limitations必须为string[]。'
            '只返回JSON：') + trial.json.dumps(template, ensure_ascii=False)
        if getattr(self, 'adaptive', False):
            prompt = prompt.replace('最多24段。', '段落与片段数量按实际表达需要决定，不设总数上限。')
        return prompt

    def plan_check(self, value):
        validation_auth = self.auth
        if self.auth.get('duration_policy') == 'do_not_lengthen_rough':
            validation_auth = {**self.auth, 'target_tolerance_s': math.inf}
        options = {'max_segments': None, 'max_duration_s': None} if getattr(self, 'adaptive', False) else {}
        trial.plan_check(value, validation_auth, self.render_source, self.observed['story'], **options)
        if self.auth.get('duration_policy') == 'do_not_lengthen_rough':
            compiled = compile_library_plan([self.render_source],
                {'segments': value['segments'], 'audio_mode': 'silent'}, fps=FPS, width=WIDTH, height=HEIGHT,
                **({'max_duration_s': None} if getattr(self, 'adaptive', False) else {}))
            if compiled['duration_s'] > self.auth['parent']['duration_s'] + 1 / FPS:
                raise ValueError('story_finecut:must_not_lengthen_shorter_rough')
        strings(value.get('limitations'), 'plan_limitations')
        for row in value['segments']:
            trial.number(row.get('freeze_tail_s', 0), 0, 2)
        expected = {row['id'] for row in self.observed['story']}
        if {sid for row in value['segments'] for sid in row['contribution_to']} != expected:
            raise ValueError('story_finecut:all_story_contributions_required')

    def render(self, plan, index):
        require(type(index) is int and index >= 0 and (getattr(self, 'adaptive', False) or index in (0, 1)),
                'only_one_revision_render')
        value = deepcopy(plan)
        value['audio_mode'] = 'silent'
        trial.write_once(self.base / f'plan_{index}.json', value)
        self.state.set_artifact(f'{PREFIX}render_{index}', dict(plan_sha256=json_sha(value),
            directory=str(self.base / f'render_{index}'), author='GLM'))
        self.status('rendering', version=index)
        return render_library_video([self.render_source], value, self.base / f'render_{index}',
                                    fps=FPS, width=WIDTH, height=HEIGHT,
                                    **({'max_duration_s': None} if getattr(self, 'adaptive', False) else {}))

    def review_check(self, value, duration=None, *, blind=False):
        trial.review_check(value)
        strings(value.get('limitations'), 'review_limitations')
        rows = value['problems']
        if blind:
            if not isinstance(value.get('facts'), list):
                raise ValueError('story_finecut:blind_facts_required')
            rows = rows + value['facts']
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get('description'), str) or not row['description'].strip():
                raise ValueError('story_finecut:review_time_evidence_required')
            trial.number(row.get('start_s'), 0, duration if duration is not None else math.inf)
            trial.number(row.get('end_s'), 0, duration if duration is not None else math.inf)
            if row['end_s'] < row['start_s']:
                raise ValueError('story_finecut:review_interval_reversed')

    def review(self, plan, result, index):
        scope = dict(kind='continuous_window', source_sha256=result['sha256'],
                     source_start_s=0, source_end_s=result['duration_s'])
        suffix = '' if index == 0 else '_r' if index == 1 else '_r' + str(index)
        blind = self.blind_proxy(result)
        prompt = ('独立静音画面盲读。未提供目标、参考或计划，只描述实际可见行动和结果；不借电影常识。'
            '空间裁切尝试排除外围烧录字幕，但可能仍有画内文字；有文字依赖必须记录，不冒充无字理解。'
            '不能把抽样或单帧观察当连续看完。只输出JSON {"apparent_story":"可见顺序",'
            '"facts":[{"start_s":输出秒,"end_s":输出秒,"description":"实际可见事实"}],'
            '"problems":[{"start_s":输出秒,"end_s":输出秒,"description":"具体难懂或冗余"}],'
            '"status":"pass或partial或fail","limitations":[]}。limitations必须为string[]。')
        first = self.call(PREFIX + 'blind' + suffix, prompt, blind['path'], scope,
                          lambda value: self.review_check(value, result['duration_s'], blind=True),
                          purpose='independent_picture_reading_of_actual_output')
        context = dict(reference=self.reference, original_story=self.observed['story'], plan=plan,
            independent_picture_read=first, source_observations=self.inspections,
            parent_duration_s=self.auth['parent']['duration_s'], target_duration_s=self.auth['target_duration_s'])
        prompt = self.knowledge + '\n\n' + trial.json.dumps(context, ensure_ascii=False) + (
            '\n审核实际成片的表达、信息保留和剪法作用，不能把计划当实际证据，不能抹掉盲读partial/冲突。'
            '现有父片缺失的结果不能靠变速补。音乐未经听审，保留未知。'
            '只输出JSON {"apparent_story":"实际表达","status":"pass或partial或fail",'
            '"problems":[{"start_s":输出秒,"end_s":输出秒,"description":"具体问题"}],'
            '"editing_methods_used":[],"next_action":"deliver或revise","revision_reason":"具体可修复问题",'
            '"limitations":[]}。limitations必须为string[]；只有实际输出问题支持一次局部改版，'
            '不能重新规划整片。')
        if getattr(self, 'adaptive', False):
            prompt = prompt.replace('只有实际输出问题支持一次局部改版，',
                '只有实际输出问题支持局部改版；没有新剪辑操作或审核改善时停止，不循环改写说明，')
        actual = trial.proxy(dict(path=result['rendered_path'], sha256=result['sha256'], video_stream_index=0,
            duration_s=result['duration_s']), 0, result['duration_s'], self.base, label='actual_output_review')
        def target_check(value):
            self.review_check(value, result['duration_s'])
            if value.get('next_action') not in {'deliver', 'revise'}:
                raise ValueError('story_finecut:next_action_required')
        target = self.call(PREFIX + 'review' + suffix, prompt, actual['path'], scope, target_check,
                           purpose='actual_output_target_comparison')
        return dict(blind=first, target=target)

    def finish(self, selected, review):
        ref, duration = self.auth['reference'], selected['duration_s']
        require(duration <= ref['duration_s'] + 1 / FPS, 'reference_music_duration_exceeded')
        require(sha256_file(selected['rendered_path']) == selected['sha256'], 'selected_render_changed')
        target = self.base / 'delivery' / 'story_finecut.mp4'
        target.parent.mkdir(exist_ok=True)
        identity = dict(selected_sha256=selected['sha256'], reference_sha256=ref['sha256'],
                        reference_audio_stream_index=ref['audio_stream_index'], duration_s=duration)
        receipt = target.parent / 'mux.json'
        if receipt.exists():
            saved = trial.read(receipt)
            require(saved['input'] == identity and target.is_file() and sha256_file(target) == saved['sha256'],
                    'delivery_cache_changed')
        else:
            require(not target.exists(), 'unbound_delivery_exists')
            temporary = target.with_name('story_finecut.part.mp4')
            if ref['audio_stream_index'] is None:
                require(self.adaptive, 'reference_audio_required')
                copyfile(selected['rendered_path'], temporary)
            else:
                trial.command(['ffmpeg', '-nostdin', '-y', '-v', 'error', '-i', selected['rendered_path'], '-i', ref['path'],
                    '-map', '0:v:0', '-map', f"1:{ref['audio_stream_index']}", '-c:v', 'copy', '-af',
                    f'atrim=duration={duration:.9f},asetpts=PTS-STARTPTS', '-c:a', 'aac', '-b:a', '160k',
                    '-t', f'{duration:.9f}', '-movflags', '+faststart', temporary])
            trial.command(['ffmpeg', '-nostdin', '-v', 'error', '-i', temporary, '-f', 'null', '-'])
            metadata = probe_media(temporary)
            require(abs(metadata['duration_s'] - duration) <= .075, 'delivery_duration_mismatch')
            temporary.replace(target)
            trial.write_once(receipt, dict(input=identity, sha256=sha256_file(target)))
        self.state._reload()
        joint = all(review[k]['status'] == 'pass' and not review[k]['problems'] and not review[k]['limitations']
                    for k in ('blind', 'target'))
        selected_plan = trial.read(Path(selected['manifest_path']).parent / 'render_input.json')['compiled']['plan']
        result = dict(policy=POLICY, status='completed' if joint else 'completed_with_model_review_limits',
            final_video=str(target), final_sha256=sha256_file(target),
            duration_s=duration, selected_render=selected, source_provenance=selected['provenance'],
            parent_source=self.auth['parent'], render_source=self.render_source,
            parent_source_sha256=self.auth['parent']['sha256'],
            reference_sha256=ref['sha256'], review=review, joint_quality_gate_passed=joint,
            joint_quality_gate=joint,
            request_count=self.state.data['request_count'],
            new_requests=self.state.data['request_count'] - self.initial_request_count,
            knowledge_files=self.knowledge_files, goal_resumed=False,
            limitations=['Model review is not independent continuous audience verification.',
                ('Reference has no audio stream; delivery preserves the selected silent render.'
                    if ref['audio_stream_index'] is None else
                    'Reference music uses its original speed; no stem separation or beat/craft verification.'),
                'Reference context is supplied received evidence; full reference is not resubmitted.',
                'Local proxies and authentic PTS grids do not establish native cloud frame sampling.'],
            model_limitations=dict(observation=self.observed['limitations'],
                plan=selected_plan['limitations'],
                blind=review['blind']['limitations'], target=review['target']['limitations']))
        trial.write_once(self.base / 'result.json', result)
        self.state.set_artifact(PREFIX + 'result', result)
        self.status('completed', video=str(target), duration_s=duration, joint_quality_gate_passed=joint)
        return result

    def revision_check(self, revised, original, result, review):
        self.plan_check(revised)
        if len(revised['segments']) != len(original['segments']):
            raise ValueError('story_finecut:local_revision_preserves_segment_sequence')
        problems = review['target']['problems']
        intervals = []
        for row in problems:
            trial.number(row.get('start_s'), 0, result['duration_s'])
            trial.number(row.get('end_s'), 0, result['duration_s'])
            if row['end_s'] <= row['start_s']:
                raise ValueError('story_finecut:revision_problem_interval_required')
            intervals.append((row['start_s'], row['end_s']))
        compiled = compile_library_plan([self.render_source], {'segments': revised['segments'], 'audio_mode': 'silent'},
                                        fps=FPS, width=WIDTH, height=HEIGHT,
                                        **({'max_duration_s': None} if getattr(self, 'adaptive', False) else {}))
        for old_plan, new_plan, old_row, new_row in zip(original['segments'], revised['segments'],
                                                       result['provenance'], compiled['segments']):
            if old_plan['segment_id'] != new_plan['segment_id'] or old_plan['contribution_to'] != new_plan['contribution_to']:
                raise ValueError('story_finecut:local_revision_preserves_contributions')
            affected = any(a < old_row['output_out_s'] and old_row['output_in_s'] < b for a, b in intervals)
            fields = ('source_id', 'source_in_s', 'source_out_s', 'speed', 'look', 'framing',
                      'frames', 'motion_frames', 'freeze_frames')
            if not affected and any(old_row.get(k) != new_row.get(k) for k in fields):
                raise ValueError('story_finecut:revision_changes_unrelated_segment')

    def run(self):
        done = self.base / 'result.json'
        if done.exists():
            result = trial.read(done)
            require(sha256_file(result['final_video']) == result['final_sha256'], 'delivery_changed')
            selected = result['selected_render']
            require(sha256_file(selected['rendered_path']) == selected['sha256'], 'selected_render_changed')
            mux = trial.read(self.base / 'delivery' / 'mux.json')
            require(mux['sha256'] == result['final_sha256'] and
                    mux['input']['selected_sha256'] == selected['sha256'] and
                    mux['input']['reference_sha256'] == self.auth['reference']['sha256'],
                    'delivery_binding_changed')
            return result
        duration = self.auth['parent']['duration_s']
        prompt = self.knowledge + '\n\n' + trial.json.dumps(dict(reference=self.reference,
            source_duration_s=duration, target_duration_s=self.auth['target_duration_s']), ensure_ascii=False) + (
            '\n完整粗剪正常速度静音代理，时间与父片同起点，只处理空间黑边。先看整片，'
            '自主划分信息贡献，再选必要局部精看；不固定slot数量，不借电影常识补情节。'
            '只返回JSON {"story":[{"id":"beat_a","contribution":"本段新增信息","entry":"之前知道什么",'
            '"exit":"之后知道什么"}],"facts":[],"inspect":[{"target":"parent或reference",'
            '"start_s":秒,"end_s":秒,"step_s":0.1,"question":"具体缺项"}],"limitations":[]}。'
            '最多4个局部，每个≤6秒且时长/step_s≤36；整片参考仅用已收到的context，禁止全参考重发。'
            'limitations必须为string[]；没有局限时用[]。'
            f'父片可用0至{duration}秒。局部不足如实记录，不把抽帧当完整动态观看。')
        if getattr(self, 'adaptive', False):
            prompt = prompt.replace('最多4个局部，每个≤6秒且时长/step_s≤36；',
                '局部数量不设总上限；每个局部按工具粒度分为≤6秒且时长/step_s≤36的批次；')
        self.observed = self.call(PREFIX + 'observe', prompt, self.full['path'],
            self.source_scope('parent', 0, duration), self.observation_check,
            purpose='whole_dynamic_parent_observation')
        trial.write_once(self.base / 'observed.json', self.observed)
        self.inspect(self.observed.get('inspect', []))
        plan = self.call(PREFIX + 'plan', self.plan_prompt(self.observed), self.full['path'],
            self.source_scope('parent', 0, duration), self.plan_check, purpose='model_owned_finecut_EDL')
        rendered = self.render(plan, 0)
        review = self.review(plan, rendered, 0)
        version = 0
        while review['target']['next_action'] == 'revise':
            reason = review['target'].get('revision_reason')
            require(isinstance(reason, str) and bool(reason.strip()) and bool(review['target']['problems']),
                    'revision_requires_actual_output_problem')
            revision_stage = PREFIX + ('revise' if version == 0 else 'revise_' + str(version))
            revised = self.call(revision_stage, self.plan_prompt(self.observed,
                revision=dict(plan=plan, review=review)), self.full['path'], self.source_scope('parent', 0, duration),
                lambda value: self.revision_check(value, plan, rendered, review),
                purpose='single_actual_output_evidence_revision')
            options = {'max_duration_s': None} if getattr(self, 'adaptive', False) else {}
            if trial.editing_fingerprint(revised, self.render_source, **options) == trial.editing_fingerprint(plan, self.render_source, **options):
                self.state.set_artifact(PREFIX + 'no_progress', dict(original_plan_sha256=json_sha(plan),
                    revised_plan_sha256=json_sha(revised), reason='No executed edit changed; no second render or loop.'))
                break
            version += 1
            second = self.render(revised, version)
            second_review = self.review(revised, second, version)
            score = {'pass': 2, 'partial': 1, 'fail': 0}
            if any(score[second_review[k]['status']] < score[review[k]['status']] for k in ('blind', 'target')):
                break
            improved = (any(score[second_review[k]['status']] > score[review[k]['status']] for k in ('blind', 'target')) or
                sum(len(second_review[k]['problems']) for k in ('blind', 'target')) <
                sum(len(review[k]['problems']) for k in ('blind', 'target')))
            rendered, review, plan = second, second_review, revised
            if not getattr(self, 'adaptive', False) or not improved:
                if getattr(self, 'adaptive', False) and not improved:
                    self.state.set_artifact(PREFIX + 'no_progress', {'reason': 'Executed edit changed but reviews did not improve; stop revising.'})
                break
        return self.finish(rendered, review)


def execute_finecut(state, mcp, reference_context, parent_source, reference_source, base):
    """Run/cache one skill stage with external request guards and return real provenance."""
    return StoryFinecut(state, mcp, reference_context, parent_source, reference_source, base).run()
