from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
from pathlib import Path
import threading

import pytest

from omni_story.evaluation import production_subject as subject, subject_cache as module
from omni_story.evaluation.subject_cache import SubjectCache, understand
from omni_story.library.state import json_sha


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return path


@pytest.fixture
def fields():
    return dict(media_sha256='a' * 64, condition='full',
                production_release='release1', prompt_sha256='b' * 64)


@pytest.fixture
def receipt(tmp_path):
    return write(tmp_path / 'received.json', dict(status='received', validated=True))


def test_cache_miss_hit_metadata_and_idempotence(tmp_path, fields, receipt):
    cache = SubjectCache(tmp_path / 'cache')
    value = dict(theme='行动', evidence=['迈步'])
    assert cache.get(fields) is None
    assert not cache.root.exists()
    cache.put(fields, value, [receipt])
    key = json_sha(fields)
    folder = cache.root / key
    meta_path = folder / 'meta.json'
    meta_bytes = meta_path.read_bytes()
    meta = json.loads(meta_bytes)
    assert meta['key_fields'] == fields
    assert meta['understanding_sha256'] == subject.digest(folder / 'understanding.json')
    assert meta['receipts'] == {str(receipt): subject.digest(receipt)}
    assert datetime.fromisoformat(meta['written_at']).tzinfo is not None
    assert cache.get(dict(reversed(list(fields.items())))) == value
    cache.put(fields, dict(evidence=['迈步'], theme='行动'), [receipt])
    assert meta_path.read_bytes() == meta_bytes
    assert cache.receipt_paths(fields) == [str(receipt)]
    with pytest.raises(ValueError, match='^subject_cache_conflict$'):
        cache.put(fields, dict(theme='相反'), [receipt])
    assert cache.get(fields) == value
    assert list(cache.root.iterdir()) == [folder]


@pytest.mark.parametrize('field,other', [('media_sha256', 'c' * 64), ('condition', 'prefix@5.0'),
    ('production_release', 'release2'), ('prompt_sha256', 'd' * 64)])
def test_every_key_field_separates_entries(tmp_path, fields, receipt, field, other):
    cache = SubjectCache(tmp_path / 'cache')
    cache.put(fields, dict(theme='行动'), [receipt])
    assert cache.get(dict(fields, **{field: other})) is None


@pytest.mark.parametrize('damage', ['understanding', 'missing_understanding', 'missing_meta',
                                  'meta_json', 'meta_fields', 'receipt'])
def test_corruption_is_not_a_miss(tmp_path, fields, receipt, damage):
    cache = SubjectCache(tmp_path / 'cache')
    cache.put(fields, dict(theme='行动'), [receipt])
    folder = cache.root / json_sha(fields)
    if damage == 'understanding':
        write(folder / 'understanding.json', dict(theme='改动'))
    elif damage == 'missing_understanding':
        (folder / 'understanding.json').unlink()
    elif damage == 'missing_meta':
        (folder / 'meta.json').unlink()
    elif damage == 'meta_json':
        (folder / 'meta.json').write_text('[')
    elif damage == 'meta_fields':
        meta = json.loads((folder / 'meta.json').read_text())
        meta['key_fields']['condition'] = 'prefix@5.0'
        write(folder / 'meta.json', meta)
    else:
        write(receipt, dict(status='uncertain'))
    with pytest.raises(ValueError, match='^subject_cache_corrupted$'):
        cache.get(fields)


@pytest.mark.parametrize('value', [dict(status='submitted'), dict(status='uncertain'),
                                  dict(status='received', validated=False)])
@pytest.mark.parametrize('location', ['understanding', 'receipt'])
def test_unverified_returns_are_not_cached(tmp_path, fields, receipt, value, location):
    cache = SubjectCache(tmp_path / 'cache')
    if location == 'receipt':
        write(receipt, value)
        value = dict(theme='行动')
    with pytest.raises(ValueError, match='^subject_cache_unverified$'):
        cache.put(fields, value, [receipt])
    assert not cache.root.exists()


def test_receipts_are_required(tmp_path, fields):
    cache = SubjectCache(tmp_path / 'cache')
    with pytest.raises(ValueError, match='^subject_cache_receipts_required$'):
        cache.put(fields, dict(theme='行动'), [])
    assert not cache.root.exists()


