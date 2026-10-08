"""Exact known-repair metadata compatibility, using synthetic replies only."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from omni_story.library import microclip_v2_boundary_metadata as meta, microclip_v2_boundary_state as boundary, microclip_v2_state as mc
from omni_story.library.media import sha256_file
from omni_story.library.state import LibraryState, LibraryStopped, json_sha, write_json


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


@pytest.fixture
def known(tmp_path, monkeypatch):
    root=tmp_path / "run"
    base=LibraryState(root, {"synthetic": "fixed input"}, max_requests=80)
    parent=root / "parent.mp4"
    parent.write_bytes(b"fixed synthetic parent")
    auth={"parent": {"path": str(parent), "sha256": sha256_file(parent)},
          "slot": {"start_s": 0.0, "end_s": 15.0}, "allowed_render_directory": str(root / "render"),
          "execution_directory": str(root / "execution"), "protected_files": []}
    data=base.data
    for n in range(1,239):
        ident=f"old_{n}"
        (root / "calls" / ident).mkdir(parents=True)
        data["calls"].append({"id": ident,"name":f"old_{n}","status":"received","repair_of":None,"usage":{},"request_sha256":json_sha({"historical":n})})
    data["request_count"]=238
    source_sha=auth["parent"]["sha256"]
    rows=[{"frame_id":"old_anchor","source_sha256":source_sha,"source_time_s":1.8,"frame_end_s":1.84},
          {"frame_id":"candidate","source_sha256":source_sha,"source_time_s":2.1,"frame_end_s":2.133333333},
          {"frame_id":"new_neighbor","source_sha256":source_sha,"source_time_s":2.133333333,"frame_end_s":2.16666667}]
    event={"event_id":"e0","start_frame_id":"old_start","end_frame_id":"old_anchor","obligation_ids":["o0"]}
    intent={"obligations":[{"obligation_id":"o0","support":"mixed"}]}
    observe={"action":"observe","source_start_s":1.9666666666666666,"source_end_s":2.4}
    projected={"event_id":"e0","anchor_frame_id":"old_anchor","obligation_ids":["o0"],"action":"confirm",
               "cut_intent":"existing_shot_boundary","question":"","resolution":"The actual adjacent image supplies new evidence.",
               "source_start_s":None,"source_end_s":None,"confirmed_frame_id":"candidate",
               "evidence_frame_ids":["old_anchor","candidate","new_neighbor"],"blocking_questions":[],
               "limitations":["Model evidence remains fallible and requires independent neighbor confirmation."]}
    raw={**projected,"source_start_s":observe["source_start_s"],"source_end_s":observe["source_end_s"]}
    original={**raw,"source_start_s":0,"source_end_s":0}
    values=[(222,"glm_222_mc2_intent","mc2_intent",intent),
            (229,"glm_229_mc2_anchors","mc2_anchors",{"events":[event]}),
            (231,"glm_231_mc2_edge_1","mc2_edge_1",{"status":"blocked","anchor_frame_id":"old_anchor","blocking_questions":["Need new neighboring images."]}),
            (232,meta.PRIOR_ID,"mc2_edge_1_nav_0",observe),
            (233,"glm_233_mc2_edge_1_view_0_0","mc2_edge_1_view_0_0",{"frames":[{"frame_id":r["frame_id"]} for r in rows]}),
            (237,meta.ORIGINAL_ID,meta.STEM,original),(238,meta.CALL_ID,meta.STEM+"_repair",raw)]
    for n,ident,name,value in values:
        old_folder=root / "calls" / data["calls"][n-1]["id"]
        old_folder.rmdir()
        folder=root / "calls" / ident
        content=json.dumps(value,ensure_ascii=False)
        if n==237:
            content="```json\n"+content+"\n```"
        response={"status":"complete","result":{"content":[{"type":"text","text":content}]}}
        request={"tool":"analyze_image","arguments":{"prompt":name},"media_sha256":"a"*64}
        write_json(folder / "request.json",request)
        write_json(folder / "response.json",response)
        data["calls"][n-1]={"id":ident,"name":name,"status":"received","request_sha256":json_sha(request),
                             "response_sha256":json_sha(response),"repair_of":meta.ORIGINAL_ID if n==238 else None,"usage":{}}
        if n in (237,238):
            write_json(folder / "protocol_failure.json",{"error":meta.ERROR,"attempt":int(n==238),"model_text":content})
        else:
            write_json(folder / "parsed.json",value)
    manifest=root / "received_manifest.json"
    write_json(manifest,{"frames":rows})
    descriptor={"tool":"analyze_image","lineage_path":str(manifest),"observation_scope":{"source_sha256":source_sha}}
    descriptor_file=root / "descriptor.json"
    write_json(descriptor_file,descriptor)
    data["artifacts"]["mc2_input_mc2_edge_1_view_0_0"]=[{"path":str(descriptor_file),"sha256":json_sha(descriptor)}]
    plan={"decision_call_id":meta.PRIOR_ID,"decision_stage":"mc2_edge_1_nav_0",**observe,
          "pages":[{"manifest_path":str(manifest)}],"new_frame_ids":["candidate","new_neighbor"]}
    plan_file=root / "page_plan.json"
    write_json(plan_file,plan)
    data["artifacts"]["mc2_boundary_page_plan_1_0"]=[{"path":str(plan_file),"sha256":json_sha(plan)}]
    boundary_value={"policy":boundary.POLICY,"baseline_request_count":231,"base_authorization":deepcopy(auth),"protected_files":[]}
    boundary_file=root / "boundary.json"
    write_json(boundary_file,boundary_value)
    data["artifacts"][boundary.ARTIFACT]=[{"path":str(boundary_file),"sha256":json_sha(boundary_value)}]
    auth["boundary_resume"]=boundary_value
    result_file=Path(auth["execution_directory"]) / "result_boundary_recovered.json"
    write_json(result_file,{"status":"stopped","error":"model_protocol_repair_exhausted:"+meta.STEM,
                            "final_video":None,"model_quality_gate_passed":False})
    receipt_file=root / "stopped_receipt.json"
    receipt={"result_path":str(result_file)}
    write_json(receipt_file,receipt)
    data["artifacts"]["mc2_boundary_result"]=[{"path":str(receipt_file),"sha256":json_sha(receipt)}]
    base._save()
    monkeypatch.setattr(boundary,"load_boundary_auth",lambda *a,**kw: auth)
    return root,auth,raw,projected


def node(root, auth, expression):
    if not shutil.which("node"):
        pytest.skip("Node required")
    file=(Path(meta.__file__).parent / "mcp_microclip_boundary_metadata.mjs").as_uri()
    code=f"import * as m from {json.dumps(file)};const root={json.dumps(str(root))};const s=JSON.parse((await import('node:fs')).readFileSync(root+'/library_state.json','utf8'));const b=JSON.parse((await import('node:fs')).readFileSync(s.artifacts.mc2_boundary_navigation_resume[0].path,'utf8'));const p=b.base_authorization;{expression}"
    return subprocess.run(["node","--input-type=module","-e",code],capture_output=True,text=True)


def register(known):
    root,auth,_,_=known
    meta.record_metadata_resume(root,"Continue the same trial after a CPU-only inactive-range compatibility fix.")
    data=read(root / "library_state.json")
    proof=meta.load_metadata_resume(root,data,auth,force=True)
    auth["boundary_metadata_resume"]=proof
    return root,data,auth,proof


def test_exact_projection_preserves_every_semantic_field_and_failed_original(known):
    root,data,auth,proof=register(known)
    raw,projected=known[2:]
    assert proof["projected_value"]==projected
    assert {k:v for k,v in raw.items() if k not in {"source_start_s","source_end_s"}}=={k:v for k,v in projected.items() if k not in {"source_start_s","source_end_s"}}
    assert proof["prior_observation_context"]["source_start_s"]==raw["source_start_s"]
    assert proof["next_stage"]==meta.NEXT_STAGE and proof["new_renders"]==0
    assert not (root / "calls" / meta.CALL_ID / "parsed.json").exists()
    assert not (root / "calls" / meta.ORIGINAL_ID / "parsed.json").exists()
    result=node(root,auth,"console.log(JSON.stringify(m.loadBoundaryMetadata(root,s,p,b).projected_value))")
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)==projected


@pytest.mark.parametrize("change",["action","range","frame","obligation","blocker","failure","repair","unreceived_page"])
def test_other_semantic_or_protocol_failures_cannot_use_compatibility(known,change):
    root,auth,_,_=known
    data=read(root / "library_state.json")
    folder=root / "calls" / meta.CALL_ID
    response=read(folder / "response.json")
    value=json.loads(response["result"]["content"][0]["text"])
    if change=="action":value["action"]="blocked"
    elif change=="range":value["source_end_s"]=2.5
    elif change=="frame":value["confirmed_frame_id"]="unseen"
    elif change=="obligation":value["obligation_ids"]=[]
    elif change=="blocker":value["blocking_questions"]=["Still unresolved"]
    elif change=="repair":data["calls"][237]["repair_of"]="another_call"
    elif change=="unreceived_page":data["calls"][232]["status"]="submitted"
    if change not in {"repair","unreceived_page"}:
        text=json.dumps(value,ensure_ascii=False)
        response["result"]["content"][0]["text"]=text
        write_json(folder / "response.json",response)
        data["calls"][237]["response_sha256"]=json_sha(response)
        write_json(folder / "protocol_failure.json",{"error":"other_contract" if change=="failure" else meta.ERROR,"attempt":1,"model_text":text})
    write_json(root / "library_state.json",data)
    with pytest.raises((LibraryStopped,ValueError)):
        meta.record_metadata_resume(root,"Continue")
    assert not read(root / "library_state.json")["artifacts"].get(meta.ARTIFACT)


@pytest.mark.parametrize("change",["file","projection","old_result","prefix"])
def test_resume_proof_rejects_mutated_history_in_both_languages(known,change):
    root,data,auth,proof=register(known)
    if change=="file":
        (root / "calls" / meta.CALL_ID / "protocol_failure.json").write_text("{}")
    elif change=="old_result":
        (Path(auth["execution_directory"]) / "result_boundary_recovered.json").write_text("{}")
    elif change=="prefix":
        data["calls"][237]["usage"]={"fabricated":0}
        write_json(root / "library_state.json",data)
    else:
        row=data["artifacts"][meta.ARTIFACT][0]
        proof["projected_value"]["resolution"]="Another creative answer"
        write_json(row["path"],proof)
        row["sha256"]=json_sha(proof)
        write_json(root / "library_state.json",data)
    with pytest.raises(LibraryStopped):
        meta.load_metadata_resume(root,read(root / "library_state.json"),auth,force=True)
    result=node(root,auth,"m.loadBoundaryMetadata(root,s,p,b)")
    assert result.returncode!=0


def test_projection_is_only_for_exact_stem_and_does_not_make_boundary_effective(known):
    root,data,auth,proof=register(known)
    monkey=meta.received_projection(root,data,meta.STEM)
    assert monkey[0]["id"]==meta.CALL_ID and monkey[1]==proof["projected_value"]
    assert meta.received_projection(root,data,"mc2_edge_1_confirm_1") is None
    assert not data["artifacts"].get("mc2_effective_edge_1")
    result=node(root,auth,"console.log(JSON.stringify(m.metadataReceived(root,s,'mc2_edge_1_nav_1')))")
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)==proof["projected_value"]


def synthetic_state(known, monkeypatch):
    root,data,auth,proof=register(known)
    state=object.__new__(mc.SlotMicroclipState)
    state.output,state.path,state.lock_path=root,root / "library_state.json",root / ".state.lock"
    state.data,state.authorization=data,auth
    auth.update(infrastructure_resume=None,known_failure_resume=None,known_failure_dispatch_resume=None,base_request_limit=80)
    monkeypatch.setattr(state,"assert_protected",lambda: auth)
    monkeypatch.setattr(state,"_check_input",lambda *a: None)
    monkeypatch.setattr(state,"_predecessor",lambda *a: None)
    monkeypatch.setattr(boundary,"predecessor",lambda *a: None)
    return state


def test_state_reads_derived_238_and_forbids_third_nav_or_other_next_stage(known,monkeypatch):
    state=synthetic_state(known,monkeypatch)
    assert state._received(meta.STEM)[0]["id"]==meta.CALL_ID
    assert state._received(meta.STEM)[1]["source_start_s"] is None
    with pytest.raises(LibraryStopped,match="finished_or_repeated_stage"):
        state.begin_call(meta.STEM,{})
    with pytest.raises(LibraryStopped,match="boundary_metadata_next_stage_required"):
        state.begin_call("mc2_plan",{})
    request={"tool":"analyze_image","arguments":{"prompt":"Actual new candidate neighbors"},"media_sha256":"b"*64}
    call,_=state.begin_call(meta.NEXT_STAGE,request)
    assert call["id"]=="glm_239_mc2_edge_1_confirm_1"
    assert state.data["calls"][237]["id"]==meta.CALL_ID
    assert not (state.output / "calls" / meta.CALL_ID / "parsed.json").exists()


def test_state_original_238_and_prior_calls_are_frozen_after_projection(known,monkeypatch):
    state=synthetic_state(known,monkeypatch)
    for index in (231,236,237):
        with pytest.raises(LibraryStopped,match="historical_call_read_only"):
            state._new(state.data["calls"][index])


def test_metadata_result_preserves_old_stop_and_prevents_another_trial(known,monkeypatch):
    state=synthetic_state(known,monkeypatch)
    old=Path(state.authorization["execution_directory"]) / "result_boundary_recovered.json"
    before=old.read_bytes()
    result=state.finish({"status":"stopped","error":"New semantic limit", "model_quality_gate_passed":False})
    assert result.name=="result_boundary_metadata_recovered.json" and old.read_bytes()==before
    assert state.data["artifacts"].get("mc2_boundary_metadata_result")
    with pytest.raises(LibraryStopped,match="finished_or_repeated_stage"):
        state.begin_call(meta.NEXT_STAGE,{})
