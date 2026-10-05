"""Forward-only explicit claim serialization; immutable evidence stays strict."""
from copy import deepcopy
import json
import re

from ..contract import ids, require, text
from . import semantic_audit as audit
from .state import json_sha

POLICY = "explicit_slice_claim_check_v1"


def comparison_fingerprint(observation, claims, hypotheses):
    """Ignore ID aliases; bind actual source/facts and substantive claim text."""
    facts = [{k:e.get(k) for k in ('kind','local_start_s','local_end_s','description')}
             for e in observation.get('evidence',[])]
    roles = [{k:v for k,v in r.items() if k != 'role_id'} for r in hypotheses]
    core = {'source':{k:observation.get(k) for k in ('source_id','source_sha256','source_in_s','source_out_s')},
        'facts':facts,'character_appearances':sorted(c['appearance'] for c in observation.get('characters',[])),
        'claims':sorted((c['kind'],c['description']) for c in claims), 'role_hypotheses':roles}
    return json_sha(core)


def old_claim_input(old_prompt):
    """Read the three legacy request JSON blocks, never a model response."""
    if not isinstance(old_prompt, str):
        return None
    markers = ("\nobservation：", "\nrequired_claims：", "\nrole_hypotheses：")
    offset = old_prompt.find(markers[0])
    if offset < 0:
        return None
    decoder, values = json.JSONDecoder(), []
    try:
        for marker in markers:
            if not old_prompt.startswith(marker, offset):
                return None
            offset += len(marker)
            while offset < len(old_prompt) and old_prompt[offset].isspace():
                offset += 1
            value, offset = decoder.raw_decode(old_prompt, offset)
            values.append(value)
        tail = old_prompt[offset:].strip()
        if tail:
            repair = "上次输出未通过本地协议校验。只修复JSON字段、ID和时间域，不得补造画面证据。"
            if not tail.startswith(repair):
                return None
            record = json.loads(tail[len(repair):])
            if not isinstance(record, dict) or set(record) != {"validation_error", "previous_response"}:
                return None
        return tuple(values)
    except (ValueError, TypeError):
        return None


def prompt(observation, claims, hypotheses):
    """Show every actual claim ID and every mandatory response field once."""
    require(isinstance(observation, dict), "explicit_claims:observation_object_required")
    text(observation.get("segment_id"), "explicit_claims/segment_id")
    require(observation.get("protocol") == audit.SEMANTIC_PROTOCOL, "explicit_claims:observation_protocol")
    ids(claims, "claim_id", "explicit_claims/claims", nonempty=False)
    template = {"protocol": audit.SEMANTIC_PROTOCOL, "segment_id": observation["segment_id"],
        "observation_sha256": json_sha(observation), "claim_checks": [
            {"claim_id": claim["claim_id"], "status": "unverifiable", "evidence_ids": [],
             "reason": "根据这一个片段的独立事实填写真实依据或缺项。",
             "limitations": ["填写真实限制；仅supported时允许空数组。"]} for claim in claims],
        "uncertainties": []}
    return """你只比较这一段不可改写的独立画面事实与待核说法，不生成新事实。
required_claims和role_hypotheses不是观察真值；不靠参考主题、电影常识、字幕或更长窗口补事件。
仅引用observation.evidence的ID。动作和结果需要对应直接视觉证据；身份需要实际画内人物。
结果supported必须有visual_outcome，推断和文字不能证明动作、身份或行动结果。
逐个核对所有真实claim_id，不增不漏。模板中的unverifiable只是合法值示例，你须独立判断。
status只能选择一个literal：supported、partial、unsupported、unverifiable。
根字段protocol、segment_id、observation_sha256、claim_checks、uncertainties全部必需。
每个claim_checks项必须包含claim_id、status、evidence_ids、reason、limitations全部六个字段。
limitations对supported也必须显式写数组（可以[]）；非supported必须至少写一项真实限制。
根uncertainties必须显式写数组，即使没有其它不确定性也写[]，不能省略或用null。
evidence_ids必须是本段证据ID的无重复数组；supported和partial必须提供证据。
绑定字段逐字保持。reason非空。不要修改观察、添加伪造证据或把缺项转成成功。
只返回一个完整JSON对象，下面是包含全部实际claim IDs的唯一输出形状：
""" + json.dumps(template, ensure_ascii=False) + "\n输入事实与待核说法：\n" + json.dumps({
        "observation": observation, "required_claims": claims, "role_hypotheses": hypotheses},
        ensure_ascii=False, allow_nan=False)


