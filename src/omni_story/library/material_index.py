"""Offline event-level material index with fused multi-channel retrieval (plan C, phase 1).

Reads received library observations plus film shot caches and writes small local
artefacts (records.jsonl, bm25.json, float32 .npy vector files). Retrieval runs
at event / usable-range granularity over separated text fields, so content
matches (who/action/state/expression/place/speech) never ride on craft jargon;
camera/continuity fields stay attached to records but outside the content
channels. Channels are fused by Reciprocal Rank Fusion over ranks only — raw
scores from different channels are never added.

Phase 1 is deliberately standalone: the creative pipeline keeps its current
retrieval until the evaluation harness justifies integration.
"""
from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path

import numpy as np

from .film_cast import canonical_name, overlaps_stylized
from .state import json_sha, write_json

LOGGER = logging.getLogger('material_index')

SCHEMA_VERSION = 'material_index_v1'
RECORDS_NAME = 'records.jsonl'
BM25_NAME = 'bm25.json'
TEXT_VECTORS_NAME = 'text_vectors.npy'
TEXT_IDS_NAME = 'text_ids.json'
IMAGE_VECTORS_NAME = 'image_vectors.npy'
IMAGE_SHOTS_NAME = 'image_shots.json'
MANIFEST_NAME = 'manifest.json'

# Content channels see these fields only; craft text lives on the record but
# never enters BM25 documents, dense text or image queries.
CONTENT_FIELDS = ('who', 'action', 'state', 'expression', 'place', 'speech')
CRAFT_FIELDS = ('camera',)
DEFAULT_FIELD_WEIGHTS = {'who': 1.0, 'action': 1.6, 'state': 0.9,
                         'expression': 0.8, 'place': 1.0, 'speech': 0.5}

DEFAULT_TOP_K = 50
DEFAULT_FINAL_K = 20
DEFAULT_RRF_K = 60
CHANNEL_NAMES = ('bm25', 'text', 'image')

# Single-character function words and multi-character editing jargon. Bigrams of
# craft phrasing (镜头/切换/特写/画面…) must not drive content retrieval, because
# they appear in nearly every observation card and produced the flat scores of
# the window-level baseline.
_SINGLE_CHAR_STOPWORDS = '的了在是和与或一上下之间等个们地得着过也都就还又再很不太没我你他她它这那有对从到向跟给让被把而为并只'
_JARGON_STOPWORDS = (
    '镜头', '切换', '特写', '近景', '中景', '远景', '画面', '景别', '全景', '空镜',
    '机位', '拍摄', '摄影', '剪辑', '剪接', '转场', '构图', '运镜', '推近', '拉远',
    '横摇', '摇镜', '跟拍', '仰拍', '俯拍', '平视', '视角', '可见', '不可见', '无声',
    '画外', '固定', '持续', '随后', '然后', '因为', '所以', '如果', '虽然', '于是',
    '其中', '以及', '这个', '那个', '什么', '怎么', '一些', '只是', '不过',
)
DEFAULT_STOPWORDS = frozenset(_SINGLE_CHAR_STOPWORDS) | frozenset(_JARGON_STOPWORDS)

_ASCII_RUN = re.compile(r'[a-z0-9]+')
_CJK_RUN = re.compile(r'[㐀-鿿]+')
# Multi-character stopwords are stripped from the text before bigrams form, so
# jargon phrases cannot leak cross-boundary bigrams (画面切换 → 面切).
_JARGON_PATTERN = re.compile('|'.join(sorted(_JARGON_STOPWORDS, key=len, reverse=True)))


def tokenize(text):
    """Word-ish Chinese tokens: character bigrams plus ASCII words, minus stopwords."""
    lowered = _JARGON_PATTERN.sub(' ', (text or '').lower())
    tokens = []
    for run in _CJK_RUN.findall(lowered):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[index:index + 2] for index in range(len(run) - 1))
    tokens.extend(_ASCII_RUN.findall(lowered))
    return [token for token in tokens if token not in DEFAULT_STOPWORDS]


def has_dialogue_hint(text):
    """Retrieval hint only; nonempty sound-effect/narration notes are not mouths."""
    negative = r'无(?:需|须)?(?:角色)?(?:对白|对话|说话|动嘴)|未(?:说话|动嘴|记录)|不(?:说话|适合|可见)|闭(?:嘴|合)|沉默|未知|insufficient|no (?:speech|dialogue)|mouth closed'
    positive = r'对白|对话|说话|开口|动嘴|嘴.{0,8}(?:动作|开合|张开|张合|张大|清晰|可见)|speaking|talking|dialogue|mouth'
    return any(not re.search(negative, part, re.I) and re.search(positive, part, re.I)
               for part in re.split(r'[。；;，,]', text or ''))


def content_text(record):
    """The document string fed to dense text channels (content fields only)."""
    return ' '.join((record.get(field) or '') for field in CONTENT_FIELDS).strip()


def _join_parts(parts, separator='；'):
    """Join non-empty strings, dropping duplicates while keeping first order."""
    seen, result = set(), []
    for part in parts:
        text = str(part or '').strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return separator.join(result)


# ---------------------------------------------------------------------------
# Record expansion


def iter_effective_windows(library):
    """Yield effective windows from a VisualLibrary (or an iterable of windows)."""
    if hasattr(library, 'effective_window'):
        excluded = library.quality_excluded_ids() if hasattr(library, 'quality_excluded_ids') else set()
        for path in sorted(Path(library.records).glob('*.json')):
            record = json.loads(path.read_text(encoding='utf-8'))
            if record['observation_id'] in excluded:
                continue
            window = library.effective_window(record)
            window['observation_id'] = record.get('observation_id', '')
            yield window
    else:
        for window in library:
            yield window


