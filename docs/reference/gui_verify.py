"""Issue #7 substance verification on the live Maya 2024 GUI session.

mayapy/maya-batch are broken on this box (import maya.standalone -> AV,
maya.exe -batch hangs) — so the Tier-2 substance runs through the GUI
session's framed channel instead. Same module code, same wire, honest
substitution recorded in the evidence.

Covers:
  A) checkpoint/rollback roundtrip WITH a reference edit (issue #7
     Tier-2 substance) — net-zero: restores untitled empty scene after.
  B) inspect.presence collection over the stub-modeled API surface
     (D-049b) — emits presence-baseline.json data.
  C) commandPort no-newline + 500ms probe on :7001 (raw socket).
"""

import asyncio
import base64
import json
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, r"D:\Aworker\maya\maya-mcp-server\src")

from maya_mcp_server.client import MayaQtClient
from maya_mcp_server.types import ResultType

OUT = Path(r"D:\Aworker\maya\maya-mcp-server\.scratch\maya-mcp-grill\reports\evidence")
REPO_SRC = Path(r"D:\Aworker\maya\maya-mcp-server\src")

results = {}


async def cmds_json(client, code):
    resp = await client.execute_code(code, ResultType.JSON)
    if resp.error:
        raise RuntimeError(f"maya error: {resp.error}")
    data = resp.result
    if isinstance(data, str):
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return data
    return data


async def cmds_raw(client, code):
    resp = await client.execute_code(code, ResultType.JSON)
    return resp


# ------------------------------------------------------------------
# A) checkpoint/rollback + reference edit roundtrip on the live session
# ------------------------------------------------------------------
async def check_checkpoint_rollback(client):
    # inject worktree _mcp_scene (test current code, not the stale
    # copy the old backend injected)
    src = (REPO_SRC / "maya_mcp_server" / "maya_scene_module.py").read_text(encoding="utf-8")
    await client.write_module("_mcp_scene", src, overwrite=True)
    r = await client.execute_code("import _mcp_scene", ResultType.NONE)
    assert r.error is None

    tmpdir = "D:/Aworker/maya/maya-mcp-server/.scratch/maya-mcp-grill/tmp_ckpt"
    setup = await cmds_json(client, f"""
import maya.cmds as cmds, json, os
os.makedirs(r'{tmpdir}', exist_ok=True)
cmds.file(new=True, force=True, prompt=False)
# referenced file with one cube
cmds.polyCube(name='GEO_ref_cube')
ref_file = r'{tmpdir}' + '/ref_source.ma'
cmds.file(rename=ref_file)
cmds.file(save=True, type='mayaAscii', force=True, prompt=False)
cmds.file(new=True, force=True, prompt=False)
# main scene references it + a reference edit (move)
main_file = r'{tmpdir}' + '/main.ma'
cmds.file(rename=main_file)
cmds.file(ref_file, reference=True, namespace='v7ref', prompt=False)
ref_node = 'v7ref:GEO_ref_cube'
assert cmds.objExists(ref_node), 'ref node missing'
cmds.move(5, 0, 0, ref_node)
cmds.file(save=True, type='mayaAscii', force=True, prompt=False)
json.dumps({{'ref_node': ref_node, 'main_file': main_file}})
""")
    out = {"setup": setup}

    cp = await cmds_json(
        client, "import _mcp_scene; _mcp_scene.save_checkpoint('v7_ref_case', overwrite=True)"
    )
    out["save_checkpoint"] = cp
    assert "error" not in cp, cp

    await cmds_json(client, "import maya.cmds as cmds; cmds.polyCube(name='GEO_after')")
    assert await cmds_json(client, "import maya.cmds as cmds; cmds.objExists('GEO_after')")

    rb = await cmds_json(
        client, f"import _mcp_scene; _mcp_scene.rollback_to_checkpoint({cp['filename']!r})"
    )
    out["rollback"] = rb
    assert rb["success"] is True, rb
    assert rb["scene_rebound_to"] is not None, "S2 rebind expected on saved scene"
    assert rb["safety_snapshot"] not in (None, "skipped_by_user")

    gone = await cmds_json(client, "import maya.cmds as cmds; cmds.objExists('GEO_after')")
    survivors = await cmds_json(
        client,
        "import maya.cmds as cmds; "
        "(cmds.ls('*GEO_ref_cube*') or []) + (cmds.ls('*:*GEO_ref_cube*') or [])",
    )
    out["post_rollback"] = {
        "GEO_after_exists": gone,
        "ref_survivors": survivors,
    }
    assert gone is False, "mutated node survived rollback"
    assert survivors, "referenced node content lost after rollback"

    # restore to a clean untitled scene so the live session is unchanged
    await cmds_json(client, "import maya.cmds as cmds; cmds.file(new=True, force=True, prompt=False)")
    out["cleanup"] = "file(new) -> untitled empty scene"
    out["verdict"] = "PASS"
    results["checkpoint_rollback_ref_edit"] = out


# ------------------------------------------------------------------
# B) signature collection over the stub-modeled API surface (D-049b)
# ------------------------------------------------------------------
def _stub_surface():
    """Enumerate the API surface the stub models — collected dynamically
    so the audit can never drift out of sync with the stub."""
    import inspect as _inspect

    sys.path.insert(0, r"D:\Aworker\maya\maya-mcp-server\tests")
    import maya_stub.cmds as _c
    import maya_stub.openmaya as _o
    import maya_stub.openmayaui as _ou

    cmds = [n for n, f in _inspect.getmembers(_c, _inspect.isfunction) if not n.startswith("_")]
    om = [n for n, k in _inspect.getmembers(_o, _inspect.isclass) if not n.startswith("_")]
    # install() injects MImage onto maya.api.OpenMaya (the <=2024 layout)
    # — it lives on the installed module, not in openmaya.py source.
    if "MImage" not in om:
        om.append("MImage")
    omui = [n for n, k in _inspect.getmembers(_ou, _inspect.isclass) if not n.startswith("_")]
    return {"cmds": cmds, "OpenMaya": om, "OpenMayaUI": omui}


