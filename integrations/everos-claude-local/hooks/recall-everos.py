#!/usr/bin/env python3
"""Claude Code UserPromptSubmit hook — 提问即自动召回 EverOS 相关记忆。

契约（照官方 evermem 插件 inject-memories.js 的输出形状）：
  stdout 输出 JSON:
    {"systemMessage": "给用户看的提示",
     "hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                            "additionalContext": "注入给模型的上下文"}}
  无命中 / 服务不可达 / 超时 → 只输出 {"continue": true}，完全静默。

检索默认 method=hybrid（向量+关键词融合，若服务端配了 rerank 会再精排）。
实测：EverOS 的 keyword(BM25) 对查询只做空白分词，中文整句问话被当成单个
巨型 token → 0 命中；只有 hybrid/vector 能把自然语言问句语义匹配上。代价是
每次提问多一个 embedding 云端调用（约 0.2~0.4s），但召回正确性优先。
"""
import json
import os
import sys
import urllib.request

# Force UTF-8 on stdout: 中文 Windows 默认 cp936/gbk，钩子输出里的 emoji
# （如 📝）非 gbk 可编字符，print() 会抛 UnicodeEncodeError 被顶层 except
# 吞成静默；且 Claude Code 以 UTF-8 解析 stdout，必须显式锁 UTF-8。
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 用 127.0.0.1 而非 localhost：Windows 上 Python 解析 localhost 会先试
# IPv6 ::1，而 WSL mirrored 回环的 ::1:8000 接受连接却不回话，要等 ~21s
# 才回落到 IPv4，直接把召回拖死。127.0.0.1 实测 <10ms。
EVEROS_URL = os.environ.get("EVEROS_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
USER_ID = os.environ.get("EVEROS_USER_ID", "local-user")
APP_ID = os.environ.get("EVEROS_APP_ID", "claude-code")
PROJECT_ID = os.environ.get("EVEROS_PROJECT_ID", "claude-code")
TOP_K = int(os.environ.get("EVEROS_RECALL_TOP_K", "5"))
METHOD = os.environ.get("EVEROS_RECALL_METHOD", "hybrid")
SEARCH_TIMEOUT_S = float(os.environ.get("EVEROS_RECALL_TIMEOUT_S", "5"))


def _silent() -> None:
    print(json.dumps({"continue": True}))


def _read_stdin_json() -> dict:
    """Decode stdin as raw UTF-8 bytes.

    中文 Windows 上 sys.stdin 文本模式默认 cp936/gbk，会把 Claude Code
    传进来的 UTF-8 中文字节解成乱码并触发 UnicodeDecodeError，被上层 except
    吞掉后 recall 永远静默。必须读字节再显式按 utf-8 解码。
    """
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", "replace")
    except Exception:
        return {}
    try:
        return json.loads(raw or "{}")
    except (json.JSONDecodeError, ValueError):
        return {}


def _search(query: str) -> dict:
    body = {
        "query": query[:500],
        "user_id": USER_ID,
        "app_id": APP_ID,
        "project_id": PROJECT_ID,
        "method": METHOD,
        "top_k": TOP_K,
    }
    req = urllib.request.Request(
        EVEROS_URL + "/api/v2/memory/search",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=SEARCH_TIMEOUT_S) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def _format(resp: dict) -> tuple | None:
    data = resp.get("data") or {}
    lines, disp = [], []
    for ep in (data.get("episodes") or [])[:TOP_K]:
        date = str(ep.get("timestamp", ""))[:10]
        summary = str(ep.get("summary") or ep.get("episode") or "").strip()[:300]
        subject = str(ep.get("subject") or "").strip()[:80]
        if not (summary or subject):
            continue
        lines.append(f"- [情景 {date}] {subject}：{summary}")
        disp.append(f"  • [{date}] {subject or summary[:40]}")
        for af in (ep.get("atomic_facts") or [])[:2]:
            c = str(af.get("content") or "").strip()[:150]
            if c:
                lines.append(f"- [事实] {c}")
    if not lines:
        return None
    context = (
        "EverOS 长期记忆自动召回（用户 " + USER_ID + " 的历史对话记忆，"
        "与当前问题无关则忽略；相关请自然使用，不要向用户复述本段来源说明）：\n"
        + "\n".join(lines)
    )
    system_msg = "📝 EverOS 召回 " + str(len(lines)) + " 条记忆\n" + "\n".join(disp)
    return context, system_msg


def main() -> int:
    payload = _read_stdin_json()
    prompt = str(payload.get("prompt") or payload.get("user_prompt") or "").strip()
    if len(prompt) < 4:  # 太短的输入没有检索价值
        _silent()
        return 0
    try:
        fmt = _format(_search(prompt))
    except Exception:
        _silent()
        return 0
    if not fmt:
        _silent()
        return 0
    context, system_msg = fmt
    print(json.dumps({
        "systemMessage": system_msg,
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context,
        },
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        try:
            _silent()
        except Exception:
            pass
        sys.exit(0)