def diagnostics(value, observation, claims):
    """Report mechanical omissions together; never fill fields or judge footage."""
    errors = []
    def add(path, code, expected, actual):
        errors.append({"path": path, "code": code, "expected": deepcopy(expected), "actual": deepcopy(actual)})
    def array(raw, path, strings=False, nonempty=False):
        if not isinstance(raw, list):
            add(path, "missing" if raw is None else "type", "array", raw)
            return None
        if nonempty and not raw:
            add(path, "count", "at least one item", 0)
        if strings:
            for i, item in enumerate(raw):
                if not isinstance(item, str) or not item.strip():
                    add(f"{path}[{i}]", "text", "nonempty string", item)
        return raw
    if not isinstance(value, dict):
        add("$", "type", "object", value)
        return errors
    expected = {"protocol": audit.SEMANTIC_PROTOCOL, "segment_id": observation["segment_id"],
                "observation_sha256": json_sha(observation)}
    for key, bound in expected.items():
        if key not in value or value[key] != bound:
            add("$." + key, "missing" if key not in value else "binding", bound, value.get(key))
    array(value.get("uncertainties"), "$.uncertainties", strings=True)
    required = {row["claim_id"] for row in claims}
    allowed = {row["evidence_id"] for row in observation["evidence"]}
    checks = array(value.get("claim_checks"), "$.claim_checks")
    found = set()
    for i, row in enumerate(checks or []):
        path = f"$.claim_checks[{i}]"
        if not isinstance(row, dict):
            add(path, "type", "object", row)
            continue
        ident = row.get("claim_id")
        if not isinstance(ident, str) or re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", ident) is None:
            add(path + ".claim_id", "id", "safe string ID", ident)
        elif ident in found or ident not in required:
            add(path + ".claim_id", "duplicate_or_unknown_id", sorted(required), ident)
        else:
            found.add(ident)
        status = row.get("status")
        if not isinstance(status, str) or status not in audit.CLAIM_STATUSES:
            add(path + ".status", "enum", sorted(audit.CLAIM_STATUSES), status)
        reason = row.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            add(path + ".reason", "text", "nonempty string", reason)
        array(row.get("limitations"), path + ".limitations", strings=True,
              nonempty=isinstance(status, str) and status in audit.CLAIM_STATUSES - {"supported"})
        evidence = array(row.get("evidence_ids"), path + ".evidence_ids", strings=True,
                         nonempty=status in ("supported", "partial"))
        if evidence is not None and all(isinstance(item, str) for item in evidence):
            if len(evidence) != len(set(evidence)) or not set(evidence) <= allowed:
                add(path + ".evidence_ids", "duplicate_or_unknown_ref", sorted(allowed), evidence)
    if found != required:
        add("$.claim_checks", "coverage", sorted(required), sorted(found))
    return errors


def validate(value, observation, claims):
    """Keep the original strict contract; append all mechanical errors on failure."""
    try:
        return audit.validate_segment_claim_check(value, observation, claims)
    except (ValueError, TypeError, KeyError) as error:
        issues = diagnostics(value, observation, claims)
        raise ValueError(str(error) + "; explicit_claim_mechanical_diagnostics=" +
                         json.dumps(issues, ensure_ascii=False, allow_nan=False)) from error
