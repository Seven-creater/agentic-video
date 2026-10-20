"""Validate a frozen evaluation set without submitting any model requests."""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
import re


METHODS = {'baseline_single_pass', 'full'}
SPLITS = {'development', 'heldout'}
TARGET_CASE_COUNT = 20


class ManifestError(ValueError):
    pass


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _identifier(value, label):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', value):
        raise ManifestError(f'{label}: expected a safe identifier')
    return value


def case_method_key(manifest, case, method):
    """Directory-independent identity: changing an output folder cannot retry a run."""
    experiment = _identifier(manifest['experiment_id'], 'experiment_id')
    case_id = _identifier(case['case_id'] if isinstance(case, dict) else case, 'case_id')
    if method not in METHODS:
        raise ManifestError('method: unsupported method')
    return f'{experiment}/{case_id}/{method}'


def _number(value, label, *, integer=False, minimum=0, strict=False):
    if (type(value) not in ((int,) if integer else (int, float)) or
            not math.isfinite(value) or value < minimum or (strict and value == minimum)):
        raise ManifestError(f'{label}: expected a finite {"integer" if integer else "number"} above {minimum}')


def _sha(value, label):
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value):
        raise ManifestError(f'{label}: expected a lowercase SHA-256')


def _bound_file(record, prefix, base_path, verify_files, *, path_key='path', sha_key='sha256'):
    if not isinstance(record, dict):
        raise ManifestError(f'{prefix}: expected an object')
    raw_path = record.get(path_key)
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ManifestError(f'{prefix}.{path_key}: required')
    _sha(record.get(sha_key), f'{prefix}.{sha_key}')
    path = Path(raw_path).expanduser()
    path = (path if path.is_absolute() else base_path / path).resolve()
    if verify_files:
        if not path.is_file():
            raise ManifestError(f'{prefix}: file missing: {path}')
        if file_sha256(path) != record[sha_key]:
            raise ManifestError(f'{prefix}: file hash mismatch: {path}')
    record[path_key] = str(path)