def build_records(library, *, cast=None, entity_names=None, film_by_sha=None):
    """Expand effective windows into event and usable-range retrieval records.

    One record per event and one per usable range, each carrying separated text
    fields, neighbour links, resolved entity ids (plus names when the optional
    labelling file knows them) and source-time provenance. Ranges inside the
    cast's stylized (2D) spans are excluded entirely.
    """
    records = []
    for window in iter_effective_windows(library):
        records.extend(window_records(window, cast=cast, entity_names=entity_names or {},
                                      film_by_sha=film_by_sha or {}))
    records.sort(key=lambda record: (record['source_sha256'], record['window_source_start_s'],
                                     record['window_id'], 0 if record['kind'] == 'event' else 1,
                                     record['record_id']))
    for record in records:
        record['record_sha256'] = json_sha(record)
    return records


def window_records(window, *, cast, entity_names, film_by_sha):
    # Naming workflow envelopes publish only completed, positively named identities.
    enveloped = 'entities' in entity_names
    entity_names = entity_names.get('entities', entity_names)
    observed = window.get('observation', {}) or {}
    rejected_roles, rejected_events, rejected_ranges = set(), set(), set()
    if window.get('source_visual_policy') == 'silent_subtitle_visible_v1':
        from .subtitle_library import rejected_visual_evidence
        rejected_roles, rejected_events, rejected_ranges = rejected_visual_evidence(window)
    sha = window['source_sha256']
    base = float(window['source_start_s'])
    film = film_by_sha.get(sha)
    stylized = (cast or {}).get('stylized_ranges', {}).get(sha, []) if cast else []
    scene = observed.get('scene', {}) or {}
    place = _join_parts([scene.get('place', ''), scene.get('lighting', '')])
    summary = (observed.get('summary') or '').strip()
    if rejected_roles:
        place = ''
        summary = _join_parts([row.get('observed_fact','') for i,row in enumerate(observed.get('events',[]))
                               if i not in rejected_events])
    roles = {role.get('role_id'): role for role in observed.get('roles', []) if role.get('role_id')}

    def role_row(role_id):
        role = roles.get(role_id, {}) or {}
        entity_id = role.get('entity_id') or ''
        label = entity_names.get(entity_id) or {}
        name = (label.get('name') or '') if label.get('status', None if enveloped else 'named') == 'named' else ''
        return {'role_id': role_id, 'entity_id': entity_id,
                'identity_confirmed': bool(role.get('identity_confirmed')), 'name': name}

    def who_text(row_ids):
        parts = []
        for row_id in dict.fromkeys(row_ids):
            role = roles.get(row_id, {}) or {}
            label = entity_names_lookup(row_id)
            description = _join_parts([role.get('state', ''), role.get('appearance', '')], '，')
            parts.append((label + '：' + description) if label else description)
        return _join_parts(parts)

    def entity_names_lookup(role_id):
        row = role_row(role_id)
        return row['name']

    def label_of(role_id):
        # Real records sometimes give several actors/recipients as a list.
        if isinstance(role_id, list):
            return '、'.join(label for label in (label_of(item) for item in role_id) if label)
        return entity_names_lookup(role_id) if isinstance(role_id, str) and role_id else ''

    def action_text(event):
        actor, recipient = label_of(event.get('actor_id')), label_of(event.get('recipient_id'))
        segments = []
        if actor or event.get('action'):
            segments.append((actor or '') + (event.get('action') or ''))
        if event.get('object'):
            segments.append(str(event['object']))
        if recipient:
            segments.append('→' + recipient)
        parts = ['，'.join(segments)] if segments else []
        if event.get('observed_fact'):
            parts.append(str(event['observed_fact']))
        return _join_parts(parts)

    def state_text(event):
        parts = []
        if event.get('before_state'):
            parts.append('前：' + str(event['before_state']))
        if event.get('after_state'):
            parts.append('后：' + str(event['after_state']))
        flag = event.get('complete_action_visible')
        if window.get('source_visual_policy') == 'silent_subtitle_visible_v1':
            parts.append('动作先后及完整性为模型粗标注，剪辑前须复看')
        elif flag is True:
            parts.append('动作过程完整可见')
        elif flag is False:
            parts.append('动作过程不完整可见')
        return _join_parts(parts)

    events = list(observed.get('events', []))
    kept_ranges, excluded = [], 0
    for index, span in enumerate(observed.get('usable_ranges', [])):
        if index in rejected_ranges:
            continue
        start, end = base + float(span.get('local_in_s', 0)), base + float(span.get('local_out_s', 0))
        if not start < end:
            LOGGER.warning('material_index:zero_length_range:%s:r%d', window.get('window_id'), index)
            continue
        if stylized and overlaps_stylized(start, end, stylized):
            excluded += 1
            continue
        kept_ranges.append((index, span, start, end))
    if excluded:
        LOGGER.info('material_index:stylized_ranges_excluded:%s:%d', window.get('window_id'), excluded)

    ranges_by_event = {}
    for index, span, start, end in kept_ranges:
        for event_index in span.get('event_indices', []):
            ranges_by_event.setdefault(event_index, []).append((index, span, start, end))

    event_ids = [f"{window['window_id']}:e{index:03d}" for index in range(len(events))]
    retained_indices = [i for i in range(len(events)) if i not in rejected_events]
    neighbors = {index:(event_ids[retained_indices[pos-1]] if pos else '',
                       event_ids[retained_indices[pos+1]] if pos+1 < len(retained_indices) else '')
                 for pos,index in enumerate(retained_indices)}
    records = []
    for index, event in enumerate(events):
        if index in rejected_events:
            continue
        covering = ranges_by_event.get(index, [])
        row_ids = [role_id for role_id in event.get('role_ids', []) if isinstance(role_id, str) and role_id]
        rows = [role_row(role_id) for role_id in dict.fromkeys(row_ids)]
        records.append({
            'record_id': event_ids[index], 'kind': 'event', 'event_index': index,
            'observation_id': window.get('observation_id', ''),
            'window_id': window['window_id'], 'source_id': window.get('source_id', ''),
            'source_sha256': sha, 'film': film,
            'source_start_s': round(base + float(event.get('local_start_s', 0)), 3),
            'source_end_s': round(base + float(event.get('local_end_s', 0)), 3),
            'window_source_start_s': round(base, 3),
            'window_source_end_s': round(float(window['source_end_s']), 3),
            'local_start_s': float(event.get('local_start_s', 0)),
            'local_end_s': float(event.get('local_end_s', 0)),
            'prev_event_id': neighbors[index][0],
            'next_event_id': neighbors[index][1],
            'event_ids': [], 'range_ids': [f"{window['window_id']}:r{rindex:03d}" for rindex, _, _, _ in covering],
            'roles': rows,
            'entity_ids': sorted({row['entity_id'] for row in rows if row['entity_id']}),
            'names': sorted({row['name'] for row in rows if row['name']}),
            'who': who_text(row_ids),
            'action': action_text(event),
            'state': state_text(event),
            'expression': str(event.get('expression_cues') or ''),
            'place': place,
            'speech': _join_parts([range_row.get('speech_fit', '') for _, range_row, _, _ in covering]),
            'camera': _join_parts([part for _, range_row, _, _ in covering
                                   for part in (range_row.get('camera', ''), range_row.get('shot_role', ''),
                                                range_row.get('continuity_notes', ''),
                                                scene.get('continuity', ''))]),
            'window_summary': summary,
            'usable_duration_s': round(max([end - start for _, _, start, end in covering] or [0.0]), 3),
        })
    for index, span, start, end in kept_ranges:
        linked = [event_index for event_index in span.get('event_indices', [])
                  if 0 <= event_index < len(events)]
        row_ids = [role_id for role_id in span.get('role_ids', []) or [] if isinstance(role_id, str) and role_id]
        for event_index in linked:
            row_ids.extend(role_id for role_id in events[event_index].get('role_ids', []) or []
                           if isinstance(role_id, str) and role_id)
        rows = [role_row(role_id) for role_id in dict.fromkeys(row_ids)]
        records.append({
            'record_id': f"{window['window_id']}:r{index:03d}", 'kind': 'range', 'range_index': index,
            'observation_id': window.get('observation_id', ''),
            'window_id': window['window_id'], 'source_id': window.get('source_id', ''),
            'source_sha256': sha, 'film': film,
            'source_start_s': round(start, 3), 'source_end_s': round(end, 3),
            'window_source_start_s': round(base, 3),
            'window_source_end_s': round(float(window['source_end_s']), 3),
            'local_in_s': float(span.get('local_in_s', 0)), 'local_out_s': float(span.get('local_out_s', 0)),
            'prev_event_id': '', 'next_event_id': '',
            'event_ids': [event_ids[event_index] for event_index in linked], 'range_ids': [],
            'roles': rows,
            'entity_ids': sorted({row['entity_id'] for row in rows if row['entity_id']}),
            'names': sorted({row['name'] for row in rows if row['name']}),
            'who': who_text(row_ids),
            'action': _join_parts([action_text(events[event_index]) for event_index in linked]),
            'state': _join_parts([state_text(events[event_index]) for event_index in linked]),
            'expression': _join_parts([events[event_index].get('expression_cues', '') for event_index in linked]),
            'place': place,
            'speech': str(span.get('speech_fit') or ''),
            'camera': _join_parts([span.get('camera', ''), span.get('shot_role', ''),
                                   span.get('continuity_notes', ''), scene.get('continuity', '')]),
            'window_summary': summary,
            'duration_s': round(end - start, 3),
            'shot_role': span.get('shot_role', ''),
        })
    if window.get('source_visual_policy') == 'silent_subtitle_visible_v1':
        from .subtitle_library import text_hints, story_hints
        # Whole-library classification is a retrieval hint. A false title label
        # must not silently delete a real interaction before selected-scene review.
        for record in records:
            record['source_text'] = text_hints([t for t in observed.get('source_text', []) if
                base+t['local_start_s'] < record['source_end_s'] and record['source_start_s'] <= base+t['local_end_s']])
            record['story_segment_hints'] = [s for s in story_hints(window) if
                base+s['local_start_s'] < record['source_end_s'] and record['source_start_s'] < base+s['local_end_s']]
            record['requires_selected_scene_observation'] = True
            record['source_visual_policy'] = window['source_visual_policy']
            record['subtitle_treatment'] = 'local_translucent_mosaic_then_new_captions'
    return records


