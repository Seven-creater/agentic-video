"""Build reusable, reference-independent visual observations on the server."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import subprocess
import time

from . import contracts
from .media import (create_contact_sheet, inventory_sources, prepare_window,
                    probe_media, sha256_file, verify_source)
from .opencode_provider import OpenCodeMCP, PROVIDER
from .pipeline import _fine_navigation_points, _source_subtitles, _received_note_seed
from .server_cli import VISION_GENERATION, credential, load_history, settings
from .state import LibraryState, LibraryStopped, file_lock, json_sha, write_json
from .visual_library import VisualLibrary


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _received_note_seed_bank(seed, window, bank):
    """Restore only the entities supplied to the SHA-bound original 062 request."""
    marker = _received_note_seed()
    evidence = marker['evidence']
    ledger = Path(seed['ledger_path'])
    saved = _read(ledger)
    if (seed.get('compatibility_policy') != marker['policy'] or
            str(ledger) != evidence['ledger_path'] or seed['call_id'] != evidence['call_id'] or
            sha256_file(ledger) != marker['parent_ledger_file_sha256'] or
            saved['task_id'] != marker['parent_task_id'] or
            any(row['status'] not in {'received', 'failed_known'} for row in saved['calls']) or
            any(window.get(key) != expected for key, expected in marker['window'].items())):
        raise LibraryStopped('received_note_seed:original_ledger_or_window_changed')
    original = next(row for row in saved['calls'] if row['id'] == marker['original_call_id'])
    repair = next(row for row in saved['calls'] if row['id'] == evidence['call_id'])
    if (original['status'] != 'received' or original.get('repair_of') is not None or
            repair['status'] != 'received' or repair.get('repair_of') != original['id'] or
            original['request_sha256'] != marker['original_request_sha256'] or
            original['response_sha256'] != marker['original_response_sha256'] or
            any(repair[key] != evidence[key] for key in ('request_sha256', 'response_sha256'))):
        raise LibraryStopped('received_note_seed:original_repair_pair_changed')
    original_folder = ledger.parent / 'calls' / original['id']
    repair_folder = ledger.parent / 'calls' / repair['id']
    request, reply = _read(original_folder / 'request.json'), _read(original_folder / 'response.json')
    repair_request, repair_reply = _read(repair_folder / 'request.json'), _read(repair_folder / 'response.json')
    scope = request.get('observation_scope', {})
    if (json_sha(request) != marker['original_request_sha256'] or
            json_sha(reply) != marker['original_response_sha256'] or
            json_sha(repair_request) != evidence['request_sha256'] or
            json_sha(repair_reply) != evidence['response_sha256'] or
            request['tool'] != 'analyze_video' or request['media_sha256'] != window['sha256'] or
            request['arguments']['video_source'] != window['path'] or
            scope.get('kind') != 'continuous_window' or any(scope.get(key) != window[key] for key in
                ('source_sha256', 'source_start_s', 'source_end_s')) or
            sha256_file(original_folder / 'protocol_failure.json') != marker['original_failure_file_sha256'] or
            sha256_file(repair_folder / 'protocol_failure.json') != marker['repair_failure_file_sha256'] or
            sha256_file(repair_folder / 'response.json') != marker['repair_response_file_sha256'] or
            sha256_file(Path(window['path']).parent / 'lineage.json') != marker['lineage_file_sha256']):
        raise LibraryStopped('received_note_seed:original_request_reply_or_failure_changed')
    prompt = request['arguments']['prompt']
    context_start = prompt.rfind('\n{"window_id":')
    if context_start < 0:
        raise LibraryStopped('received_note_seed:original_context_missing')
    context, context_end = json.JSONDecoder().raw_decode(prompt[context_start + 1:])
    if (prompt[context_start + 1 + context_end:].strip() or
            json_sha(context) != marker['request_context_sha256']):
        raise LibraryStopped('received_note_seed:original_context_changed')
    ids = [row['entity_id'] for row in context['known_entities']]
    if len(ids) != marker['known_entity_count'] or len(set(ids)) != len(ids):
        raise LibraryStopped('received_note_seed:original_known_entity_set_changed')
    supplied = [row for row in bank if row['entity_id'] in set(ids)]
    if len(supplied) != len(ids) or {row['entity_id'] for row in supplied} != set(ids):
        raise LibraryStopped('received_note_seed:original_known_entities_not_available')
    window['received_protocol_compatibility'] = deepcopy(marker)
    return supplied


def _received_seed(seed, window, bank):
    """Revalidate a known received observation after a local contract correction."""
    ledger = Path(seed["ledger_path"])
    saved = _read(ledger)
    if any(row["status"] in {"submitted", "uncertain"} for row in saved["calls"]):
        raise LibraryStopped("received_seed_predecessor_has_unknown")
    call = next(row for row in saved["calls"] if row["id"] == seed["call_id"])
    folder = ledger.parent / "calls" / call["id"]
    request, reply = _read(folder / "request.json"), _read(folder / "response.json")
    scope = request.get("observation_scope", {})
    if (saved["input_lock"]["configuration"].get("workflow") != "visual_library_v1"
            or call["status"] != "received" or not call["name"].startswith("fine_")
            or json_sha(request) != call["request_sha256"]
            or json_sha(reply) != call["response_sha256"]
            or request["tool"] != "analyze_video" or request["media_sha256"] != window["sha256"]
            or sha256_file(window["path"]) != window["sha256"]
            or scope.get("kind") != "continuous_window"
            or any(scope.get(key) != window[key] for key in
                   ("source_sha256", "source_start_s", "source_end_s"))):
        raise LibraryStopped("received_seed_original_binding_changed")
    value = contracts.parse_model_json("\n".join(row["text"] for row in reply["result"]["content"]
                                               if row.get("type") == "text"))
    if seed.get('compatibility_policy') is not None:
        marker = _received_note_seed()
        if (seed['compatibility_policy'] != marker['policy'] or
                window.get('received_protocol_compatibility') != marker):
            raise LibraryStopped('received_note_seed:explicit_bound_marker_required')
    validate_observation(value, window, bank)
    return value, call, ledger


def observation_prompt(window, bank):
    context = {
        "window_id": window["window_id"], "source_id": window["source_id"],
        "duration_s": window["source_end_s"] - window["source_start_s"],
        "known_entities": [{"entity_id": row["entity_id"], "description":
                           [ref["reference_check"]["visible_appearance"] for ref in row.get("exemplars", [])
                            if ref.get("reference_check", {}).get("approved") is True]}
                           for row in bank],
        "reference_board": window.get("reference_board", []),
    }
    return """你在建立可反复用于不同短视频创作的纯视觉素材库，没有本次故事或参考主旨。
