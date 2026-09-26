"""系统效率全面评估。"""
import sqlite3
import json
import time
from pathlib import Path
from datetime import datetime, timedelta

conn = sqlite3.connect('data/beacon.db')
c = conn.cursor()

print("=" * 60)
print("1. 数据量评估")
print("=" * 60)

# 各表数据量
tables = ['announcements', 'field_extractions', 'attachments', 'graph_nodes', 'graph_edges', 'graph_evidence', 'heat_topics', 'policy_topics']
for t in tables:
    try:
        c.execute(f"SELECT COUNT(*) FROM {t}")
        print(f"  {t}: {c.fetchone()[0]}")
    except:
        print(f"  {t}: 表不存在")

# DB 文件大小
db_size = Path('data/beacon.db').stat().st_size
print(f"\n  DB 文件大小: {db_size / 1024 / 1024:.1f} MB")

# announcements 表大小估算
c.execute("SELECT SUM(LENGTH(content)) FROM announcements")
total_content = c.fetchone()[0] or 0
print(f"  content 总字符数: {total_content:,}")
print(f"  content 平均字符/条: {total_content // 631 if 631 > 0 else 0:,}")

print("\n" + "=" * 60)
print("2. 采集来源评估")
print("=" * 60)

c.execute("SELECT COUNT(*) FROM source_sync_state")
print(f"  配置源数: {c.fetchone()[0]}")

c.execute("SELECT source_id, last_success_at, next_due_at, status, consecutive_failures FROM source_sync_state LIMIT 15")
print("  样本源调度状态:")
for row in c.fetchall():
    print(f"    {row[0]}: status={row[3]}, failures={row[4]}, last={row[1]}, next={row[2]}")

print("\n" + "=" * 60)
print("3. 数据质量评估")
print("=" * 60)

# 有 content 的比例
c.execute("SELECT COUNT(*) FROM announcements WHERE content IS NOT NULL AND LENGTH(content) > 100")
has_content = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM announcements")
total = c.fetchone()[0]
print(f"  有实质内容(>100字): {has_content}/{total} = {has_content/total*100:.1f}%")

# 有附件的
c.execute("SELECT COUNT(*) FROM announcements WHERE extra LIKE '%attachment_links%'")
has_attach = c.fetchone()[0]
print(f"  有附件链接: {has_attach}/{total} = {has_attach/total*100:.1f}%")

# 字段抽取覆盖率
c.execute("SELECT COUNT(DISTINCT announcement_id) FROM field_extractions")
has_extraction = c.fetchone()[0]
print(f"  有字段抽取: {has_extraction}/{total} = {has_extraction/total*100:.1f}%")

# needs_review 比例
c.execute("SELECT COUNT(*) FROM announcements WHERE needs_review = 1")
needs_review = c.fetchone()[0]
print(f"  需人工审核: {needs_review}/{total} = {needs_review/total*100:.1f}%")

# 印证状态
c.execute("SELECT corroboration_status, COUNT(*) FROM announcements GROUP BY corroboration_status")
print("  印证状态:")
for row in c.fetchall():
    print(f"    {row[0]}: {row[1]}")

print("\n" + "=" * 60)
print("4. 附件管线评估")
print("=" * 60)

c.execute("SELECT file_type, COUNT(*), AVG(text_length) FROM attachments GROUP BY file_type")
print("  附件提取:")
for row in c.fetchall():
    print(f"    {row[0]}: {row[1]} 条, 平均 {int(row[2])} 字/条")

c.execute("SELECT extract_status, COUNT(*) FROM attachments GROUP BY extract_status")
print("  提取状态:")
for row in c.fetchall():
    print(f"    {row[0]}: {row[1]}")

print("\n" + "=" * 60)
print("5. 图谱评估")
print("=" * 60)

c.execute("SELECT COUNT(*) FROM graph_nodes")
nodes = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM graph_edges")
edges = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM graph_evidence")
evidence = c.fetchone()[0]
print(f"  节点: {nodes}, 边: {edges}, 证据: {evidence}")
print(f"  平均边/节点: {edges/nodes:.1f}" if nodes > 0 else "")

c.execute("SELECT node_type, COUNT(*) FROM graph_nodes GROUP BY node_type")
print("  节点类型:")
for row in c.fetchall():
    print(f"    {row[0]}: {row[1]}")

print("\n" + "=" * 60)
print("6. 时间分布")
print("=" * 60)

c.execute("SELECT DATE(created_at) as d, COUNT(*) FROM announcements GROUP BY d ORDER BY d")
print("  按天分布:")
for row in c.fetchall():
    print(f"    {row[0]}: {row[1]} 条")

conn.close()
