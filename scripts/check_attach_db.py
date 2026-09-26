"""查数据库附件入库情况。"""
import sqlite3
import json

conn = sqlite3.connect('data/beacon.db')
c = conn.cursor()
c.execute('SELECT COUNT(*) FROM announcements')
print(f'announcements: {c.fetchone()[0]}')

c.execute("SELECT source_id, title, extra FROM announcements WHERE extra LIKE '%attachment%' LIMIT 10")
rows = c.fetchall()
print(f'records with attachment in extra: {len(rows)}')
for r in rows:
    extra = json.loads(r[2]) if r[2] else {}
    links = extra.get('attachment_links', [])
    count = extra.get('attachment_count', 0)
    print(f'  {r[0]}: {str(r[1])[:40]} -> {len(links)} links, count={count}')

# 也查 data/attachments 目录
from pathlib import Path
attach_dir = Path('data/attachments')
if attach_dir.exists():
    files = list(attach_dir.iterdir())
    print(f'\nattachment files downloaded: {len(files)}')
    for f in files[:5]:
        print(f'  {f.name} ({f.stat().st_size} bytes)')
else:
    print('\nno data/attachments directory')

conn.close()
