"""Pinned film character allow-list and source-bound stylized footage exclusions."""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import unicodedata

from .state import LibraryStopped


def normalise(text):
    return ''.join(c for c in unicodedata.normalize('NFKC', text).casefold()
                   if not c.isspace() and not unicodedata.category(c).startswith('P'))


def character_for(name, film_cast):
    key = normalise(name)
    return next((c for c in film_cast['characters']
                 if key in {normalise(n) for n in [c['name'], *c['aliases']]}), None)


def canonical_name(name, film_cast):
    character = character_for(name, film_cast)
    return character['name'] if character else name


def load_film_cast(root):
    try:
        if not root:
            raise ValueError('visual_library_root_required')
        raw = (Path(root) / 'cast.json').read_bytes()
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get('schema_version') != 'film_cast_v1':
            raise ValueError('schema_version')
        characters = data.get('characters')
        if not isinstance(characters, list) or not characters:
            raise ValueError('characters')
        owners = {}
        for character in characters:
            if (not isinstance(character, dict) or not isinstance(character.get('name'), str)
                    or not normalise(character['name']) or not isinstance(character.get('aliases'), list)
                    or any(not isinstance(n, str) or not normalise(n) for n in character['aliases'])
                    or not isinstance(character.get('films'), list) or not character['films']
                    or any(type(n) is not int or n not in {1, 2, 3} for n in character['films'])):
                raise ValueError('character')
            for name in [character['name'], *character['aliases']]:
                key = normalise(name)
                if key in owners and owners[key] != character['name']:
                    raise ValueError('ambiguous_alias:' + name)
                owners[key] = character['name']
        ranges = data.get('stylized_ranges')
        if not isinstance(ranges, dict):
            raise ValueError('stylized_ranges')
        for sha, rows in ranges.items():
            if (len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha)
                    or not isinstance(rows, list)):
                raise ValueError('stylized_source')
            for row in rows:
                if (not isinstance(row, dict)
                        or any(type(row.get(k)) not in (int, float) or not math.isfinite(row[k])
                               for k in ('start_s', 'end_s'))
                        or not 0 <= row['start_s'] < row['end_s']
                        or type(row.get('film')) is not int or row['film'] not in {1, 2, 3}
                        or row.get('style') != '2d_stylized'
                        or not isinstance(row.get('verified_by'), str) or not row['verified_by'].strip()):
                    raise ValueError('stylized_range')
        return dict(data, file_sha256=hashlib.sha256(raw).hexdigest())
    except (OSError, ValueError, TypeError, KeyError, OverflowError) as error:
        detail = str(error) if isinstance(error, ValueError) else type(error).__name__
        raise LibraryStopped('visual_library:cast_missing_or_invalid:' + detail) from None


def recorded_film_cast(output, root, snapshot=None):
    state = Path(output) / 'library_state.json'
    if state.exists():
        pinned = json.loads(state.read_text())['input_lock']['configuration'].get('film_cast')
        if not pinned:
            raise LibraryStopped('visual_library:cast_missing_or_invalid:input_lock_missing')
        return pinned  # Resume/replay must never read today's cast.json.
    loaded = load_film_cast(root)
    if snapshot is not None and snapshot != loaded:
        raise LibraryStopped('visual_library:cast_missing_or_invalid:snapshot_changed')
    return loaded


def bind_film_cast(catalog, snapshot):
    for source in catalog['sources']:
        source['stylized_ranges'] = deepcopy(snapshot['stylized_ranges'].get(source['sha256'], []))
    return catalog


def overlaps_stylized(start, end, ranges):
    return any(max(start, row['start_s']) < min(end, row['end_s']) for row in ranges)
