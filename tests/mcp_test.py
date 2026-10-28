#!/usr/bin/env python3
"""End-to-end test of `unienv mcp` speaking MCP over stdio, the way Claude Desktop does.

Starts the server with a minimal GUI-app PATH, then checks the handshake, the
tool list, every tool, the safety refusals, and that stdout carries nothing
but JSON-RPC. Exit code = number of failures.

    python3 tests/mcp_test.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

UE = Path(os.environ.get("UNIENV_HOME", Path.home() / ".unienv")) / "bin" / "unienv"
WORK = Path(tempfile.mkdtemp(prefix="ue_mcp_test."))
HAS_SOFFICE = os.access("/Applications/LibreOffice.app/Contents/MacOS/soffice", os.X_OK) or bool(shutil.which("soffice"))


def call(i, name, args):
    return {"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": args}}


msgs = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize",
     "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "mcp_test", "version": "1"}}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    call(3, "unienv_capabilities", {}),
    call(4, "run_python", {"code": "import docx\nd = docx.Document(); d.add_heading('MCP works', 1)\n"
                                   "d.add_paragraph('hello from unienv-mcp'); d.save('t.docx'); print('saved')"}),
    call(5, "read_document", {"path": "t.docx"}),
    call(6, "convert_document", {"input_path": "t.docx", "output_format": "md"}),
    call(7, "run_tool", {"tool": "bash", "args": ["-c", "id"]}),
    call(8, "run_python", {"code": "raise SystemExit(3)"}),
    call(9, "install_package", {"name": "x; rm -rf /", "kind": "python"}),
    {"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {"name": "nope", "arguments": {}}},
    {"jsonrpc": "2.0", "id": 11, "method": "initialize",
     "params": {"protocolVersion": "1999-01-01", "capabilities": {}, "clientInfo": {"name": "old", "version": "1"}}},
]
if HAS_SOFFICE:
    msgs.append(call(12, "convert_document", {"input_path": "t.docx", "output_format": "pdf"}))

env = {"HOME": str(Path.home()), "PATH": "/usr/bin:/bin", "UNIENV_WORKDIR": str(WORK)}
if "UNIENV_HOME" in os.environ:
    env["UNIENV_HOME"] = os.environ["UNIENV_HOME"]
proc = subprocess.run([str(UE), "mcp"], input="\n".join(map(json.dumps, msgs)) + "\n",
                      capture_output=True, text=True, env=env, timeout=600)
lines = proc.stdout.splitlines()
replies = {m.get("id"): m for m in map(json.loads, lines)}
failures = 0


def check(label, cond):
    global failures
    print(f"  {'PASS' if cond else 'FAIL'} {label}")
    failures += not cond


def text(i):
    return replies[i]["result"]["content"][0]["text"]


def is_error(i):
    return replies[i]["result"]["isError"]


check("stdout is pure JSON-RPC", all(l.startswith("{") for l in lines))
check("initialize negotiates the client's version", replies[1]["result"]["protocolVersion"] == "2025-06-18")
check("unknown version falls back to the newest supported", replies[11]["result"]["protocolVersion"] >= "2025-06-18")
check("server identifies as unienv", replies[1]["result"]["serverInfo"]["name"] == "unienv")
tools = {t["name"]: t for t in replies[2]["result"]["tools"]}
check("6 tools listed", set(tools) == {"unienv_capabilities", "read_document", "convert_document",
                                      "run_python", "run_tool", "install_package"})
check("read-only tools are annotated read-only",
      tools["read_document"]["annotations"]["readOnlyHint"] and not tools["run_python"]["annotations"]["readOnlyHint"])
check("capabilities lists python-docx", "python-docx" in text(3))
check("run_python creates a file and reports it", not is_error(4) and "t.docx" in text(4) and "saved" in text(4))
check("read_document reads it back", not is_error(5) and "hello from unienv-mcp" in text(5))
check("convert_document docx → md", not is_error(6) and (WORK / "t.md").exists())
check("run_tool refuses non-UniEnv tools", is_error(7) and "not a UniEnv tool" in text(7))
check("run_python reports failures as tool errors", is_error(8) and "exit code: 3" in text(8))
check("install_package rejects shell metacharacters", is_error(9) and "invalid package name" in text(9))
check("unknown tool is a JSON-RPC error", replies[10].get("error", {}).get("code") == -32602)
if HAS_SOFFICE:
    check("convert_document docx → pdf via LibreOffice", not is_error(12) and (WORK / "t.pdf").exists())
else:
    print("  SKIP convert_document → pdf (LibreOffice not installed)")

shutil.rmtree(WORK, ignore_errors=True)
print(f"mcp: {failures} failed")
sys.exit(failures)
