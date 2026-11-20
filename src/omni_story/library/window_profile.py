"""Offline window profiles and online coarse recall (王骐昊 11-17: offline text representation,
online coarse recall by text, fine verification by watching).

Two layers, both template-independent and computed once per library observation:
  * program fields, read straight from the received observation (no model call);
  * semantic tags in a closed vocabulary, written by GLM from the observation TEXT only
    (no frames; Evaporate-style extraction from existing descriptions). Every tag cites the
    event indices it rests on; a dimension without evidence stays empty ("unknown").

A reference scene is mapped to the same vocabulary, so recall is a program score. Unknown never
excludes a window; it only earns no points. Watching, grouping and every later check are unchanged.
"""
from __future__ import annotations

import json
import re

from .state import json_sha

POLICY = 'window_profile_v1'
# Closed vocabulary, induced from the 11-17 survey: reference scene `interaction` fields
# (当面对话正反打 / 独白 / 对峙 / 身体冲突 / 围观 / 无对白动作) and the library's observed actions.
STAGING = ('两人当面交流', '多人同场交流', '单人独白或自语', '对峙或僵持', '身体冲突或拉扯', '追逐或逃离',
           '围观或群体反应', '递物或交易', '进食或饮用', '登场或展示', '独自动作', '无人物画面')
EMOTION = ('愤怒', '惊讶', '恐惧或慌张', '悲伤或哭泣', '开心', '委屈', '得意或炫耀', '尴尬或心虚', '平静')
POWER = ('一方压制另一方', '双方相当', '无互动关系')
DIMENSIONS = dict(staging=STAGING, emotion=EMOTION, power=POWER)
TITLE_ACTIONS = re.compile(r'标题|片尾|字幕卡|logo', re.I)


def _observed(record):
    window = record['window']
    observed = window['observation']
    from .subtitle_library import rejected_visual_evidence
    roles, events, ranges = (rejected_visual_evidence(window)
        if window.get('source_visual_policy') == 'silent_subtitle_visible_v1' else (set(), set(), set()))
    return window, observed, roles, events, ranges


def program_profile(record):
    """Structural facts the observation already states; rejected identity evidence is dropped."""
    window, observed, bad_roles, bad_events, bad_ranges = _observed(record)
    roles = [r for r in observed.get('roles', []) if r['role_id'] not in bad_roles]
    events = [e for i, e in enumerate(observed.get('events', [])) if i not in bad_events]
    usable = [u for i, u in enumerate(observed.get('usable_ranges', [])) if i not in bad_ranges]
    individuals = [r for r in roles if r.get('entity_kind') == 'individual' and r.get('identity_confirmed')]
    return dict(policy=POLICY, observation_id=record['observation_id'], record_sha256=json_sha(record),
        window_id=window['window_id'], source_sha256=window['source_sha256'],
        source_start_s=window['source_start_s'], source_end_s=window['source_end_s'],
        individuals=len(individuals), entities=sorted({r['entity_id'] for r in individuals if r.get('entity_id')}),
        place=(observed.get('scene') or {}).get('place', '') if not bad_roles else '',
        objects=sorted({e['object'] for e in events if isinstance(e.get('object'), str) and e['object'].strip()}),
        directed_events=sum(1 for e in events if e.get('actor_id') and e.get('recipient_id')),
        title_events=sum(1 for e in events if TITLE_ACTIONS.search(str(e.get('action', '')))),
        usable_s=round(sum(max(0.0, u['local_out_s'] - u['local_in_s']) for u in usable), 3),
        events=len(observed.get('events', [])))


def semantic_text(record):
    """The window as text only: what the library already wrote down, indexed by event."""
    window, observed, bad_roles, bad_events, bad_ranges = _observed(record)
    return dict(window_id=window['window_id'], summary=observed.get('summary', '') if not bad_roles else '',
        place=(observed.get('scene') or {}).get('place', '') if not bad_roles else '',
        roles=[dict(role_id=r['role_id'], appearance=r.get('appearance', ''), confirmed=bool(r.get('identity_confirmed')))
               for r in observed.get('roles', []) if r['role_id'] not in bad_roles],
        events=[dict(index=i, fact=e.get('observed_fact', ''), actor=e.get('actor_id'), recipient=e.get('recipient_id'),
                     action=e.get('action'), object=e.get('object'), cues=e.get('expression_cues', ''))
                for i, e in enumerate(observed.get('events', [])) if i not in bad_events],
        shots=[dict(events=u.get('event_indices', []), camera=u.get('camera', ''), speech_fit=u.get('speech_fit', ''))
               for i, u in enumerate(observed.get('usable_ranges', [])) if i not in bad_ranges],
        uses=[a.get('expression', '') for a in observed.get('affordances', [])
              if not bad_events & set(a.get('event_indices', []))])


