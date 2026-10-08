"""探测候选新源的列表页，报告能不能取到公告链接。

只做探测，不写配置。判据是**能不能取到像公告的链接**，而不是首页能不能打开
——首页 200 不代表列表页有数据（实测 miit.gov.cn 首页 200 但列表页要渲染）。

用法
----
    python scripts/probe_candidates.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 候选：四川之外的省市，优先「政府信息公开-通知公告」这类稳定栏目。
# 选取依据是各省政府门户的公开导航结构，不猜域名。
CANDIDATES: list[tuple[str, str, str, str]] = [
    # (id, 名称, 层级, URL)
    ("sd_sz_gov", "深圳市人民政府", "municipal", "https://www.sz.gov.cn/"),
    ("qd_gov", "青岛市人民政府", "municipal", "https://www.qingdao.gov.cn/"),
    ("jn_gov", "济宁市人民政府", "municipal", "https://www.jining.gov.cn/"),
    ("zz_gov", "郑州市人民政府", "municipal", "https://www.zhengzhou.gov.cn/"),
    ("ly_gov", "洛阳市人民政府", "municipal", "https://www.ly.gov.cn/"),
    ("cs_gov", "长沙市人民政府", "municipal", "https://www.changsha.gov.cn/"),
    ("wh_gov", "武汉市人民政府", "municipal", "https://www.wuhan.gov.cn/"),
    ("hf_gov", "合肥市人民政府", "municipal", "https://www.hefei.gov.cn/"),
    ("fz_gov", "福州市人民政府", "municipal", "https://www.fuzhou.gov.cn/"),
    ("nc_gov", "南昌市人民政府", "municipal", "https://www.nc.gov.cn/"),
    ("xa_gov", "西安市人民政府", "municipal", "https://www.xa.gov.cn/"),
    ("nj_gov", "南京市人民政府", "municipal", "https://www.nanjing.gov.cn/"),
    ("sz_hb_gov", "苏州市人民政府", "municipal", "https://www.suzhou.gov.cn/"),
]

# 像公告的链接：路径里有通知/公告/公示/文件，或者带日期的详情页
DOC_URL = re.compile(
    r"/(?:content|art|info|detail|notice|article)[^/]*"
    r"|(?:通知|公告|公示|通告|文件|政策)", re.I)
DATE_IN_TEXT = re.compile(r"(20\d{2})[-年/]\s?\d{1,2}[-月/]\s?\d{1,2}")


def probe(url: str) -> tuple[int, int, list[str]]:
    try:
        response = httpx.get(url, timeout=25, follow_redirects=True,
                             trust_env=False,
                             headers={"User-Agent": "Mozilla/5.0"})
    except Exception as exc:                            # noqa: BLE001
        print("    请求失败 %s" % type(exc).__name__)
        return 0, 0, []
    html = response.text
    links = re.findall(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
                       html, re.S | re.I)
    hits: list[str] = []
    for href, text in links:
        clean = re.sub(r"<[^>]+>", "", text).strip()
        if len(clean) < 6:
            continue
        if DOC_URL.search(href) or DATE_IN_TEXT.search(clean):
            hits.append(clean[:44])
    return response.status_code, len(hits), hits


def main() -> int:
    print("探测候选源（关注「能否取到公告链接」，首页 200 不算数）\n")
    ok: list[tuple[str, str, str, str, int]] = []
    for sid, name, level, url in CANDIDATES:
        status, count, hits = probe(url)
        flag = "可用" if count >= 5 else ("少" if count else "无")
        print("  %-12s HTTP %-4s 取到 %3d 条像公告的链接  %s"
              % (sid, status, count, flag))
        for h in hits[:2]:
            print("       - %s" % h)
        if count >= 5:
            ok.append((sid, name, level, url, count))
    print("\n可用（>=5 条公告链接）: %d / %d" % (len(ok), len(CANDIDATES)))
    for sid, name, level, url, count in ok:
        print("  %-12s %-14s %s  (%d 条)" % (sid, name, level, count))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())