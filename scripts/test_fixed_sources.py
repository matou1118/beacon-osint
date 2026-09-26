"""测试之前失败的源是否修复。"""
import sys
sys.path.insert(0, '.')

from main import load_sources, collector_for

# 之前失败的源
test_ids = ['sc_ndrc_gov', 'gd_swt', 'hk_gov', 'sc_ha_gov', 'mofcom_gov']

sources = load_sources('config/sources.yaml')
source_map = {s.get('id', ''): s for s in sources}

for sid in test_ids:
    src = source_map.get(sid)
    if not src:
        print(f"\n{sid}: 未找到配置")
        continue
    print(f"\n=== {sid} - {src.get('name', '')} ===")
    print(f"  url: {src.get('url', '')}")
    print(f"  render_mode: {src.get('render_mode', '')}")
    print(f"  playwright_fallback: {src.get('playwright_fallback', False)}")

    collector = collector_for(src)
    if not collector:
        print("  no collector")
        continue
    try:
        records = collector.collect(limit=3)
        print(f"  采集到 {len(records)} 条记录")
        for r in records[:2]:
            title = str(r.get('title', ''))[:50]
            print(f"    - {title}")
        errors = getattr(collector, 'errors', [])
        if errors:
            print(f"  errors: {errors[:2]}")
    except Exception as e:
        print(f"  采集失败: {e}")
    finally:
        collector.close()
