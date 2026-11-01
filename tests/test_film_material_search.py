"""Original-film retrieval keeps verified bounds and historical input locks."""
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from omni_story.library import clean_chain, content_ranges, pipeline, question_first, server_cli, visual_library
from omni_story.library import subtitle_bar
from omni_story.library.media import sha256_file
from omni_story.library.state import LibraryState, LibraryStopped, write_json
from test_protocol_feedback import reference
from test_question_first import cache_fixture, film_cast_fixture


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    write_json(home / 'server.json', dict(project_root=str(tmp_path),
        mcp_package_root='/fake', opencode_executable='/fake'))
    write_json(home / 'credentials.json', dict(Z_AI_API_KEY='synthetic-key'))
    (home / 'credentials.json').chmod(0o600)
    library = tmp_path / 'films'
    library.mkdir()
    movie = library / 'original.mp4'
    movie.write_bytes(b'synthetic original film')
    ref = tmp_path / 'reference.mp4'
    ref.write_bytes(b'synthetic reference')
    source = dict(source_id='film', sha256=sha256_file(movie), path=str(movie),
                  duration_s=10, audio_stream_index=None)
    # A synthetic encode with an already measured bar; no real media IO.
    source['subtitle_bar'] = dict(source_sha256=source['sha256'], native_width=1920, native_height=1080,
        picture_bottom_px=950, measurement=dict(method=subtitle_bar.COLUMN_MEASUREMENT_METHOD),
        mask=dict(x=0, y=950 / 1080, w=1, h=130 / 1080, color='#000000'))
    ref_source = dict(source_id='reference', sha256=sha256_file(ref), path=str(ref),
                      duration_s=4, audio_stream_index=None)
    visual = tmp_path / 'visual'
    bounds = dict(content_start_s=2, content_end_s=8, verified_by='synthetic fixture',
                  excluded=[dict(start_s=0, end_s=2, kind='logo'),
                            dict(start_s=8, end_s=10, kind='end_credits')])
    write_json(visual / 'content_ranges.json', dict(schema_version='content_ranges_v1',
        sources={source['sha256']: bounds}))
    monkeypatch.setattr(pipeline, '_catalog', lambda path, output: deepcopy(
        dict(sources=[ref_source if path == ref else source])))
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'synthetic-audio-key')
    return SimpleNamespace(home=home, library=library, ref=ref, source=source,
        visual=visual, bounds=bounds, output=tmp_path / 'task', root=tmp_path)


def cli_args(inputs, command, *flags):
    return ['--home', str(inputs.home), command, '--reference', str(inputs.ref),
            '--library', str(inputs.library), '--output', str(inputs.output), *flags]


@pytest.mark.parametrize('command', ['start', '_run'])
@pytest.mark.parametrize('flags, message', [
    (['--material-search', 'film'], '--material-search film requires --creative-audio'),
    (['--creative-audio', '--material-search', 'film', '--shot-cache', '/private/tmp'],
     '--shot-cache requires --material-search question_first'),
])
def test_film_argument_constraints(command, flags, message, capsys):
    with pytest.raises(SystemExit) as error:
        server_cli.main([command, '--reference', 'ref', '--library', 'films', '--output', 'task', *flags])
    assert error.value.code == 2
    assert message in capsys.readouterr().err


@pytest.mark.parametrize('command', ['start', '_run'])
def test_film_accepts_creative_audio_without_visual_library(inputs, monkeypatch, command):
    monkeypatch.setattr(server_cli.server_jobs, 'start', lambda *args: dict(state='validated_only'))
    run = Mock()
    monkeypatch.setattr(server_cli, '_run', run)
    assert server_cli.main(cli_args(inputs, command, '--creative-audio', '--material-search', 'film')) == 0
    if command == '_run':
        args = run.call_args.args[0]
        assert args.material_search == 'film' and args.creative_audio and args.visual_library is None


