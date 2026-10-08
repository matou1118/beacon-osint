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
import importlib.util
import json
import os
import re
import shutil
import sys
from collections import Counter
from dataclasses import dataclass, field
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
# write_back_field 只允许改这两个字段。别的一律拒绝：sources.yaml 里还有
# status/priority/cadence_tier 等人工登记字段，自动改了就没人知道为什么。
_WRITABLE_FIELDS = frozenset({"detail_pattern", "list_selector"})
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


def write_back_field(source_id: str, field: str, value: str) -> bool:
    """行级手术改 sources.yaml 里某个 ``key:`` 字段。保住注释和所有其他字段。

    ``write_back`` 是本函数对 ``detail_pattern`` 的特化；选容器修复（2026-09-30
    起）要写 ``list_selector``，两者的安全约束完全一样，所以共用一个实现而不是
    复制第二份 —— 复制就会出现「pattern 写回有备份、selector 写回没有」。
    """
    if field not in _WRITABLE_FIELDS:
        raise ValueError(f"拒绝写未知字段 {field!r}；"
                         f"只允许 {sorted(_WRITABLE_FIELDS)}")
    text = SOURCES_YAML.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    starts: list[tuple[str, int]] = []
    for index, line in enumerate(lines):
        found = ID_LINE.match(line)
        if found:
            starts.append((found.group(1).strip("\"'"), index))
    for order, (sid, start) in enumerate(starts):
        if sid != source_id:
            continue
        end = starts[order + 1][1] if order + 1 < len(starts) else len(lines)
        pattern = re.compile(rf"^  {field}:")
        for index in range(start, end):
            if pattern.match(lines[index]):
                newline = f"  {field}: {yaml_scalar(value)}\n"
                if lines[index] == newline:
                    return False
                lines[index] = newline
                BACKUP_DIR.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
                shutil.copy2(SOURCES_YAML,
                             BACKUP_DIR / f"sources.yaml.{stamp}.bak")
                SOURCES_YAML.write_text("".join(lines), encoding="utf-8")
                return True
    return False


def yaml_scalar(value: str) -> str:
    """按需加引号：选择器里有 # 和 : 时不加引号会解析错。"""
    text = str(value)
    if not text:
        return '""'
    if re.search(r'[:#\'"\s]', text) or text[0] in "-?":
        return '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return text


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



# ═══════════════════════════════════════════════════════════════════════
# 选择器修复（2026-09-30 并入）
#
# 原来是独立的 lighthouse/selector_healer.py + lighthouse/self_heal.py。
# 并进来的原因：诊断（self_tune）、形态推导（本模块的 derive_pattern）、
# 修复（heal_selector）分散在三个模块里，self_tune 只能写
# 「action=调 repair.py 重新提选择器」——它明知道自己不会修，把球踢给别人。
# 自愈该在这里闭环：observe -> heal_selector/heal_detail_pattern -> write_back_field。
#
# 本节只吃已在手的 DOM（绝不重新抓取），产出 Healing；写不写回由上层决定，
# 判据一律 before/after，绝不因为「推导成功」就改生产配置。
# ═══════════════════════════════════════════════════════════════════════

# 容器门槛。刻意与本模块的 MIN_LINKS(=5) 分开命名：那个是「写回 detail_pattern
# 至少要匹配多少条」，这个是「选出的容器里至少要有多少条链接才算列表」。两个门槛
# 服务两个不同的判据，共用一个数字会让「改一个把另一个也改了」。
MIN_CONTAINER_LINKS = 4
MIN_DETAIL_RATIO = 0.3
# 页面至少要有这么多条链接，才够格拿来判定「入口是否失效」。
#
# 实测 2026-09-30（mof_gov）：第 2 个归档页是 579 字节的 JS 跳转桩 ——
#     <script>location.replace("./tongzhitonggao/");</script><body></body>
# 全页只有标题里那 1 个链接。它过了 500 字节护栏（只差 79 字节），于是形态
# 过滤必然 0 条 -> 记一条「形态失效」事件；而 mof_gov 的形态其实好好的，首屏
# 64 条链接滤出 14 条。这是**误报**：把「翻页取到跳转桩」说成「站点改版、形态
# 过时」，白白惊动修复流程、消耗熔断预算。
#
# 只有 1 条链接时任何形态都滤不出 0 条 —— 推不出「形态过时」这个结论。链接少到
# 不足以判定时，正确结论是「这一页不可用，本次不判定」。
MIN_JUDGEABLE_LINKS = 5