# ---------------------------------------------------------------------------
# Shot caches


def load_shot_caches(root):
    """Light read of film_*/shots.json; only per-shot frames are used here."""
    root = Path(root)
    caches = []
    for path in sorted(root.glob('film_*/shots.json')):
        saved = json.loads(path.read_text(encoding='utf-8'))
        info = saved.get('info', {}) or {}
        film = info.get('film')
        if film is None and path.parent.name.startswith('film_'):
            film = int(path.parent.name.split('_')[-1])
        sha = saved.get('source_sha256') or info.get('source_sha256')
        shots = []
        for row in saved.get('frames', []):
            if any(not isinstance(row.get(key), (int, float)) for key in ('shot_start', 'shot_end')):
                LOGGER.warning('material_index:shot_times_missing:%s:%s', path, row.get('n'))
                continue
            shots.append({
                'shot_id': f"film{film}:{int(row.get('n', 0)):06d}",
                'film': film, 'n': int(row.get('n', 0)),
                'timestamp': float(row.get('timestamp') or 0),
                'shot_start': float(row['shot_start']), 'shot_end': float(row['shot_end']),
                'keyframe': str(_resolve_media(root, path.parent, row.get('keyframe')) or ''),
            })
        caches.append({'film': film, 'title': info.get('title', ''), 'duration': info.get('duration'),
                       'source_sha256': sha, 'shots': shots, 'path': str(path)})
    return caches


