"""评估摘要质量和管线整体效率。"""
import sqlite3
import json
import time
from pathlib import Path

conn = sqlite3.connect('data/beacon.db')
c = conn.cursor()

print("=" * 60)
print("7. 摘要质量评估")
print("=" * 60)

# 检查 extra 里的 summary
c.execute("SELECT extra FROM announcements WHERE extra LIKE '%summary%' LIMIT 200")
rows = c.fetchall()
summaries = []
for (extra_json,) in rows:
    try:
        extra = json.loads(extra_json)
        s = extra.get("summary")
        if s and len(s) > 10:
            summaries.append(s)
    except:
        pass

print(f"  有摘要的记录: {len(summaries)}/{len(rows)}")
if summaries:
    lengths = [len(s) for s in summaries]
    print(f"  摘要长度: min={min(lengths)}, max={max(lengths)}, avg={sum(lengths)//len(lengths)}")
    print(f"\n  样本摘要:")
    for s in summaries[:3]:
        print(f"    [{len(s)}字] {s[:100]}...")

print("\n" + "=" * 60)
print("8. 采集效率评估")
print("=" * 60)

# 每个源的记录数
c.execute("SELECT source_id, COUNT(*) FROM announcements GROUP BY source_id ORDER BY COUNT(*) DESC LIMIT 15")
print("  各源记录数 (top 15):")
for row in c.fetchall():
    print(f"    {row[0]}: {row[1]}")

# 失败的源
c.execute("SELECT source_id, consecutive_failures, last_error FROM source_sync_state WHERE status='error' OR consecutive_failures > 0")
print("\n  失败源:")
for row in c.fetchall():
    err = str(row[2])[:60] if row[2] else ""
    print(f"    {row[0]}: failures={row[1]}, error={err}")

print("\n" + "=" * 60)
print("9. 存储效率")
print("=" * 60)

# 各表大小
c.execute("SELECT name, SUM(pgsize) FROM dbstat GROUP BY name ORDER BY SUM(pgsize) DESC LIMIT 10")
try:
    print("  各表占用空间:")
    for row in c.fetchall():
        print(f"    {row[0]}: {row[1]/1024:.0f} KB")
except:
    print("  (dbstat 不可用)")

# content 平均长度
c.execute("SELECT AVG(LENGTH(content)), MAX(LENGTH(content)), MIN(LENGTH(content)) FROM announcements")
row = c.fetchone()
print(f"\n  content 长度: avg={int(row[0])}, max={row[1]}, min={row[2]}")

# 附件文本平均长度
c.execute("SELECT AVG(text_length), MAX(text_length) FROM attachments")
row = c.fetchone()
if row[0]:
    print(f"  附件文本长度: avg={int(row[0])}, max={row[1]}")

print("\n" + "=" * 60)
print("10. 管线瓶颈分析")
print("=" * 60)

# 检查 needs_review
c.execute("SELECT COUNT(*) FROM announcements WHERE needs_review=1")
print(f"  需审核: {c.fetchone()[0]}")

# 检查有 content 但无字段抽取的
c.execute("""
    SELECT COUNT(*) FROM announcements a
    WHERE LENGTH(a.content) > 200
    AND NOT EXISTS (SELECT 1 FROM field_extractions fe WHERE fe.announcement_id = a.id)
""")
print(f"  有内容但无字段抽取: {c.fetchone()[0]}")

# 检查印证率
c.execute("SELECT COUNT(*) FROM announcements WHERE corroboration_status='corroborated'")
corr = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM announcements")
total = c.fetchone()[0]
print(f"  印证率: {corr}/{total} = {corr/total*100:.1f}%")

# 检查媒体聚类
c.execute("SELECT COUNT(*) FROM announcements WHERE media_cluster_id IS NOT NULL")
clustered = c.fetchone()[0]
print(f"  媒体聚类覆盖: {clustered}/{total} = {clustered/total*100:.1f}%")

conn.close()
