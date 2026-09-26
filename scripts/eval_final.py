"""补充指标。"""
import sqlite3
import json

conn = sqlite3.connect('data/beacon.db')
c = conn.cursor()

# 印证率
c.execute("SELECT COUNT(*) FROM announcements WHERE corroboration_status='corroborated'")
corr = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM announcements")
total = c.fetchone()[0]
print(f"印证率: {corr}/{total} = {corr/total*100:.1f}%")

# 媒体聚类
c.execute("SELECT COUNT(*) FROM announcements WHERE media_cluster_id IS NOT NULL")
clustered = c.fetchone()[0]
print(f"媒体聚类覆盖: {clustered}/{total} = {clustered/total*100:.1f}%")

# 有内容但无字段抽取
c.execute("""
    SELECT COUNT(*) FROM announcements a
    WHERE LENGTH(a.content) > 200
    AND NOT EXISTS (SELECT 1 FROM field_extractions fe WHERE fe.announcement_id = a.id)
""")
print(f"有内容但无字段抽取: {c.fetchone()[0]}")

# content 长度分布
c.execute("SELECT AVG(LENGTH(content)), MAX(LENGTH(content)), MIN(LENGTH(content)) FROM announcements")
row = c.fetchone()
print(f"content 长度: avg={int(row[0])}, max={row[1]}, min={row[2]}")

# 失败源统计
c.execute("SELECT COUNT(*) FROM source_sync_state WHERE status='error'")
err_sources = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM source_sync_state")
all_sources = c.fetchone()[0]
print(f"\n失败源: {err_sources}/{all_sources} = {err_sources/all_sources*100:.1f}%")

# 成功源的采集量
c.execute("SELECT source_id, COUNT(*) FROM announcements GROUP BY source_id ORDER BY COUNT(*) DESC")
rows = c.fetchall()
print(f"有数据的源: {len(rows)}/{all_sources}")
print(f"总记录: {sum(r[1] for r in rows)}")
print(f"平均记录/源: {sum(r[1] for r in rows)/len(rows):.1f}")

conn.close()
