"""Small dependency-free MCP stdio server for code-kg.

The server implements the MCP JSON-RPC methods needed by tool-capable
clients: initialize, tools/list, tools/call, ping, and shutdown.  Keeping the
transport here dependency-free preserves the project's Python 3.9 support
and avoids coupling graph retrieval to a particular MCP SDK release.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .agent_tools import TOOL_DEFINITIONS, dispatch_tool

SERVER_NAME = "code-kg"
SERVER_VERSION = "2.0.0"
PROTOCOL_VERSION = "2024-11-05"


def _send(message: dict[str, Any]) -> None:
    """Write exactly one JSON-RPC message to stdout."""
    sys.stdout.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _result(request_id: Any, value: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": value}


def _error(request_id: Any, code: int, message: str,
           data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _mcp_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": item["name"],
            "description": item["description"],
            "inputSchema": item["parameters"],
        }
        for item in TOOL_DEFINITIONS
    ]


def _call_tool(name: str, arguments: dict[str, Any], graph_dir: str) -> dict[str, Any]:
    value = dispatch_tool(name, arguments, graph_dir=graph_dir)
    text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": value,
        "isError": False,
    }


def handle_message(message: dict[str, Any], *, graph_dir: str) -> dict[str, Any] | None:
    """Handle one parsed JSON-RPC message; return None for notifications."""
    method = message.get("method")
    request_id = message.get("id")
    is_notification = "id" not in message

    if not method:
        return None if is_notification else _error(request_id, -32600, "method is required")

    if method == "notifications/initialized":
        return None
    if method == "ping":
        return None if is_notification else _result(request_id, {})
    if method == "initialize":
        if is_notification:
            return None
        params = message.get("params") or {}
        requested = params.get("protocolVersion")
        protocol = requested if requested in {PROTOCOL_VERSION, "2024-10-07"} else PROTOCOL_VERSION
        return _result(request_id, {
            "protocolVersion": protocol,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": (
                "Use search_code_symbols before get_code_context. "
                "Use analyze_change_impact after code changes."
            ),
        })
    if method == "tools/list":
        return None if is_notification else _result(request_id, {"tools": _mcp_tools()})
    if method == "tools/call":
        if is_notification:
            return None
        params = message.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str) or not name:
            return _error(request_id, -32602, "tools/call requires params.name")
        if not isinstance(arguments, dict):
            return _error(request_id, -32602, "params.arguments must be an object")
        try:
            return _result(request_id, _call_tool(name, arguments, graph_dir))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return _result(request_id, {
                "content": [{"type": "text", "text": f"code-kg tool error: {exc}"}],
                "isError": True,
            })
    if method == "shutdown":
        return None if is_notification else _result(request_id, {})
    if method == "exit":
        return None
    return None if is_notification else _error(request_id, -32601, f"method not found: {method}")


def serve(graph_dir: str, input_stream=None) -> int:
    """Serve newline-delimited JSON-RPC over stdin/stdout."""
    stream = input_stream or sys.stdin
    for raw in stream:
        if not raw.strip():
            continue
        try:
            message = json.loads(raw)
            if not isinstance(message, dict):
                raise ValueError("message must be a JSON object")
            response = handle_message(message, graph_dir=graph_dir)
        except (json.JSONDecodeError, ValueError) as exc:
            _send(_error(None, -32700, f"invalid JSON-RPC message: {exc}"))
            continue
        if response is not None:
            _send(response)
        if message.get("method") == "exit":
            break
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve code-kg tools over MCP stdio")
    parser.add_argument(
        "--graph-dir",
        required=False,
        help="directory containing entity.json and relation.json; "
             "defaults to CKG_GRAPH_DIR",
    )
    args = parser.parse_args(argv)
    graph_dir = args.graph_dir
    if not graph_dir:
        import os
        graph_dir = os.environ.get("CKG_GRAPH_DIR")
    if not graph_dir:
        print("ckg-mcp: --graph-dir or CKG_GRAPH_DIR is required", file=sys.stderr)
        return 2
    return serve(graph_dir)


if __name__ == "__main__":
    raise SystemExit(main())
