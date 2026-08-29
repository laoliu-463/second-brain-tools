# OCR Pipeline (第二大脑专用)

> 2026-08-26：迁移后入口为 `scripts/ocr/ocr_run.py`，默认配置在 `config/ocr/books.yaml`。批量全文比对与审定修正见 [文字层批量校对](./文字层批量校对.md)。下文九本书规模、旧输出路径和故障记录属于历史运行说明，不代表当前全库校对状态。

## 概览

一个由 manifest 驱动、内存节流、错误自愈的 OCR 批处理流水线，用于处理
第二大脑原始资料库中的 PDF 书籍。

| 组件 | 文件 | 职责 |
|---|---|---|
| 入口 | `ocr_run.py` | 读 manifest → 起 monitor → 按书分派给 PageWorker |
| Manifest | `books.yaml` | 9 本书的元数据（路径、页数、模式、优先级） |
| 包 | `ocr_pipeline/` | 模块化的实现 |
| ├ state | `state.py` | SQLite (progress + skipped) + psutil 内存监控 |
| ├ ocr | `ocr.py` | 单页 ocrmypdf 子进程封装 |
| ├ worker | `worker.py` | ThreadPoolExecutor + 动态并发 + 重试 + 永久跳过 |
| └ manifest | `manifest.py` | YAML 加载 + 校验 |

## 设计要点

### 1. 真实并发 vs 旧脚本
旧 `ocr_scheduler_fixed.py` 写了 `MAX_CONCURRENT_TASKS=3` 但
`for page in pending_pages:` 是**严格串行**。新版本用 `ThreadPoolExecutor`，
按内存水位动态开 0/1/2/3 worker。

### 2. 内存节流
`MemoryMonitor.sample()` 看 psutil 可用内存：
- < 800 MB  → 0 worker（等待）
- < 1500 MB → 1 worker
- < 2500 MB → 2 worker
- 其余      → 3 worker（默认上限）

总内存 6GB 减系统占 ~1.5GB ≈ 4.5GB 可用；ocrmypdf 单页峰值约 1GB。

### 3. 错误处理
- **可恢复**：returncode -1（timeout/包装异常）→ 指数退避重试
- **不可恢复**：returncode 6/11/30（xref/image/unfixable）→ 直接进 skipped 表
- **达到 max_attempts**（默认 3）→ 永久进 skipped 表
- skipped 表里记录 `first_error` 和 `last_error`，便于后期决定是否人工修

### 4. Manifest 驱动
所有书都在 `books.yaml` 里，新加书只改 YAML 不改代码：

```yaml
- id: unique_book_id
  title: 显示名
  source: 绝对 PDF 路径
  pages: 真实页数
  mode: redo | skip | force
  priority: 数字越小越先跑
  language: chi_sim+eng
  extra_args: ["--skip-big", "50"]   # 可选
  enabled: true
```

`priority=10` 是旧 pilot 残留 4 本，`priority=50` 是新一批 5 本（"先旧后新"）。

### 5. Resume
`PipelineState.ensure_book_pages` 只在首次见到一本书时初始化 pending，
已存在的 (book_id, page) UNIQUE 约束会自动跳过。
重启脚本 = 接着上次跑。

## 使用

### 5.1 先 dry-run 校验 manifest

```bash
cd "D:/第二大脑"
python scripts/ocr/ocr_run.py --dry-run
```

应输出 9 行书名 + 页数。看到 manifest error 就修 YAML。

### 5.2 小样本验证（推荐第二步）

```bash
python ocr_run.py --sample 3
```

每本书只跑前 3 页，确认：
- ocrmypdf 能起来
- 路径都正确
- 输出目录有 `_page001.pdf`

### 5.3 跑一本书

```bash
python ocr_run.py --book cs_os_自己动手写操作系统
```

### 5.4 正式跑全部

```bash
python ocr_run.py --max-workers 3 --max-attempts 3
```

输出进度：

```
11:33:50 INFO  ocr_run: loaded manifest: 9 books
11:33:50 INFO  ocr_run: starting book=algo_intro_第3版 pages=52 pending=52 workers<=3
11:33:52 INFO  ocr_run: done book=algo_intro_第3版 page=1
...
```

### 5.5 中途暂停 / 恢复
`Ctrl+C` 触发 SIGINT → worker.stop() → 跑完当前页 → 退出。
直接重跑即恢复（pending 表还在）。

### 5.6 监控进度

```bash
sqlite3 "D:/第二大脑/ocr-pilot-20260825/ocr_progress.db" \
  "SELECT book_title, status, COUNT(*) FROM progress GROUP BY book_title, status"
```

或

```bash
python check_db.py
```

## 输出文件

每本书每页输出一个 PDF：
```
D:/第二大脑/ocr-pilot-20260825/
├── ocr_progress.db          # SQLite 进度 + skipped
├── 自己动手写操作系统_page001.pdf
├── 自己动手写操作系统_page002.pdf
├── ...
├── 经济学原理第7版_微观经济学分册_page001.pdf
└── ...
```

文件名格式：`<book_id>_<safe_title>_page<NNN>.pdf`

## 已知坑

1. **mojibake 显示**：Windows cmd 默认编码是 cp936/GBK，把 UTF-8 当 GBK 解会乱码。
   实际 SQLite 里存的是正确 UTF-8，只是 print 出来是乱码。
   用 `PYTHONIOENCODING=utf-8` 或 Python 自己的输出即可。

2. **自己动手写操作系统** 1–45 页 xref 200 不可恢复 → 走 skipped 表。
   已加 `--skip-big 50` 减少类似问题。

3. **曼昆宏观经济学** 原文件名末尾疑似重复（Z-Library 产物）。
   manifest 里加引号强制按字面字符串。

4. **Tesseract 路径**：默认在 `C:\Program Files\Tesseract-OCR`。
   改了路径改 `OcrRunner.TESSDATA_PATHS`。

5. **chi_sim.traineddata**：要保证就位。仓库根目录就有备份
   （44MB），必要时复制到 tessdata 下。

## 与旧版对比

| 维度 | `ocr_scheduler_fixed.py` | 新 `ocr_run.py` |
|---|---|---|
| 并发 | 串行（注释写了 3 worker 没实现） | 真实 ThreadPoolExecutor |
| 内存节流 | 只检查、单步 sleep 30s | 动态 0/1/2/3 worker |
| 重试 | retry_count 写库但没人重读 | 失败自动 backoff 重试 |
| 跳过 | DB 标 failed 不区分 | 可恢复 vs 永久 skipped 两表 |
| 配置 | 4 本书硬编码 | 9 本 YAML |
| 信号处理 | 无 | SIGINT/SIGBREAK graceful stop |
| Sample 模式 | 无 | `--sample N` |
| 校验 | 无 | `--dry-run` |