def _vocabulary():
    return {k: list(v) for k, v in DIMENSIONS.items()}


def semantic_prompt(texts):
    return ('下面是素材库里每个窗口已有的文字看片记录（没有画面）。按固定词表给每个窗口打标签，只依据这些文字。'
        '每个标签必须写出它依据的events.index；文字里没有依据的维度留空列表，不要猜，不要用常识补全。'
        'staging是画面里实际发生的互动形式，可多选；emotion是events.cues或fact里写明的情绪，可多选；'
        'power只在有两人以上互动时选一个，否则留空。persistent_prop是贯穿多个事件的同一物件（写名词），没有就null。'
        '词表外但重要的互动形式写进other（简短短语），不要硬塞进词表。每个window_id恰好出现一次。'
        '返回JSON {"profiles":[{"window_id":"","staging":[{"tag":"","events":[0]}],"emotion":[{"tag":"","events":[0]}],'
        '"power":[{"tag":"","events":[0]}],"persistent_prop":null,"other":[]}]}。\n'
        + json.dumps(dict(vocabulary=_vocabulary(), windows=texts), ensure_ascii=False))


def validate_semantic(value, expected):
    """expected: {window_id: set of event indices present in its text}."""
    if not isinstance(value, dict) or not isinstance(value.get('profiles'), list):
        raise ValueError('window_profile:profiles_list_required')
    rows = value['profiles']
    ids = [r.get('window_id') if isinstance(r, dict) else None for r in rows]
    if sorted(map(str, ids)) != sorted(expected) or len(set(ids)) != len(ids):
        missing = sorted(set(expected) - set(ids)); extra = sorted(set(map(str, ids)) - set(expected))
        raise ValueError(f'window_profile:each_window_exactly_once:missing={missing}:extra={extra}')
    for row in rows:
        wid = row['window_id']
        for dim, vocab in DIMENSIONS.items():
            tags = row.get(dim, [])
            if not isinstance(tags, list):
                raise ValueError(f'window_profile:{wid}:{dim}_must_be_list')
            for tag in tags:
                if not isinstance(tag, dict) or tag.get('tag') not in vocab:
                    raise ValueError(f'window_profile:{wid}:{dim}_tag_not_in_vocabulary:{tag}')
                cited = tag.get('events')
                if (not isinstance(cited, list) or not cited or
                        any(type(i) is not int or i not in expected[wid] for i in cited)):
                    raise ValueError(f'window_profile:{wid}:{dim}:{tag.get("tag")}:events_must_cite_listed_indices')
            if len({t['tag'] for t in tags}) != len(tags):
                raise ValueError(f'window_profile:{wid}:{dim}_duplicate_tag')
        if len(row.get('power', [])) > 1:
            raise ValueError(f'window_profile:{wid}:power_at_most_one')
        prop = row.get('persistent_prop')
        if prop is not None and (not isinstance(prop, str) or not prop.strip()):
            raise ValueError(f'window_profile:{wid}:persistent_prop_noun_or_null')
        other = row.get('other', [])
        if not isinstance(other, list) or any(not isinstance(o, str) for o in other):
            raise ValueError(f'window_profile:{wid}:other_must_be_strings')
    return value


def tags(row, dim):
    return {t['tag'] for t in row.get(dim, [])}


# ---- reference side -------------------------------------------------------

