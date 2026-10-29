#!/usr/bin/env python3
"""Question-first material search: cached shots, verified candidates, no POST replay."""
import base64
import json
import math
from pathlib import Path
import subprocess
import unicodedata
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor

from copy import deepcopy
import os

from .state import LibraryStopped, json_sha, write_json
from .media import sha256_file

ENDPOINT = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
FILMS = [(1, "功夫熊猫2008", 5529.5, (34, 4991)),
         (2, "功夫熊猫2", 5423.9, (21, 4869)), (3, "功夫熊猫3", 5694.7, (66, 4999))]
RANK = {"high": 2, "medium": 1, "low": 0}
STYLES = {"3d": 0, "mixed": 1, "2d_stylized": 2}
QUESTIONS = """你是断章取义剪辑的素材研究员。根据以下真实参考阅读拆成可检索的画面问题。
原片的意义、善恶、声音和台词内容都不重要；素材会用 TTS 重新叙述（断章取义）。
只有画面必须满足 must_see；不能把动机、主题、招式名字等不可见语义当作画面硬条件。
must_see 必须按功能描述可见的通用条件：谁的角色、哪类动作、哪类结果。
例如 comeuppance：先前逞强的同一反派明显落败或受辱（跌倒、被击倒、倒地受伤、惊慌逃跑等任一种）。
不要把参考中的瘫坐地上、捂着腿、废品堆等具体姿势、部位、道具或场景写进 must_see；
这些具体参考细节只放进 equivalents，作为可替代的例子，不能变成所有影片都须满足的限制。
仅输出 JSON：{"beats":[{"id":"...","function":"对观众要实现什么作用",
"must_see":{"who_role":"抽象角色","action":"动作","object":"对象或无",
"result":"可见结果","shot_size":"景别","mouth_open_needed":true},
"equivalents":["相同功能的替代画面，3至6种"],"dub_can_carry":["TTS可供给的动机、主题、台词"],
"roles":["A_boastful_villain"]}],"casting":{"A_boastful_villain":[
{"name":"统一的规范角色名","aliases":["别名或译名1","别名或译名2"],
"films":[1],"rough_part":"大概在哪一段出现"}]}}。
aliases 中每个名字单独一项，例如 ["太郎","大龙","残豹","Tai Lung"]，不要把多个名字合成一串。
节拍已由 visible_evidence 逐项固定为 b1..bN，必须按给定顺序逐字复制 id。
只填写 must_see、equivalents、dub_can_carry、roles；不得增加、删除、合并节拍或改写 function。
不要把替代画面列为必须同时满足的条件。选角仅凭你对三部影片的知识，属于线索。
参考阅读：\n"""
SKIM = """带着下面全部画面问题扫这张6x6接触表。每格左上角数字是全片索引 n，不是格子位置。n 用格子左上角印的编号。
小图模糊，角色身份只是线索，可以写其他/不确定；优先召回，宁可多报候选，不能把原剧情含义当硬条件。
画面将用TTS断章取义重配。只返回可能满足任一问题的格子，空 beats 的不用返回。
仅输出 JSON：{"tiles":[{"n":0,"beats":["问题id"],"confidence":"high|medium|low",
"who":["角色名或其他/不确定"],"fact":"一句中性可见事实"}]}。
本表实际有效索引："""
CLOSE = """仔细看无声视频，核验主节拍，并检查全部问题中其他可见地成立的节拍。
原片意义和声音不重要，将用TTS断章取义重配。
是否可见地满足 must_see 或某一种 equivalents？替代项已编号 E1..En，选择时返回 equivalent_id；无需逐字复述。
检查画面中执行动作的该角色嘴部是否确实在动，
不能把另一角色说话、字幕或静态张嘴当成该角色动作时的口型。身份不确定请写不确定。
结果节拍须说明哪个角色受挫，以便与逞强者核对。不要凭电影知识补出未出现在视频里的结果。
who[0] 必须是执行该动作/承受该结果的角色，用 casting 中的规范角色名统一别名；不可用抽象角色 id 代替人物名。
如提供 anchor_name，主节拍要求该角色与附带锚点静帧是同一个角色，而不只是同类角色；
返回 same_character_as_anchor=true/false，无法确认按 false。无锚点时为 null。
also_satisfies 只列视频中已经逐项核实、全部可见要求成立的其他节拍 id；不要列仅有部分证据的节拍。
即使主节拍为 no，也要报告满足其他节拍的画面，给出对应时间、角色和证据。
所有列出的节拍须在同一 in_s/out_s 内成立；涉及喊话的节拍必须核实该角色动作时嘴部在动。
区分三维动画与平面风格：style 为 3d、2d_stylized 或 mixed；开场二维梦境不可当作三维素材。
仅输出 JSON：{"satisfies":"must_see|equivalent|no","equivalent_id":null,
"in_s":0.0,"out_s":1.0,"who":["角色名"],"mouth_moving":true,
"same_character_as_anchor":null,"also_satisfies":["其他已核实的节拍id"],
"style":"3d|2d_stylized|mixed","confidence":"high|medium|low",
"tts_needed":["必须由TTS提供的内容"],"verdict":"usable|partial|no","reason":"可见证据和不足"}。
in_s/out_s 必须是相对于本视频开头的秒数，且位于视频内；no 且 also_satisfies 为空时才可为 null。
只有全部可见要求成立才判 usable；仅有部分证据判 partial。问题：\n"""


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def parse_json_object(text):
    """解析响应；围栏、前言和尾随说明不应导致整次调用丢失，也不重发。"""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for index, char in enumerate(text):
            if char != "{":
                continue
            try:
                data, _ = decoder.raw_decode(text, index)
                break
            except json.JSONDecodeError:
                continue
        else:
            raise ValueError("响应中没有完整JSON对象") from None
    if not isinstance(data, dict):
        raise ValueError("响应不是JSON对象")
    return data