def test_understand_produces_once_and_hits_cache(tmp_path, fields, receipt):
    cache = SubjectCache(tmp_path / 'cache', production_release=fields['production_release'],
                         prompt_sha256=fields['prompt_sha256'])
    calls = []
    value = dict(theme='行动')
    def produce():
        calls.append('produce')
        return value, [receipt]
    assert understand('media.mp4', fields['media_sha256'], 'full', cache=cache,
                      produce=produce) == (value, 'produced')
    assert understand('another-path.mp4', fields['media_sha256'], 'full', cache=cache,
                      produce=produce) == (value, 'cache_hit')
    assert calls == ['produce']


def test_produce_exception_is_unchanged_and_does_not_write(tmp_path, fields):
    cache = SubjectCache(tmp_path / 'cache', production_release=fields['production_release'],
                         prompt_sha256=fields['prompt_sha256'])
    error = RuntimeError('uncertain')
    def produce():
        raise error
    with pytest.raises(RuntimeError) as caught:
        understand('media.mp4', fields['media_sha256'], 'full', cache=cache, produce=produce)
    assert caught.value is error
    assert not cache.root.exists()


def test_failed_publication_leaves_no_partial_entry(tmp_path, fields, receipt, monkeypatch):
    cache = SubjectCache(tmp_path / 'cache')
    def fail(*args):
        raise OSError('rename failed')
    monkeypatch.setattr(module.os, 'rename', fail)
    with pytest.raises(OSError, match='rename failed'):
        cache.put(fields, dict(theme='行动'), [receipt])
    assert cache.get(fields) is None
    assert not list(cache.root.iterdir())


@pytest.mark.parametrize('different', [False, True])
def test_concurrent_puts_are_atomic(tmp_path, fields, receipt, monkeypatch, different):
    cache = SubjectCache(tmp_path / 'cache')
    rename = module.os.rename
    barrier = threading.Barrier(2)
    def publish(source, destination):
        barrier.wait(timeout=5)
        return rename(source, destination)
    monkeypatch.setattr(module.os, 'rename', publish)
    def put(value):
        try:
            cache.put(fields, value, [receipt])
        except ValueError as error:
            return str(error)
        return 'stored'
    values = [dict(theme='行动'), dict(theme='犹豫' if different else '行动')]
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(put, values))
    assert sorted(outcomes) == (['stored', 'subject_cache_conflict'] if different else ['stored', 'stored'])
    assert cache.get(fields) in values
    assert len(list(cache.root.iterdir())) == 1


@pytest.fixture
def run(tmp_path, monkeypatch):
    from omni_story.library import pipeline
    root = tmp_path / 'production'
    root.mkdir()
    monkeypatch.setattr(pipeline, '__file__', str(root / 'src/omni_story/library/pipeline.py'))
    media = tmp_path / 'reference.mp4'
    media.write_bytes(b'minimal media')
    return dict(production_root=str(root), production_files={}, reference=str(media),
        reference_sha256=subject.digest(media), output=str(tmp_path / 'output'),
        configuration={}, asr=False, asr_model_dir=None,
        subject_cache_root=str(tmp_path / 'cache'))


def collected(run, value=None):
    value = value or dict(theme='行动')
    output = Path(run['output'])
    folder = output / 'production_stage/calls/call0'
    request = dict(arguments=dict(video_source=run['reference']), media_sha256=run['reference_sha256'])
    response = dict(result=value)
    artifacts = [write(folder / name, data) for name, data in
                 [('request.json', request), ('response.json', response), ('parsed.json', value)]]
    saved = dict(schema_version='production_reference_subject_v1',
        binding={k: run[k] for k in ('reference', 'reference_sha256', 'production_root',
                                    'production_files', 'configuration', 'asr_model_dir')},
        understanding=value, understanding_sha256=json_sha(value), call_id='call0',
        request_sha256=json_sha(request), response_sha256=json_sha(response),
        artifacts={str(path): subject.digest(path) for path in artifacts},
        media_path=run['reference'], media_sha256=run['reference_sha256'],
        manual_correction=False, status='reference_collected', note='fixture')
    write(output / 'subject.json', saved)
    return saved


def forbidden(*args, **kwargs):
    pytest.fail('production must not be called')


