"""Local Hunter uploads and a frozen template evaluation, with no model calls."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess

from ..library.media import MEDIA_EXTENSIONS, probe_media, sha256_file
from ..library.state import file_lock, json_sha, write_json

SCHEMA = 'template_evaluation_v1'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def safe_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value):
        raise ValueError('template_evaluation_invalid_id')
    return value


def inventory(root):
    """Group by uploaded folder, not guessed title. Never modify an upload."""
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError('template_upload_directory_required')
    groups, errors, seen = {}, [], {}
    for path in sorted(root.rglob('*')):
        if path.suffix.lower() not in MEDIA_EXTENSIONS or not path.is_file():
            continue
        if path.is_symlink() or root not in path.resolve().parents:
            errors.append(dict(path=str(path), error='upload_symlink_not_allowed'))
            continue
        try:
            before = path.stat()
            facts = probe_media(path)
            subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-threads', '2',
                '-filter_threads', '2', '-filter_complex_threads', '2',
                '-i', str(path), '-map', '0:v:0', '-map', '0:a?', '-threads', '2', '-f', 'null', '-'],
                check=True, capture_output=True, timeout=600)
            digest = sha256_file(path)
            if (before.st_size, before.st_mtime_ns) != (path.stat().st_size, path.stat().st_mtime_ns):
                raise ValueError('upload_changed_during_inventory')
            folder = path.parent.relative_to(root).as_posix()
            meta = path.parent / 'metadata.json'
            row = dict(asset_id='v_' + digest, sha256=digest, path=str(path.resolve()),
                bytes=before.st_size, duration_s=facts['duration_s'],
                has_audio_track=any(s.get('codec_type') == 'audio' for s in facts['streams']),
                duplicate_group='exact_' + digest,
                metadata_sha256=sha256_file(meta) if meta.exists() else None,
                curation_status='uploaded_not_independently_verified')
            groups.setdefault(folder, []).append(row)
            seen.setdefault(digest, []).append(str(path))
        except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
            errors.append(dict(path=str(path), error=type(exc).__name__))
    return dict(schema_version='template_upload_inventory_v1', root=str(root),
        created_at=datetime.now(timezone.utc).isoformat(),
        templates=[dict(folder=k, assets=v) for k, v in sorted(groups.items())], errors=errors,
        exact_duplicates=[v for v in seen.values() if len(v) > 1],
        near_duplicate_review='not_performed', model_requests_sent=0)


def draft(scan, experiment_id, *, support_count=4, query_count=2):
    safe_id(experiment_id)
    if type(support_count) is not int or type(query_count) is not int or min(support_count, query_count) < 1:
        raise ValueError('template_split_requires_positive_counts')
    templates = []
    for group in scan['templates']:
        assets = sorted(group['assets'], key=lambda a: a['sha256'])
        identity = 't_' + json_sha(group['folder'])[:16]
        templates.append(dict(template_id=identity, display_name=group['folder'],
            family='unknown', assets=assets, support=[a['asset_id'] for a in assets[:support_count]],
            query=[a['asset_id'] for a in assets[support_count:support_count + query_count]],
            dedup_reviewed=False, rubric=dict(elements=[], order_edges=[])))
    return dict(schema_version=SCHEMA, experiment_id=experiment_id, split='development',
        methods=['single_reference', 'multi_reference'], templates=templates,
        readiness='draft_requires_split_review_and_rubric', upload_errors=scan['errors'],
        scoring=dict(element_scale=[0, 1, 2], required_weight=2, optional_weight=1),
        model_requests_sent=0)


def validate(value, *, verify_files=True):
    if value.get('schema_version') != SCHEMA or value.get('split') not in {'development', 'test'}:
        raise ValueError('template_evaluation_schema_or_split_invalid')
    if value.get('upload_errors'):
        raise ValueError('template_evaluation_upload_errors_must_be_resolved_before_freeze')
    safe_id(value.get('experiment_id'))
    methods = value.get('methods', [])
    if not methods or len(set(methods)) != len(methods):
        raise ValueError('template_evaluation_methods_required')
    for method in methods:
        safe_id(method)
    if value.get('scoring') != dict(element_scale=[0, 1, 2], required_weight=2, optional_weight=1):
        raise ValueError('template_evaluation_scoring_contract_changed')
    templates = value.get('templates', [])
    if not templates or len({t['template_id'] for t in templates}) != len(templates):
        raise ValueError('template_evaluation_unique_templates_required')
    support_groups, query_groups = set(), set()
    for template in templates:
        safe_id(template['template_id'])
        if template.get('family') not in {'A', 'B', 'unknown'} or template.get('dedup_reviewed') is not True:
            raise ValueError('template_evaluation_requires_explicit_duplicate_review')
        assets = template['assets']
        by_id = {a['asset_id']: a for a in assets}
        if len(by_id) != len(assets):
            raise ValueError('template_evaluation_duplicate_asset')
        for role, groups in [('support', support_groups), ('query', query_groups)]:
            ids = template[role]
            if not ids or len(set(ids)) != len(ids) or any(a not in by_id for a in ids):
                raise ValueError('template_evaluation_invalid_support_query')
            groups.update(by_id[a]['duplicate_group'] for a in ids)
        if set(template['support']) & set(template['query']):
            raise ValueError('template_evaluation_support_query_leakage')
        for asset in assets:
            if (not re.fullmatch(r'[a-f0-9]{64}', asset['sha256']) or
                    asset['asset_id'] != 'v_' + asset['sha256'] or
                    not isinstance(asset.get('duplicate_group'), str) or not asset['duplicate_group']):
                raise ValueError('template_evaluation_asset_identity_invalid')
            if verify_files and sha256_file(asset['path']) != asset['sha256']:
                raise ValueError('template_evaluation_media_changed')
        rubric = template['rubric']
        elements = rubric['elements']
        keys = {safe_id(e['id']) for e in elements}
        if not elements or len(keys) != len(elements) or not any(e['required'] is True for e in elements):
            raise ValueError('template_evaluation_rubric_required')
        for element in elements:
            if (type(element['required']) is not bool or not isinstance(element['description'], str)
                    or not element['description'].strip()):
                raise ValueError('template_evaluation_rubric_invalid')
        edges = rubric['order_edges']
        if len({tuple(edge) for edge in edges}) != len(edges) or any(
                len(e) != 2 or e[0] == e[1] or not set(e) <= keys for e in edges):
            raise ValueError('template_evaluation_order_edges_invalid')
    # Check both exact bytes AND reviewed duplicate groups globally. Same media
    # cannot cross sides under another template name or a different group label.
    support_sha = {a['sha256'] for t in templates for a in t['assets'] if a['asset_id'] in t['support']}
    query_sha = {a['sha256'] for t in templates for a in t['assets'] if a['asset_id'] in t['query']}
    if support_groups & query_groups or support_sha & query_sha:
        raise ValueError('template_evaluation_duplicate_group_leakage')
    return value


def freeze(value, target):
    validate(value)
    frozen = dict(value, readiness='frozen')
    frozen['protocol_sha256'] = json_sha(frozen)
    target = Path(target)
    with file_lock(target.with_suffix('.lock')):
        if target.exists():
            if read(target) != frozen:
                raise ValueError('template_evaluation_frozen_protocol_cannot_change')
        else:
            write_json(target, frozen)
    return frozen


def load(path, *, verify_files=True):
    value = read(path)
    original = {k: v for k, v in value.items() if k != 'protocol_sha256'}
    if value.get('readiness') != 'frozen' or json_sha(original) != value.get('protocol_sha256'):
        raise ValueError('template_evaluation_frozen_identity_changed')
    return validate(value, verify_files=verify_files)


def learner_inputs(template, method):
    """No rubric, template title, query, producer analysis or author metadata."""
    if method not in {'single_reference', 'multi_reference'}:
        raise ValueError('template_understanding_method_not_implemented')
    ids = template['support'][:1] if method == 'single_reference' else template['support']
    by_id = {a['asset_id']: a for a in template['assets']}
    return [dict(asset_id=a, path=by_id[a]['path'], sha256=by_id[a]['sha256']) for a in ids]
