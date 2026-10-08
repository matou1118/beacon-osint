"""验证 A4 接线真的被触发：走 fetch_list（内部会调 _list_html -> 观测）。

做法：在内存里给源塞一个必定失效的 list_selector，然后 fetch_list。
如果接线生效，selector_events 表会多出一行 broken 事件；不生效就是 0。
不改 config/sources.yaml。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lighthouse import repair
from lighthouse.routes import build_collector

TARGET = "zj_fgw"


def main() -> int:
    data = yaml.safe_load(Path("config/sources.yaml").read_text(encoding="utf-8")) or {}
    src = next(s for s in (data.get("sources") or []) if str(s.get("id")) == TARGET)

    before = repair.stats()
    print("接线前事件数:", before["total"])

    broken = dict(src)
    broken["list_selector"] = str(src.get("list_selector") or "body") + "__a4_broken"
    broken["playwright_fallback"] = False       # 别把浏览器也拉起来，慢且没必要

    collector = build_collector(broken)
    try:
        links = collector.fetch_list()
        notes = getattr(collector, "_notes", None)
        notes = notes() if callable(notes) else (notes or [])
    except Exception as exc:                        # noqa: BLE001
        links, notes = [], [f"fetch_list 抛错 {type(exc).__name__}: {str(exc)[:60]}"]
    finally:
        try:
            collector.close()
        except Exception:
            pass

    print("fetch_list 返回 %d 条" % len(links))
    print("collector notes:")
    for n in (notes or [])[:6]:
        print("   ", n if isinstance(n, str) else n)

    time.sleep(1)
    after = repair.stats()
    print("\n接线后事件数: %d  (新增 %d)" % (after["total"], after["total"] - before["total"]))
    print("明细:", json.dumps(after["breakdown"], ensure_ascii=False))

    from lighthouse import event_bus
    rows = [e for e in event_bus.read_events()
            if e.get("scope") == "source_id:" + TARGET][:3]
    for r in rows:
        ex = r.get("extra") or {}
        print("  事件 %s | %s | old=%r | result=%s"
              % (str(r.get("event_id", ""))[:20], r.get("failure_type"),
                 (ex.get("old_selector") or "")[:34], r.get("repair_result")))
        print("       原因: %s" % (r.get("detail") or "")[:96])

    fired = after["total"] > before["total"]
    print("\n接线%s" % ("生效：失效入口被记成事件" if fired else "未生效：没有事件产生"))
    return 0 if fired else 1


if __name__ == "__main__":
    raise SystemExit(main())
