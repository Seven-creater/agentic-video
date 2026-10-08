"""Lexeme-preserving, immutable JSON tree reuse for the active v2 process."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest


def node(tmp_path, source, code, *, enabled=True):
    if not shutil.which("node"):
        pytest.skip("Node required")
    file = tmp_path / "input.json"
    file.write_text(source, encoding="utf-8")
    module = (Path(__file__).resolve().parents[1] / "omni_story/library/mcp_forward_slot_guard.mjs").as_uri()
    setup = "process.env.OMNI_LIBRARY_MICROCLIP_V2_AUTH_FILE='synthetic-enable';" if enabled else "delete process.env.OMNI_LIBRARY_MICROCLIP_V2_AUTH_FILE;"
    script = f"import fs from 'node:fs';import assert from 'node:assert/strict';import {{jsonHash}} from {json.dumps(module)};" \
        f"const file={json.dumps(str(file))};{setup}{code}"
    result = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_cached_hash_preserves_numeric_literal_lexemes(tmp_path):
    values = {"integer": "0", "decimal": "0.0", "negative": "-0", "exponent": "1e3", "tiny": "1e-08"}
    expected = {key: hashlib.sha256(value.encode()).hexdigest() for key, value in values.items()}
    source = "{" + ",".join(json.dumps(key) + ":" + value for key, value in values.items()) + "}"
    node(tmp_path, source, f"const expected={json.dumps(expected)};for(const key of Object.keys(expected))assert.equal(jsonHash(file,v=>v[key]),expected[key]);")


def test_repeated_proofs_parse_once_but_each_captured_selector_runs(tmp_path):
    node(tmp_path, '{"items":{"a":0,"b":0.0}}', """
        const original=JSON.parse;let parses=0,selects=0;
        JSON.parse=(...args)=>{parses++;return original(...args);};
        const selector=key=>v=>{selects++;return v.items[key];};
        const first=jsonHash(file,selector('a')),second=jsonHash(file,selector('b'));
        assert.notEqual(first,second);assert.equal(jsonHash(file,selector('a')),first);
        assert.equal(parses,1);assert.equal(selects,3);
    """)


def test_same_size_mutation_with_restored_mtime_invalidates_ctime_proof(tmp_path):
    node(tmp_path, '{"value":1}', """
        fs.utimesSync(file,1700000000,1700000000);
        const original=JSON.parse;let parses=0;JSON.parse=(...args)=>{parses++;return original(...args);};
        const before=fs.statSync(file,{bigint:true}),first=jsonHash(file);
        Atomics.wait(new Int32Array(new SharedArrayBuffer(4)),0,0,10);
        fs.writeFileSync(file,'{"value":2}');fs.utimesSync(file,1700000000,1700000000);
        const after=fs.statSync(file,{bigint:true});
        assert.equal(after.size,before.size);assert.equal(after.mtimeNs,before.mtimeNs);
        assert.notEqual(after.ctimeNs,before.ctimeNs);
        assert.notEqual(jsonHash(file),first);assert.equal(parses,2);
    """)


def test_selector_cannot_mutate_nested_cached_objects_arrays_or_numeric_tokens(tmp_path):
    node(tmp_path, '{"nested":{"text":"safe"},"rows":[1]}', """
        const original=JSON.parse;let parses=0;JSON.parse=(...args)=>{parses++;return original(...args);};
        const before=jsonHash(file);
        assert.throws(()=>jsonHash(file,v=>{v.nested.text='changed';return v;}),TypeError);
        assert.throws(()=>jsonHash(file,v=>{v.rows.push(2);return v;}),TypeError);
        assert.throws(()=>jsonHash(file,v=>{v.rows[0].token='99';return v;}),TypeError);
        jsonHash(file,v=>{assert.ok(Object.isFrozen(v));assert.ok(Object.isFrozen(v.nested));
            assert.ok(Object.isFrozen(v.rows));assert.ok(Object.isFrozen(v.rows[0]));return v;});
        assert.equal(jsonHash(file),before);assert.equal(parses,1);
    """)


@pytest.mark.parametrize("when", ["parse", "cached_selector"])
def test_file_changed_during_proof_never_returns_a_cached_hash(tmp_path, when):
    code = """
        const original=JSON.parse;let changed=false;
        JSON.parse=(...args)=>{const value=original(...args);if(!changed){changed=true;
            fs.writeFileSync(file,'{"value":22}');}return value;};
        assert.throws(()=>jsonHash(file),/json_changed_during_proof/);
    """ if when == "parse" else """
        jsonHash(file);
        assert.throws(()=>jsonHash(file,v=>{fs.writeFileSync(file,'{"value":22}');return v;}),/json_changed_during_proof/);
    """
    node(tmp_path, '{"value":1}', code)


def test_frozen_routes_do_not_enable_the_new_tree_cache(tmp_path):
    node(tmp_path, '{"value":1}', """
        const original=JSON.parse;let parses=0;JSON.parse=(...args)=>{parses++;return original(...args);};
        assert.equal(jsonHash(file),jsonHash(file));assert.equal(parses,2);
    """, enabled=False)