def requirement_prompt(scenes, lines, ext=()):
    rows = [dict(scene_id=s['scene_id'], setting=s.get('setting', ''), roles=s.get('roles', []),
                 interaction=s.get('interaction', ''), template=s.get('template', ''),
                 visual_function=s.get('visual_function', ''), must_see=s.get('must_see', []),
                 reference_objects=s.get('reference_objects', []),
                 line_functions=[l.get('function') for l in lines if l.get('line_id') in s.get('line_ids', [])])
            for s in scenes]
    return ('把参考视频的每个场景抽象成与素材库相同词表的需求，用来在素材库里粗召回。'
        '素材是另一部作品，原物件、原职业、原台词都可以被剪辑和配音替换；所以只写画面上必须能看到的互动形式和情绪，'
        '不写只靠台词能表达的内容。staging列出能承载该场景的互动形式（可多选，按重要性排序，第一个最关键）；'
        'emotion列出画面上需要看得见的情绪；power选一个或留空。'
        '如果这个场景还需要词表表达不了、但对选素材确实重要的画面维度，写进needed_attributes（name与一句定义）。'
        + ('extensions是后来补充的维度，ext里按name给出需要的选项（不需要就省略该name）。' if ext else '')
        + '每个scene_id恰好一次。返回JSON {"scenes":[{"scene_id":"","staging":[],"emotion":[],"power":null,'
        '"persistent_prop":null,' + ('"ext":{},' if ext else '') + '"needed_attributes":[{"name":"","definition":""}]}]}。\n'
        + json.dumps(dict(vocabulary=_vocabulary(), **(dict(extensions=list(ext)) if ext else {}), scenes=rows),
                     ensure_ascii=False))


def validate_requirement(value, scene_ids, ext=()):
    if not isinstance(value, dict) or not isinstance(value.get('scenes'), list):
        raise ValueError('window_profile:scenes_list_required')
    got = [s.get('scene_id') if isinstance(s, dict) else None for s in value['scenes']]
    if sorted(map(str, got)) != sorted(scene_ids) or len(set(got)) != len(got):
        raise ValueError('window_profile:each_scene_exactly_once')
    for s in value['scenes']:
        for dim in ('staging', 'emotion'):
            if not isinstance(s.get(dim), list) or any(t not in DIMENSIONS[dim] for t in s[dim]):
                raise ValueError(f'window_profile:{s["scene_id"]}:{dim}_not_in_vocabulary')
        if not s['staging']:
            raise ValueError(f'window_profile:{s["scene_id"]}:staging_required')
        if s.get('power') is not None and s['power'] not in POWER:
            raise ValueError(f'window_profile:{s["scene_id"]}:power_not_in_vocabulary')
        options = {d['name']: set(d['options']) for d in ext}
        chosen = s.get('ext', {})
        if not isinstance(chosen, dict) or any(k not in options or not isinstance(v, list) or not set(v) <= options[k]
                                               for k, v in chosen.items()):
            raise ValueError(f'window_profile:{s["scene_id"]}:ext_options_not_in_extension_vocabulary')
        needed = s.get('needed_attributes', [])
        if not isinstance(needed, list) or any(not isinstance(n, dict) or not str(n.get('name', '')).strip()
                                               or not str(n.get('definition', '')).strip() for n in needed):
            raise ValueError(f'window_profile:{s["scene_id"]}:needed_attributes_name_and_definition')
    return value


# ---- online coarse recall -------------------------------------------------

def score(program, semantic, need, people):
    """Higher is closer. Unknown dimensions add nothing; nothing here excludes a window."""
    if program['title_events'] and program['title_events'] == program['events']:
        return -1.0                                  # title/credit cards only
    staging = tags(semantic, 'staging') if semantic else set()
    value = 0.0
    for rank, tag in enumerate(need['staging']):
        if tag in staging:
            value += 3.0 if rank == 0 else 1.5
    if semantic:
        value += 1.0 * len(tags(semantic, 'emotion') & set(need.get('emotion', [])))
        if need.get('power') and need['power'] in tags(semantic, 'power'):
            value += 1.0
        for name, wanted in (need.get('ext') or {}).items():
            got = {t['tag'] for t in (semantic.get('ext') or {}).get(name, [])}
            value += 1.0 * len(got & set(wanted))
        prop = need.get('persistent_prop')
        if prop and semantic.get('persistent_prop') and (prop in semantic['persistent_prop'] or semantic['persistent_prop'] in prop):
            value += 0.5
    value += 0.5 * min(program['individuals'], people)
    value += 0.3 * min(program['directed_events'], 3)
    value += 0.02 * min(program['usable_s'], 17.0)
    return value


