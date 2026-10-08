"""把候选站点探测结果**落盘**成 JSON，供扩源和复现使用。

为什么单独写
------------
``scripts/probe_candidates.py`` 探测完只打印到 stdout，**结果不留盘**——
2026-10-05 跑完 13 个候选，终端里看得见 7 个可用（jn_gov 63 条、
nc_gov 43、xa_gov 39、zz_gov 20、cs_gov 9、sz_hb_gov 9、wh_gov 8，
合计约 191 条，正好补上市级源这个最大缺口），但
``data/probe_results.jsonl`` 还停在 2026-10-02。终端滚一屏就没了，
更没法据此写进 sources.yaml。

所以这里重跑一遍并把结果写进 ``data/runtime/lighthouse/candidate_probe.json``，
每条含：HTTP 状态、可抽取条数、判定、以及（可用时）前几条标题样本——
标题样本是人工确认「抽出来的到底是公告还是栏目页」的唯一依据，
只看数字会误收。

用法：
    python scripts/probe_candidates.py --json-out data/runtime/lighthouse/candidate_probe.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT = ROOT / "data" / "runtime" / "lighthouse" / "candidate_probe.json"

#: (id, URL, 层级) —— 市级是当前最大缺口：112 个省级源 vs 4 个市级源。
CANDIDATES: list[tuple[str, str, str]] = [
    ("jn_gov", "https://www.jinan.gov.cn/col/col31337/index.html", "municipal"),
    ("zz_gov", "https://www.zhengzhou.gov.cn/", "municipal"),
    ("cs_gov", "https://www.changsha.gov.cn/", "municipal"),
    ("wh_gov", "https://www.wuhan.gov.cn/", "municipal"),
    ("nc_gov", "https://www.nanchang.gov.cn/", "municipal"),
    ("xa_gov", "https://www.xa.gov.cn/", "municipal"),
    ("sz_hb_gov", "https://www.sz.gov.cn/", "municipal"),
    ("ly_gov", "https://www.ly.gov.cn/", "municipal"),
    ("qd_gov", "https://www.qingdao.gov.cn/", "municipal"),
    ("sd_sz_gov", "https://www.sdsz.gov.cn/", "municipal"),
    ("nj_gov", "https://www.nanjing.gov.cn/", "municipal"),
    ("sz_gov", "https://www.suzhou.gov.cn/", "municipal"),
    ("nb_gov", "https://www.ningbo.gov.cn/", "municipal"),
]

MIN_LINKS = 5          # 少于这个数不值得加（加一个源要占回访间隔和预算）
BAD_TITLE = re.compile(r"(更多|more|下一页|上一页|首页|返回|导航|索引)")


def probe(sid: str, url: str, level: str) -> dict:
    import httpx

    from collectors.html_list import HtmlListCollector

    row = {"id": sid, "url": url, "gov_level": level, "http": 0, "links": 0,
           "titles": [], "verdict": "unknown", "reason": ""}
    try:
        src = {"id": sid, "url": url, "gov_level": level, "enabled": True,
               "max_items": 120, "archive_pages": 3, "base_url": url}
        collector = HtmlListCollector(src)
        try:
            html, base = collector._list_html()
            links = collector._extract_links(html, base) if html else []
            row["http"] = 200
            seen: set[str] = {str(x) for x in links}
            # 标题只能取**列表容器内**的锚文本。
            #
            # 踩过的坑：第一版直接抓全页 `a[href]`，结果 8 个"可用"站里有 4 个
            # 标题高度雷同（jn_gov / zz_gov / xa_gov 都抽出「…77 名…意见」、
            # 「2026 年特岗教师…」），wh_gov 甚至抽出 `FRAN?AIS` —— 那是
            # 全站导航/页脚里的链接，不是公告。全页 `<a>` 会把
            # 「首页/导航/友情链接」全收进来，于是**数字很大但一条公告都没有**，
            # 这正是之前会话拒绝长沙/南昌的理由（当时判断是「只拿到栏目页」，
            # 其实症状就是这个，只是当时没抓标题所以归因错了）。
            #
            # 正确做法：用 collector 自己的容器定位（`_container`），它已经
            # 按「链接密度 + 文本密度」挑出最像列表的那块。
            titles: list[str] = []
            if html:
                try:
                    node = collector._container(collector._soup(html))
                except Exception:                           # noqa: BLE001
                    node = None
                scope = node if node is not None else collector._soup(html)
                if node is None:
                    row["reason"] = "未定位到列表容器，标题可能混入导航"
                for a in scope.select("a[href]"):
                    text = (a.get_text(" ", strip=True) or "").strip()
                    if len(text) < 8 or BAD_TITLE.search(text):
                        continue
                    if "?" in text and text.isascii():
                        continue          # FRAN?AIS 这类乱码一律不要
                    titles.append(text[:60])
            row["titles"] = titles
            # 链接数只统计**容器内**的，避免全站导航把数字撑大
            row["links"] = len(titles) or len(seen)
        finally:
            collector.close()
    except Exception as exc:                                   # noqa: BLE001
        row["reason"] = f"{type(exc).__name__}: {exc}"[:120]
        row["verdict"] = "unreachable"
        return row

    if row["links"] >= MIN_LINKS:
        row["verdict"] = "usable"
    else:
        row["verdict"] = "too_few_links"
        row["reason"] = "只抽到 %d 条链接，低于门槛 %d" % (row["links"], MIN_LINKS)
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json-out", default=str(OUT))
    args = ap.parse_args()

    rows: list[dict] = []
    for sid, url, level in CANDIDATES:
        print("探测 %s ..." % sid, flush=True)
        row = probe(sid, url, level)
        rows.append(row)
        print("   HTTP %s  链接 %s  -> %s %s"
              % (row["http"], row["links"], row["verdict"], row["reason"]), flush=True)

    usable = [r for r in rows if r["verdict"] == "usable"]
    payload = {"probed_at": datetime.now().isoformat(timespec="seconds"),
               "min_links": MIN_LINKS,
               "usable_count": len(usable),
               "total_links": sum(r["links"] for r in usable),
               "sources": rows}
    out = Path(args.json_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 62)
    print("可用 %d / %d，合计约 %d 条链接"
          % (len(usable), len(rows), payload["total_links"]))
    for r in usable:
        print("   %-12s %-4s %4d 条" % (r["id"], r["gov_level"][:4], r["links"]))
    print("\n已写入 %s" % out)
    print("标题样本在 JSON 的 titles 字段 —— 人工确认「是公告不是栏目页」的唯一依据。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())