@pytest.mark.parametrize('mode', [None, 'library', 'film', 'question_first'])
def test_detached_command_and_worker_configuration(inputs, monkeypatch, mode):
    flags = ['--creative-audio', '--visual-library', str(inputs.visual)]
    if mode is not None:
        flags += ['--material-search', mode]
    qfs_config = None
    if mode == 'question_first':
        cache = cache_fixture(inputs.root, inputs.source)
        write_json(inputs.visual / 'cast.json', film_cast_fixture())
        flags += ['--shot-cache', str(cache)]
        qfs_config = dict(material_search='question_first', creative_audio=True,
            shot_cache=str(cache.resolve()), visual_library_root=str(inputs.visual.resolve()))
        question_first.configure(qfs_config, dict(sources=[deepcopy(inputs.source)]), inputs.output)
    commands, configs = [], []
    monkeypatch.setattr(server_cli.server_jobs, 'start', lambda command, *args:
        commands.append(command) or dict(state='validated_only'))
    monkeypatch.setattr(clean_chain, 'execute', lambda *args, **kwargs:
        configs.append(kwargs['provider_config']) or dict(usage=dict(requests=0)))
    assert server_cli.main(cli_args(inputs, 'start', *flags)) == 0
    command = commands[0]
    if mode in {'film', 'question_first'}:
        assert command[command.index('--material-search') + 1] == mode
    else:
        assert '--material-search' not in command
    if mode == 'question_first':
        assert command[command.index('--shot-cache') + 1] == str(cache.resolve())
    else:
        assert '--shot-cache' not in command
    assert command[command.index('--visual-library') + 1] == str(inputs.visual.resolve())
    # Run the exact detached worker arguments, with execution replaced above.
    assert server_cli.main(command[3:]) == 0
    expected = dict(server_cli.PROVIDER)
    if qfs_config:
        expected.update(qfs_config)
    expected['workflow'] = 'reference_rough_skill_v1'
    if mode == 'film':
        expected['material_search'] = 'film'
    expected.update(creative_audio=True, vision_reasoning_effort='low',
        visual_library_root=str(inputs.visual.resolve()),
        content_ranges=content_ranges.load_content_ranges(inputs.visual, dict(sources=[inputs.source])),
        vision_generation=dict(server_cli.VISION_GENERATION))
    assert configs == [expected]
    # These insertion-ordered bytes match the pre-film worker configuration.
    assert json.dumps(configs[0], ensure_ascii=False) == json.dumps(expected, ensure_ascii=False)
    if mode in {None, 'library'}:
        assert 'material_search' not in configs[0]


@pytest.mark.parametrize('mode', [None, 'library', 'question_first'])
def test_historical_pipeline_input_lock_bytes_unchanged(inputs, monkeypatch, mode):
    config = dict(creative_audio=True, visual_library_root=str(inputs.visual))
    if mode is not None:
        config['material_search'] = mode
    if mode == 'question_first':
        config['shot_cache'] = str(cache_fixture(inputs.root, inputs.source))
        write_json(inputs.visual / 'cast.json', film_cast_fixture())
    expected = dict(span_s=600, frames=18, max_fine=16, asr=False, max_rounds=2,
                    max_renders=2, provider='official_vision_mcp_in_codex')
    expected.update(config)
    expected['content_ranges'] = content_ranges.load_content_ranges(inputs.visual, dict(sources=[inputs.source]))
    if mode == 'question_first':
        question_first.configure(expected, dict(sources=[deepcopy(inputs.source)]), inputs.output)
    expected['creative_strategy_policy'] = 'reference_remix_v1'
    class Locked(Exception):
        pass
    def stop_after_lock(state):
        raise Locked()
    with pytest.raises(Locked):
        pipeline.execute(inputs.ref, inputs.library, inputs.output, asr=False,
                         model_factory=stop_after_lock, provider_config=config)
    saved = json.loads((inputs.output / 'library_state.json').read_text())['input_lock']['configuration']
    assert saved == expected
    assert json.dumps(saved, ensure_ascii=False) == json.dumps(expected, ensure_ascii=False)
    if mode is None:
        assert 'material_search' not in saved


class ReachedFine(Exception):
    pass