def validate_manifest(value, *, base_path='.', verify_files=True, require_complete=True):
    """Return a normalized copy; incomplete preparation sets require an explicit opt-out."""
    if not isinstance(value, dict):
        raise ManifestError('manifest: expected an object')
    result = copy.deepcopy(value)
    if type(result.get('schema_version')) is not int or result['schema_version'] != 1:
        raise ManifestError('schema_version: expected 1')
    experiment = _identifier(result.get('experiment_id'), 'experiment_id')
    split = result.get('split', 'heldout')
    if split not in SPLITS:
        raise ManifestError('split: expected development or heldout')
    result['split'] = split
    if result.get('target_case_count') != TARGET_CASE_COUNT:
        raise ManifestError('target_case_count: this protocol requires 20 heldout cases')
    methods = result.get('methods')
    if (not isinstance(methods, list) or len(methods) != 2 or
            any(not isinstance(method, str) for method in methods) or set(methods) != METHODS):
        raise ManifestError('methods: require baseline_single_pass and full exactly once')
    base_path = Path(base_path).resolve()
    library = result.get('library')
    if not isinstance(library, dict) or not isinstance(library.get('version'), str) or not library['version'].strip():
        raise ManifestError('library.version: required')
    _bound_file(library, 'library', base_path, verify_files,
                path_key='manifest_path', sha_key='manifest_sha256')
    for name in ('media_root', 'visual_root'):
        raw_path = library.get(name)
        if not isinstance(raw_path, str) or not Path(raw_path).is_absolute():
            raise ManifestError(f'library.{name}: require an absolute directory')
        path = Path(raw_path).resolve()
        if verify_files and not path.is_dir():
            raise ManifestError(f'library.{name}: directory missing: {path}')
        library[name] = str(path)
    spec = result.get('output_spec')
    if not isinstance(spec, dict):
        raise ManifestError('output_spec: required')
    if set(spec) != {'width', 'height', 'fps', 'duration_min_s', 'duration_max_s'}:
        raise ManifestError('output_spec: unknown or missing parameters')
    for name in ('width', 'height'):
        _number(spec.get(name), f'output_spec.{name}', integer=True, strict=True)
        if spec[name] % 2:
            raise ManifestError(f'output_spec.{name}: require an even dimension')
    for name in ('fps', 'duration_min_s', 'duration_max_s'):
        _number(spec.get(name), f'output_spec.{name}', strict=True)
    if spec['duration_min_s'] > spec['duration_max_s']:
        raise ManifestError('output_spec: duration_min_s exceeds duration_max_s')
    if (spec['width'], spec['height'], spec['fps']) != (1280, 720, 30):
        raise ManifestError('output_spec: current runner supports only 1280x720 at 30 fps')
    if spec['duration_max_s'] > 300:
        raise ManifestError('output_spec: duration_max_s must be at most 300')
    budget = result.get('budget')
    if not isinstance(budget, dict):
        raise ManifestError('budget: required per-run caps')
    _number(budget.get('max_model_calls'), 'budget.max_model_calls', integer=True, strict=True)
    _number(budget.get('max_wall_time_s'), 'budget.max_wall_time_s', strict=True)
    _number(budget.get('max_cost_cny'), 'budget.max_cost_cny')
    if 'per_run_max_model_calls' in budget:
        _number(budget['per_run_max_model_calls'], 'budget.per_run_max_model_calls', integer=True, strict=True)
        if budget['per_run_max_model_calls'] > budget['max_model_calls']:
            raise ManifestError('budget: per-run calls exceed shared experiment cap')
    if 'max_revision_rounds' in budget:
        raise ManifestError('budget.max_revision_rounds: unsupported hard cap; describe core_default_revision_policy')
    if 'core_default_revision_policy' in budget and (
            not isinstance(budget['core_default_revision_policy'], str) or not budget['core_default_revision_policy'].strip()):
        raise ManifestError('budget.core_default_revision_policy: expected descriptive text')

    development = result.get('development_references')
    if not isinstance(development, list) or not development:
        raise ManifestError('development_references: list known development references before freezing')
    development_topics, development_hashes, development_pairs = set(), set(), set()
    for row in development:
        if not isinstance(row, dict) or not isinstance(row.get('topic_id'), str) or not row['topic_id'].strip():
            raise ManifestError('development_references.topic_id: required')
        _sha(row.get('sha256'), 'development_references.sha256')
        development_topics.add(row['topic_id'])
        development_hashes.add(row['sha256'])
        development_pairs.add((row['topic_id'], row['sha256']))

    cases = result.get('cases')
    if not isinstance(cases, list) or not cases:
        raise ManifestError('cases: expected a nonempty list')
    ids, topics, hashes = set(), set(), set()
    for case in cases:
        if not isinstance(case, dict):
            raise ManifestError('case: expected an object')
        case_id = _identifier(case.get('case_id'), 'case.case_id')
        topic = case.get('topic_id')
        if not isinstance(topic, str) or not topic.strip():
            raise ManifestError('case.topic_id: required')
        case['split'] = case.get('split', split)
        if case['split'] not in SPLITS:
            raise ManifestError('case.split: expected development or heldout')
        reference = case.get('reference')
        _bound_file(reference, f'case {case_id}.reference', base_path, verify_files)
        reference_sha = reference['sha256']
        for index, source in enumerate(case.get('source_evidence', [])):
            _bound_file(source, f'case {case_id}.source_evidence[{index}]', base_path, verify_files)
        if case_id in ids or topic in topics or reference_sha in hashes:
            raise ManifestError(f'case {case_id}: duplicate case_id, topic_id or reference SHA-256')
        if case['split'] == 'heldout' and (topic in development_topics or reference_sha in development_hashes):
            raise ManifestError(f'case {case_id}: development reference leaked into heldout set')
        if case['split'] == 'development' and (topic, reference_sha) not in development_pairs:
            raise ManifestError(f'case {case_id}: declare development reference before using it')
        ids.add(case_id)
        topics.add(topic)
        hashes.add(reference_sha)
        case['case_key'] = f'{experiment}/{case_id}'

    heldout = [case for case in cases if case['split'] == 'heldout']
    if len(heldout) > TARGET_CASE_COUNT:
        raise ManifestError('cases: freeze exactly 20 heldout cases')
    ready = len(heldout) == TARGET_CASE_COUNT
    if require_complete and not ready:
        raise ManifestError(f'cases: heldout set incomplete ({len(heldout)}/20)')
    result['evaluation_ready'] = ready

    expected = {case_method_key(result, case, method): (case['case_id'], method)
                for case in cases for method in methods}
    runs = result.get('planned_runs')
    if not isinstance(runs, list):
        raise ManifestError('planned_runs: freeze every case and method combination')
    seen = set()
    for run in runs:
        if not isinstance(run, dict):
            raise ManifestError('planned_runs: expected objects')
        run_id = run.get('run_id')
        if not isinstance(run_id, str) or run_id not in expected or run_id in seen:
            raise ManifestError('planned_runs: duplicate or unexpected run_id')
        if (run.get('case_id'), run.get('method_id')) != expected[run_id]:
            raise ManifestError('planned_runs: run identity mismatch')
        seen.add(run_id)
    if seen != set(expected):
        raise ManifestError('planned_runs: missing case and method combination')
    return result


def load_manifest(path, *, verify_files=True, require_complete=True):
    path = Path(path).expanduser().resolve()
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as error:
        raise ManifestError(f'invalid manifest JSON: {error}') from error
    result = validate_manifest(value, base_path=path.parent, verify_files=verify_files,
                               require_complete=require_complete)
    result['source_manifest_path'] = str(path)
    result['source_manifest_sha256'] = hashlib.sha256(raw).hexdigest()
    return result