仅依据实际画面，忽略原字幕，不使用电影常识、原对白或熟悉的剧情补身份和动机。
这是正常时间流逝的静音视频代理，可能经过技术采样；不宣称逐帧看清或听见原声。
视频只显示当前片段，CURRENT是本片段的本地时间，不是电影总时间。
known_entities仅为此前实际图片核对通过的外形记录，不表示这些人出现在当前片段。
必须先看当前画面；不能把已有外形记录扩写成当前动作或场景。
所有local时间从当前视频0秒开始。按实际变化记录，不逐秒重复，也不压缩成一条剧情摘要。
人物role_id在窗口内有效；identity_confirmed表示此窗口人物可以视觉区分，不等于知道原名。
每个可分辨的个体独立编号；一群动物或人不能冒充一个稳定人物。无法区分的群体可记录，
但entity_kind=group且identity_confirmed=false，只作环境或集体反应，不用于个体角色对白绑定。
entity_id仅在当前清楚可见的独特外形与known_entities记录明确对应时复用ID；否则null。
必须具体核对面部/身体比例、独特外形或清楚的服装特征；同动物类别、同绘画风格、
同颜色剪影或同职业都不是合并理由。看不清个体的剪影identity_confirmed=false、entity_id=null。
群体参照不能拿来确认群内某个个体。usable_ranges.role_ids只引用identity_confirmed=true的个体；
群体反应和环境仍可有event_indices及usable_ranges，但role_ids=[]，不强造个体身份。
角色的动作对象、接收者、表情线索、物件状态与空间要分清。
exemplar_local_s依据可见CURRENT计时选择人物清楚的一刻；exemplar_box是该时刻视频内x/y/w/h归一化框，
框住对应人物的头部或清楚外形；不确定框或时间时省略，不能乱框其他人。
记录完整的开始—变化—结果、听者反应和可用停顿，usable_ranges只给真正可独立剪用的正长度区间。
events的local_start_s/local_end_s与usable_ranges的local_in_s/local_out_s不可混用；不输出零长度事件。
event_indices从0开始，须存在于本次events数组，且每个被引用事件与该可用区间有正长度重叠。
角色entity_id必须是known_entities所给的完整ID，不得只抄REF短标签；新人物为null。
当前没有旧人物图片供你比较，identity_merges保持空数组；不能凭已有文字合并人物。
潜在用途只是条件性解释，须引用实际event_indices，不能把拒绝诱惑等新故事当成可见事实。
不限制未来题材；给少量有画面依据的可迁移表达机制、适用条件和冲突，不穷举主题。
只有LOGO/字幕/黑场也应如实记录，roles/events/usable_ranges可为空，说明limitations。
仅返回一个JSON，不要Markdown；文字中文，ID ASCII。结构：
{"window_id":"固定ID","source_id":"固定ID","summary":"实际变化概述",
 "scene":{"place":"可见环境","lighting":"光照","continuity":"同场/切换及不确定处"},
 "roles":[{"role_id":"A","entity_kind":"individual","identity_confirmed":true,"state":"外形、衣服、持物等",
 "identity_evidence":"本窗视觉依据","entity_id":null,"appearance":"本次外观",
 "exemplar_local_s":1,"exemplar_box":[0.1,0.1,0.4,0.7],
 "identity_match_evidence":"匹配REF的依据；不匹配为空"}],
 "events":[{"local_start_s":0,"local_end_s":2,"observed_fact":"可见过程与结果",
 "role_ids":["A"],"actor_id":"A","recipient_id":null,"action":"动作","object":"对象",
 "before_state":"动作前","after_state":"动作后","complete_action_visible":true,
 "expression_cues":"可见表情姿态；推测需注明"}],
 "usable_ranges":[{"local_in_s":0,"local_out_s":2,"event_indices":[0],"role_ids":["A"],
 "continuity_notes":"边界、服装、位置、物件状态限制","shot_role":"action",
 "camera":"景别、运动、朝向与构图","speech_fit":"嘴部可见/反应停顿/画外声音条件",
 "limitations":[]}],
 "affordances":[{"event_indices":[0],"expression":"可能承载的表达机制",
 "possible_uses":["潜在主题例子"],"requires":["成立需要的声音/前后关系"],"conflicts":["明显不支持什么"]}],
 "identity_merges":[],"uncertainties":[],"limitations":[]}
