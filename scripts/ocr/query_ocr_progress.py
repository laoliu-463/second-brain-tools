"""查询OCR进度数据库"""

import sqlite3
from pathlib import Path

DB_PATH = Path("D:/第二大脑/ocr-pilot-20260825/ocr_progress.db")

def query_progress():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 查询每本书的进度
    cursor.execute("""
        SELECT book_id, status, COUNT(*) as pages 
        FROM progress 
        GROUP BY book_id, status 
        ORDER BY book_id, status
    """)
    
    results = cursor.fetchall()
    
    print("OCR进度统计：")
    print("=" * 60)
    for book_id, status, count in results:
        print(f"{book_id}: {status} - {count}页")
    
    # 查询书籍摘要
    cursor.execute("""
        SELECT name FROM sqlite_master WHERE type='table'
    """)
    
    tables = cursor.fetchall()
    print("\n数据库表：")
    for table in tables:
        print(f"  {table[0]}")
    
    # 从progress表直接统计
    cursor.execute("""
        SELECT book_id, status, COUNT(*) as pages 
        FROM progress 
        GROUP BY book_id, status 
        ORDER BY book_id, status
    """)
    
    results = cursor.fetchall()
    
    print("\n每本书完成情况：")
    print("=" * 60)
    for book_id, status, count in results:
        print(f"{book_id}: {status} - {count}页")
    
    conn.close()

if __name__ == "__main__":
    query_progress()