def _resolve_media(root, film_dir, value):
    """Absolute keyframe paths, or relative to film_N / cache root (as question_first)."""
    if not value:
        return None
    media = Path(value)
    if media.is_absolute():
        return media
    local = film_dir / media
    if local.is_file():
        return local
    fallback = root / media
    if fallback.is_file():
        return fallback
    return None


def resolve_film_by_sha(shot_caches=None, explicit=None):
    """Map source_sha256 -> film number; explicit mapping wins, conflicts fail."""
    mapping = {}
    for cache in shot_caches or []:
        sha = cache.get('source_sha256')
        if not sha:
            continue
        if sha in mapping and mapping[sha] != cache['film']:
            raise ValueError('material_index:conflicting_film_mapping:' + sha)
        mapping[sha] = cache['film']
    for sha, film in (explicit or {}).items():
        if sha in mapping and mapping[sha] != film:
            raise ValueError('material_index:conflicting_film_mapping:' + sha)
        mapping[sha] = film
    return mapping


def map_shots_to_records(records, shots):
    """Image hits enter retrieval as the records their shots overlap in source time."""
    by_film = {}
    for record in records:
        by_film.setdefault(record.get('film'), []).append(record)
    result = {}
    for shot in shots:
        hits = [record['record_id'] for record in by_film.get(shot['film'], [])
                if max(record['source_start_s'], shot['shot_start']) < min(record['source_end_s'], shot['shot_end'])]
        if hits:
            result[shot['shot_id']] = hits
    return result


# ---------------------------------------------------------------------------
# Keyword channel


class BM25Index:
    """Okapi BM25 over weighted content fields, pure Python + numpy."""

    def __init__(self, *, field_weights=None, k1=1.5, b=0.75):
        self.field_weights = {**DEFAULT_FIELD_WEIGHTS, **(field_weights or {})}
        self.k1, self.b = float(k1), float(b)
        self.ids, self.postings, self.doc_len, self.doc_tokens = [], {}, [], []
        self._by_id = {}
        self.avgdl = 0.0

    def fit(self, docs):
        """docs: iterable of (record_id, {field: text}) over content fields."""
        self.ids, self.postings, self.doc_len, self.doc_tokens = [], {}, [], []
        for ident, fields in docs:
            counts, tokens, length = {}, set(), 0.0
            for field, text in fields.items():
                weight = float(self.field_weights.get(field, 1.0))
                for token in tokenize(text):
                    tokens.add(token)
                    counts[token] = counts.get(token, 0.0) + weight
                    length += weight
            index = len(self.ids)
            self.ids.append(ident)
            self.doc_tokens.append(tokens)
            self.doc_len.append(length)
            for token, tf in counts.items():
                self.postings.setdefault(token, []).append((index, tf))
        self._by_id = {ident: index for index, ident in enumerate(self.ids)}
        self.avgdl = (sum(self.doc_len) / len(self.doc_len)) if self.doc_len else 0.0
        return self

    def content_tokens(self, ident):
        index = self._by_id.get(ident)
        return self.doc_tokens[index] if index is not None else set()

    def search(self, query, top_k=DEFAULT_TOP_K):
        """Ranked [(record_id, score)]; queries with no content tokens return []."""
        scores = np.zeros(len(self.ids), dtype=np.float64)
        for token in set(tokenize(query)):
            posting = self.postings.get(token)
            if not posting:
                continue
            idf = math.log(1.0 + (len(self.ids) - len(posting) + 0.5) / (len(posting) + 0.5))
            for index, tf in posting:
                denominator = tf + self.k1 * (1.0 - self.b + self.b *
                                              (self.doc_len[index] / self.avgdl if self.avgdl else 1.0))
                scores[index] += idf * tf * (self.k1 + 1.0) / denominator
        order = sorted(range(len(self.ids)), key=lambda index: (-scores[index], self.ids[index]))
        return [(self.ids[index], float(scores[index])) for index in order if scores[index] > 0][:top_k]

    def save(self, path):
        write_json(path, {'schema_version': SCHEMA_VERSION, 'k1': self.k1, 'b': self.b,
                          'field_weights': self.field_weights, 'ids': self.ids,
                          'doc_len': self.doc_len, 'avgdl': self.avgdl,
                          'postings': {token: [value for pair in rows for value in pair]
                                       for token, rows in self.postings.items()}})

    @classmethod
    def load(cls, path, expect_ids=None):
        """Read a saved index; returns None when it does not match current records."""
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        if data.get('schema_version') != SCHEMA_VERSION:
            return None
        index = cls(field_weights=data.get('field_weights'), k1=data.get('k1', 1.5), b=data.get('b', 0.75))
        index.ids = list(data.get('ids', []))
        if expect_ids is not None and index.ids != list(expect_ids):
            return None
        index.doc_len = [float(value) for value in data.get('doc_len', [])]
        index.avgdl = float(data.get('avgdl') or 0.0)
        index.postings, index.doc_tokens = {}, []
        for ident in index.ids:
            index.doc_tokens.append(set())
            index._by_id[ident] = len(index._by_id)
        for token, flat in (data.get('postings') or {}).items():
            rows = [(int(flat[i]), float(flat[i + 1])) for i in range(0, len(flat), 2)]
            index.postings[token] = rows
            for doc_index, _ in rows:
                index.doc_tokens[doc_index].add(token)
        return index


def build_bm25(records, **kwargs):
    index = BM25Index(**kwargs)
    index.fit((record['record_id'], {field: record.get(field) or '' for field in CONTENT_FIELDS})
              for record in records)
    return index


# ---------------------------------------------------------------------------
# Vector channel state (incremental encode / resume)