def recall(programs, semantics, needs, people, n, neighbors=1):
    """Top-n windows per scene need, plus same-source neighbours, ordered by best score."""
    order = sorted(programs, key=lambda w: (programs[w]['source_sha256'], programs[w]['source_start_s'], w))
    pos = {w: i for i, w in enumerate(order)}
    best = {}
    for need in needs:
        people_n = people.get(need['scene_id'], 2)
        ranked = sorted(programs, key=lambda w: (-score(programs[w], semantics.get(w), need, people_n), w))
        for w in ranked[:n]:
            s = score(programs[w], semantics.get(w), need, people_n)
            best[w] = max(best.get(w, s), s)
    chosen = dict(best)
    for w, s in best.items():
        for d in range(1, neighbors + 1):
            for j in (pos[w] - d, pos[w] + d):
                if 0 <= j < len(order) and programs[order[j]]['source_sha256'] == programs[w]['source_sha256']:
                    chosen.setdefault(order[j], s - 0.01 * d)
    return sorted(chosen, key=lambda w: (-chosen[w], w))


# ---- offline backfill -----------------------------------------------------

def batches(records, size):
    rows = sorted(records, key=lambda r: r['window']['window_id'])
    return [rows[i:i + size] for i in range(0, len(rows), size)]


def _run_batches(library, output, folder_name, stage, make_prompt, make_validate, lock_extra, *,
                 batch_size, workers, max_batches, model_factory, only=None):
    """One isolated ledger per batch (like catalog pages): batches run in parallel, a rerun reuses
    received replies, and an unsettled request is never resent (that batch is reported)."""
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path
    from .opencode_provider import PROVIDER
    from .pipeline import _material_call
    from .state import LibraryState, LibraryStopped
    library, output = Path(library).resolve(), Path(output).resolve()
    if output == library / 'observations' or library / 'observations' in output.parents:
        raise ValueError('window_profile:output_cannot_be_library_records')
    records = [json.loads(p.read_text(encoding='utf-8')) for p in sorted((library / 'observations').glob('*.json'))]
    programs = {r['window']['window_id']: program_profile(r) for r in records}
    plan = batches([r for r in records if only is None or r['window']['window_id'] in only], batch_size)[:max_batches]

    def run(item):
        number, rows = item
        texts = [semantic_text(r) for r in rows]
        expected = {t['window_id']: {e['index'] for e in t['events']} for t in texts}
        folder = output / folder_name / f'batch_{number:03d}'
        lock = dict(library_root=str(library), window_ids=sorted(expected), texts_sha256=json_sha(texts),
                    configuration={**PROVIDER, 'workflow': POLICY}, **lock_extra)
        state = LibraryState(folder, lock, max_requests=None, registry_path=output / '.runs.json')
        if any(c['status'] in ('submitted', 'uncertain') for c in state.data['calls']):
            return number, None, 'request_outcome_unknown_no_replay'
        glm = model_factory(state)
        validate = make_validate(expected)
        validate.repair_limit = 2
        try:
            value = _material_call(state, glm, stage.format(number=number), make_prompt(texts), validate)
        except (LibraryStopped, ValueError) as exc:
            return number, None, str(exc)[:300]
        return number, value, None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run, enumerate(plan, 1)))
    return output, programs, plan, results


