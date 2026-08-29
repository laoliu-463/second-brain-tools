import sqlite3

conn = sqlite3.connect(r'D:\第二大脑\ocr-pilot-20260825\ocr_progress.db')
cursor = conn.cursor()

# 查看所有失败记录
cursor.execute('SELECT * FROM progress WHERE status="failed" ORDER BY book_name, page')
failed_rows = cursor.fetchall()

print("失败记录:")
for row in failed_rows:
    print(row)

# 查看进度统计
cursor.execute('SELECT book_name, status, COUNT(*) FROM progress GROUP BY book_name, status')
stats = cursor.fetchall()

print("\n进度统计:")
for row in stats:
    print(row)

conn.close()