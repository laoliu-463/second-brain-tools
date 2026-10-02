#!/usr/bin/env python3
"""EverOS flush worker.

Modes (mutually exclusive):
  --session <id>   Flush a single session (called by stop-everos.py worker).
  --all            Flush every session recorded in <script_dir>/.recent-sessions
                   (SessionEnd fallback when stdin gives us nothing useful).
  (no flag)        Read session_id from stdin JSON (SessionEnd default contract).

Errors are appended to <script_dir>/flush.log; never raised. A hook / detached
worker must not crash visibly.
"""
from __future__ import annotations

import json
import os
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

HERE = Path(__file__).resolve().parent
LOG_FILE = HERE / "flush.log"
RECENT_FILE = HERE / ".recent-sessions"


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


def _post(path: str, payload: dict, timeout: int = 60) -> tuple[int, str]:
    try:
        req = urllib.request.Request(
            EVEROS_URL + path,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def _log(msg: str) -> None:
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}\n")
    except OSError:
        pass


def flush_session(session_id: str) -> None:
    if not session_id:
        return
    body = {
        "session_id": session_id[:128],
        "app_id": APP_ID,
        "project_id": PROJECT_ID,
    }
    code, text = _post("/api/v1/memory/flush", body, timeout=60)
    _log(f"flush session={session_id[:64]} status={code} body={text[:200]}")
    # Record in .recent-sessions (de-duped, capped at 50)
    try:
        existing: list[str] = []
        if RECENT_FILE.exists():
            existing = [ln.strip() for ln in RECENT_FILE.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if session_id not in existing:
            existing.insert(0, session_id)
            existing = existing[:50]
            RECENT_FILE.write_text("\n".join(existing) + "\n", encoding="utf-8")
    except OSError:
        pass


def main() -> int:
    args = sys.argv[1:]

    if "--session" in args:
        i = args.index("--session")
        if i + 1 < len(args):
            flush_session(args[i + 1])
        return 0

    if "--all" in args:
        if RECENT_FILE.exists():
            for line in RECENT_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
                sid = line.strip()
                if sid:
                    flush_session(sid)
        return 0

    # Default: read session_id from stdin (SessionEnd hook contract).
    payload = _read_stdin_json()
    session_id = (payload.get("session_id") or "").strip()
    if session_id:
        flush_session(session_id)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        _log(f"fatal: {type(e).__name__}: {e}")
        sys.exit(0)
