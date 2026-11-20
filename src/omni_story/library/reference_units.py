"""Keep source ASR evidence separate from the utterances used by the editor."""
from copy import deepcopy
import re


def _text(value):
    return re.sub(r'[\W_]+', '', value, flags=re.UNICODE).casefold()


def role_aliases(value, characters, duration):
    """Only the full-video review may explicitly merge observed identities."""
    rows = value.get('role_aliases', [])
    if not isinstance(rows, list):
        raise ValueError('reference_turns:role_aliases须为数组，无确认合并则[]')
    aliases, known = {}, sorted(characters)
    for i, row in enumerate(rows):
        where = f'reference_turns:role_aliases[{i}]'
        if not isinstance(row, dict):
            raise ValueError(f'{where}须为对象{{alias_role,canonical_role,evidence,reference_spans}}')
        old, canonical = row.get('alias_role'), row.get('canonical_role')
        spans = row.get('reference_spans')
        if old not in characters or canonical not in characters:
            raise ValueError(f'{where}: alias_role={old!r}、canonical_role={canonical!r}都须是已有人物role，可用={known}')
        if old == canonical or old in aliases:
            raise ValueError(f'{where}: alias_role={old!r}不能等于canonical_role，也不能重复合并')
        if not isinstance(row.get('evidence'), str) or not row['evidence'].strip():
            raise ValueError(f'{where}: evidence须写外观/连续动作依据')
        if not isinstance(spans, list) or len(spans) < 2 or any(
                not isinstance(s, list) or len(s) != 2 or any(type(t) not in (int, float) for t in s) or
                not 0 <= s[0] < s[1] <= duration for s in spans):
            raise ValueError(f'{where}: reference_spans须为至少两个[起秒,止秒]数字数组，'
                             f'0≤起秒<止秒≤{duration:.2f}，用参考片秒数而非"1:33"文字；当前={spans!r}')
        aliases[old] = canonical
    for old in aliases:
        seen, current = set(), old
        while current in aliases:
            if current in seen:
                raise ValueError('reference_turns:人物别名合并不能形成循环')
            seen.add(current); current = aliases[current]
        aliases[old] = current
    return aliases


def expand_turn(line, observation):
    """Optional speaker changes partition known words; never invent a timestamp."""
    parts = observation.get('parts')
    base = dict(line, source_asr_line_id=line.get('source_asr_line_id', line['line_id']))
    fields = ('kind', 'role', 'speaker_evidence', 'function')
    if parts is None:
        return [dict(base, **{k: observation.get(k) for k in fields})]
    words = line.get('source_words', [])
    if not isinstance(parts, list) or len(parts) < 2 or not words or not all(
            w.get('timing_usable') is True for w in words):
        raise ValueError('reference_turns:拆分须有至少两段及完整可用词级时间；否则保留整行并说明不确定')
    result, cursor = [], 0
    for part in parts:
        first, last = part.get('word_start'), part.get('word_end')
        if (type(first) is not int or type(last) is not int or first != cursor or
                not first < last <= len(words) or not isinstance(part.get('text'), str)):
            raise ValueError('reference_turns:parts按顺序覆盖全部词索引[word_start,word_end)，不重叠不遗漏')
        # Use a source-word boundary, shared by both neighbours, rather than a
        # model-authored second value. Keep any small intra-word silence.
        start = line['start_s'] if first == 0 else words[first]['source_start_s']
        end = line['end_s'] if last == len(words) else words[last]['source_start_s']
        if not line['start_s'] <= start < end <= line['end_s']:
            raise ValueError('reference_turns:拆分的词级边界须位于原ASR行内')
        result.append(dict(base, start_s=start, end_s=end, text=part['text'],
            source_word_range=[first, last], source_words=deepcopy(words[first:last]),
            **{k: part.get(k) for k in fields}))
        cursor = last
    if cursor != len(words) or _text(''.join(r['text'] for r in result)) != _text(line['text']):
        raise ValueError('reference_turns:拆分只能分配已核实原句，不得增加、删除或重复文字')
    return result


