"""从真实页面反推源配置（list_selector / link_selector / detail_pattern）。

为什么需要
  57 个 blocked 源里绝大多数 `list_selector=None`——页面完全可达，只是从来没
  配过选择器。它们不是"抓不到"被禁用，而是"没配"。手工一个个看 HTML 找
  选择器既慢又不可复现，所以从页面结构反推。

做法
  1. 抓归档页，用 <p> 级正文与全页两种口径
  2. 找出链接最密集的容器（ul / table / div），作为 list_selector
  3. 容器内的 <a> 作为 link_selector
  4. 从 href 里归纳详情页 URL 形态，得到 detail_pattern（正则）
  5. 报告置信度：详情链接占比、链接数、URL 形态一致性

只读探测：遵守 robots 与限速，不写任何数据。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

sys.path.insert(0, ".")

ROOT = Path(__file__).resolve().parents[1]

_TAG_RE = re.compile(r"<[^>]+>")
_A_RE = re.compile(r'<a\b[^>]*href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', re.S | re.I)
# 明显不是内容的链接
_JUNK_TEXT = re.compile(r"^(首页|更多|下一页|上一页|末页|首页|返回|打印|关闭|登录|注册|English|网站地图)$")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", _TAG_RE.sub("", text)).strip()


# 文档标题指纹：政务公告几乎都含这些词
_DOC_TITLE_RE = re.compile(
    r"关于|通知|公告|公示|通告|意见|办法|规定|方案|批复|印发|发布|"
    r"征集|遴选|申报|立项|建设|管理|工作|规划|细则|目录")
# 同一行里的日期：公告列表几乎每行都带
_DATE_NEAR_RE = re.compile(r"(?:19|20)\d{2}\s*[-/年.]\s*\d{1,2}")


def _doc_score(links: list[tuple[str, str]]) -> float:
    """容器里的链接有多像"文档列表"（0~1）。

    不能按链接数排序：政务公开页的"相关单位/导航"区链接最密，却一条公告都没有。
    实测广东省发改委 /ywtz/index.html 的 div.mt30 有 97 条链接，全部是
    「信用广东」「广东省粮食和物资储备局」这类机构入口——按链接数选它就废了。

    文档列表的指纹：链接文字是完整标题（长且含公文词）、同行带日期。
    日期权重更高：标题词在导航里也会误命中，日期却几乎只在列表里出现。
    """
    if not links:
        return 0.0
    titleish = dated = 0
    for _href, text in links:
        if len(text) >= 10 and _DOC_TITLE_RE.search(text):
            titleish += 1
        if _DATE_NEAR_RE.search(text):
            dated += 1
    n = len(links)
    return (titleish / n) * 0.4 + (dated / n) * 0.6


def _containers(html: str) -> list[tuple[str, int]]:
    """找出所有容器选择器与其中的链接数，按链接数降序。"""
    found: Counter[str] = Counter()
    for match in re.finditer(
        r"<(ul|ol|table|tbody|div)\b([^>]*)>", html, re.I
    ):
        tag, attrs = match.group(1).lower(), match.group(2)
        class_attr = re.search(r'class=["\']([^"\']+)["\']', attrs)
        id_attr = re.search(r'id=["\']([^"\']+)["\']', attrs)
        if not class_attr and not id_attr:
            continue
        # 找到这个容器的闭合位置，统计其中链接数
        depth = 0
        end = match.end()
        for inner in re.finditer(rf"<(/?){tag}\b[^>]*>", html[match.end():], re.I):
            if inner.group(1):
                depth -= 1
                if depth <= 0:
                    end = match.end() + inner.end()
                    break
            else:
                depth += 1
        else:
            end = len(html)
        segment = html[match.start():end]
        count = len(_A_RE.findall(segment))
        if count < 4:
            continue
        if id_attr:
            found[f"#{id_attr.group(1)}"] = count
        if class_attr:
            for cls in id_attr and [] or class_attr.group(1).split()[:2]:
                if cls and not cls.startswith(("nav", "menu", "foot", "head", "crumb")):
                    found[f"{tag}.{cls}"] = count
    return found.most_common(12)


def _link_shape(href: str) -> str:
    """把绝对 href 归纳成形态，用于生成 detail_pattern。

    必须在 urljoin 之后算：早先对相对路径归纳，结果所有形态都塌成 "/"
    和空串，detail_pattern 成了无意义的 "/|"。同域名下 path 才有意义，
    所以顺带把域名剥掉。
    """
    parsed = urlparse(href)
    path = parsed.path or "/"
    # 数字必须抹掉，否则 post_4960402 / post_4947366 各算一个形态，
    # 没有任何形态能过门槛，detail_pattern 永远是 null。
    path = re.sub(r"\d", "N", path)
    if parsed.query:
        path += "?" + re.sub(r"\d", "N", parsed.query)
    return path


def _shape_to_regex(shape: str) -> str:
    """把形态（数字已抹成 N）转成可匹配真实 href 的正则。

    逐段处理而不是整体 re.escape：整体转义后字母 N 不会被转义成 \\N，
    再 replace 也就匹配不到了——这是早先 detail_ratio 恒为 0 的原因。
    """
    parts = re.split(r"(N+)", shape)
    return "".join(r"\d+" if part and set(part) == {"N"} else re.escape(part)
                   for part in parts if part)


def propose(url: str, source: dict | None = None) -> dict:
    from collectors.base import BaseCollector  # noqa: F401 - 仅为类型提示
    from main import collector_for

    collector = collector_for(dict(source or {}, url=url, request_interval_seconds=3))
    out: dict = {"url": url, "list_selector": None, "link_selector": None,
                 "detail_pattern": None, "link_count": 0, "detail_ratio": 0.0,
                 "shapes": [], "candidates": []}
    try:
        response = collector._request(url)
        html = str(getattr(response, "text", "") or "")
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out
    finally:
        try:
            collector.close()
        except Exception:  # noqa: BLE001
            pass

    if not html:
        out["error"] = "空响应"
        return out
    out["page_chars"] = len(html)

    candidates = _containers(html)
    out["candidates"] = [{"selector": sel, "links": n} for sel, n in candidates]
    if not candidates:
        out["error"] = "未找到链接密集的容器"
        return out

    soup = BeautifulSoup(html, "html.parser")
    best = None
    for selector, _count in candidates:
        # 用该选择器实际取链接，验证不是误报
        try:
            node = soup.select_one(selector)
            if node is None:
                continue
            links = [
                (urljoin(url, a.get("href")), _clean(a.get_text()))
                for a in node.find_all("a", href=True)
                if a.get("href") and not a.get("href").startswith("#") and _clean(a.get_text())
                and not _JUNK_TEXT.match(_clean(a.get_text()))
            ]
        except Exception:  # noqa: BLE001
            continue
        if len(links) < 4:
            continue
        # 文档相似度为主、链接数为辅：导航区链接多但一条公告都没有
        score = _doc_score(links) * 10.0 + min(len(links), 60) / 60.0
        if best is None or score > best[0]:
            best = (score, selector, len(links), links)

    if best is None:
        out["error"] = "候选容器都取不到有效链接"
        return out

    _score, selector, count, links = best
    out["doc_score"] = round(_doc_score(links), 3)
    shapes = Counter(_link_shape(href) for href, _t in links)
    # 详情页形态：出现次数最多的少数几个。门槛同时看绝对数量与占比，
    # 只按占比会让"1/93 但形态唯一"的噪声混进来。
    top_shapes = [
        shape for shape, n in shapes.most_common(4)
        if n >= 3 and n >= count * 0.08 and shape != "/"
    ]
    # 同域才算详情页：跨域链接（友情链接、机构入口）不是可抓的公告
    target_host = urlparse(url).netloc
    # 形态要变成能匹配真实 href 的正则：形态里的 N 代表数字段。
    # 早先写成 re.escape(shape).replace(r"\N", r"\d") 是空操作——
    # Python 3.7+ 的 re.escape 不转义字母 N，所以 N 仍是字面量，
    # 结果 detail_ratio 恒为 0、生成的 pattern 也匹配不到任何真实链接。
    pattern = "|".join(_shape_to_regex(shape) for shape in top_shapes)[:220] if top_shapes else None
    detail_links = [
        href for href, _t in links
        if urlparse(href).netloc == target_host and re.search(pattern, href)
    ] if pattern else []
    # 导航区判定：文档相似度低且形态退化，直接说清楚而不是给个假选择器
    if out["doc_score"] < 0.15 and not top_shapes:
        out["error"] = "该页是导航/栏目页，未找到公告列表容器（需人工定位通知公告栏目）"
        return out

    out["list_selector"] = selector
    out["link_selector"] = f"{selector} a[href]"
    out["link_count"] = count
    out["detail_ratio"] = round(len(detail_links) / count, 3) if count else 0.0
    out["shapes"] = top_shapes
    out["detail_pattern"] = pattern
    out["sample_titles"] = [t for _h, t in links[:5]]
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="", help="直接给一个归档页 URL")
    parser.add_argument("--source-id", default="", help="从 sources.yaml 取 URL")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    source: dict | None = None
    url = args.url
    if args.source_id:
        import yaml

        data = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text(encoding="utf-8"))
        sources = data["sources"] if isinstance(data, dict) else data
        source = next((s for s in sources if s.get("id") == args.source_id), None)
        if source is None:
            print(f"找不到源 {args.source_id}")
            return 1
        url = source.get("url", "")
    if not url:
        parser.error("需要 --url 或 --source-id")

    result = propose(url, source)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
