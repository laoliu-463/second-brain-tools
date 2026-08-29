"""
proofread_scan.py — 文字层校对候选扫描器（只读）

用途：在授权改写前跑全 150 份 OCR 文字层文件，统计候选改动的分类分布 + 每类样本。

不做任何写盘。输出到 stdout / `--out` 文件，方便决策白名单。

分类：
  A. CJK 字内空格（中文字之间被插入空格）—— 最高频
  B. 半角标点混入 CJK 上下文
  C. 重复标点（。。 ，、 ， ，）
  D. 页眉页脚 / 扫描噪声
  E. 高频 OCR 错字（按全语料统计替换频率，挑出 top-N）
  F. 行尾硬换行（中文按英文规则硬断行）
  G. 全角空格 / 不可见字符
  H. 文件级元信息（大小 / 行数 / 字符数）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable

# ---------- 路径 ----------

DEFAULT_ROOTS = [
    Path("资料库/1_五本书/文字层"),
    Path("资料库/3_普通人系列/文字层"),
    Path("资料库/4_中美博弈系列/文字层"),
    Path("资料库/5_摆万—认知红利系列/文字层"),
]

# ---------- 模式 ----------

# A. CJK 字内空格：CJK 字符 + 空格 + CJK 字符（连续 CJK 文本中的空格）
CJK = r"一-鿿㐀-䶿"
RE_CJK_INNER_SPACE = re.compile(rf"([{CJK}]) ([{CJK}])")

# B. 半角标点混入 CJK：CJK 字符后面紧跟 ASCII 标点
RE_HALF_PUNCT_AFTER_CJK = re.compile(rf"[{CJK}]([(),.;:?!])")

# C. 重复标点
RE_REPEAT_PUNCT = re.compile(r"([。，、；：？！,.;:?!“”])\1{1,}")

# D. 页眉页脚 / 扫描噪声（典型 Tesseract 5.x 产物）
RE_NOISE_MARKERS = re.compile(r"(‖…?\s*[oO]?\s*|\.{3,}\s*[oO]?\s*$|‖|…{2,})", re.MULTILINE)
RE_PAGE_FOOTER = re.compile(r"^第\s*\d+\s*页[\s/上中下]*$|^-\s*\d+\s*-\s*$|^\d+/\d+$", re.MULTILINE)

# E. 高频 OCR 错字候选 —— 依靠上下文中"几乎一定是 X"的位置
#    这些是经验候选；后续按全语料统计替换频次再决策
KNOWN_OCR_SUSPECTS = "砚 口 义 巳 已 目 戡 仁 场 习 丽 宫 乾 趴 讥 翻 巳 戍 芸".split()
# 也包括一些可见的英文被错换成汉字
# 此处只列出"反复出现且上下文明显是另一字"的候选，不在此处直接做替换

# G. 全角空格 / 不可见
RE_FULLWIDTH_SPACE = re.compile(r"[　]")
RE_BOM = re.compile(r"﻿")
RE_ZERO_WIDTH = re.compile(r"[​-‍⁠﻿]")

# ---------- 工具 ----------

def iter_files(roots: list[Path]) -> Iterable[Path]:
    for root in roots:
        if not root.exists():
            continue
        for p in sorted(root.rglob("*.md")):
            # 跳过 README
            if p.name.lower() == "readme.md":
                continue
            yield p


def scan_file(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    # 分离 frontmatter
    body_start = 0
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            body_start = end + 5

    body = text[body_start:]

    # A. CJK 字内空格
    a_hits = RE_CJK_INNER_SPACE.findall(body)
    a_pairs = Counter()
    # 同时统计"二字间距"和"二字 pair"以便看哪些 pair 高频
    for m in RE_CJK_INNER_SPACE.finditer(body):
        pair = m.group(1) + m.group(2)
        a_pairs[pair] += 1

    # B. 半角标点
    b_hits = RE_HALF_PUNCT_AFTER_CJK.findall(body)

    # C. 重复标点
    c_hits = list(RE_REPEAT_PUNCT.finditer(body))

    # D. 噪声
    d_noise = list(RE_NOISE_MARKERS.finditer(body))
    d_footer = list(RE_PAGE_FOOTER.finditer(body))

    # E. 错字候选频次
    e_counts = Counter()
    for ch in KNOWN_OCR_SUSPECTS:
        e_counts[ch] = body.count(ch)

    # G. 不可见
    g_fullwidth = len(RE_FULLWIDTH_SPACE.findall(body))
    g_bom = len(RE_BOM.findall(body))
    g_zw = len(RE_ZERO_WIDTH.findall(body))

    # 文件级
    return {
        "path": str(path),
        "lines": len(lines),
        "chars": len(body),
        "A_cjk_inner_space": {
            "total_hits": len(a_hits),
            "top_pairs": a_pairs.most_common(20),
        },
        "B_half_punct_after_cjk": {
            "total_hits": len(b_hits),
            "by_punct": dict(Counter(b_hits)),
        },
        "C_repeat_punct": {
            "groups": len(c_hits),
            "samples": [
                {"line": i + 1, "match": m.group(0)}
                for m in c_hits[:5]
                for i, _ in [(_approx_line(body, m.start()), 0)]
            ],
        },
        "D_noise_and_footers": {
            "noise": len(d_noise),
            "footers": len(d_footer),
        },
        "E_ocr_suspect_chars": dict(e_counts.most_common()),
        "G_invisible": {
            "fullwidth_space": g_fullwidth,
            "bom": g_bom,
            "zero_width": g_zw,
        },
    }


def _approx_line(body: str, pos: int) -> int:
    return body.count("\n", 0, pos)


def summarize(scan_results: list[dict]) -> dict:
    agg = defaultdict(int)
    pair_total = Counter()
    suspect_total = Counter()
    punct_total = Counter()
    noise_files = []
    for r in scan_results:
        agg["files"] += 1
        agg["A_total_hits"] += r["A_cjk_inner_space"]["total_hits"]
        pair_total.update(dict(r["A_cjk_inner_space"]["top_pairs"]))
        agg["B_total_hits"] += r["B_half_punct_after_cjk"]["total_hits"]
        punct_total.update(r["B_half_punct_after_cjk"]["by_punct"])
        agg["C_groups"] += r["C_repeat_punct"]["groups"]
        agg["D_noise"] += r["D_noise_and_footers"]["noise"]
        agg["D_footers"] += r["D_noise_and_footers"]["footers"]
        suspect_total.update(r["E_ocr_suspect_chars"])
        agg["G_fullwidth"] += r["G_invisible"]["fullwidth_space"]
        agg["G_bom"] += r["G_invisible"]["bom"]
        agg["G_zw"] += r["G_invisible"]["zero_width"]
        if r["D_noise_and_footers"]["noise"] > 0 or r["D_noise_and_footers"]["footers"] > 0:
            noise_files.append(r["path"])

    return {
        "totals": dict(agg),
        "A_top_pairs": pair_total.most_common(30),
        "B_punct_breakdown": dict(punct_total),
        "E_ocr_suspect_freq": dict(suspect_total),
        "noise_files": noise_files[:30],
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", action="append", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--summary-only", action="store_true")
    args = ap.parse_args(argv)

    roots = args.root if args.root else DEFAULT_ROOTS
    files = list(iter_files(roots))
    print(f"[scan] files: {len(files)} | roots: {[str(r) for r in roots]}")

    results = []
    for p in files:
        try:
            results.append(scan_file(p))
        except Exception as exc:
            print(f"[scan][WARN] {p}: {exc}", file=sys.stderr)

    summary = summarize(results)

    output = {"per_file": results, "summary": summary}

    if args.summary_only:
        out = summary
    else:
        out = output

    text = json.dumps(out, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"[scan] wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