def backfill(library, output, *, batch_size=20, workers=3, max_batches=None, model_factory=None, rebatch_unknown=False):
    """Profile every library observation once in the base vocabulary.

    rebatch_unknown (user 11-17, after INC-20261117-10): windows whose batch request has an unknown
    outcome are asked again in a differently composed batch set ('batches_r2'); the unknown
    requests themselves are never resent and their ledgers stay as they are."""
    from .state import write_json
    output, programs, plan, results = _run_batches(library, output, 'batches', 'material_scene_profile_{number:03d}',
        semantic_prompt, lambda expected: (lambda v: validate_semantic(v, expected)), {},
        batch_size=batch_size, workers=workers, max_batches=max_batches, model_factory=model_factory)
    if rebatch_unknown:
        unknown = {r['window']['window_id'] for (n, v, e), rows in zip(results, plan)
                   if e == 'request_outcome_unknown_no_replay' for r in rows}
        if unknown:
            _, _, plan2, results2 = _run_batches(library, output, 'batches_r2', 'material_scene_profile_r2_{number:03d}',
                semantic_prompt, lambda expected: (lambda v: validate_semantic(v, expected)), dict(rebatch='unknown_outcome_r2'),
                batch_size=batch_size - 3, workers=workers, max_batches=None, model_factory=model_factory, only=unknown)
            results = [r for r in results if r[2] != 'request_outcome_unknown_no_replay'] + [(1000 + n, v, e) for n, v, e in results2]
            plan = plan + plan2
    semantics, failures, other = {}, [], {}
    for number, value, error in results:
        if error:
            failures.append(dict(batch=number, error=error)); continue
        for row in value['profiles']:
            semantics[row['window_id']] = row
            for phrase in row.get('other', []):
                other.setdefault(phrase, []).append(row['window_id'])
    output.mkdir(parents=True, exist_ok=True)
    with (output / f'{POLICY}.jsonl').open('w', encoding='utf-8') as f:
        for wid in sorted(programs):
            f.write(json.dumps(dict(programs[wid], semantic=semantics.get(wid)), ensure_ascii=False) + '\n')
    summary = dict(policy=POLICY, vocabulary=_vocabulary(), windows=len(programs), profiled=len(semantics),
                   batches=len(plan), failures=failures,
                   other_phrases=sorted(([p, len(w)] for p, w in other.items()), key=lambda x: -x[1]))
    write_json(output / 'schema.json', summary)
    return summary


def load(output):
    """Base profiles plus every backfilled extension dimension (semantic['ext'][name])."""
    from pathlib import Path
    output = Path(output)
    rows = [json.loads(l) for l in (output / f'{POLICY}.jsonl').read_text(encoding='utf-8').splitlines() if l]
    rows = {r['window_id']: r for r in rows}
    for dim in extensions(output):
        path = output / f'ext_{dim["name"]}.jsonl'
        if not path.is_file():
            continue
        for line in path.read_text(encoding='utf-8').splitlines():
            ext = json.loads(line)
            row = rows.get(ext['window_id'])
            if row and row.get('semantic') is not None:
                row['semantic'].setdefault('ext', {})[dim['name']] = ext['tags']
    return rows


def main(argv=None):
    import argparse, os
    parser = argparse.ArgumentParser(description='offline window profiles (text only, GLM)')
    parser.add_argument('library'); parser.add_argument('output')
    parser.add_argument('--batch-size', type=int, default=20); parser.add_argument('--workers', type=int, default=3)
    parser.add_argument('--max-batches', type=int)
    parser.add_argument('--rebatch-unknown', action='store_true',
                        help='ask again, in a different batch set, for windows whose batch outcome is unknown')
    parser.add_argument('--extend-from', nargs='*', default=None,
                        help='P4: library_state.json ledgers whose pending attributes may extend the vocabulary')
    args = parser.parse_args(argv)
    from .opencode_provider import OpenCodeMCP
    from .server_cli import _home, credential, settings
    server = settings(_home())
    os.environ['Z_AI_API_KEY'] = credential(_home())
    factory = lambda state: OpenCodeMCP(state, package_root=server['mcp_package_root'],
                                        executable=server['opencode_executable'])
    if args.extend_from is not None:
        from pathlib import Path
        from .opencode_provider import PROVIDER
        from .pipeline import _material_call
        from .state import LibraryState
        pending = collect_pending(args.extend_from)
        out = Path(args.output).resolve()
        state = LibraryState(out / 'extend' / json_sha(pending)[:12], dict(pending=pending,
            configuration={**PROVIDER, 'workflow': POLICY + '_extend'}), max_requests=None, registry_path=out / '.runs.json')
        glm = factory(state)
        added = propose_extensions(lambda n, pr, v: _material_call(state, glm, n, pr, v), out, pending)
        result = backfill_extensions(args.library, out, batch_size=args.batch_size, workers=args.workers,
                                     max_batches=args.max_batches, model_factory=factory)
        print(json.dumps(dict(pending=len(pending), proposed=[d['name'] for d in added], **result), ensure_ascii=False))
        return
    summary = backfill(args.library, args.output, batch_size=args.batch_size, workers=args.workers,
                       max_batches=args.max_batches, model_factory=factory, rebatch_unknown=args.rebatch_unknown)
    print(json.dumps({k: summary[k] for k in ('windows', 'profiled', 'batches', 'failures')}, ensure_ascii=False))



