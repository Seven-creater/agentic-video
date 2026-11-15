"""Ground the actual speaking shots together before casting and writing dialogue."""
from copy import deepcopy
import json

from .media import _run, verify_source, sha256_file
from .state import json_sha, write_json


def contact_sheet(route, slots, masks):
    from PIL import Image, ImageDraw

    folder = route.cache / ('scene_grounding_' + json_sha(dict(slots=slots, masks=masks))[:20])
    folder.mkdir(parents=True, exist_ok=True)
    sheet = folder / 'shots.jpg'
    entries, pictures = [], []
    for number, slot in enumerate(slots, 1):
        low, high = slot.get('loop_range') or [slot['source_start_s'], slot['source_end_s']]
        times = [low + (high-low)*fraction for fraction in (.1, .5, .9)]
        entries.append(dict(shot=number, slot_id=slot['slot_id'], role_id=slot['role_id'],
            source_start_s=low, source_end_s=high, loopable=slot['loopable']))
        for index, time in enumerate(times):
            path = folder / f'{number}_{index}.jpg'
            if not path.exists():
                source = route.sources[slot['source_sha256']]
                _run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-ss', str(time),
                    '-i', str(verify_source(source)), '-map', f"0:{source.get('video_stream_index', 0)}",
                    '-frames:v', '1', '-vf', 'scale=400:226', path.name], cwd=folder)
            with Image.open(path) as picture:
                frame = picture.convert('RGB')
            bottom = min((m['y'] for m in (masks or []) if m['start_s'] <= time < m['end_s']), default=1)
            ImageDraw.Draw(frame).rectangle((0, round(226*bottom), 400, 226), fill='black')
            pictures.append(frame)
    canvas = Image.new('RGB', (1200, len(slots)*250), 'black')
    draw = ImageDraw.Draw(canvas)
    for i, picture in enumerate(pictures):
        x, y = (i % 3)*400, (i // 3)*250
        canvas.paste(picture, (x, y+24))
        draw.text((x+8, y+4), f'Shot {i//3+1} / {i%3+1}', fill='yellow')
    canvas.save(sheet, quality=90)
    write_json(folder/'lineage.json', dict(kind='sparse_contact_sheet', source_sha256=slots[0]['source_sha256'],
        source_start_s=min(s['source_start_s'] for s in slots), source_end_s=max(s['source_end_s'] for s in slots),
        path=str(sheet), sha256=sha256_file(sheet), entries=entries))
    return sheet, entries


def review(route, scene, slots, hints, index, masks):
    if not slots:
        return dict(groups=[], actors={})
    sheet, entries = contact_sheet(route, slots, masks)
    prompt = ('逐行看实际候选镜头，每行是同一镜头的头中尾三帧。角色外观提示仅供定位，旧电影姓名可能标错。'
        '先按实际外观确认每个role_id的name（确知才填，否则null）和appearance，不要把不同熊猫/鸟混为一人。'
        '再将镜头划分为可直接剪成同一段当面对话的groups。每组须是同一连续地点、同一时段、同一组对话人物；'
        '允许双方正反打，但不能把围观人群、对另一组人讲话、远处喊话、旁观者反应当成回答者镜头。'
        '同一电影、同一夜晚、同一村庄不代表同一对话。庭院与吊桥等必须分组；无法确认共同对话关系则分组。'
        '不得为了凑齐说话人数合并组。每个镜头必须且仅属于一组，可以独立成组。'
        'description只描述该组实际可见人物、空间和互动，不沿用旧的故事角色或旧场景描述。'
        '只输出JSON {"actors":{"role_id":{"name":null,"appearance":"可见外观"}},'
        '"groups":[{"group_id":"g1","slot_ids":["槽位ID"],"description":"同组的视觉依据"}]}。\n' +
        json.dumps(dict(shots=entries, characters=[dict(role_id=r['role_id'], appearance=r['appearance'])
            for r in hints.values()]), ensure_ascii=False))
    expected = {s['slot_id'] for s in slots}
    roles = {s['role_id'] for s in slots}
    def validate(value):
        groups, actors = value.get('groups', []), value.get('actors', {})
        listed = [s for g in groups for s in g.get('slot_ids', [])]
        if set(listed) != expected or len(listed) != len(expected):
            raise ValueError('每个实际槽位必须且仅属于一个对话组：' + str(sorted(expected)))
        if len({g.get('group_id') for g in groups}) != len(groups):
            raise ValueError('group_id须唯一')
        if any(not g.get('description') or not g.get('slot_ids') for g in groups):
            raise ValueError('每组须有镜头与实际视觉依据')
        if any(not isinstance(actors.get(r), dict) or not actors[r].get('appearance') for r in roles):
            raise ValueError('须逐一确认实际说话者的外观：' + str(sorted(roles)))
    number = len([c for c in route.state.data['calls'] if c['name'].startswith(f'material_scene_grounding_{index}_')]) + 1
    reply = route._call(f'material_scene_grounding_{index}_{number}', prompt, validate, sheet, image=True)
    return dict(reply, image=str(sheet), entries=entries)


def select(scene, slots, grounding):
    """Only cast from one verified conversation; missing speakers trigger retrieval."""
    if not grounding['groups']:
        return [], None
    by_id = {s['slot_id']: s for s in slots}
    needed = len({r.get('role') for r in scene['reference_lines']} - {None}) or len(scene['roles'])
    def score(group):
        shots = [by_id[s] for s in group['slot_ids']]
        looping = {s['role_id'] for s in shots if s.get('loop_range')}
        speakers = {s['role_id'] for s in shots}
        return (min(needed, len(looping)), min(needed, len(speakers)),
                sum(s['source_end_s']-s['source_start_s'] for s in shots if s.get('loop_range')))
    chosen = max(grounding['groups'], key=score)
    return [dict(by_id[s], conversation_id=chosen['group_id']) for s in chosen['slot_ids']], deepcopy(chosen)
