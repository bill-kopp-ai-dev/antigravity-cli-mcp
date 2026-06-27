#!/usr/bin/env python3
"""Direct JSON-RPC client for MCP servers (proven pattern from diagnostic).

Bypasses the Trae/Claude Code wrapper which has a known args-serialization
bug. Spawns a fresh server subprocess, sends raw JSON-RPC, prints result.

Usage:
    python3 tools/mcp_direct.py agy_run_task /tmp "echo hello"
    python3 tools/mcp_direct.py claude_run_task /tmp "echo hello"
"""
import json
import os
import subprocess
import sys


def call(server, tool, arguments):
    if server == "agy":
        fastmcp = "/home/bill/.cache/uv/archive-v0/VcjsdVWQjiVkp2o6/bin/fastmcp"
        cwd = "/home/bill/Codes/CLI-router-project/antigravity-cli-mcp"
        module = "src/agy_mcp_server/server.py"
        prefix = "AGY_MCP_"
    else:
        fastmcp = "/home/bill/.cache/uv/archive-v0/AyjWFUmqg4Vz7Q8x/bin/fastmcp"
        cwd = "/home/bill/Codes/CLI-router-project/claude-code-cli-mcp"
        module = "src/claude_code_mcp/server.py"
        prefix = "CLAUDE_MCP_"

    env = os.environ.copy()
    env[f"{prefix}ALLOWED_ROOTS"] = '["/"]'
    env[f"{prefix}MODE"] = "safe"
    env[f"{prefix}FORCE_SANDBOX_IN_SAFE_MODE"] = "true"

    proc = subprocess.Popen(
        [fastmcp, "run", module],
        cwd=cwd, env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=0,
    )

    def send(req):
        proc.stdin.write(json.dumps(req) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        return json.loads(line) if line else None

    init_resp = send({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                   "clientInfo": {"name": "mcp-direct", "version": "0.1"}}
    })
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
    proc.stdin.flush()

    r = send({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
              "params": {"name": tool, "arguments": arguments}})

    proc.terminate()
    try: proc.wait(timeout=3)
    except: proc.kill()

    return init_resp, r


def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    tool = sys.argv[1]
    server = "agy" if tool.startswith("agy_") else "claude"

    if "--json" in sys.argv:
        idx = sys.argv.index("--json")
        arguments = json.loads(sys.argv[idx + 1])
    elif len(sys.argv) >= 4:
        arguments = {"req": {"workspace_path": sys.argv[2], "prompt": sys.argv[3]}}
    else:
        arguments = {"req": {"workspace_path": "/tmp", "prompt": "echo hello from mcp_direct"}}

    print(f"[mcp-direct] {server}.{tool} args={json.dumps(arguments)}", file=sys.stderr)
    init, r = call(server, tool, arguments)
    if init:
        print(f"[mcp-direct] init: {init.get('result',{}).get('serverInfo',{}).get('version','?')}", file=sys.stderr)
    if r is None:
        print("NO RESPONSE"); sys.exit(2)
    if "error" in r:
        print(json.dumps(r["error"], indent=2)); sys.exit(1)
    content = r.get("result", {}).get("content", [{}])[0].get("text", "")
    print(content)


if __name__ == "__main__":
    main()