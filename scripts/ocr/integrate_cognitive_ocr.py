"""将认知系列OCR结果集成到文字层"""

import hashlib
import sys
from pathlib import Path
from pypdf import PdfReader

PROJECT_ROOT = Path(__file__).resolve().parent
OCR_DIR = Path("D:/第二大脑/ocr-cognitive-20260826")

# 中美博弈系列需要OCR的文件
OCR_FILES = {
    "china_us_01": {
        "title": "中美博弈+资本收割模型（一）",
        "source": PROJECT_ROOT / "资料库/原始资料/书籍与讲义/01_认知与思维/下载-认知-带数字/03-中美博弈系列/中美博弈+资本收割模型（一）.pdf",
        "output": PROJECT_ROOT / "资料库/文字层/书籍与讲义/01_认知与思维/下载-认知-带数字/03-中美博弈系列/中美博弈+资本收割模型（一）.md",
        "pages": 1
    },
    "china_us_02": {
        "title": "中美博弈+资本收割模型（二）",
        "source": PROJECT_ROOT / "资料库/原始资料/书籍与讲义/01_认知与思维/下载-认知-带数字/03-中美博弈系列/中美博弈+资本收割模型（二）.pdf",
        "output": PROJECT_ROOT / "资料库/文字层/书籍与讲义/01_认知与思维/下载-认知-带数字/03-中美博弈系列/中美博弈+资本收割模型（二）.md",
        "pages": 1
    },
    "china_us_03": {
        "title": "中美博弈+资本收割模型（三）",
        "source": PROJECT_ROOT / "资料库/原始资料/书籍与讲义/01_认知与思维/下载-认知-带数字/03-中美博弈系列/中美博弈+资本收割模型（三）.pdf",
        "output": PROJECT_ROOT / "资料库/文字层/书籍与讲义/01_认知与思维/下载-认知-带数字/03-中美博弈系列/中美博弈+资本收割模型（三）.md",
        "pages": 1
    },
    "china_us_04": {
        "title": "中美博弈＋资本收割模型（四）",
        "source": PROJECT_ROOT / "资料库/原始资料/书籍与讲义/01_认知与思维/下载-认知-带数字/03-中美博弈系列/中美博弈＋资本收割模型（四）.pdf",
        "output": PROJECT_ROOT / "资料库/文字层/书籍与讲义/01_认知与思维/下载-认知-带数字/03-中美博弈系列/中美博弈＋资本收割模型（四）.md",
        "pages": 1
    },
    "china_us_05": {
        "title": "中美博弈＋资本收割模型（五）",
        "source": PROJECT_ROOT / "资料库/原始资料/书籍与讲义/01_认知与思维/下载-认知-带数字/03-中美博弈系列/中美博弈＋资本收割模型（五）.pdf",
        "output": PROJECT_ROOT / "资料库/文字层/书籍与讲义/01_认知与思维/下载-认知-带数字/03-中美博弈系列/中美博弈＋资本收割模型（五）.md",
        "pages": 1
    },
    "china_us_06": {
        "title": "中美博弈＋资本收割模型（六）",
        "source": PROJECT_ROOT / "资料库/原始资料/书籍与讲义/01_认知与思维/下载-认知-带数字/03-中美博弈系列/中美博弈＋资本收割模型（六）.pdf",
        "output": PROJECT_ROOT / "资料库/文字层/书籍与讲义/01_认知与思维/下载-认知-带数字/03-中美博弈系列/中美博弈＋资本收割模型（六）.md",
        "pages": 1
    },
    "china_us_07": {
        "title": "中美博弈+资本收割模型（七）",
        "source": PROJECT_ROOT / "资料库/原始资料/书籍与讲义/01_认知与思维/下载-认知-带数字/03-中美博弈系列/中美博弈+资本收割模型（七）.pdf",
        "output": PROJECT_ROOT / "资料库/文字层/书籍与讲义/01_认知与思维/下载-认知-带数字/03-中美博弈系列/中美博弈+资本收割模型（七）.md",
        "pages": 8
    },
}

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()

def extract_ocr_text(book_id: str, title: str, pages: int) -> dict[int, str]:
    """从OCR结果文件中提取文本"""
    ocr_pages = {}
    # OCR系统生成的文件名格式：{book_id}_{title}_page{number}.pdf
    prefix = f"{book_id}_{title}_"
    
    for page_num in range(1, pages + 1):
        page_file = OCR_DIR / f"{prefix}page{page_num:03d}.pdf"
        if not page_file.exists():
            print(f"OCR文件不存在: {page_file}")
            continue
        
        try:
            reader = PdfReader(page_file, strict=False)
            if len(reader.pages) != 1:
                print(f"OCR文件页数错误: {page_file}")
                continue
            
            text = reader.pages[0].extract_text() or ""
            ocr_pages[page_num] = text
        except Exception as e:
            print(f"读取OCR文件失败: {page_file}, 错误: {e}")
    
    return ocr_pages

def build_markdown(book_id: str, config: dict, ocr_pages: dict[int, str]) -> str:
    """构建Markdown文字层"""
    source_path = config["source"]
    output_path = config["output"]
    title = config["title"]
    pages = config["pages"]
    
    if not source_path.exists():
        print(f"源文件不存在: {source_path}")
        return ""
    
    source_hash = sha256_file(source_path)
    text_pages = sum(bool(text.strip()) for text in ocr_pages.values())
    text_chars = sum(len(text) for text in ocr_pages.values())
    
    frontmatter = f"""---
type: source-transcript
status: extracted
confidence: medium
source_format: pdf
source_file: "{source_path}"
source_sha256: {source_hash}
pages: {pages}
text_pages: {text_pages}
text_chars: {text_chars}
extracted: 2026-08-26
extraction_method: ocrmypdf
ocr_engine: OCRmyPDF 17.10.0 / Tesseract 5.5.3
quality_note: OCR正文可检索；数字、专名、图表和强因果判断仍须对照原始PDF核验。
---

# {title}

> 本页是对原始文件的自动文字提取层，不等同于人工校对稿。原始文件保持只读。
> 低置信度或待OCR文件不得直接作为事实依据。
"""

    body = []
    for page_num in range(1, pages + 1):
        text = ocr_pages.get(page_num, "")
        body.append(f"\n## Page {page_num}\n")
        if text.strip():
            body.append(text)
        else:
            body.append("[本页 OCR 未识别到文本，请对照原始 PDF。]")
    
    return frontmatter + "\n".join(body)

def main() -> int:
    print("开始集成认知系列OCR结果到文字层...")
    
    success_count = 0
    for book_id, config in OCR_FILES.items():
        print(f"处理: {config['title']}")
        
        # 提取OCR文本
        ocr_pages = extract_ocr_text(book_id, config["title"], config["pages"])
        
        if not ocr_pages:
            print(f"OCR文本提取失败: {config['title']}")
            continue
        
        # 构建Markdown
        markdown = build_markdown(book_id, config, ocr_pages)
        
        if not markdown:
            print(f"Markdown构建失败: {config['title']}")
            continue
        
        # 写入文件
        output_path = config["output"]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(markdown, encoding="utf-8")
        
        print(f"成功写入: {output_path}")
        success_count += 1
    
    print(f"集成完成: {success_count}/{len(OCR_FILES)} 成功")
    return 0 if success_count == len(OCR_FILES) else 1

if __name__ == "__main__":
    sys.exit(main())