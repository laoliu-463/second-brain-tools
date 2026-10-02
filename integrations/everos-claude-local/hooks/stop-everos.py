#!/usr/bin/env python3
"""Claude Code Stop hook: push last user+assistant turn into EverOS.

Two-phase design:
  1. ADD (blocking, fast): POST /api/v1/memory/add with defer_extraction=true.
     Only network call on the hot path; store is fast.
  2. FLUSH (throttled, non-blocking): if >=90s since last spawn, start a
     detached worker (flush-everos.py) to trigger LLM extraction. The worker
     runs off the hot path so chat ending is never blocked.

State: last spawn timestamp in <script_dir>/.last-flush (epoch seconds, float).
Hook contract: stdin = Stop hook JSON; stdout = {"continue": true} on success.
Errors are swallowed — a hook must never block chat from ending.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# 锁 UTF-8 stdout（中文 Windows 默认 gbk，非 gbk 字符会抛 UnicodeEncodeError）
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 用 127.0.0.1 而非 localhost（避开 WSL mirrored 回环上 Python 试 ::1 的 ~21s 卡顿）
EVEROS_URL = os.environ.get("EVEROS_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
USER_ID = os.environ.get("EVEROS_USER_ID", "local-user")
APP_ID = os.environ.get("EVEROS_APP_ID", "claude-code")
PROJECT_ID = os.environ.get("EVEROS_PROJECT_ID", "claude-code")
THROTTLE_SECONDS = int(os.environ.get("STOP_FLUSH_THROTTLE", "90"))

HERE = Path(__file__).resolve().parent
STATE_FILE = HERE / ".last-flush"
FLUSH_WORKER = HERE / "flush-everos.py"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _read_stdin_json() -> dict:
    """Decode stdin as raw UTF-8 bytes (see recall-everos.py docstring)."""
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", "replace")
    except Exception:
        return {}
    try:
        return json.loads(raw or "{}")
    except (json.JSONDecodeError, ValueError):
        return {}


def _post(path: str, payload: dict, timeout: int = 5) -> bool:
    try:
        req = urllib.request.Request(
            EVEROS_URL + path,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status < 400
    except Exception:
        return False


def _read_transcript_tail(path: str, max_chars: int = 4000) -> list[dict]:
    """Return the last 2 messages (user + assistant) as plain dicts."""
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception:
        return []
    if len(content) > max_chars * 4:
        content = content[-max_chars * 4:]
    msgs: list[dict] = []
    for line in content.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("type") in ("user", "assistant"):
            msg = obj.get("message") or obj
            content_field = msg.get("content") if isinstance(msg, dict) else None
            text = ""
            if isinstance(content_field, str):
                text = content_field
            elif isinstance(content_field, list):
                for part in content_field:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text += part.get("text", "")
            if text.strip():
                msgs.append({"role": obj["type"], "content": text.strip()[:2000]})
                if len(msgs) >= 2:
                    break
    return msgs[-2:]


def _read_state() -> float:
    try:
        return float(STATE_FILE.read_text().strip())
    except (FileNotFoundError, ValueError, OSError):
        return 0.0


def _write_state(ts: float) -> None:
    try:
        STATE_FILE.write_text(str(ts))
    except OSError:
        pass


def _spawn_flush(session_id: str) -> None:
    """Start a detached flush worker. Returns immediately."""
    if not FLUSH_WORKER.exists():
        return
    try:
        kwargs = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "close_fds": True,
        }
        if sys.platform == "win32":
            # CREATE_NO_WINDOW = 0x08000000
            kwargs["creationflags"] = 0x08000000
        subprocess.Popen(
            [sys.executable, str(FLUSH_WORKER), "--session", session_id],
            **kwargs,
        )
    except Exception:
        pass


def main() -> int:
    payload = _read_stdin_json()
    session_id = (payload.get("session_id") or "claude-default")[:128]
    transcript_path = payload.get("transcript_path") or ""
    msgs = _read_transcript_tail(transcript_path)

    if msgs:
        body_msgs = [
            {
                "sender_id": USER_ID if m["role"] == "user" else APP_ID,
                "sender_name": None,
                "role": m["role"],
                "timestamp": _now_ms(),
                "content": m["content"],
            }
            for m in msgs
        ]
        body = {
            "session_id": session_id,
            "app_id": APP_ID,
            "project_id": PROJECT_ID,
            "messages": body_msgs,
            "defer_extraction": True,
        }
        _post("/api/v1/memory/add", body)

        # Throttled background flush — only spawn if worker script exists
        # and >= THROTTLE_SECONDS since last spawn.
        now = time.time()
        last = _read_state()
        if now - last >= THROTTLE_SECONDS:
            _spawn_flush(session_id)
            _write_state(now)

    print(json.dumps({"continue": True}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        print(json.dumps({"continue": True}))
        sys.exit(0)
