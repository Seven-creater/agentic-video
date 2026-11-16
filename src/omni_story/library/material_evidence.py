"""Creative-task visual identities and quarantines; never change the source library."""
from copy import deepcopy
from pathlib import Path

from .media import sha256_file
from .state import LibraryStopped, json_sha, write_json


def overlap_conflicts(previous, current, tolerance_s=.3):
    conflicts=[]
    for a in previous:
        for b in current:
            if a['source_sha256']!=b['source_sha256'] or a['window_id']==b['window_id']:
                continue
            start=max(a['source_start_s'],b['source_start_s'])
            end=min(a['source_end_s'],b['source_end_s'])
            if end-start<tolerance_s:
                continue
            fields=[k for k in ('visible','speaking','clear_face') if set(a[k])!=set(b[k])]
            if fields:
                conflicts.append(dict(source_sha256=a['source_sha256'],start_s=start,end_s=end,
                    fields=fields,observations=[{k:s[k] for k in
                        ('window_id','slot_id','visible','speaking','clear_face','action')} for s in (a,b)]))
    return conflicts


def exclude_conflicts(shots, conflicts):
    result=[]
    for shot in shots:
        spans=[(shot['source_start_s'],shot['source_end_s'])]
        for conflict in conflicts:
            if conflict['source_sha256']!=shot['source_sha256']:
                continue
            a,b=conflict['start_s'],conflict['end_s']
            spans=[piece for x,y in spans for piece in ((x,min(y,a)),(max(x,b),y))
                   if piece[1]-piece[0]>=.3]
        for a,b in spans:
            item=deepcopy(shot)
            if (a,b)!=(shot['source_start_s'],shot['source_end_s']):
                item['slot_id']='shot_'+json_sha([shot['slot_id'],a,b])[:12]
                item['events']=[dict(e,event_id=item['slot_id']+'_event_'+str(i+1))
                                for i,e in enumerate(shot['events'])]
            item.update(source_start_s=a,source_end_s=b)
            result.append(item)
    return result


