"""检测附件链接统计。"""
import sqlite3, re

db = sqlite3.connect("data/beacon.db")
rows = db.execute("SELECT id, title, content FROM announcements WHERE content IS NOT NULL").fetchall()
db.close()

pdf_count = 0
doc_count = 0
samples = []
for rid, title, content in rows:
    text = content or ""
    pdfs = re.findall(r"https?://[^\s\"'<>]+\.pdf", text, re.I)
    docs = re.findall(r"https?://[^\s\"'<>]+\.(?:doc|docx|xls|xlsx)", text, re.I)
    if pdfs:
        pdf_count += 1
        if len(samples) < 3:
            samples.append((rid, "pdf", pdfs[0][:80]))
    if docs:
        doc_count += 1
        if len(samples) < 5:
            samples.append((rid, "doc", docs[0][:80]))

print("总记录:", len(rows))
print("含PDF链接:", pdf_count)
print("含Word/Excel链接:", doc_count)
for s in samples:
    print("  样本:", s)
