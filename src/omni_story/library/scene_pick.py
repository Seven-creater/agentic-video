"""Whole-scene picking by text (user 11-18/11-19), replacing the per-template catalog reading.

Offline, once per library (`<visual_library_root>/scene_pick/`): the source is cut into single
scenes (title/credits cards, then GLM reading the window records: one place, one group of people,
continuous time), and each scene's record is the library's own window observations of the events
inside it. Picture records made from small sheets are not used: they misread a market stall as a
pink room (s033, 11-18). Duplicates the compilation plays twice are marked.

Online, per template, text only and as fast as possible (user 11-19): the scene records are read in
a few pages in parallel, each page proposes 2 scenes, and one call ranks them; casting gets the
best one first and the others only if it asks. Similar people, positions and kind of place are
enough: dialogue, emotion, conflict and props are made by editing and dubbing (user 11-18). The
film is adapted inside one chosen scene, as the browser always did.
"""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

POLICY = 'whole_scene_pick_v2'
PAGE_CHARS = 42000
PER_PAGE = 2
RANKED = 4
RULE = ('用途：把动画素材里的一整场戏重新剪辑并配上新台词，改编成参考的内容。原片台词、情绪、有没有冲突、道具都不重要，'
        '普通聊天也能剪成吵架；画面里说话的镜头可以循环使用来配新台词。判断三件事：'
        '1)人数（必须）：能分清的人物数不少于参考有台词的角色数，最好相等或多一个，且能分清谁是谁；'
        '2)人物位置关系（最重要）：要有这些人当面对话的画面，例如两人隔着东西面对面、一坐一站、一人走近另一人；'
        '3)场景能被说成参考的地方（尽量）：同类功能即可，卖什么就改成买什么，例如摊位、柜台、小店都算做买卖的地方。'
        '没有相似地点时，人数和当面对话对得上的优先。整条参考在同一场素材里改编完成。')


def load(root):
    """Offline scene data next to the library, or None (the browser then reads the catalog)."""
    if not root:
        return None
    root = Path(root)
    # Runs read a published snapshot (<library>/publications/<id>/library); offline derived data
    # lives with the library itself, beside its observations (03 cold start 11-18 missed it).
    roots = [root] + ([root.parent.parent.parent] if root.name == 'library' and root.parent.parent.name == 'publications' else [])
    folder = next((r / 'scene_pick' for r in roots if (r / 'scene_pick' / 'scenes.json').is_file()), None)
    if folder is None:
        return None
    data = json.loads((folder / 'scenes.json').read_text())
    scenes = {s['scene_id']: s for s in data['scenes'] if not s.get('duplicate_of') and s.get('record')}
    if not scenes:
        return None
    return dict(folder=str(folder), scenes=scenes, characters=data.get('characters', {}), source_sha256=data.get('source_sha256'))


def for_selection(route, folder):
    """Read only the original text catalog, including when resuming an older run."""
    return load(getattr(route, 'config', {}).get('visual_library_root'))


def references(route):
    return [dict(scene_id=s['scene_id'], people=len(s.get('roles', [])), roles=s.get('roles', []),
                 place=s.get('setting', ''), position=s.get('interaction', ''),
                 **(dict(visual_function=s.get('visual_function',''), required=s.get('required_actions',''),
                         must_see=s.get('must_see',[]), has_dialogue=bool(s.get('line_ids')),
                         start_s=s.get('start_s'), end_s=s.get('end_s'),
                         picture_beats=s.get('picture_beats', []),
                         turns=[{k: line[k] for k in ('role','addressee_roles','on_screen','function') if k in line}
                                for line in getattr(route,'lines',[]) if line['line_id'] in s.get('line_ids',[])]) if len(route.scenes)>1 else {})) for s in route.scenes]