def normalise(text):
    return "".join(c for c in unicodedata.normalize("NFKC", text).casefold()
                   if not c.isspace() and not unicodedata.category(c).startswith("P"))


def beat_anchors(beat, cast_anchors):
    return {role: cast_anchors[role] for role in beat["roles"] if role in cast_anchors}


def close_prompt(beat, questions, anchors, window):
    def numbered(b):
        return dict(b, equivalents={f"E{i}": text for i, text in enumerate(b["equivalents"], 1)})

    identities = [dict(role=role, anchor_name=a["name"], aliases=a["aliases"])
                  for role, a in anchors.items()]
    return CLOSE + json.dumps(dict(main_beat=numbered(beat),
        other_beats=[numbered(b) for b in questions["beats"] if b["id"] != beat["id"]],
        casting=questions["casting"], required_anchors=identities), ensure_ascii=False) + (
            f"\n视频长度：{window[1] - window[0]:.3f}s")


def media_command(args, threads=1):
    return subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-threads", str(threads),
                           "-filter_threads", str(threads), *map(str, args)], check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


def match_duration(duration):
    for number, title, expected, bounds in FILMS:
        if abs(duration - expected) <= 1:
            return {"film": number, "title": title, "duration": duration, "bounds": list(bounds)}
    raise ValueError(f"影片时长 {duration:.3f}s 不匹配三部影片（容差±1s），拒绝猜测内容区间")


def detect_cuts(frames, threshold=25):
    """返回镜头起始帧下标，含第0帧；转有符号数以免 uint8 相减溢出。"""
    import numpy as np

    if not len(frames):
        return []
    delta = np.abs(np.diff(np.asarray(frames, dtype=np.int16), axis=0))
    differences = delta.reshape(len(frames) - 1, -1).mean(axis=1) if len(frames) > 1 else []
    return [0, *(np.flatnonzero(np.asarray(differences) > threshold) + 1).tolist()]


def prepare_film(info, cache_dir):
    import numpy as np

    # 镜头切点、关键帧、宫格只取决于影片本身，与题目无关；放在共享缓存里供以后所有参考复用。
    folder = cache_dir / f"film_{info['film']}"
    folder.mkdir(parents=True, exist_ok=True)
    stat = Path(info["path"]).stat()
    signature = ["qfs_shots_v1", info, stat.st_size, stat.st_mtime_ns]
    cache = folder / "shots.json"
    if cache.exists():
        saved = read_json(cache)
        if saved["signature"] == signature and all(Path(x).exists() for x in
                [f["keyframe"] for f in saved["frames"]] + [s["path"] for s in saved["sheets"]]):
            return saved
    lo, hi = info["bounds"]
    raw = media_command(["-ss", lo, "-i", info["path"], "-t", hi - lo, "-map", "0:0",
                         "-vf", "fps=2,scale=32:18,format=gray", "-f", "rawvideo", "pipe:1"], threads=4)
    sampled = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 18, 32)
    starts = detect_cuts(sampled)
    if not starts:
        raise ValueError("内容区间未解码出任何帧")
    frames, indices = [], []
    for n, (a, b) in enumerate(zip(starts, starts[1:] + [len(sampled)])):
        index = min(a + 1, b - 1)  # 短于1秒的镜头使用镜头内现有采样点。
        indices.append(index)
        frames.append(dict(n=n, film=info["film"], timestamp=lo + index / 2,
                           shot_start=lo + a / 2, shot_end=min(hi, lo + b / 2),
                           keyframe=str(folder / f"frame_{n:06d}.jpg")))
    # 一次顺序解码抽完关键帧，避免几千次随机启动解码器；n 为全片索引。
    select = "+".join(f"eq(n,{i})" for i in indices)
    filters = (f"fps=2,select='{select}',scale=320:-2,"
               "drawtext=text='%{n}':x=5:y=5:fontsize=20:fontcolor=white:box=1:boxcolor=black")
    media_command(["-ss", lo, "-i", info["path"], "-t", hi - lo, "-map", "0:0", "-vf", filters,
                   "-vsync", "vfr", "-q:v", "3", "-start_number", "0", folder / "frame_%06d.jpg"], threads=4)
    sheets = []
    for offset in range(0, len(frames), 36):
        tiles = [dict(frame, position=i) for i, frame in enumerate(frames[offset:offset + 36])]
        path = folder / f"sheet_{offset // 36:04d}.jpg"
        media_command(["-start_number", offset, "-i", folder / "frame_%06d.jpg", "-vf",
                       f"tile=6x6:padding=4:color=white:nb_frames={len(tiles)}", "-frames:v", 1, path])
        sheets.append(dict(id=f"film{info['film']}_sheet{offset // 36}", path=str(path), tiles=tiles))
    saved = dict(signature=signature, info=info, decoded_frames=len(sampled), frames=frames, sheets=sheets)
    write_json(cache, saved)
    return saved


def select_regions(beats, sheets, skim, cast_anchors=None):
    selected = {}
    for beat in beats:
        names = {normalise(name) for a in beat_anchors(beat, cast_anchors or {}).values()
                 for name in [a["name"], *a["aliases"]]}
        hits = []
        for sheet in sheets:
            mapping = {t["n"]: t for t in sheet["tiles"]}
            for tile in skim.get(sheet["id"], {}).get("tiles", []):
                if beat["id"] in tile["beats"] and tile["n"] in mapping:
                    hits.append(dict(mapping[tile["n"]], confidence=tile["confidence"],
                                     anchor_match=any(normalise(n) in names for n in tile["who"])))
        regions = []
        for hit in sorted(hits, key=lambda h: (h["film"], h["timestamp"])):
            if regions and regions[-1]["film"] == hit["film"] and hit["timestamp"] - regions[-1]["last"] <= 8:
                region = regions[-1]
                region["end_s"] = max(region["end_s"], hit["shot_end"])
                region["last"] = hit["timestamp"]
                if (hit["anchor_match"], RANK[hit["confidence"]]) > (region["anchor_match"], RANK[region["confidence"]]):
                    region.update(confidence=hit["confidence"], anchor=hit["timestamp"],
                                  anchor_match=hit["anchor_match"])
            else:
                regions.append(dict(film=hit["film"], start_s=hit["shot_start"], end_s=hit["shot_end"],
                                    last=hit["timestamp"], anchor=hit["timestamp"], confidence=hit["confidence"],
                                    anchor_match=hit["anchor_match"]))
        rank = lambda r: (not r["anchor_match"], -RANK[r["confidence"]])
        ranked = sorted(regions, key=rank)
        preferred = [r for r in ranked if r["anchor_match"]][:6]
        remaining = [r for r in ranked if r not in preferred]
        slots = 6 - len(preferred)
        explore = [r for r in remaining if r["confidence"] != "high"][:min(2, slots)]
        top = [r for r in remaining if r not in explore][:slots - len(explore)]
        selected[beat["id"]] = [dict(r, exploration=r in explore or r["confidence"] != "high")
                                 for r in sorted(preferred + top + explore, key=rank)]
    return selected