def build_text_vectors(records, embedder, previous=None, *, batch_size=32):
    """Encode record content texts, reusing vectors whose content hash is unchanged.

    embedder needs encode(list[str]) -> float32 [n, dim] for documents; queries go
    through encode_query so instruction prefixes stay the embedder's business.
    """
    ids = [record['record_id'] for record in records]
    texts = {record['record_id']: content_text(record) for record in records}
    shas = {ident: json_sha(texts[ident]) for ident in ids}
    previous_vectors = (previous or {}).get('vectors')
    previous_shas = (previous or {}).get('sha256') or {}
    previous_position = {ident: index for index, ident in enumerate((previous or {}).get('ids') or [])}

    def reusable(ident):
        return (previous_vectors is not None and ident in previous_position
                and previous_shas.get(ident) == shas[ident])

    pending = [ident for ident in ids if not reusable(ident)]
    encoded = {}
    for start in range(0, len(pending), max(1, batch_size)):
        chunk = pending[start:start + max(1, batch_size)]
        matrix = np.asarray(embedder.encode([texts[ident] for ident in chunk]), dtype=np.float32)
        if matrix.ndim != 2 or matrix.shape[0] != len(chunk):
            raise ValueError('material_index:embedder_shape_mismatch')
        for offset, ident in enumerate(chunk):
            encoded[ident] = matrix[offset]
    if encoded:
        dim = int(next(iter(encoded.values())).shape[0])
    elif previous_vectors is not None and previous_vectors.size:
        dim = int(previous_vectors.shape[1])
    else:
        dim = 0
    vectors = None
    if dim and ids:
        vectors = np.zeros((len(ids), dim), dtype=np.float32)
        for index, ident in enumerate(ids):
            if ident in encoded:
                vectors[index] = encoded[ident]
            elif reusable(ident):
                vectors[index] = previous_vectors[previous_position[ident]]
        vectors /= np.maximum(np.linalg.norm(vectors, axis=1), 1e-12)[:, None]
    return {'ids': ids, 'sha256': shas, 'vectors': vectors,
            'encoded_ids': pending, 'reused': len(ids) - len(pending), 'dim': dim}


def build_image_vectors(shots, clip, previous=None, *, batch_size=8):
    """Encode shot keyframes in batches; only one batch of images is ever resident.

    A shot is reused when its shot_id and keyframe file hash match the previous
    run; missing keyframe files are skipped and counted, never fatal.
    """
    previous_shots = {shot['shot_id']: shot for shot in ((previous or {}).get('shots') or [])}
    previous_vectors = (previous or {}).get('vectors')
    previous_position = {shot['shot_id']: index for index, shot in enumerate((previous or {}).get('shots') or [])}

    def reusable(shot):
        saved = previous_shots.get(shot['shot_id'])
        return (previous_vectors is not None and saved is not None
                and shot.get('keyframe_sha256') == saved.get('keyframe_sha256')
                and shot['shot_id'] in previous_position)

    usable, missing = [], 0
    for shot in shots:
        if not shot.get('keyframe') or not Path(shot['keyframe']).is_file():
            missing += 1
            continue
        usable.append(shot)
    pending = [shot for shot in usable if not reusable(shot)]
    encoded, dim = {}, 0
    for start in range(0, len(pending), max(1, batch_size)):
        chunk = pending[start:start + max(1, batch_size)]
        matrix = np.asarray(clip.encode_images([shot['keyframe'] for shot in chunk]), dtype=np.float32)
        if matrix.ndim != 2 or matrix.shape[0] != len(chunk):
            raise ValueError('material_index:clip_shape_mismatch')
        dim = int(matrix.shape[1])
        for offset, shot in enumerate(chunk):
            encoded[shot['shot_id']] = matrix[offset]
    if not encoded and previous_vectors is not None and previous_vectors.size and usable:
        dim = int(previous_vectors.shape[1])
    vectors = None
    if usable:
        vectors = np.zeros((len(usable), dim or 1), dtype=np.float32) if dim else None
        if dim:
            for index, shot in enumerate(usable):
                if shot['shot_id'] in encoded:
                    vectors[index] = encoded[shot['shot_id']]
                elif reusable(shot):
                    vectors[index] = previous_vectors[previous_position[shot['shot_id']]]
            vectors /= np.maximum(np.linalg.norm(vectors, axis=1), 1e-12)[:, None]
    return {'shots': usable, 'vectors': vectors, 'encoded_ids': [shot['shot_id'] for shot in pending],
            'reused': len(usable) - len(pending), 'missing': missing, 'dim': dim}


def write_text_channel(directory, state):
    if state.get('vectors') is None:
        LOGGER.warning('material_index:text_channel_empty_nothing_written')
        return
    directory = Path(directory)
    np.save(directory / TEXT_VECTORS_NAME, state['vectors'])
    write_json(directory / TEXT_IDS_NAME, {'schema_version': SCHEMA_VERSION, 'ids': state['ids'],
                                           'sha256': state['sha256'], 'dim': state['dim']})


def read_text_channel(directory):
    directory = Path(directory)
    ids_path, vectors_path = directory / TEXT_IDS_NAME, directory / TEXT_VECTORS_NAME
    if not ids_path.is_file() or not vectors_path.is_file():
        return None
    data = json.loads(ids_path.read_text(encoding='utf-8'))
    vectors = np.load(vectors_path)
    if vectors.shape[0] != len(data.get('ids', [])):
        LOGGER.warning('material_index:text_channel_shape_mismatch')
        return None
    return {'ids': data['ids'], 'sha256': data.get('sha256') or {}, 'vectors': vectors}


def write_image_channel(directory, shots, shot_records, vectors=None, encoded_ids=()):
    payload = {'schema_version': SCHEMA_VERSION, 'shots': shots, 'shot_records': shot_records,
               'encoded_shot_ids': list(encoded_ids or []),
               'dim': int(vectors.shape[1]) if vectors is not None else 0}
    write_json(Path(directory) / IMAGE_SHOTS_NAME, payload)
    if vectors is not None:
        np.save(Path(directory) / IMAGE_VECTORS_NAME, vectors)