def scene_needs(call, scenes, lines, ext=()):
    """One text call: reference scenes in the library vocabulary; people come from the split."""
    ids = [s['scene_id'] for s in scenes]
    validate = lambda v: validate_requirement(v, ids, ext)
    validate.repair_limit = 2
    value = call('material_scene_profile_need', requirement_prompt(scenes, lines, ext), validate)
    people = {s['scene_id']: max(1, len(s.get('roles', []))) for s in scenes}
    return value['scenes'], people


# ---- P4: self-evolving vocabulary ----------------------------------------
# A reference may need a picture dimension the vocabulary lacks (needed_attributes, recorded online
# as window_profile_pending_attributes), and the offline taggers write phrases they could not place
# (schema.json other_phrases). Offline, GLM merges these into closed extension dimensions; each new
# dimension is then backfilled from the same text for the whole library. Online, a window without
# the dimension yet is unknown: it earns nothing and is never excluded.

def extensions(output):
    from pathlib import Path
    path = Path(output) / 'extensions.json'
    return json.loads(path.read_text(encoding='utf-8'))['dimensions'] if path.is_file() else []


def collect_pending(ledgers):
    """Pending attributes from run ledgers (library_state.json artifacts) or saved needs_*.json."""
    rows = []
    for path in ledgers:
        try:
            data = json.loads(open(path, encoding='utf-8').read())
        except (OSError, ValueError):
            continue
        if isinstance(data.get('needs'), list):          # a saved scene_needs result (needs_*.json)
            rows.extend(dict(name=a['name'], definition=a.get('definition', ''), run=str(path))
                        for n in data['needs'] for a in n.get('needed_attributes', []) if a.get('name'))
            continue
        for ref in data.get('artifacts', {}).get('window_profile_pending_attributes') or []:
            try:
                value = json.loads(open(ref['path'], encoding='utf-8').read())   # set_artifact keeps a file
            except (OSError, ValueError, KeyError, TypeError):
                continue
            for attr in (value if isinstance(value, list) else [value]):
                if isinstance(attr, dict) and attr.get('name'):
                    rows.append(dict(name=attr['name'], definition=attr.get('definition', ''), run=str(path)))
    return rows


def extension_prompt(pending, other_phrases, existing):
    return ('素材库窗口画像要新增检索维度。下面是参考视频提出、但现有词表表达不了的画面维度（pending），'
        '以及给素材打标签时词表放不下的短语（other_phrases，附出现次数）。把含义相同的合并，'
        '只保留跨作品通用、能从文字看片记录判断、对选素材确实有用的维度；每个维度给2到8个互斥或可并列的选项。'
        '已有维度（vocabulary与existing_extensions）不要重复。name用小写英文加下划线。'
        '返回JSON {"dimensions":[{"name":"","definition":"","options":[],"multi":true,"merged_from":[]}]}，'
        '没有值得新增的就返回空列表。\n'
        + json.dumps(dict(vocabulary=_vocabulary(), existing_extensions=existing, pending=pending,
                          other_phrases=other_phrases[:200]), ensure_ascii=False))


def validate_extensions(value, existing):
    if not isinstance(value, dict) or not isinstance(value.get('dimensions'), list):
        raise ValueError('window_profile:dimensions_list_required')
    taken = set(DIMENSIONS) | {d['name'] for d in existing} | {'ext', 'other', 'persistent_prop'}
    for d in value['dimensions']:
        if not isinstance(d, dict) or not re.fullmatch(r'[a-z][a-z0-9_]{1,30}', str(d.get('name', ''))):
            raise ValueError('window_profile:dimension_name_lowercase_ascii')
        if d['name'] in taken:
            raise ValueError(f'window_profile:dimension_exists:{d["name"]}')
        opts = d.get('options')
        if (not isinstance(opts, list) or not 2 <= len(opts) <= 8 or len(set(opts)) != len(opts)
                or any(not isinstance(o, str) or not o.strip() for o in opts)):
            raise ValueError(f'window_profile:{d["name"]}:two_to_eight_distinct_options')
        if not str(d.get('definition', '')).strip() or not isinstance(d.get('multi'), bool):
            raise ValueError(f'window_profile:{d["name"]}:definition_and_multi_required')
        taken.add(d['name'])
    return value


