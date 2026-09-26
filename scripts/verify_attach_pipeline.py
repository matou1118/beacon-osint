"""验证新附件管线：文件已删除，文本在DB。"""
import sqlite3
import tempfile
from pathlib import Path

# 1. 检查 data/attachments 目录
attach_dir = Path('data/attachments')
if attach_dir.exists():
    files = list(attach_dir.iterdir())
    print(f"data/attachments/ 文件数: {len(files)}")
else:
    print("data/attachments/ 目录不存在")

# 2. 检查 attachments 表
conn = sqlite3.connect('data/beacon.db')
c = conn.cursor()

# 检查表是否存在
c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='attachments'")
if c.fetchone():
    c.execute("SELECT COUNT(*) FROM attachments")
    total = c.fetchone()[0]
    print(f"\nattachments 表: {total} 条")

    c.execute("SELECT file_type, COUNT(*), SUM(text_length) FROM attachments GROUP BY file_type")
    for row in c.fetchall():
        print(f"  {row[0]}: {row[1]} 条, 总文本长度 {row[2]}")

    # 看几条样本
    c.execute("SELECT a.source_url, a.file_type, a.text_length, substr(a.extracted_text, 1, 100) FROM attachments a LIMIT 5")
    print("\n样本:")
    for row in c.fetchall():
        print(f"  [{row[1]}] {row[2]}字: {row[3][:60]}...")
        print(f"    url: {row[0][:80]}")
else:
    print("\nattachments 表不存在")

# 3. 检查临时目录没有残留
temp_dir = Path(tempfile.gettempdir())
temp_files = list(temp_dir.glob("*.docx")) + list(temp_dir.glob("*.pdf")) + list(temp_dir.glob("*.xlsx"))
print(f"\n临时目录残留文件: {len(temp_files)}")

conn.close()