def test_capture_reference_reuses_across_output_and_media_paths(run, tmp_path, monkeypatch):
    calls = []
    def capture(job, *, provider_factory):
        calls.append(job)
        assert provider_factory is forbidden
        return collected(job)
    monkeypatch.setattr(subject, '_capture_reference', capture)
    saved = subject.capture_reference(run, provider_factory=forbidden)
    assert calls == [run]
    cache_value = json.loads(next(Path(run['subject_cache_root']).glob('*/understanding.json')).read_text())
    assert cache_value == saved['understanding']
    media = tmp_path / 'same-content.mp4'
    media.write_bytes(Path(run['reference']).read_bytes())
    reused_run = dict(run, reference=str(media), output=str(tmp_path / 'reused'), reuse_only=True)
    monkeypatch.setattr(subject, '_capture_reference', forbidden)
    reused = subject.capture_reference(reused_run, provider_factory=forbidden)
    assert reused == dict(saved, binding=dict(saved['binding'], reference=str(media)))
    assert json.loads((Path(reused_run['output']) / 'subject.json').read_text()) == reused
    subject.verify_subject(reused)


def test_capture_reference_imports_existing_completed_result(run, tmp_path, monkeypatch):
    saved = collected(run)
    monkeypatch.setattr(subject, '_capture_reference', lambda *a, **kw: saved)
    assert subject.capture_reference(run) == saved
    monkeypatch.setattr(subject, '_capture_reference', forbidden)
    assert subject.capture_reference(dict(run, output=str(tmp_path / 'reuse'), reuse_only=True)) == saved


def test_capture_reference_reuse_only_miss_never_produces(run, monkeypatch):
    from omni_story.library import pipeline
    monkeypatch.setattr(pipeline, 'execute', forbidden)
    with pytest.raises(ValueError, match='^subject_reuse_only_no_completed_result$'):
        subject.capture_reference(dict(run, reuse_only=True), provider_factory=forbidden)
    assert not Path(run['subject_cache_root']).exists()


@pytest.mark.parametrize('change', [dict(condition='prefix@5.0'), dict(production_release='another'),
    dict(prompt_sha256='new'), dict(configuration=dict(reference_context=dict(instruction='prefix')))])
def test_capture_reference_namespaces_do_not_cross(run, tmp_path, monkeypatch, change):
    capture = subject._capture_reference
    monkeypatch.setattr(subject, '_capture_reference', lambda job, **kw: collected(job))
    subject.capture_reference(run)
    reused = dict(run, output=str(tmp_path / 'different'), reuse_only=True, **change)
    monkeypatch.setattr(subject, '_capture_reference', capture)
    from omni_story.library import pipeline
    monkeypatch.setattr(pipeline, 'execute', forbidden)
    with pytest.raises(ValueError, match='^subject_reuse_only_no_completed_result$'):
        subject.capture_reference(reused, provider_factory=forbidden)


def test_capture_reference_corruption_never_falls_back_to_production(run, tmp_path, monkeypatch):
    monkeypatch.setattr(subject, '_capture_reference', lambda job, **kw: collected(job))
    subject.capture_reference(run)
    cached = next(Path(run['subject_cache_root']).glob('*/understanding.json'))
    write(cached, dict(theme='changed'))
    monkeypatch.setattr(subject, '_capture_reference', forbidden)
    with pytest.raises(ValueError, match='^subject_cache_corrupted$'):
        subject.capture_reference(dict(run, output=str(tmp_path / 'different')))


def test_capture_reference_verifies_before_caching(run, monkeypatch):
    def capture(job, **kwargs):
        saved = collected(job)
        saved['understanding']['theme'] = 'changed after receipt'
        return saved
    monkeypatch.setattr(subject, '_capture_reference', capture)
    with pytest.raises(ValueError, match='subject_understanding_changed'):
        subject.capture_reference(run)
    assert not Path(run['subject_cache_root']).exists()


def test_capture_reference_without_root_uses_legacy_path(monkeypatch):
    saved = dict(status='legacy fixture')
    run = dict(reuse_only=True)
    calls = []
    def capture(job, *, provider_factory):
        calls.append((job, provider_factory))
        return saved
    monkeypatch.setattr(subject, '_capture_reference', capture)
    monkeypatch.setattr(module.SubjectCache, '__init__', forbidden)
    assert subject.capture_reference(run, provider_factory=forbidden) is saved
    assert calls == [(run, forbidden)]
