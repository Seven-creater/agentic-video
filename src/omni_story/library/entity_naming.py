"""Offline crop preparation and ledger-bound film-cast identity annotations.

Names are identity anchors only. This module neither merges library entities nor
adds actions, states, relationships or story knowledge to visual observations.
Each crop has its own numbered tile; a final grid can contain unnumbered blanks.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
from io import BytesIO
import json
import math
import os
from pathlib import Path
import re
import sys

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .contracts import parse_model_json
from .film_cast import canonical_name, character_for, load_film_cast
from .state import LibraryState, LibraryStopped, file_lock, json_sha, write_json
from .visual_library import VisualLibrary


POLICY = "film_cast_identity_anchor_v1"
MAX_IMAGE_PX = 2048
MAX_JPEG_BYTES = 2_000_000


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _film_map(value):
    if (not isinstance(value, dict) or not value or any(
            not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{64}", sha)
            or type(film) is not int or film not in {1, 2, 3}
            for sha, film in value.items())):
        raise ValueError("entity_naming:film_map_requires_full_source_sha256_to_film_1_2_3")
    return dict(sorted(value.items()))


def film_whitelist(film_cast, film):
    """Keep aliases and canonical names strictly within the requested film."""
    return {**film_cast, "characters": [c for c in film_cast["characters"] if film in c["films"]]}


def select_exemplars(entities, film_map, *, library_root=None):
    """Select at most two distinct crops: area, description present, latest time.

    Description text is never put in the recognition prompt. Actual image sizes
    and hashes, rather than optional metadata, determine selection and binding.
    Entities without crops remain in this inventory for coverage counts only.
    """
    film_map = _film_map(film_map)
    selected = []
    for entity in sorted(entities, key=lambda row: row["entity_id"]):
        source = entity["source_sha256"]
        if source not in film_map:
            raise ValueError("entity_naming:source_missing_from_film_map:" + source)
        crops = []
        for exemplar in entity.get("exemplars", []):
            path = Path(exemplar["path"])
            if not path.is_absolute() and library_root is not None:
                path = Path(library_root) / path
            path = path.resolve(strict=True)
            sha = _sha(path)
            if exemplar.get("sha256") and exemplar["sha256"] != sha:
                raise ValueError("entity_naming:crop_sha256_changed:" + str(path))
            with Image.open(path) as image:
                width, height = ImageOps.exif_transpose(image).size
            point = exemplar.get("source_time_s", 0)
            if type(point) not in (int, float) or not math.isfinite(point):
                raise ValueError("entity_naming:invalid_crop_time")
            crops.append(dict(path=str(path), sha256=sha, width=width, height=height,
                source_time_s=point, description=str(exemplar.get("description") or "")))
        crops.sort(key=lambda row: (-row["width"] * row["height"],
            -bool(row["description"].strip()), -row["source_time_s"], row["sha256"], row["path"]))
        unique = {}
        for crop in crops:
            unique.setdefault(crop["sha256"], crop)
        selected.append(dict(entity_id=entity["entity_id"], source_sha256=source,
            film=film_map[source], exemplars=list(unique.values())[:2]))
    return selected


def build_grids(selected, output, *, tiles=12):
    """Write one-film JPEG grids and a tile-to-entity manifest (no model calls).

    Grid capacity is 9..16. The last grid is padded with unnumbered blank cells;
    only real numbered tiles are requested or included in the manifest.
    """
    if type(tiles) is not int or not 9 <= tiles <= 16:
        raise ValueError("entity_naming:tiles_must_be_9_to_16")
    output = Path(output)
    (output / "grids").mkdir(parents=True, exist_ok=True)
    manifest = dict(schema_version="entity_naming_manifest_v1", tiles_per_grid=tiles,
        entities=[{key: row[key] for key in ("entity_id", "source_sha256", "film")} |
                  {"crop_count": len(row["exemplars"])} for row in selected], grids=[])
    font = ImageFont.load_default(size=46)
    for film in sorted({row["film"] for row in selected}):
        crops = [dict(entity_id=row["entity_id"], source_sha256=row["source_sha256"], **crop)
                 for row in selected if row["film"] == film for crop in row["exemplars"]]
        for offset in range(0, len(crops), tiles):
            batch = crops[offset:offset + tiles]
            capacity = max(9, len(batch))
            columns = math.ceil(math.sqrt(capacity))
            rows = math.ceil(capacity / columns)
            sheet = Image.new("RGB", (columns * 480, rows * 480), "#202020")
            draw = ImageDraw.Draw(sheet)
            entries = []
            for number, crop in enumerate(batch, 1):
                x, y = ((number - 1) % columns) * 480, ((number - 1) // columns) * 480
                draw.rectangle((x, y, x + 479, y + 63), fill="white")
                draw.text((x + 16, y + 5), str(number), fill="black", font=font)
                with Image.open(crop["path"]) as image:
                    thumb = ImageOps.contain(ImageOps.exif_transpose(image).convert("RGB"), (464, 400))
                    sheet.paste(thumb, (x + (480 - thumb.width) // 2,
                                       y + 64 + (416 - thumb.height) // 2))
                entries.append(dict(tile=number, entity_id=crop["entity_id"],
                    source_sha256=crop["source_sha256"], crop_path=crop["path"],
                    crop_sha256=crop["sha256"], source_time_s=crop["source_time_s"]))
            for quality in (88, 78, 68, 58, 48, 38, 28, 18):
                buffer = BytesIO()
                sheet.save(buffer, format="JPEG", quality=quality, optimize=True)
                if buffer.tell() <= MAX_JPEG_BYTES:
                    break
            if buffer.tell() > MAX_JPEG_BYTES or max(sheet.size) > MAX_IMAGE_PX:
                raise ValueError("entity_naming:grid_image_limit_exceeded")
            grid_id = f"film_{film}_{offset // tiles + 1:03d}"
            path = Path("grids") / (grid_id + ".jpg")
            (output / path).write_bytes(buffer.getvalue())
            manifest["grids"].append(dict(grid_id=grid_id, film=film, path=path.as_posix(),
                sha256=_sha(output / path), width=sheet.width, height=sheet.height,
                size_bytes=buffer.tell(), tiles=entries))
    write_json(output / "manifest.json", manifest)
    return manifest


def naming_prompt(grid, film_cast):
    allowed = film_whitelist(film_cast, grid["film"])
    names = [{"name": c["name"], "aliases": c["aliases"]} for c in allowed["characters"]]
    return (f"识别《功夫熊猫》第{grid['film']}部图片中各编号角色，只回答是谁。\n"
        "电影角色表仅作身份锚点，可用角色外形知识；只能从本片白名单选名。"
        "证据仅写图中可见外形特征；禁止描述剧情、关系、动作、状态或据此推断身份。"
        "不确定不要猜：白名单外为其他(other)，无法辨认为看不清(unclear)，二者name必须为null。"
        "已认出为named，name用白名单标准名。忽略空白格；每个编号恰好答一次。\n"
        '仅返回JSON：{"tiles":[{"tile":1,"name":null,"status":"unclear",'
        '"evidence":"可见外形依据"}]}\n' + json.dumps(dict(whitelist=names,
            alternatives=["其他", "看不清"], tiles=[r["tile"] for r in grid["tiles"]]), ensure_ascii=False))


class NamingValidationError(ValueError):
    def __init__(self, problems):
        self.problems = problems
        self.diagnostics = {"problems": problems}
        super().__init__("entity_naming:invalid_reply:" + json.dumps(problems, ensure_ascii=False))


def validate_names(value, grid, film_cast):
    """Collect all tile/name/status problems, then canonicalize valid aliases."""
    expected = {row["tile"] for row in grid["tiles"]}
    allowed = film_whitelist(film_cast, grid["film"])
    problems, seen, normalized = [], set(), []
    rows = value.get("tiles") if isinstance(value, dict) else None
    if not isinstance(rows, list):
        problems.append("tiles_must_be_a_list")
        rows = []
    for index, row in enumerate(rows):
        prefix = f"row_{index}:"
        if not isinstance(row, dict):
            problems.append(prefix + "tile_answer_must_be_object")
            continue
        tile = row.get("tile")
        if type(tile) is not int or tile not in expected:
            problems.append(prefix + "unexpected_tile:" + str(tile))
        elif tile in seen:
            problems.append(prefix + "duplicate_tile:" + str(tile))
        else:
            seen.add(tile)
        name, status = row.get("name"), row.get("status")
        character = character_for(name, allowed) if isinstance(name, str) else None
        if name is not None and character is None:
            problems.append(prefix + "non_whitelist_name:" + str(name))
        if status not in ("named", "other", "unclear"):
            problems.append(prefix + "invalid_status")
        if status == "named" and character is None:
            problems.append(prefix + "named_requires_whitelist_name")
        if status in ("other", "unclear") and name is not None:
            problems.append(prefix + "name_requires_status_named")
        if "name" not in row:
            problems.append(prefix + "name_required")
        if not isinstance(row.get("evidence"), str) or not row["evidence"].strip():
            problems.append(prefix + "visible_evidence_required")
        normalized.append(dict(tile=tile, name=canonical_name(name, allowed) if character else name,
                               status=status, evidence=row.get("evidence")))
    problems.extend("missing_tile:" + str(tile) for tile in sorted(expected - seen))
    if problems:
        raise NamingValidationError(problems)
    value["tiles"] = sorted(normalized, key=lambda row: row["tile"])
    return value


class _NamingState(LibraryState):
    """The per-invocation cap also applies to provider protocol repair calls."""
    def begin_call(self, *args, **kwargs):
        self._reload()
        if self.run_limit is not None and self.data["request_count"] - self.run_start >= self.run_limit:
            raise LibraryStopped("entity_naming:max_calls_reached")
        return super().begin_call(*args, **kwargs)


class _InjectedModel:
    """Give a bare injected callable the same write-before-send single ledger."""
    def __init__(self, state, vision_model):
        self.state, self.vision_model = state, vision_model

    def call(self, name, prompt, image_path, validator, *, image=True):
        request = dict(tool="analyze_image", arguments=dict(image_source=str(image_path), prompt=prompt),
            media_sha256=_sha(image_path), provider="injected_vision_model",
            policy_version=self.state.data["policy_version"])
        call, _ = self.state.begin_call(name, request)
        try:
            reply = self.vision_model(name, prompt, str(image_path), validator, image=image)
        except BaseException as error:
            self.state.fail_call(call, str(error), uncertain=True)
            raise
        self.state.complete_call(call, reply)
        value = deepcopy(reply)
        validator(value)
        return value


def _received(state, grid, film_cast):
    name = "entity_names_" + grid["grid_id"]
    calls = [c for c in state.data["calls"] if c["name"] in (name, name + "_repair", name + "_repair2")]
    if not calls:
        return None
    call = calls[-1]
    if call["status"] != "received":
        raise LibraryStopped("entity_naming:recorded_call_no_replay:" + call["id"])
    folder = state.output / "calls" / call["id"]
    request, reply = _read(folder / "request.json"), _read(folder / "response.json")
    if (json_sha(request) != call["request_sha256"] or json_sha(reply) != call["response_sha256"]
            or request.get("media_sha256") != grid["sha256"]):
        raise LibraryStopped("entity_naming:recorded_request_or_reply_changed:" + call["id"])
    if isinstance(reply, dict) and isinstance(reply.get("result"), dict):
        reply = parse_model_json("\n".join(c["text"] for c in reply["result"].get("content", [])
                                          if c.get("type") == "text"))
    value = validate_names(deepcopy(reply), grid, film_cast)
    return dict(call_id=call["id"], value=value)


def _result(manifest, received, film_cast, revision_ids, state):
    observations = {}
    for grid in manifest["grids"]:
        receipt = received.get(grid["grid_id"])
        if receipt is None:
            continue
        answers = {r["tile"]: r for r in receipt["value"]["tiles"]}
        for tile in grid["tiles"]:
            observations.setdefault(tile["entity_id"], []).append(dict(answers[tile["tile"]],
                grid=grid["path"], call_id=receipt["call_id"]))
    entities, groups, per_film = {}, {}, {}
    for film in sorted({e["film"] for e in manifest["entities"]}):
        key = str(film)
        groups[key] = {}
        per_film[key] = dict(total=0, with_crops=0, without_crops=0,
            named=0, other=0, unclear=0, pending=0,
            characters={c["name"]: dict(before=0, after=0)
                        for c in film_whitelist(film_cast, film)["characters"]})
    for entity in manifest["entities"]:
        key, ident = str(entity["film"]), entity["entity_id"]
        counts = per_film[key]
        counts["total"] += 1
        counts["with_crops" if entity["crop_count"] else "without_crops"] += 1
        if not entity["crop_count"]:
            continue
        rows = observations.get(ident, [])
        if len(rows) != entity["crop_count"]:
            counts["pending"] += 1
            continue  # Do not publish an identity until all selected crops are answered.
        names = {r["name"] for r in rows if r["status"] == "named"}
        conflict = len(names) > 1 or bool(names) and any(r["status"] == "other" for r in rows)
        status = ("unclear" if conflict else "named" if names else
                  "other" if all(r["status"] == "other" for r in rows) else "unclear")
        primary = next((r for r in rows if r["status"] == status), rows[0])
        name = next(iter(names)) if status == "named" else None
        evidence = ("裁剪身份判断冲突；" if conflict else "") + "；".join(r["evidence"] for r in rows)
        entities[ident] = dict(name=name, status=status, evidence=evidence,
            grid=primary["grid"], tile=primary["tile"], call_id=primary["call_id"], observations=rows)
        counts[status] += 1
        if name:
            groups[key].setdefault(name, []).append(ident)
            counts["characters"][name]["before"] += 1
            counts["characters"][name]["after"] = 1
    return dict(policy=POLICY, cast_sha256=film_cast["file_sha256"], library_revision_ids=revision_ids,
        entities=entities, name_groups=groups,
        summary=dict(grouping="同片身份分组计数；未合并或修改素材库实体", per_film=per_film),
        call_count=state.data["request_count"], calls_this_run=state.data["request_count"] - state.run_start,
        complete=len(received) == len(manifest["grids"]))


def label_entity_names(library, output, film_map, *, model_factory=None, vision_model=None,
                       revision_ids=None, max_calls=None, tiles=12, configuration=None, films=None):
    """Prepare locally, then call only the injected model/factory when needed.

    ``model_factory(state)`` returns a ledger-recording object with ``.call`` as
    in visual_indexer.build. A bare ``vision_model`` callable is ledger-wrapped.
    max_calls is the number of new requests allowed in this invocation (0 = prepare).
    name_groups is film -> canonical name -> entity IDs, never across films.
    """
    from .opencode_provider import PROVIDER

    if model_factory is not None and vision_model is not None:
        raise ValueError("entity_naming:provide_only_one_model_injection")
    if max_calls is not None and (type(max_calls) is not int or max_calls < 0):
        raise ValueError("entity_naming:max_calls_must_be_nonnegative")
    if type(tiles) is not int or not 9 <= tiles <= 16:
        raise ValueError("entity_naming:tiles_must_be_9_to_16")
    film_map = _film_map(film_map)
    library, output = Path(library).resolve(), Path(output).resolve()
    for protected in (library / "observations", library / "revisions"):
        if output == protected or protected in output.parents:
            raise ValueError("entity_naming:output_cannot_be_library_records_or_revisions")
    output.mkdir(parents=True, exist_ok=True)
    with file_lock(output / ".entity_naming.lock"):
        ledger = output / "library_state.json"
        if ledger.exists():
            inputs = _read(ledger)["input_lock"]
            if (inputs["library_root"] != str(library) or inputs["film_map"] != film_map
                    or inputs["tiles"] != tiles or inputs.get("films") != (sorted(films) if films else None)
                    or revision_ids is not None and inputs["library_revision_ids"] != list(revision_ids)):
                raise LibraryStopped("entity_naming:locked_inputs_changed")
            if configuration is not None and inputs["configuration"] != {
                    **PROVIDER, **configuration, "workflow": POLICY}:
                raise LibraryStopped("entity_naming:locked_configuration_changed")
            manifest = _read(output / "manifest.json")
            if json_sha(manifest) != inputs["manifest_sha256"]:
                raise LibraryStopped("entity_naming:manifest_changed")
        else:
            cast = load_film_cast(library)
            store = VisualLibrary(library)
            revisions = list(revision_ids) if revision_ids is not None else store.entity_revision_ids()
            selected = select_exemplars(store.entities(revision_ids=revisions), film_map, library_root=library)
            if films:
                # One output directory per film lets films be named in parallel ledgers.
                selected = [row for row in selected if row["film"] in films]
            manifest = build_grids(selected, output, tiles=tiles)
            inputs = dict(library_root=str(library), film_map=film_map, tiles=tiles,
                **({"films": sorted(films)} if films else {}),
                library_revision_ids=revisions, film_cast=cast, manifest_sha256=json_sha(manifest),
                configuration={**PROVIDER, **(configuration or {}), "workflow": POLICY})
        for grid in manifest["grids"]:
            if _sha(output / grid["path"]) != grid["sha256"]:
                raise LibraryStopped("entity_naming:grid_changed:" + grid["grid_id"])
        state = _NamingState(output, inputs, max_requests=None, registry_path=output / ".runs.json")
        state.run_start, state.run_limit = state.data["request_count"], max_calls
        if any(c["status"] in ("submitted", "uncertain") for c in state.data["calls"]):
            raise LibraryStopped("entity_naming:request_outcome_unknown_no_replay")
        cast, received, model = inputs["film_cast"], {}, None
        for grid in manifest["grids"]:
            receipt = _received(state, grid, cast)
            if receipt:
                received[grid["grid_id"]] = receipt
        result = _result(manifest, received, cast, inputs["library_revision_ids"], state)
        write_json(output / "entity_names.json", result)
        for grid in manifest["grids"]:
            if grid["grid_id"] in received:
                continue
            if max_calls is not None and state.data["request_count"] - state.run_start >= max_calls:
                break
            if model is None:
                if vision_model is not None:
                    model = _InjectedModel(state, vision_model)
                elif model_factory is not None:
                    model = model_factory(state)
                else:
                    raise LibraryStopped("entity_naming:vision_model_or_model_factory_required")
            try:
                model.call("entity_names_" + grid["grid_id"], naming_prompt(grid, cast),
                    output / grid["path"], lambda value: validate_names(value, grid, cast), image=True)
            finally:
                state._reload()
            receipt = _received(state, grid, cast)
            if receipt is None:
                raise LibraryStopped("entity_naming:model_factory_did_not_record_call")
            received[grid["grid_id"]] = receipt
            result = _result(manifest, received, cast, inputs["library_revision_ids"], state)
            write_json(output / "entity_names.json", result)
        return result


def main(argv=None, *, model_factory=None, vision_model=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--film-map", type=Path, required=True, help="JSON object: full source SHA256 -> film 1/2/3")
    parser.add_argument("--max-calls", type=int)
    parser.add_argument("--tiles", type=int, default=12)
    parser.add_argument("--films", type=int, nargs="+", choices=(1, 2, 3),
                        help="only these films (use separate --out directories to run films in parallel)")
    args = parser.parse_args(argv)
    previous_key, key_set = os.environ.get("Z_AI_API_KEY"), False
    configuration = None
    if model_factory is None and vision_model is None:
        from .server_cli import VISION_GENERATION
        configuration = dict(vision_generation=VISION_GENERATION)

        def model_factory(state):
            nonlocal key_set
            from .opencode_provider import OpenCodeMCP
            from .server_cli import _home, credential, settings
            server = settings(_home())
            os.environ["Z_AI_API_KEY"] = credential(_home())
            key_set = True
            return OpenCodeMCP(state, package_root=server["mcp_package_root"],
                               executable=server["opencode_executable"])
    try:
        result = label_entity_names(args.library, args.out, _read(args.film_map),
            model_factory=model_factory, vision_model=vision_model, max_calls=args.max_calls,
            tiles=args.tiles, configuration=configuration, films=args.films)
        print(json.dumps({key: result[key] for key in
              ("call_count", "calls_this_run", "complete", "summary")}, ensure_ascii=False), flush=True)
        return 0
    except (LibraryStopped, ValueError, OSError) as error:
        print(str(error), file=sys.stderr, flush=True)
        return 1
    finally:
        if key_set:
            if previous_key is None:
                os.environ.pop("Z_AI_API_KEY", None)
            else:
                os.environ["Z_AI_API_KEY"] = previous_key