def _load_module(name: str, path: Path) -> Any:
    """按文件路径加载项目内模块。

    scripts/ 不在包内（没有 __init__.py），直接 import 会失败；用 importlib
    按路径加载，避免为了复用两个纯函数去改包结构。
    """
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)
    spec.loader.exec_module(module)
    return module


def _propose_module() -> Any:
    return _load_module("_propose_source_config",
                        _ROOT / "scripts" / "propose_source_config.py")


def _repair_module() -> Any:
    """并入本模块后就是自己。保留这个间接层：heal_selector 里调 derive_pattern
    走它，将来若拆出真正的独立模块，改这一处即可。"""
    import sys as _sys

    return _sys.modules[__name__]


@dataclass
class Healing:
    """一次修复尝试的完整结论。无论成功失败都产出，供事件表记录。"""

    healed: bool                          # 是否修好了
    kind: str = ""                        # 修的是 list_selector 还是 detail_pattern
    old_selector: str = ""
    new_selector: str = ""                # 仅 healed 时有值（容器选择器）
    new_detail_pattern: str = ""          # 修复目标为形态时的产出（可空）
    link_count: int = 0
    detail_ratio: float = 0.0
    reason: str = ""
    candidates: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def heal_selector(soup: Any, old_selector: str, *, list_url: str,
                  source_id: str = "") -> Healing:
    """选择器匹配 0 元素时，推导一个新的。只吃已在手的 soup，绝不重新抓取。

    返回 ``Healing``；``healed=False`` 表示修不了，上层应记事件 + 人工审阅，
    不许写回注册表。
    """
    if soup is None:
        return Healing(False, old_selector=old_selector, reason="没有可用的 DOM")
    html = str(soup)
    if not html.strip():
        return Healing(False, old_selector=old_selector, reason="DOM 为空")

    try:
        propose = _propose_module()
        repair = _repair_module()
    except Exception as exc:                        # noqa: BLE001
        return Healing(False, old_selector=old_selector,
                       reason=f"依赖模块加载失败: {type(exc).__name__}: {exc}")

    try:
        ranked = propose._containers(html)         # 纯函数：HTML -> [(sel, n)]
    except Exception as exc:                        # noqa: BLE001
        return Healing(False, old_selector=old_selector,
                       reason=f"容器枚举失败: {type(exc).__name__}: {exc}")

    if not ranked:
        return Healing(False, old_selector=old_selector,
                       reason="页面里找不到含 >=4 条链接的容器（多半不是列表页）")

    tried: list[dict[str, Any]] = []
    # 链接多的先试。_containers 已经按链接数降序返回。
    for selector, reported in ranked:
        row: dict[str, Any] = {"selector": selector, "reported_links": reported}
        try:
            node = soup.select_one(selector)
        except Exception as exc:                    # noqa: BLE001
            row["error"] = f"invalid selector: {type(exc).__name__}"
            tried.append(row)
            continue
        if node is None:
            row["error"] = "在当前 DOM 上匹配不到"
            tried.append(row)
            continue

        links = []
        for anchor in node.select("a[href]"):
            href = anchor.get("href")
            if not href or not isinstance(href, str):
                continue
            href = href.strip()
            if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            links.append(urljoin(list_url, href))
        links = list(dict.fromkeys(links))
        row["links"] = len(links)

        # 第一道门槛
        if len(links) < MIN_CONTAINER_LINKS:
            row["rejected"] = f"链接数 {len(links)} < {MIN_CONTAINER_LINKS}"
            tried.append(row)
            continue

        # 第二、三道门槛：形态推导本身会排掉同形、子栏目、非同主机
        try:
            pattern, diag = repair.derive_pattern(links, list_url)
        except Exception as exc:                    # noqa: BLE001
            row["rejected"] = f"形态推导异常 {type(exc).__name__}: {exc}"
            tried.append(row)
            continue

        same_host = int((diag or {}).get("same_host_links") or 0)
        ratio = round(same_host / len(links), 4) if links else 0.0
        row["same_host_links"] = same_host
        row["detail_ratio"] = ratio

        if ratio < MIN_DETAIL_RATIO:
            row["rejected"] = f"同主机占比 {ratio:.0%} < {MIN_DETAIL_RATIO:.0%}"
            tried.append(row)
            continue
        if not pattern:
            row["rejected"] = str((diag or {}).get("reason") or "推导不出详情页形态")
            tried.append(row)
            continue

        row["accepted"] = True
        row["new_detail_pattern"] = pattern
        tried.append(row)
        return Healing(
            True, old_selector=old_selector, new_selector=selector,
            new_detail_pattern=pattern, link_count=len(links),
            detail_ratio=ratio, reason="三道门槛全部通过", candidates=tried)

    return Healing(False, old_selector=old_selector,
                   reason="所有候选都未过门槛（修不了，不写回）",
                   candidates=tried)


