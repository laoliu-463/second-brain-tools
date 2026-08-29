"""为新生成的.docx文件提取文字层"""

import hashlib
import sys
from pathlib import Path
import docx

PROJECT_ROOT = Path(__file__).resolve().parent

# 新生成的.docx文件
DOCX_FILES = {
    "collection_1_20": {
        "title": "（1-20篇合集）",
        "source": PROJECT_ROOT / "资料库/原始资料/书籍与讲义/01_认知与思维/下载-认知-界面/02-人智认知系列（0-40）/（1-20篇合集）.docx",
        "output": PROJECT_ROOT / "资料库/文字层/书籍与讲义/01_认知与思维/下载-认知-界面/02-人智认知系列（0-40）/（1-20篇合集）.docx.md",
    },
    "collection_21_40": {
        "title": "21-40合集",
        "source": PROJECT_ROOT / "资料库/原始资料/书籍与讲义/01_认知与思维/下载-认知-界面/02-人智认知系列（0-40）/21-40合集.docx",
        "output": PROJECT_ROOT / "资料库/文字层/书籍与讲义/01_认知与思维/下载-认知-界面/02-人智认知系列（0-40）/21-40合集.docx.md",
    },
}

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()

def extract_docx_text(docx_path: Path) -> str:
    """从.docx文件中提取文本"""
    try:
        doc = docx.Document(docx_path)
        paragraphs = []
        for para in doc.paragraphs:
            if para.text.strip():
                paragraphs.append(para.text)
        return "\n".join(paragraphs)
    except Exception as e:
        print(f"读取.docx文件失败: {docx_path}, 错误: {e}")
        return ""

def build_markdown(config: dict, text: str) -> str:
    """构建Markdown文字层"""
    source_path = config["source"]
    output_path = config["output"]
    title = config["title"]
    
    if not source_path.exists():
        print(f"源文件不存在: {source_path}")
        return ""
    
    source_hash = sha256_file(source_path)
    text_chars = len(text)
    text_lines = len(text.split('\n')) if text else 0
    
    frontmatter = f"""---
type: source-transcript
status: extracted
confidence: medium
source_format: docx
source_file: "{source_path}"
source_sha256: {source_hash}
pages: {text_lines}
text_pages: {text_lines}
text_chars: {text_chars}
extracted: 2026-08-26
extraction_method: python-docx
quality_note: 从.docx文件自动提取文本；数字、专名、格式和强因果判断仍须对照原始文件核验。
---

# {title}

> 本页是对原始文件的自动文字提取层，不等同于人工校对稿。原始文件保持只读。
> 低置信度或待OCR文件不得直接作为事实依据。
"""

    body = []
    if text.strip():
        body.append(f"\n## 内容\n\n{text}")
    else:
        body.append(f"\n## 内容\n\n[本文件未提取到文本内容，请对照原始文件。]")
    
    return frontmatter + "\n".join(body)

def main() -> int:
    print("开始为新生成的.docx文件提取文字层...")
    
    success_count = 0
    for file_id, config in DOCX_FILES.items():
        print(f"处理: {config['title']}")
        
        # 提取文本
        text = extract_docx_text(config["source"])
        
        if not text:
            print(f"文本提取失败: {config['title']}")
            continue
        
        # 构建Markdown
        markdown = build_markdown(config, text)
        
        if not markdown:
            print(f"Markdown构建失败: {config['title']}")
            continue
        
        # 写入文件
        output_path = config["output"]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(markdown, encoding="utf-8")
        
        print(f"成功写入: {output_path}")
        success_count += 1
    
    print(f"提取完成: {success_count}/{len(DOCX_FILES)} 成功")
    return 0 if success_count == len(DOCX_FILES) else 1

if __name__ == "__main__":
    sys.exit(main())