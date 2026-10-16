"""Reusable received visual observations, with a rebuildable local search index.

The caller verifies original ledger/media provenance before adding a record.
This module performs no network calls and never promotes an uncertain result.
Facts and conditional creative uses have separate search scores and reasons.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3

from .state import _io_path, file_lock, json_sha, write_json


def _strings(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [part for item in value.values() for part in _strings(item)]
    if isinstance(value, list):
        return [part for item in value for part in _strings(item)]
    return []


def _terms(value):
    """Unicode words plus Chinese character/bigram tokens; no tokenizer service."""
    tokens = set()
    for run in re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]+", " ".join(_strings(value)).lower()):
        if re.fullmatch(r"[\u3400-\u9fff]+", run):
            tokens.update(run)
            tokens.update(run[index:index + 2] for index in range(len(run) - 1))
        else:
            tokens.add(run)
    return tokens


def _validate_record(record):
    if not isinstance(record, dict) or not isinstance(record.get('observation_id'), str) or not record['observation_id']:
        raise ValueError('visual_library:observation_id_required')
    evidence = record.get('evidence', {})
    if evidence.get('status') != 'received':
        raise ValueError('visual_library:received_evidence_required')
    for key in ('ledger_path', 'call_id', 'request_sha256', 'response_sha256'):
        if not isinstance(evidence.get(key), str) or not evidence[key]:
            raise ValueError('visual_library:evidence_' + key + '_required')
    window = record.get('window', {})
    sha = window.get('source_sha256')
    if not isinstance(sha, str) or not re.fullmatch(r'[a-f0-9]{64}', sha):
        raise ValueError('visual_library:source_sha256_required')
    start, end = window.get('source_start_s'), window.get('source_end_s')
    if (any(type(value) not in (int, float) or not math.isfinite(value) for value in (start, end))
            or not 0 <= start < end):
        raise ValueError('visual_library:invalid_source_interval')
    for key in ('window_id', 'source_id'):
        if not isinstance(window.get(key), str) or not window[key]:
            raise ValueError('visual_library:' + key + '_required')
    if window.get('status') != 'watched' or not isinstance(window.get('observation'), dict):
        raise ValueError('visual_library:watched_window_required')
    # A received sparse navigation image is not a continuous watched interval.
    if window.get('source_visual_policy') != 'silent_subtitle_masked':
        raise ValueError('visual_library:pure_visual_window_required')
    return window


def _entity_bindings(window):
    result = []
    for role in window['observation'].get('roles', []):
        role_id = role['role_id']
        entity_id = role.get('entity_id')
        provisional = not entity_id
        if provisional:
            suffix = json_sha([window['window_id'], role_id])[:20]
            entity_id = window['source_sha256'] + ':local_' + suffix
        if not entity_id.startswith(window['source_sha256'] + ':'):
            raise ValueError('visual_library:entity_source_namespace_changed')
        result.append({'role_id': role_id, 'entity_id': entity_id,
            'identity_status': role.get('identity_status', 'provisional' if provisional else 'observed'),
            'state': deepcopy(role.get('state', '')),
            'appearance': deepcopy(role.get('appearance', role.get('state', ''))),
            'identity_evidence': role.get('identity_evidence', ''),
            'identity_confirmed': role.get('identity_confirmed', False)})
    return result


def _resolved(entity_id, mapping):
    seen = set()
    while entity_id in mapping:
        if entity_id in seen:
            raise ValueError('visual_library:entity_revision_cycle')
        seen.add(entity_id)
        entity_id = mapping[entity_id]
    return entity_id


def _effective_bindings(bindings, mapping):
    result = deepcopy(bindings)
    for binding in result:
        original = binding.get('source_entity_id', binding['entity_id'])
        binding.update(source_entity_id=original, entity_id=_resolved(original, mapping))
    return result


def _projection(record, mapping=None):
    window = _validate_record(record)
    observed = window['observation']
    bindings = _effective_bindings(_entity_bindings(window), mapping or {})
    facts = {key: observed.get(key, []) for key in
        ('summary', 'scene', 'events', 'roles', 'usable_ranges')}
    # Creative possibilities deliberately do not enter the factual text index.
    uses = observed.get('affordances', [])
    card = {'observation_id': record['observation_id'], 'window_id': window['window_id'],
        'source_id': window['source_id'], 'source_sha256': window['source_sha256'],
        'source_start_s': window['source_start_s'], 'source_end_s': window['source_end_s'],
        'summary': observed.get('summary') or '；'.join(
            event.get('observed_fact', '') for event in observed.get('events', [])),
        'entity_bindings': bindings, 'entities': sorted({row['entity_id'] for row in bindings}),
        'affordances': deepcopy(uses), 'usable_ranges': deepcopy(observed.get('usable_ranges', [])),
        'limitations': deepcopy(observed.get('limitations', [])),
        'uncertainties': deepcopy(observed.get('uncertainties', [])),
        'evidence': deepcopy(record['evidence'])}
    return card, sorted(_terms(facts)), sorted(_terms(uses))


def compact_candidates(cards):
    """Copy retrieval context without truncating visual ranges or conditions.

    Full cards remain authoritative for selection and evidence validation.
    Affordances and retrieval matches describe conditional uses and requested
    needs, respectively; neither promotes those interpretations to visual facts.
    """
    result = []
    for card in cards:
        compact = {key: deepcopy(card[key]) for key in
            ('observation_id', 'source_id', 'source_sha256', 'source_start_s', 'source_end_s',
             'summary', 'entities', 'uncertainties', 'limitations', 'retrieval_matches',
             'match_strength', 'score') if key in card}
        if 'reasons' in card:
            # Keep fact/use attribution; long Unicode hit lists stay in the full card.
            compact['reasons'] = [{'kind': reason['kind']} for reason in card['reasons']]
        compact['entity_bindings'] = [{key: deepcopy(binding[key]) for key in
            ('entity_id', 'role_id', 'appearance', 'identity_status', 'identity_confirmed') if key in binding}
            for binding in card.get('entity_bindings', [])]
        compact['usable_ranges'] = [{key: deepcopy(interval[key]) for key in
            ('local_in_s', 'local_out_s', 'shot_role', 'role_ids', 'camera', 'continuity_notes', 'speech_fit',
             'limitations') if key in interval} for interval in card.get('usable_ranges', [])]
        compact['affordances'] = [{key: deepcopy(affordance[key]) for key in
            ('expression', 'possible_uses', 'requires') if key in affordance}
            for affordance in card.get('affordances', [])]
        if 'status' in card.get('evidence', {}):
            compact['evidence'] = {'status': card['evidence']['status']}
        result.append(compact)
    return result


def candidate_groups(cards):
    """Compare received same-source cards, without claiming shared scenes.

    Multi-person anchors are only entity sets already present in a card, plus
    their members' single-person cards. Individual anchors can span records.
    Shot roles describe contributions available in the referenced cards; they
    do not assign every range to every anchor person or prove anyone co-framed.
    Empty-entity cards remain separate optional material. No identity is inferred.
    """
    sources = {}
    seen = set()
    for card in cards:
        if card.get('evidence', {}).get('status') != 'received':
            continue
        key = (card['source_sha256'], card['observation_id'])
        if key in seen:
            continue
        seen.add(key)
        entities = tuple(sorted(set(card.get('entities', []))))
        sources.setdefault(key[0], []).append((card, entities))

    result = []
    for source_sha256, rows in sources.items():
        anchors = dict.fromkeys(entities for _, entities in rows)
        for _, entities in rows:
            for entity in entities:
                anchors.setdefault((entity,), None)
        for entities in anchors:
            members = [card for card, present in rows if present == entities or
                (len(entities) == 1 and entities[0] in present) or
                (len(entities) > 1 and len(present) == 1 and present[0] in entities)]
            shot_roles = sorted({interval['shot_role'] for card in members
                for interval in card.get('usable_ranges', [])
                if isinstance(interval.get('shot_role'), str) and interval['shot_role']})
            result.append({'source_sha256': source_sha256, 'entity_ids': list(entities),
                'observation_ids': [card['observation_id'] for card in members],
                'shot_roles': shot_roles})
    return result


class VisualLibrary:
    """A small server-local catalog. JSON is authoritative; SQLite is disposable."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.records = self.root / 'observations'
        self.revisions = self.root / 'revisions'
        self.database = self.root / 'library.sqlite'
        _io_path(self.records).mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS observations (
                observation_id TEXT PRIMARY KEY, record_sha256 TEXT NOT NULL,
                source_sha256 TEXT NOT NULL, source_start_s REAL NOT NULL,
                source_end_s REAL NOT NULL, card_json TEXT NOT NULL,
                fact_terms_json TEXT NOT NULL, use_terms_json TEXT NOT NULL)''')
            db.execute('CREATE INDEX IF NOT EXISTS source_windows ON observations(source_sha256, source_start_s)')
            count = db.execute('SELECT count(*) FROM observations').fetchone()[0]
        # Reconcile a crash between publishing a JSON record and indexing it.
        if count != sum(1 for _ in _io_path(self.records).glob('*.json')):
            self.rebuild()

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(str(_io_path(self.database)))
        try:
            with db:
                yield db
        finally:
            db.close()

    def _record_path(self, observation_id):
        name = hashlib.sha256(observation_id.encode('utf-8')).hexdigest() + '.json'
        return _io_path(self.records / name)

    @staticmethod
    def _index(db, record, mapping=None):
        card, facts, uses = _projection(record, mapping)
        db.execute('INSERT OR REPLACE INTO observations VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (record['observation_id'], json_sha(record), card['source_sha256'],
             card['source_start_s'], card['source_end_s'],
             json.dumps(card, ensure_ascii=False), json.dumps(facts, ensure_ascii=False),
             json.dumps(uses, ensure_ascii=False)))

    def add_observation(self, record):
        """Append one received record; identical replay is local and idempotent."""
        _projection(record)
        ident = record['observation_id']
        path = self._record_path(ident)
        with file_lock(_io_path(self.root / '.write.lock')):
            if path.exists():
                existing = json.loads(path.read_text(encoding='utf-8'))
                if json_sha(existing) != json_sha(record):
                    raise ValueError('visual_library:immutable_observation_changed:' + ident)
            else:
                write_json(path, record)
            with self._connect() as db:
                self._index(db, record, self._entity_mapping())
        return ident

    def rebuild(self):
        """Rebuild derived cards from retained JSON without revisiting any media."""
        with file_lock(_io_path(self.root / '.write.lock')):
            with self._connect() as db:
                self._rebuild_index(db)

    def _rebuild_index(self, db):
        mapping = self._entity_mapping()
        db.execute('DELETE FROM observations')
        for path in sorted(_io_path(self.records).glob('*.json')):
            self._index(db, json.loads(path.read_text(encoding='utf-8')), mapping)

    def get(self, observation_id):
        record = json.loads(self._record_path(observation_id).read_text(encoding='utf-8'))
        if record['observation_id'] != observation_id:
            raise ValueError('visual_library:observation_id_changed')
        _validate_record(record)
        with self._connect() as db:
            row = db.execute('SELECT record_sha256 FROM observations WHERE observation_id=?',
                             (observation_id,)).fetchone()
        if row is None or row[0] != json_sha(record):
            raise ValueError('visual_library:stored_record_changed:' + observation_id)
        return record

    def search(self, query, source_sha256s=None, entity_ids=None, limit=30, *, revision_ids=None,
               exclude_observation_ids=None):
        """Rank inexpensive local candidates; conditional-use hits are labelled."""
        if not isinstance(query, str) or type(limit) is not int or limit < 0:
            raise ValueError('visual_library:invalid_search')
        if limit == 0:
            return []
        wanted = _terms(query)
        sources = None if source_sha256s is None else set(source_sha256s)
        excluded = set(exclude_observation_ids or [])
        revisions = self.entity_revision_ids() if revision_ids is None else list(revision_ids)
        mapping = self._entity_mapping(revisions)
        entities = None if entity_ids is None else {_resolved(entity, mapping) for entity in entity_ids}
        ranked = []
        with self._connect() as db:
            rows = db.execute('SELECT card_json, fact_terms_json, use_terms_json FROM observations').fetchall()
        def weight(terms):
            return sum(2 if len(term) > 1 else .25 for term in terms)
        for encoded, facts, uses in rows:
            card = json.loads(encoded)
            if card['observation_id'] in excluded:
                continue
            card['entity_bindings'] = _effective_bindings(card['entity_bindings'], mapping)
            card['entities'] = sorted({row['entity_id'] for row in card['entity_bindings']})
            card['entity_revision_ids'] = revisions
            if sources is not None and card['source_sha256'] not in sources:
                continue
            if entities is not None and not entities.intersection(card['entities']):
                continue
            fact_hits, use_hits = wanted.intersection(json.loads(facts)), wanted.intersection(json.loads(uses))
            if wanted and not (fact_hits or use_hits):
                continue
            strong = {term for term in fact_hits | use_hits if len(term) > 1}
            weak = bool(wanted and any(len(term) > 1 for term in wanted) and not strong)
            card['match_strength'] = 'weak_single_character' if weak else 'lexical'
            card['score'] = round((weight(fact_hits) + .65 * weight(use_hits)) /
                                  max(weight(wanted), 1) * (.05 if weak else 1), 6)
            card['reasons'] = [{'kind': kind, 'matched_terms': sorted(hits)}
                for kind, hits in [('visual_fact', fact_hits), ('conditional_use', use_hits)] if hits]
            ranked.append(card)
        ranked.sort(key=lambda card: (-card['score'], card['source_sha256'], card['source_start_s'], card['observation_id']))
        # Near-duplicate windows remain available, but do not occupy the first
        # whole page when a different scene offers a useful alternative.
        diverse, deferred = [], []
        for card in ranked:
            overlaps = any(previous['source_sha256'] == card['source_sha256'] and
                max(0, min(previous['source_end_s'], card['source_end_s']) -
                    max(previous['source_start_s'], card['source_start_s'])) /
                min(previous['source_end_s'] - previous['source_start_s'],
                    card['source_end_s'] - card['source_start_s']) >= .8 for previous in diverse)
            (deferred if overlaps else diverse).append(card)
        return (diverse + deferred)[:limit]

    def coverage(self, source_sha256, duration_s):
        """Received continuous input ranges, not a guarantee of per-frame recall."""
        if type(duration_s) not in (int, float) or not math.isfinite(duration_s) or duration_s <= 0:
            raise ValueError('visual_library:invalid_source_duration')
        with self._connect() as db:
            rows = db.execute('''SELECT source_start_s, source_end_s FROM observations
                WHERE source_sha256=? ORDER BY source_start_s''', (source_sha256,)).fetchall()
        merged = []
        for start, end in rows:
            if start >= duration_s or end > duration_s + .001:
                raise ValueError('visual_library:observation_outside_source_duration')
            end = min(end, duration_s)
            if merged and start <= merged[-1][1] + .000001:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        gaps, cursor = [], 0.0
        for start, end in merged:
            if start > cursor:
                gaps.append([cursor, start])
            cursor = end
        if cursor < duration_s:
            gaps.append([cursor, duration_s])
        covered = sum(end - start for start, end in merged)
        return {'source_sha256': source_sha256, 'duration_s': duration_s,
            'observed_intervals_s': merged, 'gaps_s': gaps, 'covered_s': covered,
            'coverage_ratio': covered / duration_s, 'observation_count': len(rows),
            'evidence_limit': 'Received continuous uploaded ranges; not native per-frame understanding.'}

    def entities(self, source_sha256=None, *, revision_ids=None):
        """Return stable supplied IDs, local provisional IDs and actual exemplars."""
        with self._connect() as db:
            query = 'SELECT observation_id FROM observations'
            args = ()
            if source_sha256 is not None:
                query += ' WHERE source_sha256=?'
                args = (source_sha256,)
            ids = [row[0] for row in db.execute(query + ' ORDER BY observation_id', args)]
        result = {}
        for ident in ids:
            window = self.effective_window(self.get(ident), revision_ids)
            for binding in _entity_bindings(window):
                entity_id = binding['entity_id']
                entity = result.setdefault(entity_id, {'entity_id': entity_id,
                    'source_sha256': window['source_sha256'], 'appearances': [], 'exemplars': []})
                entity['appearances'].append(dict(binding, observation_id=ident,
                    window_id=window['window_id'], source_start_s=window['source_start_s'],
                    source_end_s=window['source_end_s']))
                for exemplar in window.get('entity_exemplars', []):
                    if exemplar.get('entity_id') == entity_id and exemplar not in entity['exemplars']:
                        entity['exemplars'].append(deepcopy(exemplar))
        return list(result.values())

    def entity_revision_ids(self, source_sha256=None):
        """Immutable correction IDs; callers can freeze this list per task."""
        result = []
        for path in sorted(_io_path(self.revisions).glob('*.json')):
            revision = self._revision(path.stem)
            if source_sha256 is None or revision['old_id'].startswith(source_sha256 + ':'):
                result.append(path.stem)
        return result

    def _revision(self, ident):
        if not isinstance(ident, str) or not re.fullmatch('[a-f0-9]{64}', ident):
            raise ValueError('visual_library:invalid_entity_revision_id')
        revision = json.loads(_io_path(self.revisions / (ident + '.json')).read_text(encoding='utf-8'))
        if json_sha(revision) != ident:
            raise ValueError('visual_library:entity_revision_changed')
        return revision

    def _entity_mapping(self, revision_ids=None):
        revision_ids = self.entity_revision_ids() if revision_ids is None else revision_ids
        mapping = {}
        for ident in revision_ids:
            revision = self._revision(ident)
            old, new = revision['old_id'], revision['new_id']
            if old in mapping and mapping[old] != new:
                raise ValueError('visual_library:conflicting_entity_revision')
            mapping[old] = new
        for entity in mapping:
            _resolved(entity, mapping)
        return mapping

    def effective_window(self, record, revision_ids=None):
        """Derive entity bindings for one snapshot; raw get() remains unchanged."""
        window = deepcopy(_validate_record(record))
        revisions = self.entity_revision_ids() if revision_ids is None else list(revision_ids)
        mapping = self._entity_mapping(revisions)
        for role, binding in zip(window['observation'].get('roles', []), _entity_bindings(window)):
            original = binding['entity_id']
            effective = _resolved(original, mapping)
            role['entity_id'] = effective
            if effective != original:
                role['identity_status'] = 'received_visual_identity_revision'
        for exemplar in window.get('entity_exemplars', []):
            exemplar['entity_id'] = _resolved(exemplar['entity_id'], mapping)
        if revisions:
            window['source_identity_revision'] = {'revision_ids': revisions}
        return window

    def merge_entities(self, old_id, new_id, evidence):
        """Append a same-film correction explicitly authored in a received visual reply."""
        if (not all(isinstance(entity, str) and re.fullmatch('[a-f0-9]{64}:[A-Za-z][A-Za-z0-9_-]*', entity)
                    for entity in (old_id, new_id))
                or old_id == new_id or old_id.split(':')[0] != new_id.split(':')[0]):
            raise ValueError('visual_library:merge_requires_distinct_same_source_entities')
        if not isinstance(evidence, dict) or evidence.get('status') != 'received':
            raise ValueError('visual_library:received_identity_evidence_required')
        ledger = Path(evidence['ledger_path'])
        data = json.loads(_io_path(ledger).read_text(encoding='utf-8'))
        call = next((row for row in data['calls'] if row['id'] == evidence['call_id']), None)
        if (call is None or call['status'] != 'received' or
                any(call.get(key) != evidence.get(key) for key in ('request_sha256', 'response_sha256'))):
            raise ValueError('visual_library:identity_received_call_changed')
        folder = ledger.parent / 'calls' / call['id']
        request = json.loads(_io_path(folder / 'request.json').read_text(encoding='utf-8'))
        reply = json.loads(_io_path(folder / 'response.json').read_text(encoding='utf-8'))
        if json_sha(request) != call['request_sha256'] or json_sha(reply) != call['response_sha256']:
            raise ValueError('visual_library:identity_request_reply_changed')
        if request.get('tool') not in {'analyze_image', 'analyze_video'}:
            raise ValueError('visual_library:identity_visual_request_required')
        media = request['arguments'].get('image_source') or request['arguments'].get('video_source')
        from .media import sha256_file
        if sha256_file(media) != request['media_sha256']:
            raise ValueError('visual_library:identity_visual_media_changed')
        scope = request.get('observation_scope')
        lineage_path = Path(media).parent / 'lineage.json'
        if scope is None and lineage_path.exists():
            scope = json.loads(_io_path(lineage_path).read_text(encoding='utf-8'))
        if not isinstance(scope, dict) or scope.get('source_sha256') != old_id.split(':')[0]:
            raise ValueError('visual_library:identity_visual_source_changed')
        from .contracts import parse_model_json
        parsed = parse_model_json('\n'.join(row['text'] for row in reply['result']['content'] if row.get('type') == 'text'))
        match = next((row for row in parsed.get('identity_merges', []) if isinstance(row, dict)
            and row.get('old_id') == old_id and row.get('new_id') == new_id
            and isinstance(row.get('evidence'), str) and row['evidence'].strip()), None)
        if match is None:
            raise ValueError('visual_library:explicit_received_identity_merge_required')
        known = {row['entity_id'] for row in self.entities(old_id.split(':')[0], revision_ids=[])}
        if not {old_id, new_id} <= known:
            raise ValueError('visual_library:merge_entity_not_observed')
        revision = {'old_id': old_id, 'new_id': new_id, 'evidence': deepcopy(evidence),
                    'received_identity_merge': deepcopy(match)}
        ident = json_sha(revision)
        with file_lock(_io_path(self.root / '.write.lock')):
            mapping = self._entity_mapping()
            if old_id in mapping and mapping[old_id] != new_id:
                raise ValueError('visual_library:conflicting_entity_revision')
            mapping[old_id] = new_id
            _resolved(old_id, mapping)
            path = _io_path(self.revisions / (ident + '.json'))
            if not path.exists():
                write_json(path, revision)
            with self._connect() as db:
                self._rebuild_index(db)
        return ident
