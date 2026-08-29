"""转换旧版.doc文件为.docx，以便后续文字提取"""

import sys
from pathlib import Path
import win32com.client as win32

PROJECT_ROOT = Path(__file__).resolve().parent
DOC_FILES = [
    PROJECT_ROOT / "资料库/原始资料/书籍与讲义/01_认知与思维/下载-认知-界面/02-人智认知系列（0-40）/（1-20篇合集）.doc",
    PROJECT_ROOT / "资料库/原始资料/书籍与讲义/01_认知与思维/下载-认知-界面/02-人智认知系列（0-40）/21-40合集.doc",
]

def convert_doc_to_docx(doc_path: Path) -> bool:
    """使用Microsoft Word转换.doc为.docx"""
    if not doc_path.exists():
        print(f"文件不存在: {doc_path}")
        return False
    
    docx_path = doc_path.with_suffix('.docx')
    if docx_path.exists():
        print(f"目标文件已存在: {docx_path}")
        return True
    
    try:
        word = win32.Dispatch("Word.Application")
        word.Visible = False
        doc = word.Documents.Open(str(doc_path.absolute()))
        doc.SaveAs(str(docx_path.absolute()), FileFormat=16)  # 16 = wdFormatXMLDocument (docx)
        doc.Close()
        word.Quit()
        print(f"转换成功: {doc_path} -> {docx_path}")
        return True
    except Exception as e:
        print(f"转换失败: {doc_path}")
        print(f"错误: {e}")
        return False

def main() -> int:
    print("开始转换旧版.doc文件...")
    success_count = 0
    for doc_path in DOC_FILES:
        if convert_doc_to_docx(doc_path):
            success_count += 1
    
    print(f"转换完成: {success_count}/{len(DOC_FILES)} 成功")
    return 0 if success_count == len(DOC_FILES) else 1

if __name__ == "__main__":
    sys.exit(main())