def heal_detail_pattern(links: list[str], *, list_url: str,
                        old_pattern: str = "", source_id: str = "") -> Healing:
    """推导新的 ``detail_pattern``。

    这是部委那类源的修复路径：它们**没有 list_selector**，靠 URL 形态过滤
    通用启发式抓到的链接（实测 mof_gov/moa_gov/mee_gov/mct_gov/mohrss_gov
    的 list_selector 全为空，detail_pattern 分别是 /\\d{6}/t\\d{8}_\\d+\\.html?$
    这类形态）。所以「选择器失效」对它们表现为「形态对不上，0 条入库」，
    要修的是形态而不是容器。

    复用 repair.derive_pattern —— 它已经排掉同形、子栏目、非同主机。
    """
    if not links:
        return Healing(False, kind="detail_pattern", old_selector=old_pattern,
                       reason="没有可用的链接，无法推导形态")
    repair = _repair_module()
    try:
        pattern, diag = repair.derive_pattern(list(links), list_url)
    except Exception as exc:                            # noqa: BLE001
        return Healing(False, kind="detail_pattern", old_selector=old_pattern,
                       reason=f"形态推导异常 {type(exc).__name__}: {exc}")

    same_host = int((diag or {}).get("same_host_links") or 0)
    ratio = round(same_host / len(links), 4) if links else 0.0
    detail: dict[str, Any] = {"same_host_links": same_host,
                              "detail_ratio": ratio,
                              "shapes": (diag or {}).get("shapes") or {}}
    if not pattern:
        return Healing(False, kind="detail_pattern", old_selector=old_pattern,
                       detail_ratio=ratio,
                       reason=str((diag or {}).get("reason") or "推导不出形态"),
                       candidates=[detail])
    if old_pattern and pattern == old_pattern:
        return Healing(False, kind="detail_pattern", old_selector=old_pattern,
                       detail_ratio=ratio,
                       reason="推导结果与现登记形态一致，无需修",
                       candidates=[detail])
    return Healing(True, kind="detail_pattern", old_selector=old_pattern,
                   new_detail_pattern=pattern, link_count=len(links),
                   detail_ratio=ratio,
                   reason="形态推导通过同主机占比门槛", candidates=[detail])


