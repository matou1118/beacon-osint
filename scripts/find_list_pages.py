"""在候选门户里找「通知公告」栏目页，并验证它取不取得到公告。

首页能取到链接不代表首页就是好的采集入口——实测首页多为新闻（习近平…、
政府常务会议…），而我们要的是政策公告。所以逐个探测常见栏目路径，
按「取到的公告链接里有多少条含通知/公告/公示/文件」来选入口。

用法
----
    python scripts/find_list_pages.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SITES: dict[str, str] = {
    "jn_gov": "https://www.jining.gov.cn/",
    "zz_gov": "https://www.zhengzhou.gov.cn/",
    "cs_gov": "https://www.changsha.gov.cn/",
    "wh_gov": "https://www.wuhan.gov.cn/",
    "nc_gov": "https://www.nc.gov.cn/",
    "xa_gov": "https://www.xa.gov.cn/",
    "sz_hb_gov": "https://www.suzhou.gov.cn/",
}

# 常见「通知公告 / 政府信息公开 / 政策文件」栏目路径片段
LIST_PATTERNS = [
    r"/zwgk/[\w/]*?(?:tzgg|gg|gsgg|zcwj|zfwj|xxgk)",
    r"/(?:tzgg|gsgg|zcwj|zfwj)/",
    r"/info/[\d/]+",
    r"/xxgk/",
    r"/zfwj/",
]

GENRE = re.compile(r"(通知|公告|公示|通告|文件|办法|规定|意见|批复)")
DATE = re.compile(r"20\d{2}[-年/]\s?\d{1,2}[-月/]\s?\d{1,2}")


def links_of(html: str) -> list[tuple[str, str]]:
    out = []
    for href, text in re.findall(
            r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
            html, re.S | re.I):
        clean = re.sub(r"<[^>]+>", "", text)
        clean = re.sub(r"\s+", " ", clean).strip()
        if len(clean) >= 8:
            out.append((href, clean))
    return out


def score(html: str) -> tuple[int, list[str]]:
    """返回「像政策公告的链接条数」和样例。"""
    hits = []
    for href, text in links_of(html):
        if GENRE.search(text) and DATE.search(text):
            hits.append(text[:46])
        elif GENRE.search(text):
            hits.append(text[:46])
    return len(hits), hits


def main() -> int:
    for sid, base in SITES.items():
        print("=" * 70)
        print(sid, base)
        try:
            home = httpx.get(base, timeout=20, follow_redirects=True,
                             trust_env=False,
                             headers={"User-Agent": "Mozilla/5.0"}).text
        except Exception as exc:                        # noqa: BLE001
            print("  首页失败 %s" % type(exc).__name__)
            continue

        # 从首页链接里找出所有看起来像栏目页的
        candidates: list[str] = []
        for href, text in links_of(home):
            if re.search(r"(通知公告|政务公开|信息公开|政策文件|公示)", text):
                full = href if href.startswith("http") else _join(base, href)
                if full and full not in candidates:
                    candidates.append(full)
        # 再补几个常见路径
        for pat in LIST_PATTERNS:
            candidates.append(base.rstrip("/") + pat)

        best: tuple[int, str, list[str]] | None = None
        for url in candidates[:10]:
            try:
                html = httpx.get(url, timeout=15, follow_redirects=True,
                                 trust_env=False,
                                 headers={"User-Agent": "Mozilla/5.0"}).text
            except Exception:                           # noqa: BLE001
                continue
            count, hits = score(html)
            if best is None or count > best[0]:
                best = (count, url, hits)
        if best and best[0] >= 3:
            print("  最佳栏目: %s  (%d 条公告样链接)" % (best[1], best[0]))
            for h in best[2][:3]:
                print("     - %s" % h)
        else:
            print("  没找到合适的栏目页（首页不够）")
    return 0


def _join(base: str, href: str) -> str:
    from urllib.parse import urljoin
    return urljoin(base, href)


if __name__ == "__main__":
    raise SystemExit(main())