@pytest.mark.parametrize('with_visual_library', [True, False])
def test_film_pipeline_watches_original_overview_zoom_and_fine(inputs, monkeypatch, with_visual_library):
    # Same catalog, received-reference and fake-model pattern as the library
    # path in test_v4_replay_regressions; media IO is replaced with local fakes.
    reading = reference()
    reading['reference_sha256'] = sha256_file(inputs.ref)
    reading['strategy'] = dict(adaptation_mode='reinterpret', speech_mode='none', timing_driver='action')
    seed = dict(full_response=dict(reference=reading), evidence_limit='synthetic received reference')
    config = dict(creative_audio=True, workflow='reference_rough_skill_v1', material_search='film')
    if with_visual_library:
        config['visual_library_root'] = str(inputs.visual)
    library = Mock(side_effect=AssertionError('film mode must not construct VisualLibrary'))
    configure_qfs = Mock(side_effect=AssertionError('film mode must not configure question_first/storyboard'))
    monkeypatch.setattr(visual_library, 'VisualLibrary', library)
    monkeypatch.setattr(question_first, 'configure', configure_qfs)
    monkeypatch.setattr(pipeline, 'create_navigation_sheet', lambda *args: dict(path=str(inputs.ref)))
    sheets, prepared, calls = [], [], []
    def media(source, start, end, **kwargs):
        return dict(path=str(inputs.ref), sha256=sha256_file(inputs.ref),
            source_id=source['source_id'], source_sha256=source['sha256'],
            source_start_s=start, source_end_s=end, source_offset_s=start,
            duration_s=end-start, source_visual_policy='silent_subtitle_masked',
            source_subtitle_masks=kwargs.get('subtitle_masks', []))
    def contact_sheet(source, start, end, cache, **kwargs):
        sheets.append((start, end))
        return dict(media(source, start, end, **kwargs), kind='sparse_contact_sheet',
                    frames=[dict(frame_id='f0', source_time_s=start)])
    def prepare(source, start, end, cache, **kwargs):
        prepared.append((start, end))
        return media(source, start, end, **kwargs)
    monkeypatch.setattr(pipeline, 'create_contact_sheet', contact_sheet)
    monkeypatch.setattr(pipeline, 'prepare_window', prepare)
    class Model:
        def __init__(self, state):
            self.state = state
            self.exclusions = []
        def call(self, name, prompt, media_path, validator, **kwargs):
            calls.append(name)
            if name.startswith('overview_') or name.startswith('zoom_'):
                result = dict(source_id=inputs.source['source_id'],
                    coverage_s=list(sheets[-1]), roles=[], events=[], uncertainties=[])
            elif name in {'coarse_zoom_search', 'search_0'}:
                assert 'reusable_visual_candidates' not in prompt
                result = dict(reason='确认原片动作', windows=[dict(source_id=inputs.source['source_id'],
                    start_s=3, end_s=7 if name == 'coarse_zoom_search' else 4,
                    question='确认可见动作', role_ids=[])])
                if with_visual_library:
                    for start, end in [(0, 4), (7, 9)]:
                        invalid = deepcopy(result)
                        invalid['windows'][0].update(start_s=start, end_s=end)
                        with pytest.raises(ValueError, match='range_outside_content'):
                            validator(invalid)
            elif name.startswith('fine_'):
                assert 'reference_storyboard' not in prompt
                window = dict(zip(validator.__code__.co_freevars,
                    (cell.cell_contents for cell in validator.__closure__)))['window']
                assert 'qfs_identity' not in window
                assert window['question_first_subtitle_bar'] == inputs.source['subtitle_bar']
                if with_visual_library:
                    assert 'content_range' in prompt
                raise ReachedFine()
            else:
                pytest.fail('unexpected model call: ' + name)
            validator(result)
            return result
    with pytest.raises(ReachedFine):
        pipeline.execute(inputs.ref, inputs.library, inputs.output, asr=False, max_fine=None,
            model_factory=Model, provider_config=config, reference_seed=seed)
    assert calls[0].startswith('overview_')
    assert calls[1] == 'coarse_zoom_search' and calls[2].startswith('zoom_')
    assert calls[3] == 'search_0' and calls[4].startswith('fine_')
    assert sheets[:2] == ([(2, 8)] * 2 if with_visual_library else [(0, 10)] * 2)
    assert sheets[2:] == [(3, 7)] * 2
    assert prepared == [(3, 4)] * 2
    assert not any(name.startswith('subtitle_regions_') for name in calls)
    saved_bar = json.loads((inputs.output / 'question_first_film_info' /
                           (inputs.source['sha256'] + '.json')).read_text())
    assert saved_bar == inputs.source['subtitle_bar']
    library.assert_not_called()
    configure_qfs.assert_not_called()
    state_path = inputs.output / 'library_state.json'
    state = json.loads(state_path.read_text())
    locked = state['input_lock']['configuration']
    assert locked['material_search'] == 'film'
    assert 'reference_storyboard' not in locked and 'shared_visual_library' not in state['artifacts']
    if with_visual_library:
        assert locked['content_ranges'] == content_ranges.load_content_ranges(
            inputs.visual, dict(sources=[inputs.source]))
        # Resume must use the pinned bounds even after today's library changes.
        before = json.dumps(state['input_lock'], ensure_ascii=False)
        (inputs.visual / 'content_ranges.json').unlink()
        with pytest.raises(ReachedFine):
            pipeline.execute(inputs.ref, inputs.library, inputs.output, asr=False, max_fine=None,
                model_factory=Model, provider_config=config, reference_seed=seed)
        assert json.dumps(json.loads(state_path.read_text())['input_lock'], ensure_ascii=False) == before
        assert sheets[-4:-2] == [(2, 8)] * 2
    # Switching retrieval modes cannot reopen the same immutable input lock.
    with pytest.raises(LibraryStopped, match='locked_inputs_policy_or_budget_changed'):
        LibraryState(inputs.output, {**state['input_lock'],
            'configuration': {**locked, 'material_search': 'library'}})


