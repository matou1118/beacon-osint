"""测试附件下载+文本提取。"""
import sys
sys.path.insert(0, '.')

from main import load_sources, collector_for
from extractors.attachment_downloader import process_record_attachments

sources = load_sources('config/sources.yaml')

src = sources[0]  # 财政部
print(f"=== {src.get('name', '')} ===")
collector = collector_for(src)
if collector:
    try:
        records = collector.collect(limit=2)
        for r in records:
            links = r.get('attachment_links') or []
            title = str(r.get('title', ''))[:50]
            print(f"\n  title: {title}")
            print(f"  attachment_links: {links}")
            if links:
                r['source_id'] = src.get('source_id', 'test')
                process_record_attachments(r)
                print(f"  attachment_count: {r.get('attachment_count', 0)}")
                for t in r.get('attachment_texts', []):
                    print(f"    file: {t['file']}")
                    print(f"    text (first 200): {t['text'][:200]}")
    finally:
        collector.close()
