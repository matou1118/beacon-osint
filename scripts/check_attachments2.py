"""深度检测附件链接：查 content 中的所有 URL 和 href。"""
import sqlite3, re

db = sqlite3.connect("data/beacon.db")
rows = db.execute("SELECT id, title, content, source_url FROM announcements WHERE content IS NOT NULL LIMIT 50").fetchall()
db.close()

all_urls = []
href_count = 0
for rid, title, content, source_url in rows:
    text = content or ""
    # 查所有 href
    hrefs = re.findall(r'href=["\']?([^"\'\s>]+)', text, re.I)
    if hrefs:
        href_count += 1
        all_urls.extend(hrefs[:5])
    # 查所有 URL
    urls = re.findall(r"https?://[^\s\"'<>]+", text)
    all_urls.extend(urls[:5])

print("检查 50 条记录")
print("含 href 的记录:", href_count)
print("总 URL 数:", len(all_urls))
# 按扩展名分类
from collections import Counter
exts = Counter()
for url in all_urls:
    m = re.search(r"\.(\w+)(?:[?#]|$)", url)
    if m:
        exts[m.group(1).lower()] += 1
    else:
        exts["(no ext)"] += 1
print("URL 扩展名分布:")
for ext, count in exts.most_common(10):
    print(f"  .{ext}: {count}")
# 样本
print("\nURL 样本:")
for url in all_urls[:10]:
    print(f"  {url[:100]}")