@pytest.mark.parametrize('mode', ['film', 'question_first'])
@pytest.mark.parametrize('sha', subtitle_bar.VERIFIED_LIBRARY_BARS)
@pytest.mark.parametrize('override', [False, True])
def test_film_and_question_first_use_identical_persisted_masks(tmp_path, mode, sha, override):
    source = dict(source_id='film', sha256=sha, path='synthetic.mkv')
    config = dict(material_search=mode)
    if override:
        config['source_subtitle_regions'] = {sha: [dict(x=.2, y=.9, w=.4, h=.1,
            color='#FFFFFF', reason='legacy override')]}
    glm = SimpleNamespace(state=SimpleNamespace(output=tmp_path, input_lock=dict(configuration=config)),
                          call=Mock(side_effect=AssertionError('no model mask queries')))
    for image, start, end in [(True, 2, 8), (False, 3, 4)]:
        raw = dict(source_start_s=start, source_end_s=end)
        assert pipeline._source_subtitles(glm, source, raw, image=image) == [dict(
            x=0, y=950 / 1080, w=1, h=130 / 1080, color='#000000',
            start_s=start, end_s=end, reason='verified_library_native_frames')]
        saved = json.loads((tmp_path / 'question_first_film_info' / (sha + '.json')).read_text())
        assert saved == source.pop('subtitle_bar')
    glm.call.assert_not_called()


@pytest.mark.parametrize('mode', [None, 'library'])
@pytest.mark.parametrize('image', [False, True])
@pytest.mark.parametrize('override', [False, True])
def test_library_subtitle_model_path_and_override_unchanged(tmp_path, mode, image, override):
    source = dict(source_id='film', sha256='a' * 64)
    region = dict(x=.2, y=.9, w=.4, h=.1, color='#FFFFFF', reason='received region')
    config = {} if mode is None else dict(material_search=mode)
    if override:
        config['source_subtitle_regions'] = {source['sha256']: [region]}
    glm = SimpleNamespace(state=SimpleNamespace(output=tmp_path, input_lock=dict(configuration=config)),
        call=Mock(return_value=dict(masks=[dict(region, start_s=0, end_s=5)], protected_text=[], limitations=[])))
    raw = dict(source_start_s=17, source_end_s=22, sha256='b' * 64, path='synthetic.mp4',
               kind='sparse_contact_sheet' if image else 'continuous_window', source_sha256=source['sha256'])
    assert pipeline._source_subtitles(glm, source, raw, image=image) == [dict(region, start_s=17, end_s=22)]
    if override:
        glm.call.assert_not_called()
    else:
        glm.call.assert_called_once()
        assert glm.call.call_args.args[0].startswith('subtitle_regions_')
        assert glm.call.call_args.kwargs['image'] is image
    assert not (tmp_path / 'question_first_film_info').exists()