def heal(soup: Any, source: Mapping[str, Any], *,
         list_url: str = "", fallback_links: list[str] | None = None) -> Healing:
    """按源的类型选对修复路径。**这是唯一入口。**

    两类源，两种失效形态（实测自 A3 夹具）：

    * **有 list_selector 的源**（注册表里 50 个）：靠容器选择器定位列表。
      选择器失效 = 匹配 0 元素 -> 重新推导容器选择器。
    * **只有 detail_pattern 的源**（部委那批：mof_gov / moa_gov / mee_gov /
      mct_gov / mohrss_gov …实测 list_selector 全为空）：靠 URL 形态过滤
      通用启发式抓到的链接。失效表现为「形态对不上，0 条入库」-> 重新推导形态。

    第一版只做了容器选择器那一类，夹具里 5 个部委源全部「跳过」，因为它们
    根本没有 list_selector —— 那不是它们没问题，是这条路径压根不适用。
    """
    list_url = list_url or str(source.get("url") or "")
    old_selector = str(source.get("list_selector") or "")

    if old_selector:
        out = heal_selector(soup, old_selector, list_url=list_url,
                            source_id=str(source.get("id") or ""))
        if out.healed:
            out.kind = "list_selector"
        return out

    # 没有 list_selector -> 修形态。链接优先用调用方传入的（就是本次真实抓到的），
    # 否则从 DOM 里现取。
    links = list(fallback_links or [])
    if not links and soup is not None:
        try:
            from urllib.parse import urljoin
            links = [urljoin(list_url, a.get("href"))
                     for a in soup.select("a[href]") if a.get("href")]
            links = list(dict.fromkeys(u for u in links
                                       if u and not u.startswith("#")))
        except Exception:                                   # noqa: BLE001
            links = []
    return heal_detail_pattern(links, list_url=list_url,
                              old_pattern=str(source.get("detail_pattern") or ""),
                              source_id=str(source.get("id") or ""))


def observe_list_page(html: str, source: Mapping[str, Any], *,
                      list_url: str = "") -> dict[str, Any]:
    """感知：列表页取回来之后，判断「这个源的采集入口还灵不灵」。

    放在列表层而不是详情层，因为「选择器失效」的现象是**列表为空**，不是详情
    变少。上面那句「不是静默写入零行」说的就是这件事。

    只观测，不修复、不写回 —— 这是影子模式的全部内容。

    返回 ``{"broken": bool, "kind": str, "detail": str, "links": int}``。
    """
    out: dict[str, Any] = {"broken": False, "kind": "", "detail": "", "links": 0}
    list_url = list_url or str(source.get("url") or "")
    if not html or len(html) < 500:
        out["detail"] = f"页面过短({len(html or '')} 字)，可能没加载出来"
        return out
    from bs4 import BeautifulSoup

    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception as exc:                              # noqa: BLE001
        out["detail"] = f"解析失败 {type(exc).__name__}: {exc}"
        return out

    # ── 页面够不够格当「列表页」来判定 ──────────────────────────────────
    # 这个闸必须在两个分支之前。判据是**链接数**而不是字节数（见
    # MIN_JUDGEABLE_LINKS 的注释：mof_gov 的翻页跳转桩 579 字节，只差 79 字
    # 就绕过字节护栏，但全页只有 1 个链接，不足以判定）。
    from urllib.parse import urljoin
    all_links = [urljoin(list_url, a.get("href"))
                 for a in soup.select("a[href]")
                 if a.get("href") and not a.get("href").startswith(("#", "javascript:"))]
    out["links"] = len(all_links)
    if len(all_links) < MIN_JUDGEABLE_LINKS:
        out["detail"] = (f"全页仅 {len(all_links)} 条链接，不足以判定入口"
                         f"（可能是跳转桩/空页/翻页失败），本次不判定")
        return out

    list_selector = str(source.get("list_selector") or "")
    old_pattern = str(source.get("detail_pattern") or "")

    if list_selector:
        out["kind"] = "list_selector"
        if selector_matches(soup, list_selector):
            node = soup.select_one(list_selector)
            out["links"] = len(node.select("a[href]")) if node else 0
            return out
        out["broken"] = True
        out["detail"] = f"list_selector={list_selector} 在当前页面上匹配 0 元素"
        return out

    if old_pattern:
        out["kind"] = "detail_pattern"
        links = all_links
        try:
            kept = [u for u in links if re.search(old_pattern, u)]
        except re.error:
            kept = []
        out["matched"] = len(kept)
        # 形态类源的「有效」判据是**滤出了东西**，不是滤出多少。
        #
        # 门槛不能沿用容器的 MIN_LINKS：像 zj_fgw 这种源，它登记的形态就是
        # 栏目页本身（detail_pattern=/col/col\d+/index\.html），列表页里 45 条
        # 链接有 33 条是同形态、12 条跨域，滤出数天然就少。用「>=4」判它失效，
        # 会去修一个没坏的形态 —— 而 derive_pattern 又会正确地拒绝（页面里确实
        # 没有文章链接），最后记一条假事件，白白消耗一轮修复预算。
        if kept:
            return out
        out["broken"] = True
        out["detail"] = (f"detail_pattern={old_pattern} 一条都滤不出"
                         f"（页面共 {len(links)} 条链接）")
        return out


    out["detail"] = "该源既无 list_selector 也无 detail_pattern，用通用启发式"
    return out