def read_image_channel(directory):
    directory = Path(directory)
    shots_path = directory / IMAGE_SHOTS_NAME
    if not shots_path.is_file():
        return None
    payload = json.loads(shots_path.read_text(encoding='utf-8'))
    vectors_path = directory / IMAGE_VECTORS_NAME
    vectors = np.load(vectors_path) if vectors_path.is_file() else None
    if vectors is not None and vectors.shape[0] != len(payload.get('shots', [])):
        LOGGER.warning('material_index:image_channel_shape_mismatch')
        vectors = None
    return {'shots': payload.get('shots') or [], 'shot_records': payload.get('shot_records') or {},
            'encoded_shot_ids': payload.get('encoded_shot_ids') or [], 'vectors': vectors}


# ---------------------------------------------------------------------------
# Fusion and search


def fuse_ranks(rankings, *, k=DEFAULT_RRF_K):
    """Reciprocal Rank Fusion: sum 1/(k + rank) per channel, ranks 1-based."""
    fused = {}
    for channel, idents in (rankings or {}).items():
        for position, ident in enumerate(idents, start=1):
            fused[ident] = fused.get(ident, 0.0) + 1.0 / (k + position)
    return sorted(fused.items(), key=lambda item: (-item[1], item[0]))


def _cosine_top(matrix, query, top_k):
    if matrix is None or not len(matrix):
        return []
    query = np.asarray(query, dtype=np.float32).reshape(-1)
    query = query / max(float(np.linalg.norm(query)), 1e-12)
    scores = matrix @ query
    order = sorted(range(len(scores)), key=lambda index: (-float(scores[index]), index))
    return order[:top_k]


def validate_need(need):
    if not isinstance(need, dict):
        raise ValueError('material_index:need_object_required')
    need_id = need.get('need_id')
    if not isinstance(need_id, str) or not need_id.strip():
        raise ValueError('material_index:need_id_required')
    query = need.get('query_text', '')
    if not isinstance(query, str):
        raise ValueError('material_index:query_text_string_required')
    cleaned = {'need_id': need_id.strip(), 'beat': str(need.get('beat') or '').strip(),
               'query_text': ' '.join(query.split())}
    for key in ('characters', 'must', 'avoid'):
        values = need.get(key, [])
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise ValueError('material_index:' + key + '_strings_required')
        cleaned[key] = [value.strip() for value in values if value.strip()]
    return cleaned


def combined_query_text(need):
    """Query text for content channels: conditions + must terms + wanted characters.

    avoid terms never reach any vector channel; they only become result flags.
    """
    return ' '.join([need['query_text'], *need['must'], *need['characters']]).strip()


def _normalize_name(name, cast):
    return canonical_name(name, cast) if cast else name


