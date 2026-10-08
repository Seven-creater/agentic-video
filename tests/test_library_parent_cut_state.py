"""Synthetic direct-parent guards: no network or real ledger mutations."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from test_library_forward_slot_budget import prepared as old_prepared
from omni_story.library import forward_slot_budget as forward, parent_cut_state as pc
from omni_story.library.media import sha256_file
from omni_story.library.state import LibraryStopped, json_sha, write_json


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


@pytest.fixture
def registered(old_prepared, monkeypatch):
    root, source, grant = old_prepared
    data = read(root / "library_state.json")
    for i in range(191,194):
        ident = f"old_{i+1:03d}"
        req, reply = {"old":i}, {"old_reply":i}
        write_json(root / "calls" / ident / "request.json", req)
        write_json(root / "calls" / ident / "response.json", reply)
        data["calls"].append({"id":ident,"name":"old_frozen","status":"received",
            "request_sha256":json_sha(req),"response_sha256":json_sha(reply),"repair_of":None,"usage":{}})
    data["request_count"] = 194
    write_json(root / "library_state.json",data)
    parents=[]
    for p in (0,3):
        path=root/f"render_{p}"/"final.mp4"
        path.parent.mkdir(parents=True)
        path.write_bytes(f"synthetic parent {p}".encode())
        parent={"round":p,"path":str(path),"sha256":sha256_file(path),"duration_s":12.0}
        parents.append(parent)
        write_json(Path(grant["execution_directory"])/f"render_{p}"/"outline.json",{
            "parent_sha256":parent["sha256"],"slots":[{"slot_id":"s1","start_s":0.0,"end_s":6.0},
                {"slot_id":"s2","start_s":6.0,"end_s":12.0}]})
    write_json(grant["preparation_path"],{"parents":parents})
    unknown=[]
    for call in data["calls"]:
        if call["status"]=="uncertain":
            req=read(root/"calls"/call["id"]/"request.json")
            unknown.append({"call_id":call["id"],"request_sha256":call["request_sha256"],
                "media_sha256":req["media_sha256"],"scope":req["observation_scope"]})
    old={**grant,"original_authorization_path":grant["authorization_path"],
        "original_authorization_sha256":grant["authorization_sha256"],
        "old_execution_directory":grant["execution_directory"],"unknown_inputs":unknown,"protected_files":[]}
    monkeypatch.setattr(forward,"get_auth",lambda path:old)
    auth=pc.record_authorization(root,"Use actual parent slots directly; do not reconstruct the movie library.")
    return root,auth


def request(root,auth,parent=0,index=0,prompt="synthetic task"):
    source=next(p for p in auth["parents"] if p["round"]==parent)
    slot=read(auth["outline_paths"][str(parent)])["slots"][index]
    folder=root/"new_media"/f"{parent}_{index}"
    folder.mkdir(parents=True,exist_ok=True)
    media=folder/"window.mp4"
    media.write_bytes(f"synthetic actual crop {parent}/{index}".encode())
    scope={"kind":"continuous_window","source_sha256":source["sha256"],
        "source_start_s":slot["start_s"],"source_end_s":slot["end_s"]}
    write_json(folder/"lineage.json",{**scope,"path":str(media),"source_path":source["path"],"sha256":sha256_file(media)})
    return f"pc_{parent}_slot_{json_sha(slot)[:16]}",{"provider":"official_vision_mcp_in_codex","tool":"analyze_video",
        "arguments":{"video_source":str(media),"prompt":prompt},"media_sha256":sha256_file(media),"observation_scope":scope}


def complete(state,call,value):
    state.complete_call(call,{"result":{"content":[{"type":"text","text":json.dumps(value)}]}})
    write_json(state.output/"calls"/call["id"]/"parsed.json",value)


def node(root,auth,code):
    if not shutil.which("node"):
        pytest.skip("Node required")
    module=(Path(pc.__file__).parent/"mcp_parent_cut_guard.mjs").as_uri()
    env={"OMNI_LIBRARY_PARENT_CUT_AUTH_FILE":auth["authorization_path"],"OMNI_LIBRARY_PARENT_CUT_AUTH_SHA256":auth["authorization_sha256"]}
    script=f"import * as g from {json.dumps(module)};const root={json.dumps(str(root))},env={json.dumps(env)};{code}"
    return subprocess.run(["node","--input-type=module","-e",script],capture_output=True,text=True)


def test_two_lanes_append_global_count_and_block_same_parent(registered):
    root,auth=registered
    s=pc.ParentCutState(root)
    name,req=request(root,auth)
    first,_=s.begin_call(name,req)
    assert first["id"].startswith("glm_195_")
    with pytest.raises(LibraryStopped,match="own_pending"):
        s.begin_call(*request(root,auth,index=1))
    other=pc.ParentCutState(root)
    second,_=other.begin_call(*request(root,auth,parent=3))
    assert second["id"].startswith("glm_196_")
    assert read(root/"library_state.json")["max_requests"]==80
    assert node(root,auth,"console.log(g.loadParentCut(root,env).state.calls.length)").stdout.strip()=="196"


def test_one_repair_and_known_exhaustion_no_third_stage(registered):
    root,auth=registered
    s=pc.ParentCutState(root)
    name,req=request(root,auth)
    call,_=s.begin_call(name,req)
    s.complete_call(call,{"result":{"content":[{"type":"text","text":"invalid"}]}})
    repair,_=s.begin_call(name+"_repair",{**req,"arguments":{**req["arguments"],"prompt":"repair only"}},repair_of=call)
    s.complete_call(repair,{"result":{"content":[{"type":"text","text":"invalid again"}]}})
    with pytest.raises(LibraryStopped,match="repeated_stage"):
        s.begin_call(name+"_repair",req,repair_of=call)
    with pytest.raises(LibraryStopped,match="predecessor_not_parsed"):
        s.begin_call(*request(root,auth,index=1))


def test_new_unknown_halts_both_lanes_and_old_calls_read_only(registered):
    root,auth=registered
    s=pc.ParentCutState(root)
    call,_=s.begin_call(*request(root,auth))
    s.fail_call(call,"synthetic lost result")
    with pytest.raises(LibraryStopped,match="unknown_or_own_pending"):
        s.begin_call(*request(root,auth,parent=3))
    with pytest.raises(LibraryStopped,match="historical_call_read_only"):
        s.fail_call(s.data["calls"][0],"cannot edit history")


@pytest.mark.parametrize("change",["old_reply","old_call_add","journal_prefix","old_artifact"])
def test_history_byte_or_structure_mutations_rejected(registered,change):
    root,auth=registered
    if change=="old_reply":
        (root/"calls"/"old_001"/"response.json").write_text("{}")
    elif change=="old_call_add":
        (root/"calls"/"old_001"/"new.json").write_text("{}")
    elif change=="journal_prefix":
        (root/"mcp_http_sf_3.jsonl").write_text("changed")
    else:
        state=read(root/"library_state.json")
        state["artifacts"]["slot_finecut_comparison_v1"]=[]
        write_json(root/"library_state.json",state)
    with pytest.raises(LibraryStopped):
        pc.get_auth(root,force=True)
    assert node(root,auth,"g.loadParentCut(root,env)").returncode!=0


def test_journal_append_allowed_and_old_frozen_queue_skipped(registered):
    root,auth=registered
    with (root/"mcp_http_sf_3.jsonl").open("ab") as f:
        f.write(b'{"new":"legitimate"}\n')
    pc.get_auth(root,force=True)
    result=node(root,auth,"const skip=g.parentCutFrozenSlotJob(root,env);console.log(skip(root+'/mcp_queue/glm_166_sf_3_source_feedback_replan_v1.request.json'));")
    assert result.returncode==0 and result.stdout.strip()=="true"


@pytest.mark.parametrize("mutation",["movie_scope","unknown_media","false_crop","made_up_slot"])
def test_forbidden_or_unbound_actual_input_rejected(registered,mutation):
    root,auth=registered
    s=pc.ParentCutState(root)
    name,req=request(root,auth)
    if mutation=="movie_scope":
        req["observation_scope"]["source_sha256"]="b"*64
    elif mutation=="unknown_media":
        old=read(root/"calls"/auth["unknown_inputs"][0]["call_id"]/"request.json")
        req=deepcopy(old)
        req["arguments"]["prompt"]="still forbidden"
    elif mutation=="false_crop":
        lineage=Path(req["arguments"]["video_source"]).parent/"lineage.json"
        value=read(lineage);value["sha256"]="c"*64;write_json(lineage,value)
    else:
        name="pc_0_slot_"+"0"*16
    with pytest.raises(LibraryStopped):
        s.begin_call(name,req)


def test_paid_guard_accepts_exact_queue_and_blocks_changed_arguments(registered):
    root,auth=registered
    s=pc.ParentCutState(root)
    name,req=request(root,auth)
    call,_=s.begin_call(name,req)
    queued={"job_id":call["id"],"tool":req["tool"],"arguments":req["arguments"]}
    queue=root/"mcp_queue"/(call["id"]+".request.json")
    write_json(queue,queued)
    result=node(root,auth,f"console.log(g.parentCutRequestLimit(root,{{job_id:{json.dumps(call['id'])}}},env))")
    assert result.returncode==0 and result.stdout.strip()=="Infinity"
    queued["arguments"]["prompt"]="changed after ledger"
    write_json(queue,queued)
    assert node(root,auth,f"g.parentCutRequestLimit(root,{{job_id:{json.dumps(call['id'])}}},env)").returncode!=0


def test_model_cut_binding_actual_output_proxy_and_partial_result(registered):
    from omni_story.library.parent_timeline_cut import build_plan
    root,auth=registered
    s=pc.ParentCutState(root)
    outline=read(auth["outline_paths"]["0"])
    parent=auth["parents"][0]
    source={"source_id":"src_"+parent["sha256"][:16],"path":parent["path"],"sha256":parent["sha256"],"duration_s":12.0}
    folder=Path(auth["execution_directory"])/"render_0"
    write_json(folder/"parent_catalog/inventory.json",{"sources":[source]})
    cuts=[]
    for i,slot in enumerate(outline["slots"]):
        call,_=s.begin_call(*request(root,auth,index=i))
        value={"slot_id":slot["slot_id"],"shots":[{"in_s":0.0,"out_s":2.0,"speed":.5 if i==0 else 2.0,"hold_s":0,
            "reason":"synthetic model-selected operation","visible_content":"synthetic visible event"}],
            "preserved_meaning":"synthetic model-selected meaning","limitations":[]}
        complete(s,call,value)
        cuts.append(value)
    plan=build_plan(source,outline,cuts)
    bad=deepcopy(plan);bad["segments"][0]["speed"]=1.0
    with pytest.raises(LibraryStopped,match="differs_from_model"):
        s.claim_render(0,bad)
    directory=s.claim_render(0,plan)
    assert not directory.exists()
    directory.mkdir()
    final=directory/"final.mp4"
    final.write_bytes(b"synthetic actual output")
    sha=sha256_file(final)
    write_json(directory/"render_result.json",{"sha256":sha,"measured_duration_s":5.0})
    proxy=root/"new_output_proxy/video.mp4"
    proxy.parent.mkdir();proxy.write_bytes(b"synthetic continuous full-output proxy")
    scope={"kind":"continuous_window","source_sha256":sha,"source_start_s":0.0,"source_end_s":5.0}
    write_json(proxy.parent/"lineage.json",{**scope,"sha256":sha256_file(proxy),"path":str(proxy),"source_path":str(final)})
    req={"provider":"official_vision_mcp_in_codex","tool":"analyze_video","arguments":{"video_source":str(proxy),"prompt":"independent review"},
        "media_sha256":sha256_file(proxy),"observation_scope":scope}
    partial={"status":"partial","slots":[{"status":"partial"}],"limitations":["synthetic model limitation"]}
    for kind in ("blind","review"):
        call,_=s.begin_call(f"pc_0_{kind}",{**req,"arguments":{**req["arguments"],"prompt":kind}})
        write_json(root/"mcp_queue"/(call["id"]+".request.json"),{"job_id":call["id"],"tool":req["tool"],"arguments":{"video_source":str(proxy),"prompt":kind}})
        result=node(root,auth,f"console.log(g.parentCutRequestLimit(root,{{job_id:{json.dumps(call['id'])}}},env))")
        assert result.returncode==0 and result.stdout.strip()=="Infinity"
        complete(s,call,partial)
    with pytest.raises(LibraryStopped,match="model_review_did_not_pass"):
        s.finish(0,{"status":"pass","model_quality_gate_passed":True})
    result=s.finish(0,{"status":"candidate_with_limitations","model_quality_gate_passed":False})
    assert read(result)["status"]=="candidate_with_limitations"
    final.write_bytes(b"modified result")
    with pytest.raises(LibraryStopped,match="completed_bytes_changed"):
        s.assert_protected()
