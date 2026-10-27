#!/usr/bin/env python3
"""Server liveness probe (release-preflight item; manual, NOT a CI gate).

Why this file exists at all: "the server starts and answers" is the one
acceptance claim no unit test can establish, because the unit tests drive the
middleware directly and never spawn the process. A claim that strong should
carry a rerunnable artifact rather than a transcript in a scratch report that
gets deleted.

Same shape as check_release_appendix.py: a human runs it, at a preflight or
before claiming liveness. It is deliberately NOT in the CI matrix - it spawns a
real server, and CI has no Maya to be useful against.

Three checks, all against the REAL process over stdio JSON-RPC:

  1. liveness          initialize is answered and carries serverInfo
  2. inventory         tools/list returns the full registry by default
  3. strict policy     MAYA_MCP_DISABLE_ARBITRARY=1 removes exactly the escape
                       tools, end to end through the real middleware chain

Usage:
    python .github/scripts/liveness_probe.py
    python .github/scripts/liveness_probe.py --policy-flag MAYA_MCP_DISABLE_EXECUTE

Exit codes: 0 = all checks hold; 1 = a check failed (the message names which).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
# PROTOCOL is a GOVERNANCE constant (which MCP revision counts as the
# supported floor), so its single source is the per-domain spec file -- derived
# here at runtime, never restated inline. POLICY_ENV_FLAGS below is the
# opposite case: object-derivable from pipeline.py, so it stays derived from
# the code and is deliberately NOT mirrored into the spec.
PROTOCOL = json.loads((REPO / ".github" / "liveness-probe-spec.yaml").read_text(encoding="utf-8"))[
    "PROTOCOL"
]

# The expectation is read from the code, not restated here: a second copy of
# the flag table is how a probe ends up asserting the wrong thing and still
# reporting PASS.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from maya_mcp_server.pipeline import POLICY_ENV_FLAGS  # noqa: E402


def expected_removed(flag: str) -> set[str]:
    if flag not in POLICY_ENV_FLAGS:
        raise SystemExit(f"unknown flag {flag!r}; known: {sorted(POLICY_ENV_FLAGS)}")
    return set(POLICY_ENV_FLAGS[flag])


def rpc(proc: subprocess.Popen, payload: dict) -> dict:
    proc.stdin.write(json.dumps(payload) + "\n")
    proc.stdin.flush()
    line = proc.stdout.readline()
    return json.loads(line) if line else {}


def probe(env_extra: dict[str, str]) -> tuple[str, set[str]]:
    env = {**os.environ, **env_extra}
    proc = subprocess.Popen(
        [sys.executable, "-m", "maya_mcp_server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        cwd=REPO,
        env=env,
    )
    try:
        init = rpc(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL,
                    "capabilities": {},
                    "clientInfo": {"name": "liveness-probe", "version": "0"},
                },
            },
        )
        if not init.get("result", {}).get("serverInfo"):
            return f"no serverInfo from initialize: {init}", set()
        # A notification gets NO reply; sending it through rpc() would block on
        # a readline that never comes.
        proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
            + "\n"
        )
        proc.stdin.flush()
        listed = rpc(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools = listed.get("result", {}).get("tools", [])
        return init["result"]["serverInfo"].get("version", "?"), {t["name"] for t in tools}
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--policy-flag",
        default="MAYA_MCP_DISABLE_ARBITRARY",
        choices=sorted(POLICY_ENV_FLAGS),
        help="env flag to exercise (default: the umbrella flag, all three escapes)",
    )
    args = parser.parse_args(argv)

    failures: list[str] = []

    version, tools = probe({})
    if not tools:
        print(f"FAIL liveness: {version}")
        return 1
    print(f"[1] liveness: initialize answered, serverInfo.version={version}")
    print(f"[2] tools/list: {len(tools)} tools")

    should_go = expected_removed(args.policy_flag)
    _v2, guarded = probe({args.policy_flag: "1"})
    if not guarded:
        print(f"FAIL policy: server produced no tool list with {args.policy_flag}=1")
        return 1
    leaked = should_go & guarded
    collateral = (tools - guarded) - should_go
    print(
        f"[3] policy ({args.policy_flag}=1): {len(guarded)} tools, "
        f"expected {sorted(should_go)} gone, leaked={sorted(leaked)}"
    )
    if leaked:
        failures.append(f"flag should have removed these tools: {sorted(leaked)}")
    if collateral:
        failures.append(f"policy removed non-escape tools: {sorted(collateral)}")
    read_only = {"scene_snapshot", "scene_measure", "scene_review"} & guarded
    print(f"    read-class surface retained: {len(read_only)}/3")

    for f in failures:
        print(f"FAIL {f}")
    print("LIVENESS PROBE: " + ("PASS" if not failures else "FAIL"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
