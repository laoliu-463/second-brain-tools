"""Detached launcher: 启动 ocr_run.py 作为独立进程。

为什么需要这个？
  Claude Code 的 Bash 后台任务在退出时会 SIGTERM 子进程（即使已 detach 到后台）。
  Windows 上只有 subprocess.DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP + 父进程立即
  退出 才能彻底脱离父进程组。Python 的 Popen 默认是 inherit，进程会跟父进程同 group。

用法
----
  python launch_detached.py                  # 跑 manifest 默认书（按 priority）
  python launch_detached.py --book <id>      # 跑指定书
  python launch_detached.py --no-wait        # 不等子进程

退出后
------
  python launch_detached.py --book history_古本竹书纪年译注
  → 立刻返回 0
  → ocr_run.py 仍在后台跑，日志写到 ocr_run_detached_<timestamp>.log
  → DB 进度实时可查（D:/第二大脑/ocr-pilot-20260825/ocr_progress.db）
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Detached launcher for ocr_run.py")
    p.add_argument("--book", "-b", default=None, help="只跑指定 book_id")
    p.add_argument("--max-workers", type=int, default=3)
    p.add_argument("--max-attempts", type=int, default=3)
    p.add_argument("--no-wait", action="store_true", help="不等子进程启动确认")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = PROJECT_ROOT / f"ocr_run_detached_{ts}.log"

    cmd = [
        sys.executable,
        "-u",                                       # 不缓冲 stdout
        str(PROJECT_ROOT / "ocr_run.py"),
        "--manifest", str(PROJECT_ROOT / "books.yaml"),
        "--max-workers", str(args.max_workers),
        "--max-attempts", str(args.max_attempts),
        "--log-level", "INFO",
    ]
    if args.book:
        cmd.extend(["--book", args.book])

    print(f"[launcher] python pid={os.getpid()}")
    print(f"[launcher] cmd = {' '.join(cmd)}")
    print(f"[launcher] log = {log_path}")

    flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    with open(log_path, "wb") as logf:
        proc = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            stdout=logf,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=flags,
            close_fds=True,
        )
    print(f"[launcher] spawned ocr_run pid={proc.pid}")
    print(f"[launcher] detached; parent exiting now")

    if not args.no_wait:
        # 1.5s 后确认子进程仍在（启动失败立刻能感知）
        time.sleep(1.5)
        if proc.poll() is None:
            print(f"[launcher] [OK] child alive after 1.5s (pid={proc.pid})")
        else:
            print(f"[launcher] [FAIL] child exited rc={proc.returncode}; check log: {log_path}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())