def selector_matches(soup: Any, selector: str | None) -> bool:
    """保存的选择器在当前 DOM 上还能不能匹配到东西。

    这是「感知」环节：匹配 0 元素说明选择器已失效（站点改版），该触发修复，
    而不是静默写入零行。
    """
    if soup is None or not selector:
        return True                      # 没配选择器 = 走通用启发式，不算失效
    try:
        node = soup.select_one(str(selector))
    except Exception:                   # noqa: BLE001
        return False                     # 选择器本身非法 = 失效
    if node is None:
        return False
    # 匹配到空容器也算失效：改版常把 class 留着、内容挪走
    if node.select("a[href]") or node.get_text(strip=True):
        return True
    return False



# ── 事件记录（感知到失效时的副作用）────────────────────────────────────
# 只记事件，不写回注册表。写回要过 before/after 验证，由 self_tune 那一层决定，
# 采集时这一层无权改生产配置 —— 采集正在跑着，改坏了当场就体现为数据错误。

EVENTS_DB = str(_ROOT / "data" / "beacon.db")


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def event_id(source_id: str, url: str, ts: str) -> str:
    """幂等键：同一个源同一秒内的同一事件只记一次。"""
    import hashlib

    raw = f"{source_id}|{url}|{ts}".encode("utf-8")
    return "evt-sel-" + hashlib.sha1(raw).hexdigest()[:12]


def report_broken(source: Mapping[str, Any], obs: Mapping[str, Any],
                  *, html: str = "", url: str = "") -> dict[str, Any]:
    """记一条「感知到入口失效、修都没试」的事件。到此为止 —— 不写回。

    绝不抛异常：事件记录失败不能拖垮采集。
    """
    source_id = str(source.get("id") or "")
    list_url = url or str(source.get("url") or "")
    kind = str(obs.get("kind") or "unknown")
    links = int(obs.get("links") or 0)
    matched = float(obs.get("matched") or 0)
    try:
        from lighthouse import event_bus

        eid = event_bus.emit(
            layer="collector",
            scope=f"source_id:{source_id}",
            failure_type=("selector_broken" if kind == "list_selector"
                          else "detail_pattern_stale"),
            action="observe_only",
            repair_result="skipped",
            source="repair",
            detail=str(obs.get("detail") or "入口失效"),
            extra={
                "url": list_url,
                "old_selector": str(source.get(kind) or ""),
                "new_selector": "",
                "link_count": links,
                "detail_ratio": round(matched / max(1, links), 4),
            },
        )
        return {"recorded": eid is not None, "kind": kind, "event_id": eid or ""}
    except Exception as exc:                             # noqa: BLE001
        return {"recorded": False, "kind": kind,
                "record_error": f"{type(exc).__name__}: {exc}"}