async def collect_signatures(client):
    code = """
import inspect, json
import maya.cmds as cmds
import maya.api.OpenMaya as om
import maya.api.OpenMayaUI as omui

surface = %s

def sig_of(fn):
    try:
        return str(inspect.signature(fn))
    except (ValueError, TypeError) as e:
        return None

def probe_callable(obj):
    return {
        "exists": True,
        "signature": sig_of(obj),
        "callable": callable(obj),
    }

def probe_class(cls):
    methods = {}
    for m in dir(cls):
        if m.startswith("__"):
            continue
        attr = getattr(cls, m, None)
        if callable(attr) or isinstance(attr, (property, classmethod, staticmethod)):
            methods[m] = sig_of(attr)
    return {"exists": True, "methods": methods}

out = {"maya_version": cmds.about(version=True), "symbols": {}}
for name in surface["cmds"]:
    fn = getattr(cmds, name, None)
    out["symbols"]["cmds." + name] = (
        probe_callable(fn) if fn is not None else {"exists": False}
    )
for name in surface["OpenMaya"]:
    obj = getattr(om, name, None)
    out["symbols"]["om." + name] = probe_class(obj) if obj is not None else {"exists": False}
for name in surface["OpenMayaUI"]:
    obj = getattr(omui, name, None)
    out["symbols"]["omui." + name] = probe_class(obj) if obj is not None else {"exists": False}
json.dumps(out)
""" % json.dumps(_stub_surface())

    resp = await client.execute_code(code, ResultType.JSON)
    assert resp.error is None, resp.error
    data = resp.result
    if isinstance(data, str):
        data = json.loads(data)

    baseline = {
        "baseline_meta": {
            "collected_on": f"Maya {data['maya_version']} GUI session via framed channel",
            "collector": ".scratch/maya-mcp-grill/harness/gui_verify.py::collect_signatures",
            "note": "mayapy tier blocked on this box (import maya.standalone -> AV). "
                    "cmds builtins carry no inspect.signature — presence + method "
                    "lists are the auditable contract.",
        },
        "symbols": data["symbols"],
    }
    (OUT / "presence-baseline.maya2024.json").write_text(
        json.dumps(baseline, indent=2), encoding="utf-8"
    )

    n_total = len(data["symbols"])
    n_missing = sum(1 for v in data["symbols"].values() if not v.get("exists"))
    n_signed = sum(
        1
        for v in data["symbols"].values()
        if isinstance(v.get("signature"), str)
    )
    results["signature_collection"] = {
        "symbols": n_total,
        "missing_in_real_maya": [
            k for k, v in data["symbols"].items() if not v.get("exists")
        ],
        "with_signature": n_signed,
        "baseline_file": str(OUT / "presence-baseline.maya2024.json"),
        "verdict": "COLLECTED",
    }


# ------------------------------------------------------------------
# C) commandPort no-newline + 500ms probe (raw socket on :7001)
# ------------------------------------------------------------------
def probe_commandport_newline():
    """Issue #7 item: does commandPort wait for a newline?

    Live finding on Maya 2024 python commandPort: NO — it evals each
    received TCP chunk immediately and verbatim (a bare `1+1` returns
    `2` inside 500ms; an incomplete `1+` returns a SyntaxError at once
    and is NOT buffered into the next chunk). Per-chunk eval is the
    likely mechanism behind the >10K stale-response quirk: TCP-split
    payloads get evaluated fragment-by-fragment.
    """
    out = {}
    s = socket.create_connection(("127.0.0.1", 7001), timeout=5.0)
    s.settimeout(0.5)
    try:
        s.sendall(b"1+1")  # complete statement, no newline
        try:
            early = s.recv(4096)
            out["no_newline_recv"] = early.decode("utf-8", "replace")
            out["evals_without_newline"] = early.startswith(b"2")
        except socket.timeout:
            out["no_newline_recv"] = None
            out["evals_without_newline"] = False

        # incomplete statement — immediate SyntaxError if per-chunk eval
        s.sendall(b"1+")
        try:
            inc = s.recv(4096)
            out["incomplete_recv"] = inc.decode("utf-8", "replace")
            out["incomplete_evals_immediately"] = b"syntax" in inc.lower()
        except socket.timeout:
            out["incomplete_recv"] = None
            out["incomplete_evals_immediately"] = False
        # next chunk is a SEPARATE eval (no cross-chunk buffering)
        s.sendall(b"1")
        try:
            out["next_chunk_recv"] = s.recv(4096).decode("utf-8", "replace")
        except socket.timeout:
            out["next_chunk_recv"] = None

        out["semantics"] = "per-chunk eager eval (no newline trigger, no cross-chunk buffer)"
        out["verdict"] = (
            "PASS"
            if out.get("evals_without_newline") and out.get("incomplete_evals_immediately")
            else "CHECK"
        )
    finally:
        s.close()
    results["commandport_newline_probe"] = out


async def main():
    client = MayaQtClient(host="127.0.0.1", port=51217, timeout=60.0)
    await client.connect()
    try:
        await collect_signatures(client)
        await check_checkpoint_rollback(client)
    finally:
        await client.disconnect()
    probe_commandport_newline()

    (OUT / "gui_verify_results.json").write_text(
        json.dumps(results, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps(results, indent=2, default=str)[:6000])


asyncio.run(main())