def appearances(data, ids):
    catalog = data.get('characters', {})
    codes = {code for sid in ids for code in data['scenes'][sid].get('characters', [])}
    return {code: catalog.get(code, {}).get('appearance') or '外观未登记' for code in sorted(codes)}


def catalog_text(data, ids):
    lines = [data['scenes'][sid]['record'] + ' 人物:' + ','.join(data['scenes'][sid].get('characters', []))
             for sid in ids]
    return '本页人物编号和外观（整场名单只供初选）：' + json.dumps(appearances(data, ids), ensure_ascii=False) + '\n' + '\n'.join(lines)


def pages(data, pool):
    out, cur, size = [], [], 0
    for sid in pool:
        n = len(data['scenes'][sid]['record'])
        if cur and size + n > PAGE_CHARS:
            out.append(cur); cur, size = [], 0
        cur.append(sid); size += n
    return out + [cur] if cur else out


def pick(call, call_page, route, data, eligible):
    """call(name, prompt, validate) for one text request; call_page(index, name, prompt, validate)
    for the page requests (run in parallel). eligible(sid) -> bool (watchable).
    Returns the proposed scenes and the ranked list (best first) with editing notes."""
    refs = references(route)
    pool = [sid for sid in sorted(data['scenes']) if eligible(sid)]
    if len(pool) < RANKED:
        return None
    head = RULE + '\n参考场景（按顺序）：' + json.dumps(refs, ensure_ascii=False)
    batches = pages(data, pool)

    def one(item):
        i, ids = item
        k = min(PER_PAGE, len(ids))          # a short last page offers what it has
        prompt = (head + f'\n下面是素材库第{i + 1}/{len(batches)}页，每行是一整场戏（编号|时长|地点|这场里按时间发生的事）：\n' +
                  catalog_text(data, ids) +
                  f'\n从本页挑{k}个最可能把整条参考改编进去的场景，按优先顺序。返回JSON {{"candidates":[""]}}。')

        def valid(v):
            c = v.get('candidates') if isinstance(v, dict) else None
            if not isinstance(c, list) or len(c) != k or len(set(c)) != k or any(x not in ids for x in c):
                raise ValueError(f'candidates须恰好{k}个本页不同的场景编号')
            return v
        name = f'material_scene_browser_pick_page_{i + 1}'
        return (call_page(i, name, prompt, valid) if call_page else call(name, prompt, valid))['candidates']
    if call_page is None:                    # one ledger: pages one after another
        proposed = [s for item in enumerate(batches) for s in one(item)]
    else:
        with ThreadPoolExecutor(max_workers=3) as pool_:
            proposed = [s for c in pool_.map(one, enumerate(batches)) for s in c]
    prompt = (head + '\n候选整场戏（编号|时长|地点|这场里按时间发生的事）：\n' +
              catalog_text(data, proposed) +
              f'\n把最能改编整条参考的{RANKED}个按优先顺序排好，第一个是最合适的；每个写剪辑能补什么、配音能补什么、明确限制。'
              '返回JSON {"ranked":[{"scene_id":"","editing_can_add":"","dubbing_can_add":"","limitations":""}]}。')

    def valid_rank(v):
        rows = v.get('ranked') if isinstance(v, dict) else None
        if (not isinstance(rows, list) or len(rows) != RANKED or
                len({r.get('scene_id') for r in rows if isinstance(r, dict)}) != RANKED or
                any(not isinstance(r, dict) or r.get('scene_id') not in proposed or
                    any(not isinstance(r.get(k), str) or not r[k].strip() for k in ('editing_can_add', 'dubbing_can_add', 'limitations'))
                    for r in rows)):
            raise ValueError(f'ranked须恰好{RANKED}个不同的候选编号，每个写editing_can_add/dubbing_can_add/limitations：' + json.dumps(proposed))
        return v
    ranked = call('material_scene_browser_pick_rank', prompt, valid_rank)['ranked']
    return dict(policy=POLICY, references=refs, matched=proposed, top4=ranked)