@pytest.mark.parametrize('ingress', ['received', 'seed', 'continuation'])
@pytest.mark.parametrize('change', [None, 'missing', 'white', 'partial_geometry', 'partial_time'])
def test_film_received_windows_require_full_bar_before_reuse(inputs, monkeypatch, ingress, change):
    from omni_story.library import server_capacity_recovery
    reading = reference()
    reading['reference_sha256'] = sha256_file(inputs.ref)
    reading['strategy'] = dict(adaptation_mode='reinterpret', speech_mode='none', timing_driver='action')
    seed = dict(full_response=dict(reference=reading), evidence_limit='synthetic received reference')
    source = inputs.source
    masks = [dict(source['subtitle_bar']['mask'], start_s=3, end_s=4)]
    if change == 'missing':
        masks = []
    elif change == 'white':
        masks[0]['color'] = '#FFFFFF'
    elif change == 'partial_geometry':
        masks[0]['w'] = .9
    elif change == 'partial_time':
        masks[0]['end_s'] = 3.5
    windows = [dict(window_id='received', source_id=source['source_id'], source_start_s=3, source_end_s=4,
        source_visual_policy='silent_subtitle_masked', source_subtitle_masks=masks)]
    config = dict(creative_audio=True, workflow='reference_rough_skill_v1', material_search='film')
    monkeypatch.setattr(pipeline, '_received_windows', lambda *args, **kwargs:
                        deepcopy(windows) if ingress == 'received' else [])
    if ingress == 'seed':
        config['received_visual_seed'] = dict(fixture=True)
        monkeypatch.setattr(pipeline, '_received_visual_seed', lambda *args, **kwargs: deepcopy(windows))
    glm = SimpleNamespace(call=Mock(side_effect=AssertionError('no model calls before reuse')))
    def model_factory(state):
        glm.state = state
        if ingress == 'continuation':
            state.set_artifact('server_remaining_candidate_feedback', dict(fixture=True))
        return glm
    continuation_path = inputs.root / 'continuation.json'
    write_json(continuation_path, windows)
    monkeypatch.setattr(server_capacity_recovery, 'remaining_candidate', lambda output: dict(
        watched_windows_path=str(continuation_path), remaining_candidate=1, feedback={}))
    monkeypatch.setattr(pipeline, '_adaptive_coarse', lambda *args, **kwargs: ([], [], None))
    class ReachedReuse(Exception):
        pass
    validate = Mock(wraps=pipeline._validate_question_first_windows)
    def checked(rows, source_map, output):
        validate(rows, source_map, output)
        if rows:
            assert rows[0]['question_first_subtitle_bar'] == source['subtitle_bar']
            raise ReachedReuse()
    monkeypatch.setattr(pipeline, '_validate_question_first_windows', checked)
    media = Mock(side_effect=AssertionError('no media reuse before validation'))
    monkeypatch.setattr(pipeline, 'create_contact_sheet', media)
    monkeypatch.setattr(pipeline, 'prepare_window', media)
    expected_error = ReachedReuse if change is None else ValueError
    with pytest.raises(expected_error, match=None if change is None else 'full_black_bar_and_window'):
        pipeline.execute(inputs.ref, inputs.library, inputs.output, asr=False, max_fine=None,
            model_factory=model_factory, provider_config=config, reference_seed=seed)
    assert validate.call_count == (2 if ingress == 'continuation' else 1)
    glm.call.assert_not_called()
    media.assert_not_called()