class VisualIdentityRegistry:
    """Compare actual crops, including duplicates within one observation window.

    Library entity IDs are provenance, not proof of identity. A verified creative
    identity may cover several library IDs without merging the ongoing build.
    """
    def __init__(self, root, call):
        self.root=Path(root)/'identities'
        self.root.mkdir(parents=True,exist_ok=True)
        self.call=call
        self.references={}
        self.aliases={}
        self.number=0
        self.verify_matches=False
        self.pair_number=0

    def same_person(self, left, right):
        """Confirm a proposed merge with only the two actual pictures visible."""
        from PIL import Image, ImageDraw, ImageOps
        from .material_confirm import _label_font
        self.pair_number+=1
        board=Image.new('RGB',(768,416),'#202020');draw=ImageDraw.Draw(board)
        for i,row in enumerate([left,right]):
            if sha256_file(row['path'])!=row['sha256']:raise LibraryStopped('creative_identity_reference_changed')
            with Image.open(row['path']) as original:
                picture=ImageOps.contain(original.convert('RGB'),(368,368))
            draw.text((i*384+12,3),'A' if i==0 else 'B',fill='white',font=_label_font(28))
            board.paste(picture,(i*384+(384-picture.width)//2,40+(368-picture.height)//2))
        media=self.root/f'pair_{self.pair_number}.jpg';board.save(media,quality=94)
        prompt=('只比较图A与图B是否为同一个动画人物，没有提供前一次判断或故事角色。'
            '先分别描述两图实际外观，再判断。比较头部/耳朵形状、固定花纹、稳定配色和身体比例；'
            '表情、汗滴、泪水、嘴部状态、手持物变化不代表换人。相近物种、同色衣服或同款盔甲本身不能证明同一人。'
            '明确是不同人物用false；被遮挡或外观依据不足用null；只有两图有充分一致身份依据才用true。'
            '每张图可能附带旁人，仅比较target_hint指定的候选主体，其他人不是本次比较对象。'
            '提示是待核实假设；目标在图中不清楚或不存在时用null，不以文字当作图像证据。'
            '不要因为任务是在比较就预设相同。只返回JSON {"appearance_a":"A实际外观",'
            '"appearance_b":"B实际外观","same_person":true,"evidence":"具体相同或不同的依据"}。')
        prompt += '\n'+str(dict(A_target_hint=left.get('target_hint',left.get('appearance')),
                                B_target_hint=right.get('target_hint',right.get('appearance'))))
        def valid(value):
            if (not isinstance(value,dict) or 'same_person' not in value or
                    value['same_person'] is not None and type(value['same_person']) is not bool or
                    any(not isinstance(value.get(k),str) or not value[k].strip()
                        for k in ('appearance_a','appearance_b','evidence'))):
                raise ValueError('Give both observed appearances and an explicit same_person boolean or null')
            return value
        answer=self.call(f'material_scene_discovery_identitypair_{self.pair_number}',prompt,valid,media,image=True)
        write_json(self.root/f'pair_{self.pair_number}.json',dict(left=left,right=right,
            media_sha256=sha256_file(media),answer=answer))
        return answer

    def distinguishable(self, left, right):
        """Two performers cast as different story roles must look different to a viewer.

        17 (6c184902893f): the boss (registered from a back view) and his henchman (front
        view) wore the same pink armour and beaded headband. They were never compared, and
        the identity question allows 'same armour is not proof of the same person'. Casting
        asks the audience question instead: can they be told apart at a glance?"""
        from PIL import Image, ImageDraw, ImageOps
        from .material_confirm import _label_font
        key=json_sha(sorted([left['sha256'],right['sha256']]))[:12]
        board=Image.new('RGB',(768,416),'#202020');draw=ImageDraw.Draw(board)
        for i,row in enumerate([left,right]):
            if sha256_file(row['path'])!=row['sha256']:raise LibraryStopped('creative_identity_reference_changed')
            with Image.open(row['path']) as original:
                picture=ImageOps.contain(original.convert('RGB'),(368,368))
            draw.text((i*384+12,3),'A' if i==0 else 'B',fill='white',font=_label_font(28))
            board.paste(picture,(i*384+(384-picture.width)//2,40+(368-picture.height)//2))
        media=self.root/f'distinct_{key}.jpg';board.save(media,quality=94)
        prompt=('图A和图B的目标人物将在同一部短片里扮演两个不同的故事角色。'
            '只判断观众能否仅凭外观一眼分清他们：比较体型、配色、服饰、头饰和标志特征。'
            '同款盔甲、同色身体、同样头饰而只差角度或站姿，观众会以为是同一人，填false；'
            '一张只见背影或被遮挡、无法确认两人外观不同，也填false。只有明显不同的人物才填true。'
            '只比较target_hint指定的主体，旁人不算。只返回JSON {"appearance_a":"A实际外观",'
            '"appearance_b":"B实际外观","distinguishable":true,"evidence":"区分或混淆的具体依据"}。')
        prompt += '\n'+str(dict(A_target_hint=left.get('appearance'),B_target_hint=right.get('appearance')))
        def valid(value):
            if (not isinstance(value,dict) or type(value.get('distinguishable')) is not bool or
                    any(not isinstance(value.get(k),str) or not value[k].strip()
                        for k in ('appearance_a','appearance_b','evidence'))):
                raise ValueError('Give both observed appearances and a distinguishable boolean')
            return value
        answer=self.call(f'material_scene_discovery_castdistinct_{key}',prompt,valid,media,image=True)
        write_json(self.root/f'distinct_{key}.json',dict(left=left,right=right,media_sha256=sha256_file(media),answer=answer))
        return answer

    def resolve(self, actor):
        seen=set()
        while actor in self.aliases:
            if actor in seen:raise ValueError('cyclic visual identity alias')
            seen.add(actor);actor=self.aliases[actor]
        return actor

    def bind(self, window, protected=()):
        from PIL import Image, ImageDraw, ImageOps
        from .material_confirm import _label_font
        current=[]
        for role in window['observation']['roles']:
            candidates=[e for e in window.get('entity_exemplars',[]) if e['role_id']==role['role_id']]
            if not candidates:
                continue
            exemplar=candidates[0]
            path=Path(exemplar['path'])
            if not path.is_file() or sha256_file(path)!=exemplar['sha256']:
                raise LibraryStopped('creative_identity_exemplar_changed')
            current.append(dict(key='C'+str(len(current)+1),path=str(path),sha256=exemplar['sha256'],
                role_id=role['role_id'],entity_id=role.get('entity_id'),
                target_hint=role.get('appearance') or role.get('visual_description'),
                entity_kind=role.get('entity_kind','individual'),source_time_s=exemplar['source_time_s']))
        if not current:return {}
        refs=[dict(v,key='R'+str(i+1),actor_id=a) for i,(a,v) in enumerate(self.references.items())]
        nodes={r['key']:r for r in current+refs}
        parents={k:k for k in nodes}
        descriptions={}
        usable=set()
        def find(k):
            while parents[k]!=k:k=parents[k]
            return k
        # Bound each visual request, without dropping older known actors.
        for offset in range(0,len(current),8):
            part=current[offset:offset+8]
            for r_offset in range(0,max(1,len(refs)),16):
                tiles=part+refs[r_offset:r_offset+16]
                self.number+=1
                columns=4;rows=(len(tiles)+columns-1)//columns
                board=Image.new('RGB',(columns*320,rows*368),'#202020')
                draw=ImageDraw.Draw(board)
                for i,tile in enumerate(tiles):
                    path=Path(tile['path'])
                    if sha256_file(path)!=tile['sha256']:raise LibraryStopped('creative_identity_reference_changed')
                    with Image.open(path) as original:
                        im=ImageOps.contain(original.convert('RGB'),(304,312))
                    x,y=(i%columns)*320,(i//columns)*368
                    draw.text((x+12,y+3),tile['key'],fill='white',font=_label_font(26))
                    board.paste(im,(x+(320-im.width)//2,y+48+(312-im.height)//2))
                path=self.root/f'gallery_{self.number}.jpg'
                board.save(path,quality=88)
                keys={t['key'] for t in tiles};current_keys={t['key'] for t in part}
                prompt=('只按实际图片登记当前可辨认的人物，不知道剧本或角色职业。C是当前窗口候选裁图，R是此前见过的人物图。'
                    '这不是只在R中找匹配的任务：清楚的新人物必须登记，未见过不等于看不清。'
                    '先区分两个判断：当前图能否定位一个可见的动画人物实例；不同图是否足以确认同一个人。'
                    '登记当前人物不要求看见真实面孔、皮肤或知道官方身份；面具、头盔、盔甲或机器人外壳可构成该动画人物的可见外形。'
                    '目标主体在当前图中清楚可定位、可与同图其他人区分时，先作为本图独立人物进入groups。'
                    '跨图只有相同制服或外壳、无法证明同一人时，不与R合组；不能因此把当前清楚可见的独立人物放进unresolved。'
                    '先独立判断每个C的外观是否清楚。清楚的C必须进入groups；再比较是否能与其他C或R认定为同一人。'
                    '找不到相同R时，以该C单独成组，例如{"tiles":["C1"],"primary_tile":"C1",'
                    '"appearance":"实际可见外观","evidence":"清楚的新人物，与已知人物不同"}。'
                    'R仅用于复用已有身份，没有匹配的R直接忽略，不能放入unresolved。'
                    '每个C仅登记current_targets所指的一个候选主体，图中其他同伴、背景人物和商品不参与该C的身份分组。'
                    'current_targets来自此前视频观察，是待核实假设；必须在图中看到目标，不存在或看不清才unresolved。'
                    '两张C都附带同一个旁人，不代表两个C的目标主体相同；逐张核对所指主体后再比较。'
                    '每个C必须恰好归入一个group或unresolved。只有外观清楚、能认定同一人物的图才合组；'
                    '颜色相近、同款制服/盔甲、同种群体不证明同一人；背影、裁错位置、遮挡严重或无法区分则unresolved。'
                    '同一个人可能在当前窗口被重复编号，请合为一个group；能与R确认相同时把对应R也放入同组。'
                    '判断稳定身份，忽略表情、汗滴、眼泪、嘴部状态、手持食物和临时道具；这些变化不代表换了一个人。'
                    '重点比较头部和耳朵形状、固定花纹、身体比例和稳定配色。'
                    '若evidence认为C与某个R是同一个人物，tiles必须实际列出那个R，不能只在解释中写相同却把C单独成组。'
                    'primary_tile选本组C中人物最清楚、最易辨认的一张，优先正面完整外观，不能选R。'
                    '不认识名字没有关系，只写实际颜色、形状和独特特征；不根据文字描述猜身份。'
                    '每组必须含C；没有匹配R的新人物也单独成组。evidence写具体可见身份依据。'
                    'unresolved只能表示图像本身无法辨认人物：reason_code为not_person、occluded或ambiguous_appearance，'
                    'reason必须说明具体视觉缺陷；没有匹配R、不认识名字、没有官方身份、看不到造型内部的脸均不是视觉缺陷。'
                    '若current_targets所指目标根本不在裁图中，必须unresolved并说明目标与实际裁图不符；不能把另一个人或商品登记为目标。'
                    '只返回JSON {"groups":[{"tiles":["C1"],"primary_tile":"C1","appearance":"实际外观",'
                    '"evidence":"新人物或相同身份的可见依据"}],"unresolved":[{"tile":"C2",'
                    '"reason_code":"occluded","reason":"人物被遮挡的具体位置"}]}。'
                    '\n'+str(dict(current=sorted(current_keys),references=sorted(keys-current_keys),
                                  current_targets={r['key']:r.get('target_hint') for r in part})))
                def validate(value):
                    if not isinstance(value,dict) or not isinstance(value.get('groups'),list) or not isinstance(value.get('unresolved'),list):
                        raise ValueError('identity groups and unresolved arrays required')
                    covered=[];used_refs=[]
                    for group in value['groups']:
                        ids=group.get('tiles',[])
                        if (not ids or len(set(ids))!=len(ids) or not set(ids)<=keys or not set(ids)&current_keys
                                or group.get('primary_tile') not in set(ids)&current_keys
                                or not group.get('appearance') or not group.get('evidence')):
                            raise ValueError('identity group needs known tiles and visible evidence')
                        covered.extend(k for k in ids if k in current_keys)
                        used_refs.extend(k for k in ids if k not in current_keys)
                    for row in value['unresolved']:
                        if row.get('tile') not in current_keys or not row.get('reason'):raise ValueError('invalid unresolved crop')
                        if row.get('reason_code') not in {'not_person','occluded','ambiguous_appearance'}:
                            raise ValueError('unresolved requires a visual defect reason_code; a clear new person without a matching R belongs in a standalone group')
                        covered.append(row['tile'])
                    if sorted(covered)!=sorted(current_keys) or len(used_refs)!=len(set(used_refs)):
                        raise ValueError('each current crop must be resolved exactly once; one reference cannot denote two people')
                    return value
                answer=self.call(f'material_scene_discovery_entities_{self.number}',prompt,validate,path,image=True)
                write_json(self.root/f'assessment_{self.number}.json',dict(window_id=window['window_id'],
                    tiles=tiles,media_sha256=sha256_file(path),answer=answer))
                for group in answer['groups']:
                    ids=group['tiles'];primary=group['primary_tile'];anchor=find(primary)
                    for key in ids:
                        if key in current_keys:
                            usable.add(key);descriptions[key]=group
                    for key in ids:
                        if key==primary:continue
                        comparison=self.same_person(nodes[primary],nodes[key]) if self.verify_matches else None
                        if comparison is None or comparison['same_person'] is True:
                            parents[find(key)]=anchor
                        elif key in current_keys:
                            descriptions[key]=dict(group,primary_tile=key,
                                appearance=comparison['appearance_b'],evidence=comparison['evidence'])
        components={}
        for row in current:
            if row['key'] in usable:components.setdefault(find(row['key']),[]).append(row)
        result={}
        protected={self.resolve(a) for a in protected}
        for component,members in components.items():
            matches=[r for r in refs if find(r['key'])==component]
            known=list(dict.fromkeys(self.resolve(r['actor_id']) for r in matches))
            bound=set(known)&protected
            if len(bound)>1:raise LibraryStopped('creative_identity_merges_two_locked_story_roles')
            aid=next(iter(bound)) if bound else known[0] if known else 'actor_'+json_sha(
                [window['source_sha256'],window['window_id'],members[0]['role_id']])[:12]
            for old in known:
                if old!=aid:self.aliases[old]=aid;self.references.pop(old,None)
            preferred=descriptions[members[0]['key']]['primary_tile']
            primary=next((r for r in members if r['key']==preferred),members[0])
            description=descriptions[primary['key']]
            self.references.setdefault(aid,dict(path=primary['path'],sha256=primary['sha256'],
                appearance=description['appearance'],window_id=window['window_id'],role_id=primary['role_id']))
            result[aid]=dict(actor_id=aid,role_id=primary['role_id'],appearance=description['appearance'],
                evidence=description['evidence'],entity_kind='group' if any(r['entity_kind']!='individual' for r in members) else 'individual',
                local_role_ids=[r['role_id'] for r in members],entity_ids=[r['entity_id'] for r in members])
        write_json(self.root/'registry.json',dict(references=self.references,aliases=self.aliases))
        return result