class MaterialIndex:
    """Records plus channels; search fuses channel ranks and applies structural filters."""

    def __init__(self, records, *, bm25=None, meta=None):
        self.records = list(records)
        self.by_id = {record['record_id']: record for record in self.records}
        self.records_by_window = {}
        for record in self.records:
            self.records_by_window.setdefault(record['window_id'], []).append(record)
        self.bm25 = bm25 if bm25 is not None else build_bm25(self.records)
        self.text_ids, self.text_vectors = [], None
        self.image_shots, self.image_vectors = [], None
        self.shot_records = {}
        self.meta = dict(meta or {})

    # -- loading -----------------------------------------------------------
    @classmethod
    def load(cls, directory):
        directory = Path(directory)
        records = [json.loads(line) for line in
                   (directory / RECORDS_NAME).read_text(encoding='utf-8').splitlines() if line.strip()]
        bm25 = BM25Index.load(directory / BM25_NAME, expect_ids=[record['record_id'] for record in records])
        if bm25 is None:
            LOGGER.info('material_index:bm25_rebuilt_from_records')
            bm25 = build_bm25(records)
        meta = {}
        manifest_path = directory / MANIFEST_NAME
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            meta = {'manifest': manifest, 'film_by_sha': manifest.get('film_by_sha') or {},
                    'model_dirs': manifest.get('model_dirs') or {},
                    'shot_cache': manifest.get('shot_cache')}
        index = cls(records, bm25=bm25, meta=meta)
        text_state = read_text_channel(directory)
        if text_state is not None:
            index.attach_text(text_state['ids'], text_state['vectors'])
        image_state = read_image_channel(directory)
        if image_state is not None:
            index.attach_image(image_state['shots'], image_state['vectors'], image_state['shot_records'])
        return index

    def attach_text(self, ids, vectors):
        if vectors is None or vectors.shape[0] != len(ids):
            return
        self.text_ids, self.text_vectors = list(ids), vectors

    def attach_image(self, shots, vectors, shot_records):
        self.image_shots = list(shots or [])
        self.shot_records = dict(shot_records or {})
        if vectors is not None and vectors.shape[0] == len(self.image_shots):
            self.image_vectors = vectors

    # -- search ------------------------------------------------------------
    def search(self, need, *, channels=CHANNEL_NAMES, text_embedder=None, clip=None,
               top_k=DEFAULT_TOP_K, final_k=DEFAULT_FINAL_K, k_rrf=DEFAULT_RRF_K,
               min_duration_s=None, cast=None, top_windows=12):
        need = validate_need(need)
        channels = list(dict.fromkeys(channels))
        unknown = [name for name in channels if name not in CHANNEL_NAMES]
        if unknown:
            raise ValueError('material_index:unknown_channel:' + ','.join(unknown))
        cast = cast or self.meta.get('cast')
        query = combined_query_text(need)
        rankings, matched_shots, skipped = {}, {}, {}
        if 'bm25' in channels:
            rankings['bm25'] = [ident for ident, _ in self.bm25.search(query, top_k)]
        if 'text' in channels:
            if self.text_vectors is None:
                skipped['text'] = 'index_has_no_text_vectors'
            elif text_embedder is None:
                skipped['text'] = 'text_embedder_not_provided'
            else:
                vector = np.asarray(text_embedder.encode_query([query]), dtype=np.float32)[0]
                rankings['text'] = [self.text_ids[i] for i in _cosine_top(self.text_vectors, vector, top_k)]
        if 'image' in channels:
            if self.image_vectors is None:
                skipped['image'] = 'index_has_no_image_vectors'
            elif clip is None:
                skipped['image'] = 'clip_not_provided'
            else:
                vector = np.asarray(clip.encode_texts([query]), dtype=np.float32)[0]
                rank_by_record, shots_by_record = {}, {}
                for position, shot_index in enumerate(_cosine_top(self.image_vectors, vector, top_k), start=1):
                    shot = self.image_shots[shot_index]
                    for ident in self.shot_records.get(shot['shot_id'], []):
                        if ident not in rank_by_record or position < rank_by_record[ident]:
                            rank_by_record[ident] = position
                        shots_by_record.setdefault(ident, []).append(
                            {'shot_id': shot['shot_id'], 'film': shot['film'], 'n': shot['n'],
                             'shot_start': shot['shot_start'], 'shot_end': shot['shot_end'],
                             'keyframe': shot.get('keyframe', ''), 'rank': position})
                rankings['image'] = [ident for ident, _ in
                                     sorted(rank_by_record.items(), key=lambda item: (item[1], item[0]))]
                matched_shots = shots_by_record
        hits, excluded = [], {'min_duration': 0, 'must': 0, 'characters': 0}
        for ident, score in fuse_ranks(rankings, k=k_rrf):
            record = self.by_id[ident]
            reasons = [channel + '第' + str(rankings[channel].index(ident) + 1) + '名'
                       for channel in ('bm25', 'text', 'image') if ident in rankings.get(channel, [])]
            identity_unknown = False
            if min_duration_s is not None:
                duration = record.get('duration_s', record.get('usable_duration_s', 0.0))
                if duration < min_duration_s:
                    excluded['min_duration'] += 1
                    continue
                reasons.append('可用时长' + format(duration, 'g') + 's')
            failed_must = [term for term in need['must']
                           if tokenize(term) and not (set(tokenize(term)) & self.bm25.content_tokens(ident))]
            if failed_must:
                excluded['must'] += 1
                continue
            if need['characters']:
                wanted = {_normalize_name(name, cast) for name in need['characters']}
                known = {_normalize_name(name, cast) for name in record.get('names', []) if name}
                matched_names = sorted(wanted & known)
                if matched_names:
                    reasons.append('角色匹配:' + '、'.join(matched_names))
                elif not record.get('roles') or any(not row.get('name') for row in record['roles']):
                    # Unknown identity is never grounds for exclusion on its own.
                    identity_unknown = True
                    reasons.append('identity_unknown:角色未命名，未排除')
                else:
                    excluded['characters'] += 1
                    continue
            avoid_hits = [term for term in need['avoid']
                          if tokenize(term) and (set(tokenize(term)) & self.bm25.content_tokens(ident))]
            if avoid_hits:
                reasons.append('avoid命中:' + '、'.join(avoid_hits))
            hits.append({'record_id': ident, 'kind': record['kind'], 'score': round(score, 6),
                         'channel_ranks': {channel: rankings[channel].index(ident) + 1
                                           for channel in rankings if ident in rankings[channel]},
                         'matched_shots': matched_shots.get(ident, []),
                         'identity_unknown': identity_unknown, 'avoid_hits': avoid_hits,
                         'reasons': reasons, 'record': record})
            if len(hits) >= final_k:
                break
        return {'need_id': need['need_id'], 'beat': need['beat'], 'query': query,
                'channels_used': [name for name in channels if name in rankings],
                'channels_skipped': skipped,
                'rankings': {channel: idents[:10] for channel, idents in rankings.items()},
                'hits': hits, 'excluded': excluded, 'window_groups': self.window_groups(hits, top_windows)}

    def window_groups(self, hits, limit=12):
        """Scene-combination view: each hit window with its records in time order."""
        by_window = {}
        for hit in hits:
            by_window.setdefault(hit['record']['window_id'], []).append(hit)
        groups = []
        for window_id, window_hits in sorted(by_window.items(),
                                             key=lambda item: (-max(hit['score'] for hit in item[1]), item[0]))[:limit]:
            members = sorted(self.records_by_window.get(window_id, []),
                             key=lambda record: (record['source_start_s'],
                                                 0 if record['kind'] == 'event' else 1, record['record_id']))
            first = members[0] if members else {}
            groups.append({'window_id': window_id, 'film': first.get('film'),
                           'source_sha256': first.get('source_sha256'),
                           'window_source_start_s': first.get('window_source_start_s'),
                           'window_source_end_s': first.get('window_source_end_s'),
                           'hit_record_ids': [hit['record_id'] for hit in window_hits],
                           'records': members})
        return groups

    def search_windows(self, need, *, record_filter=None, excluded_windows=(), final_k=6, **kwargs):
        """Rank continuous scenes, pooling existing channel evidence before selection.

        Generic story roles stay in query_text, never the actor-name filter.
        Only usable records contribute identity, mouth, setting and duration bonuses.
        """
        result = self.search(need, final_k=max(len(self.records), DEFAULT_FINAL_K), **kwargs)
        excluded = set(excluded_windows)
        groups = {}
        for hit in result['hits']:
            wid = hit['record']['window_id']
            if wid not in excluded and (record_filter is None or record_filter(hit['record'])):
                groups.setdefault(wid, []).append(hit)
        setting = set(tokenize(need.get('setting', need['query_text'])))
        windows = []
        for wid, hits in groups.items():
            records = [r for r in self.records_by_window[wid] if record_filter is None or record_filter(r)]
            channels = {}
            for hit in hits:
                for channel, rank in hit['channel_ranks'].items():
                    channels[channel] = min(channels.get(channel, rank), rank)
            names = set()
            entity_names = {role.get('entity_id'): _normalize_name(role['name'], kwargs.get('cast'))
                            for r in records for role in r.get('roles', [])
                            if role.get('name') and role.get('entity_id')}
            for r in records:
                for role in r.get('roles', []):
                    name = entity_names.get(role.get('entity_id')) or role.get('entity_id') or role.get('name')
                    if name:
                        names.add(name)
                if not r.get('roles'):
                    names.update(r.get('names') or r.get('entity_ids') or [])
            texts = [set(tokenize(content_text(r))) for r in records]
            coverage = sum(len(setting & t) / max(1, len(setting)) for t in texts) / max(1, len(texts))
            mouth = sum(has_dialogue_hint(r.get('speech')) for r in records) / max(1, len(records))
            spans = []
            for r in sorted(records, key=lambda r: r['source_start_s']):
                a, b = r['source_start_s'], r['source_end_s']
                if spans and a <= spans[-1][1]:
                    spans[-1][1] = max(spans[-1][1], b)
                else:
                    spans.append([a, b])
            duration = sum(b - a for a, b in spans)
            channel_score = sum(61 / (60 + rank) for rank in channels.values()) / max(1, len(result['channels_used']))
            score = (channel_score + .6 * coverage + .35 * (len(names) >= 2) + .35 * mouth + .2 * min(duration / 60, 1))
            windows.append(dict(window_id=wid, records=records, score=score,
                query_match=channel_score + .6 * coverage,
                hit_record_ids=[h['record_id'] for h in hits],
                scene_evidence=dict(characters=len(names), speech_coverage=mouth,
                                    setting_coverage=coverage, usable_duration_s=duration)))
        windows.sort(key=lambda w: (-w['score'], w['window_id']))
        return dict(need_id=need['need_id'], windows=windows[:final_k],
                    channels_used=result['channels_used'], channels_skipped=result['channels_skipped'])


