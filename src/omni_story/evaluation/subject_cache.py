"""Content-addressed storage for validated, received subject returns."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile


KEY_FIELDS = ('media_sha256', 'condition', 'production_release', 'prompt_sha256')


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _received(value):
    if (not isinstance(value, dict) or value.get('validated') is False or
            value.get('status') not in {None, 'received', 'validated', 'reference_collected'}):
        raise ValueError('subject_cache_unverified')


class SubjectCache:
    def __init__(self, root, *, production_release=None, prompt_sha256=None):
        self.root = Path(root)
        self.production_release = production_release
        self.prompt_sha256 = prompt_sha256

    def _identity(self, key_fields):
        fields = {name: key_fields[name] for name in KEY_FIELDS}
        if any(not isinstance(value, str) or not value for value in fields.values()):
            raise ValueError('subject_cache_identity_required')
        return fields, self.root / _sha(_json(fields))

    def _load(self, key_fields):
        fields, folder = self._identity(key_fields)
        if not folder.exists():
            return None, None
        try:
            meta = json.loads((folder / 'meta.json').read_bytes())
            raw = (folder / 'understanding.json').read_bytes()
            if meta['key_fields'] != fields or _sha(raw) != meta['understanding_sha256']:
                raise ValueError('subject_cache_corrupted')
            value = json.loads(raw)
            _received(value)
            if not meta['receipts']:
                raise ValueError('subject_cache_corrupted')
            for path, expected in meta['receipts'].items():
                if _sha(Path(path).read_bytes()) != expected:
                    raise ValueError('subject_cache_corrupted')
            return value, meta
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            raise ValueError('subject_cache_corrupted') from error

    def get(self, key_fields):
        return self._load(key_fields)[0]

    def receipt_paths(self, key_fields):
        value, meta = self._load(key_fields)
        return list(meta['receipts']) if value is not None else []

    def put(self, key_fields, understanding, receipt_paths):
        """The caller validates the return before supplying its original receipts."""
        fields, folder = self._identity(key_fields)
        _received(understanding)
        raw = _json(understanding)
        receipts = {}
        for path in receipt_paths:
            path = Path(path).resolve(strict=True)
            receipt = path.read_bytes()
            if path.suffix == '.json':
                value = json.loads(receipt)
                if isinstance(value, dict):
                    _received(value)
            receipts[str(path)] = _sha(receipt)
        if not receipts:
            raise ValueError('subject_cache_receipts_required')
        existing = self.get(fields)
        if existing is not None:
            if _json(existing) != raw:
                raise ValueError('subject_cache_conflict')
            return
        meta = dict(key_fields=fields, understanding_sha256=_sha(raw), receipts=receipts,
                    written_at=datetime.now(timezone.utc).isoformat())
        self.root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.subject-', dir=self.root) as temporary:
            staging = Path(temporary)
            for name, data in (('understanding.json', raw), ('meta.json', _json(meta))):
                with (staging / name).open('wb') as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
            try:
                os.rename(staging, folder)
            except OSError:
                existing = self.get(fields)
                if existing is None:
                    raise
                if _json(existing) != raw:
                    raise ValueError('subject_cache_conflict')


def understand(media_path, media_sha256, condition, *, cache, produce):
    """produce() validates media_path and returns (understanding, receipt_paths).

    The cache supplies production_release and prompt_sha256 for this adapter.
    A successful producer return attests receipt and validation; pending calls
    must raise rather than return an understanding.
    """
    fields = dict(media_sha256=media_sha256, condition=condition,
                  production_release=cache.production_release, prompt_sha256=cache.prompt_sha256)
    value = cache.get(fields)
    if value is not None:
        return value, 'cache_hit'
    value, receipts = produce()
    cache.put(fields, value, receipts)
    return value, 'produced'
