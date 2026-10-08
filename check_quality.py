import sqlite3
conn = sqlite3.connect(r'D:\TVbox\BeaconOSINT\data\beacon.db')
c = conn.cursor()

c.execute('SELECT COUNT(*) FROM announcements')
print('总记录数:', c.fetchone()[0])

c.execute('SELECT COUNT(*) FROM announcements WHERE content IS NOT NULL AND length(content) > 50')
print('有正文记录:', c.fetchone()[0])

c.execute('SELECT AVG(length(content)) FROM announcements WHERE content IS NOT NULL')
print('平均正文长度:', int(c.fetchone()[0] or 0))

c.execute('SELECT COUNT(*) FROM announcements WHERE content LIKE "%导航%" OR content LIKE "%联系我们%" OR content LIKE "%网站地图%" OR content LIKE "%友情链接%"')
print('疑似污染记录:', c.fetchone()[0])

c.execute('SELECT COUNT(*) FROM announcements WHERE content LIKE "%404%" OR content LIKE "%页面不存在%" OR content LIKE "%Not Found%"')
print('404污染记录:', c.fetchone()[0])

c.execute('SELECT COUNT(*) FROM announcements WHERE length(content) < 100')
print('正文过短记录:', c.fetchone()[0])

c.execute('SELECT source_id, COUNT(*) as cnt FROM announcements GROUP BY source_id ORDER BY cnt DESC LIMIT 10')
print('\nTop10 来源采集量:')
for row in c.fetchall():
    print(f'  {row[0]:30s} {row[1]} 条')

c.execute('SELECT source_id, COUNT(*) as cnt FROM announcements GROUP BY source_id ORDER BY cnt ASC LIMIT 10')
print('\nBottom10 来源采集量:')
for row in c.fetchall():
    print(f'  {row[0]:30s} {row[1]} 条')

conn.close()