# ---------------------------------------------------------------------------
# Evaluation harness helpers (window-level pooling and labelling metrics)


def window_candidate(record, matched_shots=()):
    """Project a record-level hit onto the window+time grid used for labelling."""
    return {'window_id': record['window_id'], 'source_sha256': record['source_sha256'],
            'film': record.get('film'), 'span': (record['source_start_s'], record['source_end_s']),
            'record_ids': [record['record_id']], 'matched_shots': list(matched_shots or [])}


def _candidate_key(row):
    span = row.get('span') or (0.0, 0.0)
    return (row.get('window_id'), round(float(span[0]), 1), round(float(span[1]), 1))


def pool_candidates(set_rankings, *, top_n=5):
    """Pool per-set ranked window candidates, deduped by window+time.

    set_rankings: {set_name: [candidate rows in rank order]}. Returns candidates
    with per-set ranks, ordered by best rank across sets then key; caller assigns
    stable candidate ids.
    """
    pool = {}
    for name, rows in (set_rankings or {}).items():
        ranked, seen = [], set()
        for row in rows:
            key = _candidate_key(row)
            if key in seen:
                continue
            seen.add(key)
            ranked.append((key, row))
            if len(ranked) >= top_n:
                break
        for rank, (key, row) in enumerate(ranked, start=1):
            candidate = pool.get(key)
            if candidate is None:
                candidate = {'key': key, 'window_id': row.get('window_id'),
                             'source_sha256': row.get('source_sha256'), 'film': row.get('film'),
                             'span': row.get('span'), 'sets': {}, 'record_ids': [], 'matched_shots': []}
                pool[key] = candidate
            candidate['sets'][name] = rank
            for ident in row.get('record_ids', []):
                if ident not in candidate['record_ids']:
                    candidate['record_ids'].append(ident)
            if row.get('matched_shots') and not candidate['matched_shots']:
                candidate['matched_shots'] = row['matched_shots']
    candidates = sorted(pool.values(), key=lambda candidate: (min(candidate['sets'].values()),
                                                              candidate['key']))
    for index, candidate in enumerate(candidates, start=1):
        candidate['candidate_id'] = 'c' + format(index, '03d')
    return candidates


def _is_good_label(label):
    return (label is True or label == 1 or
            (isinstance(label, str) and label.strip().lower() in {'good', 'true', '1', 'yes'}))


def compute_metrics(labels_rows, ranked_by_need, candidate_sets):
    """Success@5, MRR@10 per set and per-set unique-good counts.

    labels_rows: [{need_id, candidate_id, label}] with truthy labels marking good
    material. ranked_by_need: {need_id: {set_name: [candidate_id in rank order]}}.
    candidate_sets: {(need_id, candidate_id): set(set_names that returned it)}.
    """
    good = {(row['need_id'], row['candidate_id']) for row in labels_rows if _is_good_label(row.get('label'))}
    needs = sorted({row['need_id'] for row in labels_rows})
    set_names = sorted({name for ranking in ranked_by_need.values() for name in ranking})
    metrics = {'n_needs': len(needs), 'n_labelled_good': len(good), 'per_set': {}, 'unique_good': {}}
    for name in set_names:
        successes, reciprocals = [], []
        for need_id in needs:
            ranked = ranked_by_need.get(need_id, {}).get(name, [])
            successes.append(any((need_id, ident) in good for ident in ranked[:5]))
            reciprocal = 0.0
            for rank, ident in enumerate(ranked[:10], start=1):
                if (need_id, ident) in good:
                    reciprocal = 1.0 / rank
                    break
            reciprocals.append(reciprocal)
        metrics['per_set'][name] = {'success@5': round(sum(successes) / len(needs), 6) if needs else 0.0,
                                    'mrr@10': round(sum(reciprocals) / len(needs), 6) if needs else 0.0,
                                    'n_needs': len(needs)}
    for name in set_names:
        metrics['unique_good'][name] = sum(1 for key in good if candidate_sets.get(key) == {name})
    return metrics