def heal_and_report(source: Mapping[str, Any], soup: Any, *,
                    list_url: str = "", links: list[str] | None = None,
                    kind: str = "") -> dict[str, Any]:
    """感知到失效后的修复尝试。影子模式下**不写回**，只记事件。

    ``kind`` 来自观测结果（list_selector / detail_pattern）。必须显式传：
    否则会退回「按 source 里哪个字段非空来判断」，而那正是 A4 首跑暴露的
    bug —— list_selector 是坏的、但字段仍然非空，于是被当成形态失效去修形态，
    事件类型记成 detail_pattern_stale，跟实际问题对不上。
    """
    from lighthouse import repair as sh  # noqa: F811  (同模块，走自身)

    out: dict[str, Any] = {"kind": kind}
    try:
        if kind == "list_selector":
            h = sh.heal_selector(soup, str(source.get("list_selector") or ""),
                                  list_url=list_url or str(source.get("url") or ""),
                                  source_id=str(source.get("id") or ""))
        else:
            h = sh.heal_detail_pattern(
                list(links or []),
                list_url=list_url or str(source.get("url") or ""),
                old_pattern=str(source.get("detail_pattern") or ""),
                source_id=str(source.get("id") or ""))
    except Exception as exc:                             # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out

    out.update({
        "healed": h.healed, "kind": h.kind or kind,
        "old": h.old_selector, "new": h.new_selector,
        "new_pattern": h.new_detail_pattern,
        "links": h.link_count, "reason": h.reason,
    })

    source_id = str(source.get("id") or "")
    url = list_url or str(source.get("url") or "")
    effective = kind or h.kind
    failure = ("selector_broken" if effective == "list_selector"
               else "detail_pattern_stale")
    action = ("heal_selector" if effective == "list_selector"
              else "heal_detail_pattern")

    # 采集期这一层**只推导不写回**：候选方案一律记 candidate，采集正在跑着，
    # 此时改 sources.yaml 改坏了当场就体现为数据错误。写回由 self_tune 在轮后
    # 过 before/after 验证决定。
    #
    # 事件只写 event_bus 一处。原先同时写 selector_events(SQLite) 和
    # events.jsonl，同一事件两个账本，对不上且两边都得查；2026-09-30 已合并，
    # 采集专属字段（link_count / detail_ratio / 候选明细）进 extra。
    try:
        from lighthouse import event_bus

        eid = event_bus.emit(
            layer="collector",
            scope=f"source_id:{source_id}",
            failure_type=failure,
            action=action,
            repair_result="candidate" if h.healed else "skipped",
            source="repair",
            detail=h.reason or f"{effective}: {h.old_selector}->{h.new_selector}",
            extra={
                "url": url,
                "old_selector": h.old_selector,
                "new_selector": h.new_selector,
                "new_pattern": h.new_detail_pattern,
                "link_count": h.link_count,
                "detail_ratio": h.detail_ratio,
                "candidates": h.candidates[:5],
            },
        )
        out["recorded"] = eid is not None
        out["event_id"] = eid or ""
    except Exception as exc:                             # noqa: BLE001
        out["recorded"] = False
        out["record_error"] = f"{type(exc).__name__}: {exc}"
    return out


def stats() -> dict[str, Any]:
    """自愈观测统计：「感知了多少 / 推导出方案多少 / 判定修不了多少」。

    数据源是统一事件流（event_bus），不是独立的 selector_events 表 ——
    2026-09-30 合并掉了原先的双账本。
    """
    from lighthouse import event_bus

    try:
        events = event_bus.read_events()
    except Exception as exc:                             # noqa: BLE001
        return {"total": 0, "healed": 0, "breakdown": {},
                "error": f"{type(exc).__name__}: {exc}"}

    mine = [e for e in events if e.get("source") == "repair"]
    healed = [e for e in mine if e.get("repair_result") == "candidate"]
    breakdown: dict[str, int] = {}
    for e in mine:
        key = "{}/{}/{}".format(e.get("failure_type", "?"),
                                e.get("action", "?"),
                                e.get("repair_result", "?"))
        breakdown[key] = breakdown.get(key, 0) + 1
    return {"total": len(mine), "healed": len(healed),
            "breakdown": dict(sorted(breakdown.items(), key=lambda kv: -kv[1])),
            "events_total": len(events)}


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


