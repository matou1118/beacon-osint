"""抓真实列表页 HTML 作为自修复的离线夹具。

用项目自己的 collector._request —— 它内部强制 robots 检查和 3 秒限速
（collectors/base.py 的 _assert_robots_allows / _wait_for_rate_limit），
所以这里不绕过任何东西。抓完就落盘，之后的验证全程离线、不再发请求。

只抓少数几个源，且间隔 5 秒，不给正在跑的采集加负担。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lighthouse.routes import build_collector

OUT = Path("tests/fixtures/sel_heal")
INTERVAL = 5

# 优先挑「列表页直接含文章链接」的 enabled 源：只有这类源的 detail_pattern
# 才可能从列表页反推出来。
#
# 反例（第一版夹具踩过）：zj_fgw / js_fgw 的列表页是**栏目索引**，链接全是
# /col/colN/index.html，文章在子页 /art/... 上。对这类源，derive_pattern
# 正确地拒绝从列表页造 pattern —— repair.py 的原话是「把它当 detail_pattern
# 写回去，采回来的全是子栏目，是另一种垃圾」。它们的 list_selector 失效时
# 该修的是「进哪个子栏目」，属于另一类问题，不在本阶段范围。
#
# 部委那批的列表页本身就是文章流（own=/gkml/、/tzgg/、/jyb_xwfb/…），
# 是本阶段要修的形态。
WANTED = ["mof_gov", "moa_gov", "mee_gov", "mct_gov", "mohrss_gov"]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    data = yaml.safe_load(Path("config/sources.yaml").read_text(encoding="utf-8")) or {}
    by_id = {str(s.get("id")): s for s in (data.get("sources") or [])}

    saved = []
    for sid in WANTED:
        src = by_id.get(sid)
        if not src:
            print("  %-12s 不在注册表" % sid)
            continue
        url = str(src.get("url") or "")
        if not url:
            print("  %-12s 没有 url" % sid)
            continue
        collector = build_collector(dict(src))
        try:
            response = collector._request(url)          # robots 检查 + 限速都在里面
            html = str(getattr(response, "text", "") or "")
        except Exception as exc:                        # noqa: BLE001
            print("  %-12s 抓取失败 %s: %s" % (sid, type(exc).__name__, str(exc)[:60]))
            continue
        finally:
            try:
                collector.close()
            except Exception:
                pass
        if len(html) < 2000:
            print("  %-12s 内容过短(%d)，可能需渲染，跳过" % (sid, len(html)))
            continue
        path = OUT / f"{sid}.html"
        path.write_text(html, encoding="utf-8")
        meta = OUT / f"{sid}.json"
        meta.write_text(json.dumps({
            "source_id": sid, "url": url,
            "list_selector": src.get("list_selector"),
            "link_selector": src.get("link_selector"),
            "detail_pattern": src.get("detail_pattern"),
            "bytes": len(html),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        saved.append(sid)
        print("  %-12s %7d 字 -> %s" % (sid, len(html), path))
        time.sleep(INTERVAL)

    print("\n抓到 %d 份夹具: %s" % (len(saved), ", ".join(saved)))
    return 0 if saved else 1


if __name__ == "__main__":
    raise SystemExit(main())
