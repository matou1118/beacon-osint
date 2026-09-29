"""从真实链接反推 detail_pattern，并写回 sources.yaml。自动化的自迭代一环。

为什么需要
----------
实测 6 个源长期「0 条」，逐层排查下来每个症状都不一样，但根因是同一个：
**``detail_pattern`` 配的是栏目形态，不是文章形态。**

以 ``sc_js_gov``（江苏省政府信息公开）为例，渲染后页面 72647 字、224 个 a[href]：

    83 条  /col/colN/index.html      ← 栏目索引页，与列表页 URL 同形
    31 条  /art/N/N/N/art_N_N.html    ← 真正的文章详情页
    配置的 detail_pattern = /col/col\d+/index\.html   → 匹配 83 条，文章 0 条

而 ``scripts/propose_source_config.py`` 推出来也是栏目形态——因为它按
``list_selector`` 选容器，``#barrierfree_container`` 里装的正是栏目导航。
所以不能直接用它。

本模块的判据（这是和 propose 的关键区别）
-----------------------------------------
**排除与列表页 URL 自身同形的形态。** 列表页是 ``/col/col84242/index.html``，
那么所有 ``/col/colNNNNN/index.html`` 都是它的同级栏目，不是文章；形态与之
不同的（``/art/...``）才是。其余形态里取出现次数最多的那个。

安全约束
--------
* 写回用行级手术（和 promote.py 同一套），**不碰注释和人工登记字段**
* 改之前先验证：新 pattern 必须匹配 >= min_links 条、且不等于旧值、且不为空
* 默认预览，``--apply`` 才落盘
* 每个源单独记结论，报告里能看出哪些改了、哪些推不出来

    python lighthouse/repair.py --source sc_js_gov,js_fgw --dry-run
    python lighthouse/repair.py --from-report --apply
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urljoin, urlparse

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import yaml  # noqa: E402

from lighthouse.routes import build_collector, _serial_render  # noqa: E402
from lighthouse.run import load_sources, with_defaults  # noqa: E402
from lighthouse.winflags import install as _install_no_window  # noqa: E402
_install_no_window()  # 本进程 spawn 的任何子进程都不带控制台窗口

from lighthouse.quality import load_config  # noqa: E402

SOURCES_YAML = _ROOT / "config" / "sources.yaml"
BACKUP_DIR = _ROOT / "data" / "runtime"

MIN_LINKS = 5           # 新 pattern 至少要能匹配这么多条，否则不敢写
ID_LINE = re.compile(r"^- id:\s*(\S+)\s*$")
_ID_LINE = ID_LINE      # write_back 用的别名
# 明知不是文章的形态
_NEVER = re.compile(r"\.(?:jpg|jpeg|png|gif|css|js|zip|rar|ico|svg|pdf|doc|docx|xls|xlsx)$"
                    r"|/(?:javascript:|mailto:|tel:)", re.I)


def log(message: str) -> None:
    print(message, flush=True)


def _shape(url: str) -> str:
    """把 URL 归一成形态：数字段替换成 N。"""
    path = urlparse(url).path
    return re.sub(r"\d+", "N", path)


def _shape_to_regex(shape: str) -> str:
    # 注意结尾是 r"$" 不是 r"\?$"——原来写成后者，生成的 regex 要求结尾有个
    # 字面的问号，于是匹配数永远是 0，而「匹配 0 条」这个症状看起来像
    # 推导失败，掩盖了真正的笔误。
    return re.escape(shape).replace("N", r"\d+") + r"$"


# 不可能是文章形态的：站点根、栏目首页、纯编号
_NAV_SHAPE = re.compile(r"^(?:/|/index\.html?|/N|)$")


def fetch_links(list_url: str, cfg: Mapping[str, Any], *, render: bool) -> tuple[list[str], str]:
    """取列表页上的全部绝对链接。返回 (links, 用的哪条路径)。

    整页取链接而不是限定容器：这一批站的「容器」里装的恰恰是栏目导航，
    限定容器就看不到文章了。
    """
    source = {"url": list_url, "level": "national", "type": "archive",
              "access_method": "static_html", "request_interval_seconds": 3}
    collector = build_collector(source, {})
    try:
        if not render:
            try:
                response = collector._request(list_url)
                html = str(getattr(response, "text", "") or "")
            except Exception:                                # noqa: BLE001
                return [], "static_failed"
            return _links_from(html, list_url), "static"
        level = _render_level(cfg)
        html = _serial_render(
            list_url,
            timeout_ms=int(level.get("timeout_ms", 60000)),
            wait_ms=int(level.get("settle_ms", 5000)),
            wait_until=str(level.get("wait_until", "networkidle")),
        )
        return _links_from(html, list_url), "render"
    finally:
        try:
            collector.close()
        except Exception:                                    # noqa: BLE001
            pass


def _render_level(cfg: Mapping[str, Any]) -> Mapping[str, Any]:
    for level in ((cfg.get("routes") or {}).get("levels") or []):
        if level.get("name") == "render":
            return level
    return {}


def analyse(source: Mapping[str, Any], cfg: Mapping[str, Any]) -> dict[str, Any]:
    """先静态推导，推不出就升级渲染再推导一次。

    升级判据不是「链接太少」而是「推不出文章形态」：江苏/浙江这类站的栏目链接
    静态就有 15 条（>=min_links），但文章是 JS 加载的，静态页面里一条都没有。
    只看链接总数会误以为已经够了，于是永远不渲染，永远推不出 pattern。
    """
    list_url = str(source.get("url") or "")
    links, how = fetch_links(list_url, cfg, render=False)
    pattern, diag = derive_pattern(links, list_url)
    if pattern and pattern != source.get("detail_pattern"):
        return {"source_id": str(source.get("id")), "url": list_url,
                "fetched_via": how, "total_links": len(links),
                "new_pattern": pattern, "old_pattern": source.get("detail_pattern"),
                "diag": diag}
    # 静态推不出来 → 渲染一次，把两边的链接合并再推
    try:
        rendered, how2 = fetch_links(list_url, cfg, render=True)
    except Exception:                                        # noqa: BLE001
        rendered, how2 = [], "render_failed"
    if rendered:
        merged = list(dict.fromkeys(links + rendered))
        pattern2, diag2 = derive_pattern(merged, list_url)
        diag2["static_only_shapes"] = diag.get("shapes")
        if pattern2 and pattern2 != source.get("detail_pattern"):
            return {"source_id": str(source.get("id")), "url": list_url,
                    "fetched_via": f"{how}+{how2}", "total_links": len(merged),
                    "new_pattern": pattern2, "old_pattern": source.get("detail_pattern"),
                    "diag": diag2}
        diag = diag2
        how = f"{how}+{how2}"
    return {"source_id": str(source.get("id")), "url": list_url, "fetched_via": how,
            "total_links": len(links), "new_pattern": None,
            "old_pattern": source.get("detail_pattern"), "diag": diag}


def _links_from(html: str, base: str) -> list[str]:
    from bs4 import BeautifulSoup

    out: list[str] = []
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:                                        # noqa: BLE001
        soup = BeautifulSoup(html, "html.parser")
    for anchor in soup.select("a[href]"):
        href = anchor.get("href")
        if not isinstance(href, str):
            continue
        href = href.strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        url = urljoin(base, href)
        if urlparse(url).scheme not in {"http", "https"}:
            continue
        if _NEVER.search(url):
            continue
        out.append(url)
    return list(dict.fromkeys(out))


def _is_sibling_of(own_shape: str, shape: str) -> bool:
    """候选形态其实是列表页的子栏目，就不算文章。

    实测 sc_ln_gov：列表页是 /web/zwgkx/tzgg/index.shtml，页面上的链接形态是
    /web/zwgkx/tzgg/N/index.shtml——同一个栏目下的分页/子栏目，不是详情页。
    把它当 detail_pattern 写回去，采回来的全是子栏目，是另一种垃圾。
    """
    base = re.sub(r"(?:/|^)index\.s?html?$", "", own_shape)
    return bool(base) and shape.startswith(base.rstrip("/") + "/")


def derive_pattern(links: list[str], list_url: str) -> tuple[str | None, dict[str, Any]]:
    """从真实链接反推 detail_pattern。返回 (pattern, 诊断)。

    只考虑**与列表页同主机**的链接。实测 js_rst（江苏省人社厅
    jshrss.jiangsu.gov.cn）的渲染页里有 18 条 /art/... 形态的链接，但它们
    指向 jiangsu.gov.cn 主站，不是自己的文章——把它当 detail_pattern 写回去，
    采集时又会被 _is_allowed_url 的域名白名单全部拒掉，结果还是 0 条，
    而且配置被改坏了。同域是推导的前提。
    """
    own = _shape(list_url)
    host = (urlparse(list_url).hostname or "").lower()
    same_host = [url for url in links
                 if (urlparse(url).hostname or "").lower() == host]
    diagnostics = {
        "own_shape": own,
        "same_host_links": len(same_host),
        "dropped_other_host": len(links) - len(same_host),
    }
    links = same_host
    # 同形 = 和列表页 URL 自身形态一致 = 同级栏目页，不是文章
    by_shape: dict[str, list[str]] = {}
    for url in links:
        if url.rstrip("/") == list_url.rstrip("/"):
            continue
        by_shape.setdefault(_shape(url), []).append(url)

    diagnostics["shapes"] = {k: len(v) for k, v in sorted(by_shape.items(), key=lambda kv: -len(kv[1]))[:8]}
    diagnostics["excluded_own_shape"] = len(by_shape.get(own, []))
    candidates = [(shape, urls) for shape, urls in by_shape.items()
                  if shape != own and len(urls) >= MIN_LINKS
                  and not _NAV_SHAPE.match(shape)
                  and "N" in shape                 # 文章 URL 必然带数字 ID 段
                  and shape.count("/") >= 2        # 至少两段路径，排除一级栏目
                  and not _is_sibling_of(own, shape)]
    if not candidates:
        diagnostics["reason"] = "没有形态与列表页不同且 >=min_links 的链接"
        return None, diagnostics
    # 链接多的优先；同样多时取路径更深的（更可能是详情页而不是栏目）
    candidates.sort(key=lambda kv: (-len(kv[1]), -kv[0].count("/")))
    shape, urls = candidates[0]
    pattern = _shape_to_regex(shape)
    matched = sum(1 for url in links if re.search(pattern, url))
    diagnostics["chosen_shape"] = shape
    diagnostics["chosen_links"] = len(urls)
    diagnostics["pattern_matches"] = matched
    return pattern, diagnostics


def write_back(source_id: str, pattern: str) -> bool:
    """行级手术改 detail_pattern。保住注释和所有人工登记字段。"""
    text = SOURCES_YAML.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    starts: list[tuple[str, int]] = []
    for index, line in enumerate(lines):
        found = _ID_LINE.match(line)
        if found:
            starts.append((found.group(1).strip("\"'"), index))
    for order, (sid, start) in enumerate(starts):
        if sid != source_id:
            continue
        end = starts[order + 1][1] if order + 1 < len(starts) else len(lines)
        for index in range(start, end):
            if re.match(r"^  detail_pattern:", lines[index]):
                newline = lines[index][len(lines[index].rstrip("\r\n")):]
                lines[index] = f"  detail_pattern: '{pattern}'" + newline
                SOURCES_YAML.write_text("".join(lines), encoding="utf-8")
                return True
        # 145 个源里只有 75 个配了 detail_pattern。没有这一项的源要新增，
        # 插在 url 后面（和 sources.yaml 现有排版一致），不追加到块尾——
        # 块尾通常是 notes 之类的长字段，插进去会打乱人工可读性。
        for index in range(start, end):
            if re.match(r"^  url:", lines[index]):
                newline = lines[index][len(lines[index].rstrip("\r\n")):]
                lines.insert(index + 1, f"  detail_pattern: '{pattern}'" + newline)
                SOURCES_YAML.write_text("".join(lines), encoding="utf-8")
                return True
        return False
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从真实链接反推 detail_pattern")
    parser.add_argument("--source", action="append", default=[],
                        help="逗号分隔或重复指定")
    parser.add_argument("--from-report", action="store_true",
                        help="用最新影子报告挑出 no_links 的源")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    cfg = load_config()
    wanted: list[str] = []
    for chunk in args.source:
        wanted.extend(part.strip() for part in chunk.split(",") if part.strip())
    if args.from_report:
        reports = glob.glob(str(_ROOT / "data" / "runtime" / "lighthouse" / "shadow-*.json"))
        if not reports:
            log("没有影子报告，先跑 --dry-run")
            return 1
        latest = max(reports, key=os.path.getmtime)
        data = json.loads(Path(latest).read_text(encoding="utf-8"))
        wanted.extend(s["source_id"] for s in data.get("sources", [])
                      if not s.get("records") and s.get("notes")
                      and any("no links" in str(n) or "list_selector" in str(n)
                              for n in s["notes"]))
    if not wanted:
        log("没指定源（--source 或 --from-report）")
        return 1

    known = {str(s.get("id")): s for s in load_sources(())}
    outcomes: list[dict[str, Any]] = []
    for sid in dict.fromkeys(wanted):
        source = known.get(sid)
        if source is None:
            log(f"  {sid}: 配置里没有")
            continue
        info = analyse(source, cfg)
        outcomes.append(info)
        diag = info["diag"]
        log(f"  {sid}: {info['total_links']} 链接 ({info['fetched_via']}) "
            f"| 列表页形态 {diag['own_shape']}，排除同形 {diag['excluded_own_shape']} 条")
        log(f"      形态分布 {list(diag['shapes'].items())[:4]}")
        if info["new_pattern"]:
            log(f"      新 {info['new_pattern']}  (匹配 {diag['pattern_matches']} 条)")
            log(f"      旧 {info['old_pattern']}")
        else:
            log(f"      推不出：{diag.get('reason', '与旧值相同')}")

    fixable = [o for o in outcomes
               if o["new_pattern"] and o["new_pattern"] != o["old_pattern"]]
    log(f"\n可修 {len(fixable)} / {len(outcomes)} 个源")
    if not args.apply:
        log("预览模式。要写入加 --apply")
        return 0

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup = BACKUP_DIR / f"sources.yaml.{stamp}.bak"
    shutil.copy2(SOURCES_YAML, backup)
    done = 0
    for info in fixable:
        if write_back(info["source_id"], info["new_pattern"]):
            done += 1
            log(f"  已改 {info['source_id']} -> {info['new_pattern']}")
    log(f"写入 {done} 个源，备份 {backup.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