def clip_window(region, bounds):
    start, end = max(bounds[0], region["start_s"] - 4), min(bounds[1], region["end_s"] + 4)
    if end - start > 20:
        start = max(start, min(region["anchor"] - 10, end - 20))
        end = start + 20
    if end <= start:
        raise ValueError("候选区间不在影片内容范围")
    return start, end


def film_times(start, end, in_s, out_s):
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
           for v in (in_s, out_s)) or not 0 <= in_s < out_s <= end - start + 0.05:
        raise ValueError("无效的视频相对时间")
    return round(start + in_s, 3), round(min(end, start + out_s), 3)


def make_clip(path, info, window):
    start, end = window
    return atomic_media(path, lambda temporary: media_command([
        "-ss", start, "-i", info["path"], "-t", end - start, "-map", "0:0", "-an",
        "-vf", "scale=640:-2,fps=8", "-c:v", "libx264", "-threads", 1, "-crf", 30,
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", temporary]))


def valid_media(path):
    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        return False
    # A single JPEG has no container duration; image2 gives its decoded frame a
    # one-second timeline, so stills and clips both require duration > 0.
    image_options = ['-f', 'image2', '-framerate', '1'] if path.suffix == '.jpg' else []
    try:
        probe = subprocess.run(['ffprobe', '-v', 'error', *image_options,
            '-show_entries', 'format=duration:stream=codec_type', '-of', 'json', str(path)],
            check=True, capture_output=True, timeout=120)
        data = json.loads(probe.stdout)
        duration = float(data['format']['duration'])
        return (math.isfinite(duration) and duration > 0 and
                any(s.get('codec_type') == 'video' for s in data['streams']))
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return False


def atomic_media(path, generate):
    path = Path(path)
    if valid_media(path):
        return path
    temporary = path.with_name(f'{path.stem}.{uuid.uuid4().hex}{path.suffix}')
    try:
        generate(temporary)
        if not valid_media(temporary):
            raise ValueError('question_first:generated_media_not_decodable:' + str(path))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # 3xx 也不能导致自动重发。


def http_post(url, headers, payload):
    request = urllib.request.Request(url, json.dumps(payload).encode(), headers, method="POST")
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=180) as response:
        return json.load(response)


def redact(value, key):
    if isinstance(value, str):
        return value.replace(key, "[REDACTED]") if key else value
    if isinstance(value, list):
        return [redact(v, key) for v in value]
    if isinstance(value, dict):
        return {redact(k, key): redact(v, key) for k, v in value.items()}
    return value


def tiles_by_position(tiles):
    if (not isinstance(tiles, list) or not 1 <= len(tiles) <= 36 or
            any(not isinstance(t, dict) or type(t.get('position')) is not int for t in tiles) or
            {t['position'] for t in tiles} != set(range(len(tiles)))):
        raise ValueError('question_first: tile.position must be unique and cover 0..len(tiles)-1')
    return {t['position']: t for t in tiles}


def validate_skim(data, job):
    assert isinstance(data["tiles"], list)
    allowed = {t["n"] for t in job["sheet"]["tiles"]}
    positions = tiles_by_position(job['sheet']['tiles'])
    seen, valid, dropped, ambiguous = set(), [], [], []
    for index, tile in enumerate(data["tiles"]):
        if isinstance(tile, dict) and type(tile.get('n')) is int:
            tile = dict(tile)
            n = tile['n']
            local = positions[n - 1]['n'] if 1 <= n <= len(positions) else None
            if n in allowed:
                if local is not None and local != n:
                    ambiguous.append(dict(index=index, n=n, local_n=local, selected_n=n))
            elif local is not None:
                tile['n'] = local
        reason = None
        if not isinstance(tile, dict):
            reason = "tile_not_object"
        elif type(tile.get("n")) is not int or tile["n"] not in allowed:
            reason = "invalid_n"
        elif tile["n"] in seen:
            reason = "duplicate_n"
        elif not isinstance(tile.get("beats"), list) or any(
                not isinstance(b, str) or b not in job["beat_ids"] for b in tile["beats"]):
            reason = "invalid_beat_ids"
        elif not tile["beats"]:
            pass  # 无匹配无需置信度、角色或画面描述。
        elif not isinstance(tile.get("confidence"), str) or tile["confidence"] not in RANK:
            reason = "invalid_confidence"
        elif not isinstance(tile.get("who"), list) or not all(isinstance(n, str) for n in tile["who"]):
            reason = "invalid_who"
        elif not isinstance(tile.get("fact"), str):
            reason = "invalid_fact"
        if reason is None:
            try:
                json.dumps(tile, allow_nan=False)
            except ValueError:
                reason = "non_finite_value"
        if reason:
            n = tile.get("n") if isinstance(tile, dict) else None
            dropped.append(dict(index=index, n=n if type(n) is int else None, reason=reason))
        else:
            valid.append(tile)  # 空 beats 是有效的“无匹配”，不会进入候选选择。
            seen.add(tile["n"])
    return dict(tiles=valid, dropped_tiles=dropped, ambiguous_n=ambiguous)


def apply_close_constraints(data, beat, anchors):
    if data["verdict"] == "no":
        return data
    if beat["must_see"]["mouth_open_needed"] and data["mouth_moving"] is not True:
        data.update(verdict="partial", reason="未证实该角色动作时嘴部在动；" + data["reason"])
    if anchors and data.get("same_character_as_anchor") is not True:
        reason = "identity_mismatch" if data.get("same_character_as_anchor") is False else "identity_unverified"
        data.update(verdict="partial", reason=reason + "；" + data["reason"])
    return data


def validate_reply(data, job):
    stage = job["stage"]
    if stage == "questions":
        beats = data["beats"]
        assert isinstance(data["casting"], dict) and isinstance(beats, list) and beats
        ids = [b["id"] for b in beats]
        assert all(isinstance(n, str) and n for n in ids) and len(set(ids)) == len(ids)
        expected = job["beats"]
        if ids != [b["id"] for b in expected]:
            raise ValueError("qfs_questions:beat_ids_must_match_visible_evidence_in_order")
        for b, fixed in zip(beats, expected):
            b["function"] = fixed["function"]
            b["reference_evidence"] = deepcopy(fixed["reference_evidence"])
        for b in beats:
            assert all(k in b for k in ("function", "must_see", "equivalents", "dub_can_carry", "roles"))
            assert isinstance(b["function"], str) and isinstance(b["must_see"], dict)
            assert {"who_role", "action", "object", "result", "shot_size", "mouth_open_needed"} <= b["must_see"].keys()
            assert isinstance(b["must_see"]["mouth_open_needed"], bool) and 3 <= len(b["equivalents"]) <= 6
            assert all(isinstance(b[k], list) and all(isinstance(v, str) for v in b[k])
                       for k in ("equivalents", "dub_can_carry", "roles"))
        for characters in data["casting"].values():
            assert isinstance(characters, list)
            for character in characters:
                assert isinstance(character["name"], str) and character["name"]
                assert isinstance(character.get("aliases", []), list)
                assert all(isinstance(n, str) for n in character.get("aliases", []))
        assert all(role in data["casting"] for b in beats for role in b["roles"])
    elif stage == "skim":
        return validate_skim(data, job)
    else:
        assert data["verdict"] in {"usable", "partial", "no"} and data["satisfies"] in {"must_see", "equivalent", "no"}
        assert isinstance(data["who"], list) and isinstance(data["tts_needed"], list) and isinstance(data["reason"], str)
        assert all(isinstance(v, str) for k in ("who", "tts_needed") for v in data[k])
        assert data["mouth_moving"] is None or type(data["mouth_moving"]) is bool
        assert data["style"] in STYLES and data["confidence"] in RANK
        assert data.get("same_character_as_anchor") is None or type(data["same_character_as_anchor"]) is bool
        if job.get("anchors"):
            assert type(data.get("same_character_as_anchor")) is bool
        assert isinstance(data["also_satisfies"], list)
        other_ids = set(job.get("beat_ids", [])) - {job["beat"]["id"]}
        assert all(isinstance(b, str) and b in other_ids for b in data["also_satisfies"])
        data["also_satisfies"] = list(dict.fromkeys(data["also_satisfies"]))
        if data["verdict"] != "no" or data["also_satisfies"]:
            data["film_in_s"], data["film_out_s"] = film_times(*job["window"], data["in_s"], data["out_s"])
            if data["verdict"] == "usable":
                assert data["satisfies"] != "no"
            if data["satisfies"] == "equivalent":
                equivalents = {f"E{i}": text for i, text in enumerate(job["beat"]["equivalents"], 1)}
                equivalent_id = data.get("equivalent_id")
                if equivalent_id is not None:
                    assert equivalent_id in equivalents
                else:
                    text = normalise(data.get("equivalent") or "")
                    equivalent_id = next((eid for eid, value in equivalents.items()
                                          if normalise(value) and normalise(value) in text), None)
                if equivalent_id:
                    data.update(equivalent_id=equivalent_id, equivalent=equivalents[equivalent_id])
                else:
                    data["satisfies_unmatched"] = True
            apply_close_constraints(data, job["beat"], job.get("anchors", {}))
    return data


def update_cast_anchors(beat, candidates, casting, cast_anchors):
    usable = [c for c in candidates if c["beat_id"] == beat["id"] and c["verdict"] == "usable"
              and c.get("still") and c["who"] and normalise(c["who"][0])
              and c["who"][0] not in {"其他", "不确定", "其他/不确定", *beat["roles"]}]
    if not usable:
        return
    for role in beat["roles"]:
        if role in cast_anchors:
            continue  # 锚点一旦建立，后续节拍必须服从它，不能悄悄换角。
        hints = {normalise(n) for c in casting.get(role, []) for n in [c['name'], *c.get('aliases', [])]}
        eligible = [c for c in usable if normalise(c['who'][0]) in hints]
        if not eligible:
            # The primary actor may differ from a knowledge-based casting hint.
            # Do not assign that actor to every secondary role in the beat.
            if len(beat['roles']) != 1:
                continue
            eligible = usable
        best = min(eligible, key=lambda c: (-RANK[c["confidence"]], STYLES[c["style"]]))
        name, aliases = best["who"][0], []
        for character in casting.get(role, []):
            names = [character["name"], *character.get("aliases", [])]
            if normalise(name) in {normalise(n) for n in names}:
                aliases.extend(names)
                name = character['name']
        cast_anchors[role] = dict(name=name, aliases=list(dict.fromkeys([name, *aliases])),
            still=best["still"], film=best["film"], beat_id=beat["id"], clip=best["clip"],
            confidence=best["confidence"])


def route_candidates(result, job, beats, cast_anchors):
    routed = []
    for beat in beats:
        if beat["id"] not in result["also_satisfies"]:
            continue
        candidate = dict(result, beat_id=beat["id"], routed_from=job["beat"]["id"],
                         source_verdict=result["verdict"], satisfies="must_see", verdict="usable",
                         equivalent_id=None, equivalent=None,
                         reason="交叉核验满足该节拍；" + result["reason"])
        candidate.pop("satisfies_unmatched", None)
        anchors = beat_anchors(beat, cast_anchors)
        if anchors:
            checked = job.get("anchors", {})
            if all(a["clip"] == result["clip"] for a in anchors.values()):
                candidate["same_character_as_anchor"] = True  # 候选本身就是锚点。
            elif not all(role in checked and checked[role]["clip"] == a["clip"]
                         for role, a in anchors.items()):
                candidate["same_character_as_anchor"] = None
        apply_close_constraints(candidate, beat, anchors)
        routed.append(candidate)
    return routed



def reference_beats(reference):
    """One stable beat per received piece of visible evidence, in original order."""
    evidence = reference.get('visible_evidence')
    if not isinstance(evidence, list) or not evidence:
        raise ValueError('qfs_questions:visible_evidence_required')
    return [dict(id=f'b{i}', function=row['supports'], reference_evidence=deepcopy(row))
            for i, row in enumerate(evidence, 1)]


def validate_shot_cache(saved, path):
    def require(condition, field):
        if not condition:
            raise ValueError(f'question_first: malformed shot cache {path}: {field}')

    def number(value):
        return type(value) in (int, float) and math.isfinite(value)

    require(isinstance(saved, dict), 'root must be an object')
    info = saved.get('info')
    require(isinstance(info, dict), 'info must be an object')
    require(type(info.get('film')) is int and info['film'] > 0, 'info.film must be a positive integer')
    require(isinstance(info.get('title'), str) and bool(info['title']), 'info.title is required')
    require(number(info.get('duration')) and info['duration'] > 0, 'info.duration must be positive and finite')
    for sha in (saved.get('source_sha256'), info.get('source_sha256')):
        require(sha is None or (isinstance(sha, str) and len(sha) == 64 and
                               all(c in '0123456789abcdef' for c in sha)), 'source_sha256 must be a SHA256')
    for field in ('frames', 'sheets'):
        require(isinstance(saved.get(field), list) and bool(saved[field]), f'{field} must be a nonempty array')

    def frame(row, field):
        require(isinstance(row, dict), f'{field} must be an object')
        require(type(row.get('n')) is int and row['n'] >= 0, f'{field}.n must be a nonnegative integer')
        require(type(row.get('film')) is int and row['film'] == info['film'], f'{field}.film mismatch')
        require(all(number(row.get(k)) for k in ('timestamp', 'shot_start', 'shot_end')),
                f'{field} needs finite timestamp, shot_start and shot_end')
        require(0 <= row['shot_start'] <= row['timestamp'] <= row['shot_end'] <= info['duration'] and
                row['shot_start'] < row['shot_end'], f'{field} has invalid shot times')
        require(isinstance(row.get('keyframe'), str) and bool(row['keyframe']), f'{field}.keyframe is required')

    for i, row in enumerate(saved['frames']):
        frame(row, f'frames[{i}]')
    require(len({r['n'] for r in saved['frames']}) == len(saved['frames']), 'duplicate frame n')
    for i, sheet in enumerate(saved['sheets']):
        field = f'sheets[{i}]'
        require(isinstance(sheet, dict), f'{field} must be an object')
        require(isinstance(sheet.get('path'), str) and bool(sheet['path']), f'{field}.path is required')
        tiles_by_position(sheet.get('tiles'))
        for j, row in enumerate(sheet['tiles']):
            frame(row, f'{field}.tiles[{j}]')
        require(len({r['n'] for r in sheet['tiles']}) == len(sheet['tiles']), f'{field} duplicate tile n')


def load_shot_cache(root, catalog):
    """Bind every input source before any paid work; never rebuild a shared cache."""
    root = Path(root).resolve(strict=True)
    caches = []
    for path in sorted(root.glob('film_*/shots.json')):
        saved = read_json(path)
        validate_shot_cache(saved, path)
        saved['_cache_path'] = str(path)
        caches.append(saved)
    films, used = [], set()
    for source in catalog['sources']:
        matches = []
        for saved in caches:
            info = saved['info']
            sha = saved.get('source_sha256') or info.get('source_sha256')
            if sha:
                matches_source = sha == source['sha256']
            else:
                matches_source = abs(float(info['duration']) - source['duration_s']) <= 1
            if matches_source:
                matches.append(saved)
        if len(matches) != 1 or matches[0]['_cache_path'] in used:
            raise ValueError(f"question_first: shot cache mismatch for {Path(source['path']).name}; "
                             'need exactly one film_N/shots.json matching source_sha256 or duration (±1s)')
        saved = deepcopy(matches[0])
        used.add(saved['_cache_path'])
        info = saved['info']
        if (not math.isfinite(float(info['duration'])) or
                abs(float(info['duration']) - source['duration_s']) > 1 or
                Path(saved['_cache_path']).parent.name != f"film_{info['film']}"):
            raise ValueError('question_first: shot cache film/duration mismatch')
        from .content_ranges import content_range_for
        bounds = content_range_for(source) or match_duration(source['duration_s'])['bounds']
        info.update(path=source['path'], duration=source['duration_s'], bounds=bounds,
                    source_id=source['source_id'], source_sha256=source['sha256'])
        if not saved.get('sheets') or not saved.get('frames'):
            raise ValueError('question_first: shot cache has no frames/sheets')
        # Original caches contain absolute paths. Also support a relocated cache
        # with paths relative to film_N or to its root.
        for row, key in [(r, 'keyframe') for r in saved['frames']] + [
                (r, 'path') for r in saved['sheets']]:
            media = Path(row[key])
            if not media.is_absolute():
                local = Path(saved['_cache_path']).parent / media
                media = local if local.is_file() else root / media
            if not media.is_file():
                raise ValueError('question_first: shot cache media missing: ' + str(media))
            row[key] = str(media.resolve())
        films.append(saved)
    if len({f['info']['film'] for f in films}) != len(films):
        raise ValueError('question_first: duplicate film mapping')
    return films


def configure(config, catalog, output):
    """Pin QFS inputs in the task lock, without changing historical library locks."""
    if config.get('material_search', 'library') != 'question_first':
        return None
    if config.get('creative_audio') is not True or not config.get('shot_cache'):
        raise ValueError('question_first requires --creative-audio and --shot-cache')
    if config.get('reuse_plan') or config.get('received_plan_seed'):
        raise ValueError('question_first cannot be combined with --reuse-plan')
    from .content_ranges import recorded_content_ranges, bind_content_ranges
    snapshot = config.get('content_ranges')
    if config.get('visual_library_root'):
        snapshot = recorded_content_ranges(output, config['visual_library_root'], catalog, snapshot)
    if snapshot is None:
        snapshot = dict(schema_version='content_ranges_v1', sources={s['sha256']: dict(
            content_start_s=match_duration(s['duration_s'])['bounds'][0],
            content_end_s=match_duration(s['duration_s'])['bounds'][1],
            verified_by='question_first_kfp_duration_v1', excluded=[]) for s in catalog['sources']})
    config['content_ranges'] = snapshot
    bind_content_ranges(catalog, snapshot)
    films = load_shot_cache(config['shot_cache'], catalog)
    signature = [dict(path=f['_cache_path'], sha256=sha256_file(f['_cache_path']),
                      media=[dict(path=r[key], sha256=sha256_file(r[key]))
                             for rows, key in ((f['frames'], 'keyframe'), (f['sheets'], 'path')) for r in rows])
                 for f in films]
    if config.get('question_first_cache') is not None and config['question_first_cache'] != signature:
        raise LibraryStopped('question_first:recorded_shot_cache_changed')
    config['question_first_cache'] = signature
    return films


class UnknownQFSCall(LibraryStopped):
    """An original free GLM submission is lost; skip it without sending again."""


class DirectTransport:
    """Free QFS GLM quota calls; reserve/record under lock, send outside it."""

    def __init__(self, state, key=None, *, max_parallel=8):
        if type(max_parallel) is not int or max_parallel < 1:
            raise ValueError('question_first:max_parallel_must_be_positive_integer')
        self.state = state
        self.key = key if key is not None else os.environ.get('Z_AI_API_KEY', '')
        self.max_parallel = max_parallel

    def recover_submitted(self):
        # Only called at runner startup, before any workers exist. Preserve the
        # original requests and quota count; a crash never authorizes a resend.
        with self.state._thread_lock:
            self.state._reload()
            for call in list(self.state.data['calls']):
                if call['status'] != 'submitted':
                    continue
                request = read_json(self.state.output / 'calls' / call['id'] / 'request.json')
                if json_sha(request) != call['request_sha256']:
                    raise LibraryStopped('unknown_original_request_modified')
                if self.state._qfs_direct(call['name'], request):
                    self.state.fail_call(call, 'qfs_resume_submitted_without_response', uncertain=True)

    def call(self, name, job):
        content = []
        media = job['prepare']() if 'prepare' in job else job.get('media')
        paths = [a['still'] for a in job.get('anchors', {}).values()]
        if media:
            paths.append(media)
        for path in paths:
            path = Path(path)
            video = path.suffix == '.mp4'
            kind, mime, limit = ('video', 'video/mp4', 8) if video else ('image', 'image/jpeg', 5)
            if path.stat().st_size > limit * 1024 ** 2:
                raise ValueError('question_first:media_limit_exceeded_before_send')
            encoded = base64.b64encode(path.read_bytes()).decode('ascii')
            content.append(dict(type=kind + '_url', **{kind + '_url': dict(url=f'data:{mime};base64,{encoded}')}))
        content.append(dict(type='text', text=redact(job['prompt'], self.key)))
        payload = dict(model='glm-5.3-flash', thinking=dict(type='disabled'), temperature=0.2,
                       max_tokens=8192, messages=[dict(role='user', content=content)])
        request = dict(provider='qfs_direct_https_v1', stage='question_first', endpoint=ENDPOINT, payload=payload)
        digest = json_sha(request)
        # Serialize lookup + reservation too: concurrent duplicate names cannot
        # both send, even if preparation finishes in the same instant.
        with self.state._thread_lock:
            self.state._reload()
            previous = next((c for c in self.state.data['calls'] if c['name'] == name), None)
            if previous:
                # Reuse receipts written by the original serial integration.
                legacy = {k: v for k, v in request.items() if k != 'stage'}
                if previous['request_sha256'] not in {digest, json_sha(legacy)}:
                    raise LibraryStopped('question_first:recorded_request_changed:' + name)
                if previous['status'] != 'received':
                    raise UnknownQFSCall('question_first:request_outcome_unknown_no_replay:' + previous['id'])
                folder = self.state.output / 'calls' / previous['id']
                saved, response = read_json(folder / 'request.json'), read_json(folder / 'response.json')
                if (json_sha(saved) != previous['request_sha256'] or
                        json_sha(response) != previous['response_sha256']):
                    raise LibraryStopped('question_first:recorded_request_or_response_modified:' + name)
            else:
                if not self.key.strip():
                    raise LibraryStopped('question_first:Z_AI_API_KEY_missing')
                call, _ = self.state.begin_call(name, request, qfs_max_parallel=self.max_parallel)
        if previous is None:
            try:
                response = redact(http_post(ENDPOINT, {'Authorization': 'Bearer ' + self.key,
                    'Content-Type': 'application/json', 'X-Title': '4.5V MCP Local'}, payload), self.key)
            except Exception as error:
                # Never persist exception bodies or headers; both may echo credentials.
                self.state.fail_call(call, type(error).__name__, uncertain=True)
                raise UnknownQFSCall('question_first:request_outcome_unknown_no_replay:' + call['id']) from None
            self.state.complete_call(call, response, usage=response.get('usage', {}))
        # Record even malformed received replies before parsing. Reruns revalidate
        # those exact bytes and never issue a repair or a new POST.
        result = validate_reply(parse_json_object(response['choices'][0]['message']['content']), job)
        json.dumps(result, allow_nan=False)
        return result


class QuestionFirstRunner:
    def __init__(self, state, films, *, transport=None, max_parallel=8):
        if type(max_parallel) is not int or max_parallel < 1:
            raise ValueError('question_first:max_parallel_must_be_positive_integer')
        self.state, self.films = state, films
        self.out = state.output / 'question_first'
        self.out.mkdir(exist_ok=True)
        self.max_parallel = max_parallel
        self.transport = transport or DirectTransport(state, max_parallel=max_parallel)
        self.unknown_calls = []

    def _run_jobs(self, jobs):
        def invoke(item):
            name, job = item
            try:
                return self.transport.call(name, job)
            except UnknownQFSCall as error:
                return error

        # map preserves selection order despite out-of-order HTTP completion.
        with ThreadPoolExecutor(max_workers=self.max_parallel) as pool:
            results = list(pool.map(invoke, jobs))
        successful = []
        for (name, job), result in zip(jobs, results):
            if isinstance(result, UnknownQFSCall):
                self.unknown_calls.append(dict(name=name, status='unknown', error=str(result)))
            else:
                successful.append((name, job, result))
        write_json(self.out / 'unknown_calls.json', self.unknown_calls)
        return successful

    def run(self, reference):
        self.unknown_calls = []
        if isinstance(self.transport, DirectTransport):
            self.transport.recover_submitted()
        fixed = reference_beats(reference)
        questions = self.transport.call('qfs_questions', dict(stage='questions', beats=fixed,
            prompt=QUESTIONS + json.dumps(dict(reference=reference, beats=fixed), ensure_ascii=False)))
        write_json(self.out / 'questions.json', questions)
        beats = questions['beats']
        skim, sheets, skim_jobs = {}, [], []
        for film in self.films:
            for index, original in enumerate(film['sheets']):
                sheet = dict(original, id=f"film{film['info']['film']}_sheet{index}")
                sheets.append(sheet)
                skim_jobs.append((f"qfs_skim_{film['info']['film']}_{index}",
                    dict(stage='skim', sheet=sheet, media=sheet['path'], beat_ids=[b['id'] for b in beats],
                         prompt=SKIM + str([t['n'] for t in sheet['tiles']]) + '\n' + json.dumps(
                             [{k: b[k] for k in ('id', 'must_see', 'equivalents')} for b in beats], ensure_ascii=False))))
        for _, job, result in self._run_jobs(skim_jobs):
            skim[job['sheet']['id']] = result
        write_json(self.out / 'skim.json', skim)
        lookup = {f['info']['film']: f['info'] for f in self.films}
        verified, anchors, regions = [], {}, {}
        close_count = 0
        for beat in beats:
            regions.update(select_regions([beat], sheets, skim, anchors))
            required = deepcopy(beat_anchors(beat, anchors))
            batch, close_jobs = [], []
            for region in regions[beat['id']]:
                info = lookup[region['film']]
                if region['end_s'] <= info['bounds'][0] or region['start_s'] >= info['bounds'][1]:
                    continue
                window = clip_window(region, info['bounds'])
                clip = self.out / f'close_{close_count:03d}.mp4'
                name = f'qfs_close_{close_count}'
                close_count += 1
                job = dict(stage='close', beat=beat, anchors=required, window=window, media=clip, info=info,
                           prepare=lambda clip=clip, info=info, window=window: make_clip(clip, info, window),
                           beat_ids=[b['id'] for b in beats], prompt=close_prompt(beat, questions, required, window))
                close_jobs.append((name, job))
            for _, job, reply in self._run_jobs(close_jobs):
                clip, window = job['media'], job['window']
                info = job['info']
                result = dict(reply, beat_id=beat['id'], film=info['film'],
                              source_id=info['source_id'], title=info['title'], clip=str(clip), clip_window=list(window))
                if result['verdict'] != 'no' or result['also_satisfies']:
                    still = clip.with_suffix('.jpg')
                    atomic_media(still, lambda temporary: media_command([
                        '-ss', (result['film_in_s'] + result['film_out_s']) / 2, '-i', info['path'],
                        '-map', '0:0', '-vf', 'scale=640:-2', '-frames:v', 1, temporary]))
                    result['still'] = str(still)
                verified.append(result)
                batch.append((job, result))
            update_cast_anchors(beat, verified, questions['casting'], anchors)
            for job, result in batch:
                verified.extend(route_candidates(result, job, beats, anchors))
        report = dict(beats=[dict(b, candidates=[c for c in verified
            if c['beat_id'] == b['id'] and c['verdict'] in {'usable', 'partial'}]) for b in beats],
            cast_anchors=anchors, skim=skim, regions=regions)
        if self.unknown_calls:
            report['unknown_calls'] = self.unknown_calls
        report['question_first_gaps'] = material_gaps(report)
        write_json(self.out / 'report.json', report)
        return report


def material_gaps(report):
    def needs_visuals(beat):
        return any(v is True or (isinstance(v, str) and normalise(v) not in
            {'', '无', 'none', '不需要', '无需画面'}) for v in beat['must_see'].values())
    return [dict(beat_id=b['id'], must_see=b['must_see'], reason='no_usable_or_partial_visual_candidate')
            for b in report['beats'] if needs_visuals(b) and not b['candidates']]


def candidate_identities(candidate, beat, anchors, source):
    identities = {}
    for role, anchor in beat_anchors(beat, anchors).items():
        if anchor['film'] != candidate['film']:
            continue  # Fine entities are source-namespaced; never merge different films by name.
        token = json_sha([anchor['film'], anchor['still']])[:20]
        entity = source['sha256'] + ':qfs_' + token
        same = candidate.get('same_character_as_anchor')
        if candidate.get('clip') == anchor['clip']:
            same = True
        identities[entity] = dict(entity_id=entity, canonical_name=anchor['name'], aliases=anchor['aliases'],
            anchor_still_path=anchor['still'], same_character_as_anchor=same)
    return list(identities.values())


def to_search_result(report, catalog, *, max_windows, max_window_s=90):
    """Synthetic search_0 only requests observations; it grants no EDL evidence."""
    sources = {s['source_id']: s for s in catalog['sources']}
    from .content_ranges import content_range_for
    from .contracts import validate_search
    ranked = []
    for beat in report['beats']:
        # Partial evidence is only a fallback for a beat with no usable candidate.
        usable = [c for c in beat['candidates'] if c['verdict'] == 'usable']
        for c in usable or [c for c in beat['candidates'] if c['verdict'] == 'partial']:
            source = sources[c['source_id']]
            identities = candidate_identities(c, beat, report['cast_anchors'], source)
            consistent = bool(identities) and all(i['same_character_as_anchor'] is True for i in identities)
            rank = (c['style'] == '2d_stylized', not consistent, STYLES[c['style']],
                    c['verdict'] != 'usable', -RANK[c['confidence']])
            ranked.append((rank, beat, c, identities))
    windows, seen = [], {}
    for _, beat, c, identities in sorted(ranked, key=lambda row: row[0]):
        source = sources[c['source_id']]
        lo, hi = content_range_for(source) or (0, source['duration_s'])
        start, end = max(lo, c['film_in_s'] - 2), min(hi, c['film_out_s'] + 2)
        end = min(end, start + max_window_s)
        if end <= start:
            continue
        key = (c['source_id'], start, end)
        if key in seen:
            request = seen[key]
            request['question'] += '\n' + beat['id'] + ': ' + beat['function']
            request['role_ids'] = list(dict.fromkeys(request['role_ids'] + beat['roles']))
            request['qfs_identity'] = list({i['entity_id']: i for i in identities + request['qfs_identity']}.values())
            continue
        if max_windows is not None and len(windows) >= max_windows:
            continue
        request = dict(source_id=c['source_id'], start_s=start, end_s=end, role_ids=beat['roles'][:],
                       question=beat['id'] + ': ' + beat['function'] + '\n' + json.dumps(beat['must_see'], ensure_ascii=False),
                       qfs_identity=identities)
        seen[key] = request
        windows.append(request)
    search = dict(reason='Question-first candidates; every selected range requires normal continuous observation.',
        windows=windows, slots=[dict(slot_id=b['id'], intended_takeaway=b['function'],
            coverage=dict(visual_goal=json.dumps(b['must_see'], ensure_ascii=False),
                          audio_goal='；'.join(b['dub_can_carry']),
                          gaps=[g['reason'] for g in report['question_first_gaps'] if g['beat_id'] == b['id']]),
            observation_ids=[]) for b in report['beats']], question_first_gaps=report['question_first_gaps'])
    return validate_search(search, catalog, max_windows=max_windows, max_window_s=max_window_s, allow_empty=True)


def observation_context(identities):
    names = ', '.join(i['canonical_name'] for i in identities)
    return dict(qfs_identity=deepcopy(identities), qfs_identity_policy=(
        f'This window was selected because it contains {names}; name roles consistently. '
        '这只是选片线索，必须以本窗口实际画面核验。匹配的role返回qfs_anchor_entity_id（逐字复制entity_id）、'
        'same_character_as_anchor=true/false及具体identity_evidence；不能用电影常识确认。'
        '只有实际画面支持身份且与锚点为同一角色时identity_confirmed=true；不确定或不一致按false。'))


def bind_observed_entities(value, identities):
    """Derive a stable entity only from an explicit agreeing observed role."""
    anchors = {i['entity_id']: i for i in identities}
    matched, rejected_roles = set(), set()
    for role in value.get('roles', []):
        entity = role.get('qfs_anchor_entity_id') or role.get('entity_id')
        if entity not in anchors:
            continue
        agrees = (role.get('identity_confirmed') is True and role.get('same_character_as_anchor') is True
                  and anchors[entity]['same_character_as_anchor'] is True and entity not in matched)
        if agrees:
            role['entity_id'] = entity
            role['identity_status'] = 'qfs_anchor_agreed_by_window_observation'
            matched.add(entity)
        else:
            role['identity_confirmed'] = False
            role.pop('entity_id', None)
            rejected_roles.add(role['role_id'])
    # Retain observed pixels/events using the existing visual-only range rule;
    # an identity disagreement cannot authorize an individual cast binding.
    for usable in value.get('usable_ranges', []):
        if rejected_roles and isinstance(usable, dict) and isinstance(usable.get('role_ids'), list):
            usable['role_ids'] = [r for r in usable['role_ids'] if r not in rejected_roles]
    return value


def recorded_search_result(state, report, catalog, *, max_windows):
    """Pin synthetic search_0 so a lower remaining budget cannot change its resume."""
    from .contracts import validate_search
    state._reload()
    previous = state.data['artifacts'].get('question_first_search_0', [])
    if previous:
        marker = previous[0]
        saved = read_json(marker['path'])
        if json_sha(saved) != marker['sha256'] or saved['report_sha256'] != json_sha(report):
            raise LibraryStopped('question_first:recorded_search_changed')
        return validate_search(saved['search'], catalog, max_windows=saved['max_windows'], allow_empty=True)
    search = to_search_result(report, catalog, max_windows=max_windows)
    state.set_artifact('question_first_search_0', dict(search=search, max_windows=max_windows,
                                                     report_sha256=json_sha(report)))
    return search