def require_picture_beats(scenes):
    for scene in scenes:
        beats=scene.get('picture_beats')
        sid=scene.get('scene_id')
        if not isinstance(beats,list) or not beats:
            raise ValueError(f'{sid} 缺picture_beats。按出镜顺序填写每段的roles、action、kind。')
        for beat in beats:
            if (not isinstance(beat,dict) or beat.get('kind') not in ('exchange','insert','action')
                    or not isinstance(beat.get('roles'),list)
                    or any(not isinstance(r,str) or not r.strip() for r in beat['roles'])
                    or not isinstance(beat.get('action'),str) or not beat['action'].strip()):
                raise ValueError(f'{sid} 的每个picture_beat需kind=exchange/insert/action，实际可见roles数组及action，纯物件填roles=[]。')


def freeze_partition(value, lines, duration, *, require_verbatim=False):
    """Derive frozen facts and legal cut points from the semantic partition."""
    result = deepcopy(value)
    by_id = {r['line_id']: r for r in lines}
    scenes, rows = result.get('scenes'), result.get('lines')
    if not isinstance(scenes, list) or not scenes or not isinstance(rows, list):
        raise ValueError('reference_partition:须有scenes及逐行叙事要求lines')
    if [r.get('line_id') for r in rows] != list(by_id):
        raise ValueError('reference_partition:叙事要求按顺序覆盖全部已核实发言单元')
    known_roles = {role for r in lines for role in [r.get('role'), *r.get('addressee_roles', [])] if role}
    assignments = [i for s in scenes for i in s.get('line_ids', [])]
    if assignments != list(by_id):
        raise ValueError('reference_partition:场景按顺序恰好分配全部发言单元，不增删不交换')
    for row in rows:
        original = by_id[row['line_id']]
        if require_verbatim or 'verbatim_terms' in row:
            terms = row.get('verbatim_terms')
            if (not isinstance(terms, list) or any(not isinstance(t, str) or not t.strip() or
                    t != t.strip() or t not in original.get('text', '') for t in terms) or
                    len(terms) != len(set(terms))):
                raise ValueError(f'reference_partition:第{row["line_id"]}行verbatim_terms须为不重复的原句词组数组；'
                                 '逐字摘自本行text，不得换同义词；无需保留原词时填[]')
        for key in ('role', 'text'):
            if key in original:
                row[key] = original[key]
    for scene in scenes:
        # Dialogue roles are identities, not a new chance to rename characters.
        if scene.get('line_ids'):
            observed = [role for i in scene['line_ids']
                        for role in [by_id[i]['role'], *by_id[i].get('addressee_roles', [])]]
            listeners = [r for r in scene.get('roles', []) if r in known_roles]
            scene['roles'] = list(dict.fromkeys([*observed, *listeners]))
    cuts = [0.0]
    for index in range(1, len(scenes)):
        before = [by_id[i]['end_s'] for s in scenes[:index] for i in s['line_ids']]
        after = [by_id[i]['start_s'] for s in scenes[index:] for i in s['line_ids']]
        low, high = max(before, default=0.0), min(after, default=duration)
        if low > high:
            raise ValueError('reference_partition:发言时间交叠，须调整分组，不能截断已核实语音')
        hint = scenes[index].get('start_s')
        if type(hint) not in (int, float) or not low <= hint <= high:
            hint = min(max(hint, low), high) if type(hint) in (int, float) else (low+high)/2
        if not cuts[-1] < hint < duration:
            raise ValueError('reference_partition:独立视觉场景须有真实可用时间区间')
        cuts.append(hint)
    cuts.append(duration)
    for i, scene in enumerate(scenes):
        scene.update(start_s=cuts[i], end_s=cuts[i+1])
    return result
