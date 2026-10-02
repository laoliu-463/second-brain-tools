#!/usr/bin/env python3
"""EverOS local MCP server. stdio JSON-RPC 2.0 — no SDK, pure stdlib."""
import json
import os
import sys
import time
import urllib.request
import urllib.error

EVEROS_BASE = os.environ.get("EVEROS_BASE_URL", "http://localhost:8000").rstrip("/")
USER_ID = os.environ.get("EVEROS_USER_ID", "local-user")


def _post(path, body, timeout=30):
    url = EVEROS_BASE + path
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        b = e.read().decode("utf-8", errors="replace")
        return {"_error": f"HTTP {e.code}: {b[:200]}"}
    except Exception as e:
        return {"_error": f"{type(e).__name__}: {e}"}


def _now_ms():
    return int(time.time() * 1000)


# ── tools ────────────────────────────────────────────────────────────
TOOLS = [
    {
        "name": "everos_search",
        "description": (
            "Hybrid search over local EverOS long-term memory (episodes, "
            "profiles, atomic_facts, agent_cases, agent_skills, knowledge_topics). "
            "Use when the user asks about decisions, decisions, anything from past sessions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer", "default": 10, "minimum": 1, "maximum": 100},
                "method": {"type": "string", "enum": ["hybrid", "vector", "keyword", "agentic"], "default": "HYBRID"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "everos_add",
        "description": (
            "Add a conversation turn into local EverOS long-term memory. "
            "Provide messages as list of {role, content, sender_id?, sender_name?}."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "messages": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "role": {"type": "string", "enum": ["user", "assistant", "tool"]},
                            "content": {"type": "string"},
                            "sender_id": {"type": "string"},
                            "sender_name": {"type": "string"},
                        },
                        "required": ["role", "content"],
                        "additionalProperties": True,
                    },
                },
                "session_id": {"type": "string"},
                "project_id": {"type": "string", "default": "claude-code"},
                "defer_extraction": {"type": "boolean", "default": False},
            },
            "required": ["messages", "session_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "everos_get",
        "description": "List paginated episodes or profiles from local EverOS.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "memory_type": {"type": "string", "enum": ["episode", "profile"], "default": "episode"},
                "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
]


def _search(args):
    return _post("/api/v1/memory/search", {
        "user_id": USER_ID,
        "app_id": "claude-code",
        "project_id": "claude-code",
        "query": args["query"],
        "method": args.get("method", "hybrid"),
        "top_k": int(args.get("top_k", 10)),
    })


def _add(args):
    msgs = []
    for m in args["messages"]:
        msgs.append({
            "sender_id": m.get("sender_id") or (USER_ID if m["role"] == "user" else "claude-code"),
            "sender_name": m.get("sender_name"),
            "role": m["role"],
            "timestamp": _now_ms(),
            "content": m["content"],
        })
    return _post("/api/v1/memory/add", {
        "session_id": args["session_id"][:128],
        "app_id": "claude-code",
        "project_id": args.get("project_id", "claude-code"),
        "messages": msgs,
        "defer_extraction": bool(args.get("defer_extraction", True)),
    })


def _get(args):
    return _post("/api/v1/memory/get", {
        "memory_type": args.get("memory_type", "episode"),
        "limit": int(args.get("limit", 20)),
        "offset": 0,
        "user_id": USER_ID,
        "app_id": "claude-code",
        "project_id": "claude-code",
    })


HANDLERS = {"everos_search": _search, "everos_add": _add, "everos_get": _get}


# ── JSON-RPC 2.0 dispatcher ─────────────────────────────────────────
def _send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _dispatch(msg):
    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params") or {}

    if msg_id is None:
        return None  # notification, no response

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "everos-local", "version": "0.1.0"},
        }}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        h = HANDLERS.get(name)
        if not h:
            return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32602, "message": f"unknown tool: {name}"}}
        try:
            r = h(args)
        except Exception as e:
            return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32603, "message": f"exec: {e}"}}
        return {"jsonrpc": "2.0", "id": msg_id, "result": {
            "content": [{"type": "text", "text": json.dumps(r, ensure_ascii=False, indent=2)}],
            "isError": "_error" in r,
        }}

    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"unknown method: {method}"}}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            _send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"parse: {e}"}})
            continue
        resp = _dispatch(msg)
        if resp is not None:
            _send(resp)


if __name__ == "__main__":
    main()