def propose_extensions(call, output, pending):
    """One offline text call; accepted dimensions are appended with a version number."""
    from pathlib import Path
    from .state import write_json
    output = Path(output)
    existing = extensions(output)
    schema = json.loads((output / 'schema.json').read_text(encoding='utf-8')) if (output / 'schema.json').is_file() else {}
    validate = lambda v: validate_extensions(v, existing)
    validate.repair_limit = 2
    value = call('material_scene_profile_extend_' + json_sha(dict(pending=pending, existing=existing))[:12],
                 extension_prompt(pending, schema.get('other_phrases', []), existing), validate)
    version = 1 + max([d['version'] for d in existing] or [0])
    added = [dict(d, version=version) for d in value['dimensions']]
    if added:
        write_json(output / 'extensions.json', dict(dimensions=existing + added))
    return added


def extension_prompt_for_windows(texts, dims):
    return ('下面是素材库窗口已有的文字看片记录（没有画面）。只按给定的新增维度打标签，只依据这些文字。'
        '每个标签写出依据的events.index；没有依据就留空列表，不要猜。multi为false的维度最多选一个。'
        '每个window_id恰好一次。返回JSON {"profiles":[{"window_id":"","ext":{"维度name":[{"tag":"","events":[0]}]}}]}。\n'
        + json.dumps(dict(dimensions=dims, windows=texts), ensure_ascii=False))


def validate_extension_tags(value, expected, dims):
    if not isinstance(value, dict) or not isinstance(value.get('profiles'), list):
        raise ValueError('window_profile:profiles_list_required')
    ids = [r.get('window_id') if isinstance(r, dict) else None for r in value['profiles']]
    if sorted(map(str, ids)) != sorted(expected) or len(set(ids)) != len(ids):
        raise ValueError('window_profile:each_window_exactly_once')
    spec = {d['name']: d for d in dims}
    for row in value['profiles']:
        ext = row.get('ext', {})
        if not isinstance(ext, dict) or set(ext) - set(spec):
            raise ValueError(f'window_profile:{row["window_id"]}:ext_names_not_requested')
        for name, tags_ in ext.items():
            if not isinstance(tags_, list) or (not spec[name]['multi'] and len(tags_) > 1):
                raise ValueError(f'window_profile:{row["window_id"]}:{name}_tag_count')
            for t in tags_:
                if (not isinstance(t, dict) or t.get('tag') not in spec[name]['options'] or not isinstance(t.get('events'), list)
                        or not t['events'] or any(type(i) is not int or i not in expected[row['window_id']] for i in t['events'])):
                    raise ValueError(f'window_profile:{row["window_id"]}:{name}:tag_or_events_invalid')
    return value


def backfill_extensions(library, output, *, batch_size=20, workers=3, max_batches=None, model_factory=None):
    """Backfill every extension dimension that has no table yet, from the same text."""
    from pathlib import Path
    output = Path(output)
    todo = [d for d in extensions(output) if not (output / f'ext_{d["name"]}.jsonl').is_file()]
    if not todo:
        return dict(added=[], failures=[])
    tag = 'v' + str(max(d['version'] for d in todo)) + '_' + json_sha(todo)[:8]
    _, programs, plan, results = _run_batches(library, output, 'ext_' + tag, 'material_scene_profile_ext_' + tag + '_{number:03d}',
        lambda texts: extension_prompt_for_windows(texts, todo),
        lambda expected: (lambda v: validate_extension_tags(v, expected, todo)), dict(extensions=todo),
        batch_size=batch_size, workers=workers, max_batches=max_batches, model_factory=model_factory)
    failures = [dict(batch=n, error=e) for n, v, e in results if e]
    if failures:
        return dict(added=[], failures=failures)            # a table is written only when complete
    tagged = {r['window_id']: r.get('ext', {}) for n, v, e in results for r in v['profiles']}
    for d in todo:
        with (output / f'ext_{d["name"]}.jsonl').open('w', encoding='utf-8') as f:
            for wid in sorted(tagged):
                f.write(json.dumps(dict(window_id=wid, tags=tagged[wid].get(d['name'], [])), ensure_ascii=False) + '\n')
    return dict(added=[d['name'] for d in todo], failures=[])


if __name__ == '__main__':
    main()
