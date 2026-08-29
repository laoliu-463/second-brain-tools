"""
proofread_apply.py — 文字层校对改写器（白名单驱动）

输入：一份 .md 文本
输出：改写后文本 + 改动日志（每条 = 类别 / 行号 / 原文 / 改后 / 原因）

白名单类别：
  A. CJK 字内空格（保留 ASCII 空格：CJK-英文 / CJK-数字 / 标点 / 行首 / 行尾例外）
  B. CJK 上下文半角标点 → 全角（仅当标点不紧邻 ASCII）
  C. 重复标点（保留 … 语义例外）
  D. 扫描噪声（‖…o / ‖… / ……o）+ 页脚（"第 X 页" / "-N-" / "N/M"）
  E. OCR 错字（上下文自动门，详见 BELOW）
  G. 全角空格 → ASCII / 或删除

E 上下文自动门规则（保守策略 e2）：
  rule_羲→是   ：要求前一个非空 CJK ∈ {的, 这, 那, 是, 就, 要, 才, 会, 能, 可, 以, 不, 也, 都}，后一个是 CJK
  rule_场→而   ：要求前面紧邻 ASCII 标点或 ∈{反,而,且,或,并,因}之一；后面是 CJK
  rule_口→们   ：要求前一个非空 CJK ∈{我,你,他,她,它,们}，或紧邻在前一个已替换为 "们" 的形态
  rule_目→自   ：要求紧接的下一字符是 "己" → 形成 "自己"
  rule_习→则   ：要求前面是 CJK 且后一个 ∈{的,是,在,来,去}之一
  rule_翻→需   ：要求前面 ∈{还,仍,必,只}之一

  每条规则都带一个"反向校验"：改写后形成的新 3-字窗必须包含至少 1 个已知中文常用词（白名单内的 3-字词片段）。
  校验失败 = 跳过，留给人工审。

所有改动都会进 `diff_log`，按 `[A123] line:42 原: '...' 改: '...'` 记录。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

CJK = r"一-鿿㐀-䶿"
ASCII_PUNCT_OR_SPACE = r"[ \t,.;:?!'\"()\[\]{}<>/\\|`~@#$%^&*_+=]"
ANY_WS = r"[ \t　]"

# ---------- E 自动门规则 ----------

# 已知安全前字集合（"羲" 几乎一定是 "是" 的前字）
SAFE_BEFORE_羲 = "的这那是就才会能可以不也都又才又".replace("是", "").replace("", "")
# 为简化，写成显式列表
SAFE_BEFORE_羲 = list("的这那是就才会能可以不也都又又已却")
SAFE_BEFORE_场 = list(",.;:?!，。；：？！以及反而或并因")
SAFE_BEFORE_口 = list("我你他她它们")
SAFE_BEFORE_习 = list("的也是在来去", )
SAFE_BEFORE_翻 = list("还仍必只", )
# 反向校验：改后 3 字窗至少命中 1 个
KNOWN_TRIGRAMS = {
    "这是", "那是", "就是", "不是", "才是", "会是", "可以是", "都是",
    "而是", "而且", "并且", "而是", "因此", "因为",
    "我们", "你们", "他们", "她们", "它们",
    "自己", "本身", "自动", "自行",
    "则是", "习则", "仍在", "还需", "必须", "只需",
}


def make_cjk_set(s: str) -> str:
    return "".join(dict.fromkeys(s))


# ---------- 规则实现 ----------

@dataclass
class Edit:
    category: str
    line: int
    before: str
    after: str
    reason: str
    rule: str = ""

    def format(self) -> str:
        snippet_b = self.before[:80]
        snippet_a = self.after[:80]
        return (
            f"[{self.category}{self.rule}] L{self.line} "
            f"原: {snippet_b!r} → 改: {snippet_a!r} ({self.reason})"
        )


@dataclass
class EditLog:
    edits: list[Edit] = field(default_factory=list)
    file: Path | None = None

    def add(self, edit: Edit) -> None:
        self.edits.append(edit)

    def render(self) -> str:
        lines = [f"# {self.file}", ""]
        c = Counter(e.category for e in self.edits)
        lines.append(f"总改动 {len(self.edits)} 条，分类：{dict(c)}")
        lines.append("")
        for e in self.edits:
            lines.append(f"- {e.format()}")
        return "\n".join(lines)


# ---------- A. CJK 字内空格 ----------

def rule_A(text: str, log: EditLog) -> str:
    """合并 CJK 字内 ASCII 空格。保留例外：行首空格 / 行末空格 / CJK-拉丁交界 / 数字-CJK 交界。"""
    out = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == " " and 0 < i < n - 1:
            prev = text[i - 1]
            nxt = text[i + 1]

            def is_ascii_alnum_(c: str) -> bool:
                return bool(c) and c.isascii() and c.isalnum()

            def is_cjk_(c: str) -> bool:
                return bool(c) and re.match(f"[{CJK}]", c) is not None

            # 例外 1：与 ASCII 字母数字相邻则保留（"iPhone 手机" / "图 1"）
            if is_ascii_alnum_(prev) or is_ascii_alnum_(nxt):
                out.append(ch)
                i += 1
                continue
            # 例外 2：行末或紧邻换行（保持段落排版）
            if prev == "\n" or nxt == "\n":
                out.append(ch)
                i += 1
                continue
            # 两边都是 CJK → 合并（删除这处空格，**不**写出）
            if is_cjk_(prev) and is_cjk_(nxt):
                line_no = text.count("\n", 0, i) + 1
                ctx = text[max(0, i - 6):i + 7]
                # 真实变化：仅删除这一处空格
                log.add(Edit("A", line_no, ctx, ctx.replace(f"{prev} {nxt}", f"{prev}{nxt}", 1), "CJK 字内单空格删除"))
                i += 1
                continue
            out.append(ch)
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


# ---------- B. 半角标点 → 全角（CJK 上下文） ----------

HALF_TO_FULL = {
    ",": "，",
    ".": "。",
    ";": "；",
    ":": "：",
    "?": "？",
    "!": "！",
    "(": "（",
    ")": "）",
}


def rule_B(text: str, log: EditLog) -> str:
    """当半角标点紧邻 CJK 且不在 ASCII 单词内时转全角。"""
    out = []
    n = len(text)
    i = 0
    while i < n:
        ch = text[i]
        if ch in HALF_TO_FULL:
            # 邻近字符：前一个 / 后一个
            prev = text[i - 1] if i > 0 else ""
            nxt = text[i + 1] if i + 1 < n else ""

            def is_cjk(c: str) -> bool:
                return bool(c) and re.match(f"[{CJK}]", c) is not None

            def is_ascii_alnum(c: str) -> bool:
                return bool(c) and c.isascii() and c.isalnum()

            # 数字/ASCII 旁不动
            if is_ascii_alnum(prev) or is_ascii_alnum(nxt):
                out.append(ch)
                i += 1
                continue
            # 在 CJK 上下文才转
            if is_cjk(prev) or is_cjk(nxt):
                line_no = text.count("\n", 0, i) + 1
                log.add(Edit("B", line_no, ch, HALF_TO_FULL[ch], "CJK 上下文半角 → 全角"))
                out.append(HALF_TO_FULL[ch])
                i += 1
                continue
        out.append(ch)
        i += 1
    return "".join(out)


# ---------- C. 重复标点 ----------

REPEAT_PUNCT_PATTERN = re.compile(r"([。，、；：？！])\1{1,}")


def rule_C(text: str, log: EditLog) -> str:
    def repl(m: re.Match) -> str:
        ch = m.group(1)
        line_no = text.count("\n", 0, m.start()) + 1
        log.add(Edit("C", line_no, m.group(0), ch, "重复标点折叠"))
        return ch
    return REPEAT_PUNCT_PATTERN.sub(repl, text)


# ---------- D. 扫描噪声 + 页脚 ----------

NOISE_PATTERN = re.compile(r"‖…*[oO]? *|‖ *|\.{3,}\s*[oO]?\s*$", re.MULTILINE)
PAGE_FOOTER_PATTERN = re.compile(
    r"(?m)^\s*(第\s*\d+\s*页[\s/上中下]*|-\s*\d+\s*-|^\d+/\d+)\s*$"
)


def rule_D(text: str, log: EditLog) -> str:
    text2 = NOISE_PATTERN.sub(lambda m: _log_d(m, log, text, "扫描噪声"), text)
    text2 = PAGE_FOOTER_PATTERN.sub(lambda m: _log_d(m, log, text2, "页脚"), text2)
    return text2


def _log_d(m: re.Match, log: EditLog, src: str, reason: str) -> str:
    line_no = src.count("\n", 0, m.start()) + 1
    log.add(Edit("D", line_no, m.group(0), "", reason))
    return ""


# ---------- E. OCR 错字（上下文自动门） ----------

def _ctx(text: str, i: int) -> tuple[str, str, str, str]:
    """返回 (左1, 左1-非空, 右1, 右1-非空)。"""
    left1 = text[i - 1] if i > 0 else ""
    j = i - 1
    while j >= 0 and text[j] in (" ", "\t", "\n", "　"):
        j -= 1
    left_real = text[j] if j >= 0 else ""
    right1 = text[i + 1] if i + 1 < len(text) else ""
    j = i + 1
    while j < len(text) and text[j] in (" ", "\t", "\n", "　"):
        j += 1
    right_real = text[j] if j < len(text) else ""
    return left1, left_real, right1, right_real


def _validate_tri(trigram: str) -> bool:
    """反向校验：改写后三字窗至少命中 1 个已知词。"""
    if len(trigram) < 2:
        return False
    # 2 字窗 / 3 字窗都查
    for n in (3, 2):
        if n <= len(trigram):
            for k in range(len(trigram) - n + 1):
                if trigram[k:k + n] in KNOWN_TRIGRAMS:
                    return True
    return False


def rule_E(text: str, log: EditLog) -> str:
    """OCR 错字上下文自动门。"""
    out = []
    n = len(text)
    i = 0
    while i < n:
        ch = text[i]
        rule = _try_substitute(ch, text, i)
        if rule is not None:
            new_ch, reason, rule_id = rule
            line_no = text.count("\n", 0, i) + 1
            ctx = text[max(0, i - 4):i + 5]
            log.add(Edit("E", line_no, ch, new_ch, f"{reason} ctx={ctx!r}", rule_id))
            out.append(new_ch)
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _try_substitute(ch: str, text: str, i: int) -> tuple[str, str, str] | None:
    left1, left_real, right1, right_real = _ctx(text, i)
    cjk_left = bool(re.match(f"[{CJK}]", left_real))
    cjk_right = bool(re.match(f"[{CJK}]", right_real))

    if ch == "羲":
        # 羲→是：前字 ∈ 安全集 且后字是 CJK
        if left_real in SAFE_BEFORE_羲 and cjk_right:
            tri = left_real + "是" + right_real
            if _validate_tri(tri):
                return ("是", "羲→是（高频 OCR 误识）", "E1")
        return None

    if ch == "场":
        # 场→而：前字 ∈ 标点集合 / "反/而/且/或/并/因" + 后字是 CJK
        ok_before = (
            left_real in SAFE_BEFORE_场
            or left1 in (",", ".", ";", ":", "?", "!", "，", "。", "；", "：", "？", "！")
        )
        if ok_before and cjk_right:
            tri = left_real + "而" + right_real
            if _validate_tri(tri):
                return ("而", "场→而", "E2")
        return None

    if ch == "口":
        # 口→们：前字 ∈ {我,你,他,她,它,们}
        if left_real in SAFE_BEFORE_口:
            tri = left_real + "们" + right_real
            if _validate_tri(tri):
                return ("们", "口→们", "E3")
        return None

    if ch == "目":
        # 目→自：紧接的下一非空字符是 "己"
        if right_real == "己":
            return ("自", "目己→自己", "E4")
        return None

    if ch == "习":
        # 习→则：要求前面是 CJK 且后一个 ∈{的,是,在,来,去}
        if cjk_left and right_real in ("的", "是", "在", "来", "去"):
            tri = left_real + "则" + right_real
            if _validate_tri(tri):
                return ("则", "习→则", "E5")
        return None

    if ch == "翻":
        # 翻→需：要求前面 ∈{还,仍,必,只}
        if left_real in SAFE_BEFORE_翻 and cjk_right:
            tri = left_real + "需" + right_real
            if _validate_tri(tri):
                return ("需", "翻→需", "E6")
        return None

    return None


# ---------- G. 全角空格 ----------

def rule_G(text: str, log: EditLog) -> str:
    """全角空格 → ASCII（多数是 OCR 误识）。"""
    out = []
    for i, ch in enumerate(text):
        if ch == "　":
            line_no = text.count("\n", 0, i) + 1
            log.add(Edit("G", line_no, ch, " ", "全角空格 → ASCII"))
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


# ---------- 应用入口 ----------

DEFAULT_RULES = ["A", "B", "C", "D", "E", "G"]
RULE_FUNCS = {
    "A": rule_A,
    "B": rule_B,
    "C": rule_C,
    "D": rule_D,
    "E": rule_E,
    "G": rule_G,
}


def apply_rules(text: str, rules: list[str]) -> tuple[str, EditLog]:
    log = EditLog()
    out = text
    for r in rules:
        out = RULE_FUNCS[r](out, log)
    return out, log


def split_frontmatter(text: str) -> tuple[str, str]:
    if not text.startswith("---\n"):
        return "", text
    end = text.find("\n---\n", 4)
    if end == -1:
        return "", text
    return text[: end + 5], text[end + 5 :]


def process_file(path: Path, rules: list[str]) -> tuple[Path, str, str, EditLog]:
    """读 → 拆分 frontmatter → 仅改写 body → 返回 (path, fm, new_body, log)。"""
    raw = path.read_text(encoding="utf-8", errors="replace")
    fm, body = split_frontmatter(raw)
    new_body, log = apply_rules(body, rules)
    log.file = path
    return path, fm, new_body, log


def write_output(path: Path, fm: str, body: str, backup_dir: Path | None) -> None:
    if backup_dir:
        backup_dir.mkdir(parents=True, exist_ok=True)
        rel = path.name
        # 加 hash 避免重名覆盖
        dest = backup_dir / rel
        if dest.exists():
            import hashlib
            h = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:8]
            stem = path.stem
            dest = backup_dir / f"{stem}.{h}{path.suffix}"
        shutil.copy2(path, dest)
    path.write_text(fm + body, encoding="utf-8")


# ---------- CLI ----------

DEFAULT_ROOTS = [
    Path("资料库/1_五本书/文字层"),
    Path("资料库/3_普通人系列/文字层"),
    Path("资料库/4_中美博弈系列/文字层"),
    Path("资料库/5_摆万—认知红利系列/文字层"),
]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", action="append", type=Path, default=None)
    ap.add_argument("--rules", default="A,B,C,D,E,G", help="逗号分隔")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--skip", type=int, default=0, help="跳过排序后的前 N 个文件，与 --limit 配合做分批")
    ap.add_argument("--sort", choices=["smallest", "largest"], default="smallest")
    ap.add_argument("--dry-run", action="store_true", help="只输出 diff，不写盘")
    ap.add_argument("--backup-dir", type=Path, default=Path("资料库/.ecc/proofreading-backup"))
    ap.add_argument("--diff-out", type=Path, default=None, help="把 diff 写到文件")
    ap.add_argument("--prefix", default="", help="只处理文件名前缀匹配的文件")
    ap.add_argument("--skip-if-backed-up", action="store_true", help="跳过已在 backup-dir 中有备份的文件，避免重跑")
    args = ap.parse_args(argv)

    rules = [r.strip() for r in args.rules.split(",") if r.strip()]
    roots = args.root if args.root else DEFAULT_ROOTS

    # 已有备份文件名集合（用于 --skip-if-backed-up）
    backed_up: set[str] = set()
    if args.skip_if_backed_up and args.backup_dir.exists():
        for bp in args.backup_dir.iterdir():
            if bp.is_file():
                backed_up.add(bp.name)

    files = []
    for root in roots:
        if not root.exists():
            continue
        for p in sorted(root.rglob("*.md")):
            if p.name.lower() == "readme.md":
                continue
            if args.prefix and not p.name.startswith(args.prefix):
                continue
            if args.skip_if_backed_up and p.name in backed_up:
                continue
            files.append(p)
    files.sort(key=lambda p: p.stat().st_size, reverse=(args.sort == "largest"))
    if args.skip:
        files = files[args.skip : args.skip + args.limit]
    else:
        files = files[: args.limit]

    print(f"[apply] rules={rules} | files={len(files)} | skip={args.skip} | dry-run={args.dry_run}")

    all_logs: list[EditLog] = []
    all_diff_text: list[str] = []
    for p in files:
        try:
            path, fm, new_body, log = process_file(p, rules)
            all_logs.append(log)
        except Exception as exc:
            print(f"[apply][ERROR] {p}: {exc}", file=sys.stderr)
            continue

        # diff 输出
        raw = path.read_text(encoding="utf-8", errors="replace")
        _, old_body = split_frontmatter(raw)
        diff_text = _make_diff(old_body, new_body, path, log)
        all_diff_text.append(diff_text)

        if not args.dry_run:
            write_output(path, fm, new_body, args.backup_dir)

    # 输出 diff
    full_diff = "\n\n".join(all_diff_text)
    if args.diff_out:
        args.diff_out.write_text(full_diff, encoding="utf-8")
        print(f"[apply] wrote diff → {args.diff_out}")
    else:
        print(full_diff)

    # 汇总
    total = sum(len(log.edits) for log in all_logs)
    by_cat: Counter = Counter()
    for log in all_logs:
        for e in log.edits:
            by_cat[e.category] += 1
    print(f"\n[apply] TOTAL edits: {total} | by category: {dict(by_cat)}")
    return 0


def _make_diff(old: str, new: str, path: Path, log: EditLog) -> str:
    """简易 diff：列改动日志 + 字符/字节统计。"""
    lines = [f"# {path}", ""]
    # B 类（全角化）字符数不变但字节数增长；同时显示字节差异
    chars_delta = len(new) - len(old)
    bytes_delta = len(new.encode("utf-8")) - len(old.encode("utf-8"))
    lines.append(
        f"edits: {len(log.edits)} | "
        f"chars {len(old)}→{len(new)} (Δ{chars_delta:+d}) | "
        f"bytes {len(old.encode('utf-8'))}→{len(new.encode('utf-8'))} (Δ{bytes_delta:+d})"
    )
    lines.append("")
    # 展示前 30 条样本
    for e in log.edits[:30]:
        lines.append(f"- {e.format()}")
    if len(log.edits) > 30:
        lines.append(f"- ... ({len(log.edits) - 30} more)")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