""".replace('"window_id":"固定ID"', '"window_id":' + json.dumps(window['window_id'])).replace(
        '"source_id":"固定ID"', '"source_id":' + json.dumps(window['source_id'])) + json.dumps(context, ensure_ascii=False)


def validate_observation(value, window, bank):
    cleaned = _fine_navigation_points(value, window, partial_ranges=True)
    value.clear()
    value.update(cleaned)
    contracts.validate_fine(value, window)
    known = {row["entity_id"] for row in bank}
    for role in value["roles"]:
        if role.get("entity_kind") == "group" and role["identity_confirmed"]:
            raise ValueError("visual_library:collective_is_not_a_confirmed_individual")
        entity = role.get("entity_id")
        if entity is not None and entity not in known:
            raise ValueError("visual_library:entity_not_in_supplied_reference_bank")
        point = role.get("exemplar_local_s")
        if point is not None and (type(point) not in (int, float) or not math.isfinite(point)
                                 or not 0 <= point < window["duration_s"]):
            raise ValueError("visual_library:exemplar_outside_window")
        box = role.get("exemplar_box")
        if box is not None and (not isinstance(box, list) or len(box) != 4 or
                any(type(item) not in (int, float) or not math.isfinite(item) for item in box) or
                not 0 <= box[0] < 1 or not 0 <= box[1] < 1 or not 0 < box[2] <= 1 - box[0] + 1e-9 or
                not 0 < box[3] <= 1 - box[1] + 1e-9):
            raise ValueError("visual_library:exemplar_box_outside_base_video")
    for item in value.get("affordances", []):
        if (not isinstance(item, dict) or not isinstance(item.get("expression"), str)
                or not item["expression"].strip()
                or not isinstance(item.get("event_indices"), list)
                or not item["event_indices"]
                or any(type(index) is not int or not 0 <= index < len(value["events"])
                       for index in item["event_indices"])):
            raise ValueError("visual_library:affordance_requires_observed_events")
    return value


def _timecoded_window(window, cache):
    """Keep old references out of the video and expose its actual local clock."""
    folder = Path(cache) / json_sha([window["sha256"], "timecoded_current_only_v1"])[:20]
    folder.mkdir(parents=True, exist_ok=True)
    target, lineage = folder / "window.mp4", folder / "lineage.json"
    if lineage.exists():
        saved = _read(lineage)
        if sha256_file(saved["path"]) != saved["sha256"]:
            raise LibraryStopped("visual_library:timecoded_proxy_changed")
        return saved
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", window["path"], "-vf",
        r"drawtext=text='CURRENT %{pts\:hms}':x=8:y=8:fontsize=22:fontcolor=white:box=1:boxcolor=black@0.8",
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-b:v", "350k",
        "-movflags", "+faststart", str(target)], check=True, capture_output=True)
    saved = {**window, "path": str(target), "sha256": sha256_file(target),
        "size_bytes": target.stat().st_size, "metadata": probe_media(target), "reference_board": [],
        "observation_geometry": "current video only, with local timecode",
        "base_proxy": {"path": window["path"], "sha256": window["sha256"]}}
    if saved["size_bytes"] >= 8_000_000:
        raise LibraryStopped("visual_library:timecoded_proxy_exceeds_mcp_limit")
    write_json(lineage, saved)
    return saved


def _exemplars(window, cache):
    result = []
    base = window.get("base_proxy", window)
    for role in window["observation"]["roles"]:
        if not role["identity_confirmed"]:
            continue
        point = role.get("exemplar_local_s")
        if point is None:
            events = [event for event in window["observation"]["events"] if role["role_id"] in event["role_ids"]]
            if not events:
                continue
            point = (events[0]["local_start_s"] + events[0]["local_end_s"]) / 2
        folder = Path(cache) / "characters" / json_sha([window["window_id"], role["role_id"]])[:20]
        folder.mkdir(parents=True, exist_ok=True)
        image = folder / "exemplar.jpg"
        if not image.exists():
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", str(point),
                "-i", base["path"], "-frames:v", "1", str(image)], check=True, capture_output=True)
        box = role.get("exemplar_box")
        original = image
        if box is not None:
            from PIL import Image
            cropped = folder / "character.jpg"
            if not cropped.exists():
                with Image.open(image) as picture:
                    width, height = picture.size
                    x, y, w, h = box
                    picture.crop((round(x * width), round(y * height),
                                  round(min(1, x + w) * width), round(min(1, y + h) * height))).save(cropped)
            image = cropped
        result.append(dict(entity_id=role["entity_id"], role_id=role["role_id"],
            path=str(image), sha256=sha256_file(image), source_time_s=window["source_start_s"] + point,
            description=role["state"], source_frame_path=str(original),
            source_frame_sha256=sha256_file(original), model_estimated_box=box,
            scope="model-selected character crop" if box is not None else
                  "full frame at model-selected character time; identity may remain ambiguous"))
    return result


def _check_exemplars(window, bank, cache, model, state):
    """Check new appearance references; a received observation is not approval."""
    from PIL import Image, ImageDraw, ImageFont
    verified = {row["entity_id"] for row in bank for ref in row.get("exemplars", [])
                if ref.get("reference_check", {}).get("approved") is True
                and ref["reference_check"].get("evidence", {}).get("status") == "received"}
    candidates = []
    for ref in window["entity_exemplars"]:
        ref["reference_check"] = dict(approved=False, visible_appearance="", evidence=None,
            reason="existing_verified_reference_new_candidate_not_checked")
        if ref["entity_id"] not in verified:
            candidates.append(ref)
    if not candidates:
        return
    context = []
    board = Image.new("RGB", (720, 260 * len(candidates)), "#202020")
    draw, font = ImageDraw.Draw(board), ImageFont.load_default(size=14)
    for index, ref in enumerate(candidates):
        full_path = ref.get("source_frame_path", ref["path"])
        full_sha = ref.get("source_frame_sha256", ref["sha256"])
        if sha256_file(full_path) != full_sha or sha256_file(ref["path"]) != ref["sha256"]:
            raise LibraryStopped("visual_library:reference_check_image_changed")
        for image_path, x, width in ((full_path, 0, 480), (ref["path"], 488, 232)):
            with Image.open(image_path) as picture:
                picture = picture.convert("RGB")
                picture.thumbnail((width, 230))
                board.paste(picture, (x, index * 260))
        label = f"C{index:03d}"
        draw.text((4, index * 260 + 236), label + " full frame | estimated crop", fill="white", font=font)
        context.append(dict(label=label, entity_id=ref["entity_id"],
            proposed_description=ref["description"], source_frame_sha256=full_sha,
            exemplar_sha256=ref["sha256"], estimated_box=ref.get("model_estimated_box")))
    folder = Path(cache) / "reference_checks" / json_sha(context)[:20]
    folder.mkdir(parents=True, exist_ok=True)
    image = folder / "references.jpg"
    if not image.exists():
        board.save(image, quality=90)
    image_sha = sha256_file(image)
    prompt = ("仅核对人物外形参照。每行左为实际完整帧，右为模型估计裁框，裁框可能框错人。"
        "判断图中可见外形是否支持该行所提角色描述；不使用电影知识、剧情、原台词或动机补身份。"
        "看不清、裁框落在别人身上、描述与可见外形冲突均approved=false。"
        "visible_appearance只写实际可见外形，不创作新身份，不判断动作过程或跨帧剧情。"
        "同entity_id若有多行，只有所有行均支持描述才通过。每个entity_id恰好返回一项。"
        '只返回JSON：{"references":[{"entity_id":"完整ID","approved":false,'
        '"visible_appearance":"可见外形或看不清","reason":"图像依据"}]}\n'
        + json.dumps(context, ensure_ascii=False))
    expected = {ref["entity_id"] for ref in candidates}
    def validate(value):
        rows = value.get("references") if isinstance(value, dict) else None
        if (not isinstance(rows, list) or len(rows) != len(expected)
                or any(not isinstance(row, dict) for row in rows)
                or {row.get("entity_id") for row in rows} != expected
                or any(type(row.get("approved")) is not bool or any(
                    not isinstance(row.get(key), str) or not row[key].strip()
                    for key in ("visible_appearance", "reason")) for row in rows)):
            raise ValueError("visual_library:reference_check_complete_decisions_required")
    name = "fine_" + json_sha(["appearance_reference_check_v1", image_sha, context])[:16]
    result = model.call(name, prompt, image, validate, image=True,
        scope=dict(kind="sparse_contact_sheet", source_sha256=window["source_sha256"],
                   source_start_s=window["source_start_s"], source_end_s=window["source_end_s"]))
    state._reload()
    call = next(row for row in reversed(state.data["calls"])
                if row["name"] in {name, name + "_repair"} and row["status"] == "received")
    evidence = dict(status="received", ledger_path=str(state.path), call_id=call["id"],
                    request_sha256=call["request_sha256"], response_sha256=call["response_sha256"])
    decisions = {row["entity_id"]: row for row in result["references"]}
    for ref in candidates:
        ref["reference_check"] = {**decisions[ref["entity_id"]], "evidence": evidence,
            "check_image_sha256": image_sha, "exemplar_sha256": ref["sha256"],
            "source_frame_sha256": ref.get("source_frame_sha256", ref["sha256"])}


def _ranges(source, max_window_s, cuts):
    start = 0.0
    duration = source["duration_s"]
    while start < duration - 1e-6:
        target = min(duration, start + max_window_s)
        available = [point for point in cuts if start + max_window_s * .6 <= point <= target]
        end = max(available) if available and target < duration else target
        yield start, end
        start = end


def build(library, store_root, output, *, model_factory, configuration, max_window_s=60,
          batch_windows=None, shot_timelines=None):
    """Continue known received work; never resend a pending/unknown request."""
    output, store_root = Path(output).resolve(), Path(store_root).resolve()
    output.mkdir(parents=True, exist_ok=True)
    with file_lock(output / ".indexer.lock"):
        inventory = output / "source_catalog" / "inventory.json"
        catalog = _read(inventory) if inventory.exists() else inventory_sources(library, inventory.parent)
        for source in catalog["sources"]:
            verify_source(source)
        config = {**PROVIDER, **configuration, "workflow": "visual_library_v1",
                  "input_video_policy": "timecoded_current_only_v1",
                  "window_max_s": max_window_s, "store_root": str(store_root)}
        state = LibraryState(output, {"library": catalog, "configuration": config}, max_requests=None)
        model = model_factory(state)
        state._reload()
        if any(call["status"] in {"submitted", "uncertain"} for call in state.data["calls"]):
            raise LibraryStopped("request_outcome_unknown_no_replay")
        store, completed, gaps = VisualLibrary(store_root), 0, []
        worker = dict(pid=os.getpid(), status="running", started_at=time.time(), output=str(output))
        write_json(output / "worker.json", worker)
        try:
            for source in catalog["sources"]:
                timeline = None
                if shot_timelines:
                    timeline = next((item for item in Path(shot_timelines).rglob("timeline.json")
                        if _read(item).get("source_sha256") == source["sha256"]), None)
                cuts = [row["source_time_s"] for row in _read(timeline)["cuts"]] if timeline else []
                for start, end in _ranges(source, max_window_s, cuts):
                    processing = config.get("source_subtitle_regions", {}).get(source["sha256"])
                    identity = [source["sha256"], start, end]
                    if processing is not None:
                        identity.append({"subtitle_regions": processing})
                    window_id = "vl_" + json_sha(identity)[:16]
                    try:
                        store.get(window_id)
                    except FileNotFoundError:
                        pass
                    else:
                        continue
                    scope = dict(kind="continuous_window", source_sha256=source["sha256"],
                                 source_start_s=start, source_end_s=end)
                    exclusions = config.get("unknown_inputs", [])
                    if any(all(scope[key] == old.get("scope", {}).get(key)
                               for key in ("source_sha256", "source_start_s", "source_end_s"))
                           for old in exclusions):
                        gaps.append({**scope, "reason": "historical_unknown_exact_range_not_replayed"})
                        continue
                    excluded = next((row for row in config.get("excluded_ranges", [])
                        if all(scope[key] == row.get("scope", {}).get(key)
                               for key in ("source_sha256", "source_start_s", "source_end_s"))), None)
                    if excluded:
                        gaps.append({**scope, "reason": excluded["reason"], "evidence": excluded.get("evidence")})
                        continue
                    cache = store_root / "media"
                    if processing is not None:
                        masks = [dict(row, start_s=start, end_s=end) for row in processing]
                    else:
                        raw = create_contact_sheet(source, start, end, cache, frame_count=6)
                        masks = _source_subtitles(model, source, raw, image=True)
                    base = prepare_window(source, start, end, cache, include_audio=False,
                                          subtitle_masks=masks, max_bytes=3_500_000)
                    bank = store.entities(source["sha256"])
                    # Recent appearances are most useful; all entities remain searchable in the store.
                    bank.sort(key=lambda row: max(
                        [item.get("source_start_s", 0) for item in row.get("appearances", [])] or [0]), reverse=True)
                    seed = config.get("received_observation_seeds", {}).get(window_id)
                    if seed:
                        prior = _read(Path(seed["ledger_path"]).parent / "calls" / seed["call_id"] / "request.json")
                        window = _read(Path(prior["arguments"]["video_source"]).parent / "lineage.json")
                        if any(window[key] != base[key] for key in
                               ("source_sha256", "source_start_s", "source_end_s", "source_visual_policy")):
                            raise LibraryStopped("received_seed_window_source_changed")
                        if seed.get('compatibility_policy') is not None:
                            supplied = _received_note_seed_bank(seed, window, bank)
                        else:
                            reference_ids = {row["entity_id"] for row in window.get("reference_board", [])}
                            supplied = [row for row in bank if row["entity_id"] in reference_ids]
                    else:
                        excluded = set(config.get("excluded_exemplar_sha256s", []))
                        for row in bank:
                            row["exemplars"] = [ref for ref in row.get("exemplars", [])
                                if ref["sha256"] not in excluded
                                and ref.get("reference_check", {}).get("approved") is True
                                and ref["reference_check"].get("evidence", {}).get("status") == "received"]
                        supplied = [row for row in bank if row["exemplars"]]
                        window = _timecoded_window({**base, "window_id": window_id}, cache)
                    window["window_id"] = window_id
                    name = "fine_" + json_sha([identity, "visual_library_v1"])[:16]
                    prompt = observation_prompt(window, supplied)
                    if seed:
                        observation, call, evidence_ledger = _received_seed(seed, window, supplied)
                    else:
                        observation = model.call(name, prompt, window["path"],
                            lambda value: validate_observation(value, window, supplied), scope=scope)
                        state._reload()
                        call = next(row for row in reversed(state.data["calls"])
                                    if row["name"] in {name, name + "_repair"} and row["status"] == "received")
                        evidence_ledger = state.path
                    received = deepcopy(observation)
                    for role in observation["roles"]:
                        if role.get("entity_id") is None:
                            role["entity_id"] = source["sha256"] + ":" + window_id + "_" + role["role_id"]
                            role["identity_status"] = "new_local_entity_not_cross_window_confirmation"
                        else:
                            role["identity_status"] = "model_verified_appearance_record_match"
                    window.update(status="watched", observation=observation)
                    window["entity_exemplars"] = _exemplars(window, cache)
                    _check_exemplars(window, bank, cache, model, state)
                    record = dict(observation_id=window_id, window=window,
                        received_observation=received, evidence=dict(status="received",
                            ledger_path=str(evidence_ledger), call_id=call["id"],
                            request_sha256=call["request_sha256"], response_sha256=call["response_sha256"]),
                        observation_method=dict(input_kind="continuous_silent_proxy",
                            usable_range_policy="retain_valid_ranges_v1",
                            fps=base["spec"]["fps"], time_precision="model_estimate",
                            subtitle_localization="source_bound_fixed_band" if processing is not None else "six_sparse_frames",
                            limitations=["Proxy sampling and model sampling do not establish native-frame completeness.",
                                "The source subtitle layout is based on representative frames, not every native frame."]))
                    store.add_observation(record)
                    merges = received.get("identity_merges", [])
                    refs = {row["entity_id"] for row in window.get("reference_board", [])} & {
                        row["entity_id"] for row in supplied}
                    for pair in merges if isinstance(merges, list) else [merges]:
                        old = pair.get("old_id") if isinstance(pair, dict) else None
                        new = pair.get("new_id") if isinstance(pair, dict) else None
                        detail = pair.get("evidence") if isinstance(pair, dict) else None
                        revision, reason = None, None
                        if (not isinstance(old, str) or not isinstance(new, str) or old == new
                                or not {old, new} <= refs
                                or not all(entity.startswith(source["sha256"] + ":") for entity in (old, new))
                                or not isinstance(detail, str) or not detail.strip()):
                            reason = "optional_merge_requires_two_supplied_same_source_ids_and_visual_evidence"
                        else:
                            try:
                                revision = store.merge_entities(old, new, record["evidence"])
                            except ValueError as error:
                                # A conflicting proposal is optional; changed original evidence is fatal.
                                if str(error) not in {"visual_library:conflicting_entity_revision",
                                                      "visual_library:entity_revision_cycle"}:
                                    raise
                                reason = str(error)
                        state.set_artifact("identity_merge", dict(observation_id=window_id,
                            call_id=call["id"], pair=pair, status="ignored" if reason else "applied",
                            reason=reason, revision_id=revision))
                    completed += 1
                    write_json(output / "progress.json", dict(status="indexing", last_observation_id=window_id,
                        new_observations_this_run=completed, requests=state.usage()["requests"],
                        sources=[dict(source_id=item["source_id"], **store.coverage(item["sha256"], item["duration_s"]))
                                 for item in catalog["sources"]], excluded_ranges=gaps))
                    if batch_windows is not None and completed >= batch_windows:
                        worker.update(status="batch_complete", finished_at=time.time())
                        write_json(output / "worker.json", worker)
                        return _read(output / "progress.json")
            worker.update(status="completed", finished_at=time.time())
            write_json(output / "worker.json", worker)
            result = dict(status="completed" if not gaps else "completed_with_gaps",
                sources=[dict(source_id=item["source_id"], **store.coverage(item["sha256"], item["duration_s"]))
                         for item in catalog["sources"]], excluded_ranges=gaps, usage=state.usage())
            write_json(output / "result.json", result)
            return result
        except BaseException as error:
            worker.update(status="stopped", finished_at=time.time(), error=str(error))
            write_json(output / "worker.json", worker)
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build", "search", "status"))
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--library", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--home", type=Path)
    parser.add_argument("--query", default="")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--window-seconds", type=float, default=60)
    parser.add_argument("--batch-windows", type=int)
    parser.add_argument("--shot-timelines", type=Path)
    parser.add_argument("--configuration", type=Path, help="Optional existing provider/history/subtitle configuration.")
    args = parser.parse_args(argv)
    if args.command == "search":
        result = VisualLibrary(args.store).search(args.query, limit=args.limit)
    elif args.command == "status":
        result = {"entities": VisualLibrary(args.store).entities(),
                  "progress": _read(args.output / "progress.json") if args.output else None}
    else:
        if not args.library or not args.output or not args.home:
            parser.error("build requires --library, --output and --home")
        if not math.isfinite(args.window_seconds) or not 1 <= args.window_seconds <= 90:
            parser.error("--window-seconds must be a technical window size in 1..90")
        if args.batch_windows is not None and args.batch_windows < 1:
            parser.error("--batch-windows must be positive")
        server = settings(args.home)
        history = load_history(server)
        config = _read(args.configuration) if args.configuration else {}
        exclusions = [*(history or {}).get("unknown_inputs", []), *config.get("unknown_inputs", [])]
        config.update(vision_platform=config.get("vision_platform", "ZHIPU"),
            vision_generation=VISION_GENERATION, unknown_inputs=exclusions)
        if history:
            config["prior_requests"] = max(config.get("prior_requests", 0), history["baseline_requests"])
            config["history_manifest"] = dict(path=server["history_file"], sha256=server["history_sha256"])
        os.environ["Z_AI_API_KEY"] = credential(args.home)
        try:
            result = build(args.library, args.store, args.output, configuration=config,
                model_factory=lambda state: OpenCodeMCP(state, package_root=server["mcp_package_root"],
                    executable=server["opencode_executable"], exclusions=exclusions),
                max_window_s=args.window_seconds, batch_windows=args.batch_windows, shot_timelines=args.shot_timelines)
        finally:
            os.environ.pop("Z_AI_API_KEY", None)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
