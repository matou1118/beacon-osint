"""快速测试附件链接提取。"""
import sys
sys.path.insert(0, '.')

from main import load_sources, collector_for

sources = load_sources('config/sources.yaml')

for src in sources[:3]:
    src_id = src.get('source_id', '')
    print(f"\n=== {src_id} - {src.get('name', '')} ===")
    collector = collector_for(src)
    if not collector:
        print("  no collector")
        continue
    try:
        records = collector.collect(limit=2)
        for r in records:
            links = r.get('attachment_links') or []
            title = str(r.get('title', ''))[:50]
            print(f"  title: {title}")
            print(f"  attachment_links: {links}")
    except Exception as e:
        print(f"  error: {e}")
    finally:
        collector.close()
