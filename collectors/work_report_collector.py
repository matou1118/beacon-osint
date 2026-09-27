"""政府工作报告采集器。

复用 BaseCollector 而不是自己发请求，是为了白拿两样东西：
  1. robots 检查（_assert_robots_allows）—— 合规底线
  2. 每源 3 秒限速下限 —— 对政府站点的礼貌间隔

不放进 collectors/ 参与 collector_for() 的自动路由：政府工作报告是独立
文档类，走自己的脚本，避免和公告采集共用调度与去重逻辑。
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin

from collectors.base import BaseCollector
from extractors.work_report import extract_body, normalize, parse_report_year, split_sections
from extractors.work_report_metrics import extract_metrics, extract_topic_hits

# 链接文字里含"政府工作报告"即视为报告条目。实测四种写法都覆盖到了：
#   上海  龚正市长在上海市第十六届人民代表大会第三次会议的政府工作报告（2025年）
#   广州  2026年广州市政府工作报告
#   重庆  重庆市人民政府工作报告（2026年）
#   武汉  武汉市2026年政府工作报告 2026-02-10 14:48
_LINK_RE = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")


class WorkReportCollector(BaseCollector):
    """从政府工作报告归档栏目枚举并抓取历年报告。"""

    min_interval_seconds = 3.0

    def _fetch_html(self, url: str) -> str:
        """_request 返回 response 对象，不是字符串。"""
        response = self._request(url)
        return str(getattr(response, "text", "") or "")

    def _archive_links(self) -> list[tuple[str, str]]:
        html = self._fetch_html(self.list_url)
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        for href, inner in _LINK_RE.findall(html or ""):
            text = re.sub(r"\s+", " ", _TAG_RE.sub("", inner)).strip()
            if "政府工作报告" not in text:
                continue
            if "执行情况" in text or "解读" in text:
                continue
            url = urljoin(self.list_url, href.strip())
            if url in seen:
                continue
            seen.add(url)
            out.append((url, text))
        return out

    def fetch_list(self) -> list[str]:
        return [url for url, _text in self._archive_links()]

    def fetch_detail(self, url: str) -> dict[str, Any]:
        html = self._fetch_html(url)
        body = normalize(extract_body(html or ""))
        if len(body) < 3000:
            raise ValueError(f"正文过短（{len(body)} 字），疑似 URL 失效或需渲染：{url}")
        # 年份取自链接文字。归档页的 URL 年份段不可靠：武汉 2016-2018 三份
        # 报告的 URL 都是 /202003/t20200316_9701xx.shtml。
        year = parse_report_year(self._last_link_text.get(url, ""), url, body)
        if not year:
            raise ValueError(f"无法解析报告年份：{url}")
        preface, sections = split_sections(body)
        return {
            "source_id": self.source_id,
            "url": url,
            "title": self._last_link_text.get(url) or f"{self.source_name}{year}年政府工作报告",
            "year": year,
            "body": body,
            "preface": preface,
            "sections": sections,
        }

    def collect(self, limit: int | None = 60, **_: Any) -> list[dict[str, Any]]:
        self.errors = []
        links = self._archive_links()
        self._last_link_text = {url: text for url, text in links}
        out: list[dict[str, Any]] = []
        for url, _text in links:
            if limit is not None and len(out) >= limit:
                break
            if self.out_of_time():
                break
            try:
                out.append(self.fetch_detail(url))
            except Exception as exc:  # noqa: BLE001 - 单份报告失败不该中断整轮
                self.errors.append({"url": url, "error": f"{type(exc).__name__}: {exc}"})
        return out

    def __init__(self, source: dict[str, Any], **kwargs: Any) -> None:
        self._last_link_text: dict[str, str] = {}
        # collect() 会往里追加单份报告的失败原因。放在 __init__ 里是因为
        # 调用方在 collect() 抛异常后仍要读它——早先只在 collect() 里创建，
        # 于是"采集失败"的分支反而在读一个不存在的属性。
        self.errors: list[dict[str, str]] = []
        super().__init__(source, **kwargs)


def build_report_records(
    raw: dict[str, Any],
    *,
    region_code: str,
    region_name: str,
    level: str = "市级",
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """把一份原始报告拆成 (报告本体, 数值指标, 专题命中)。"""
    body = raw["body"]
    year = raw["year"]
    sections = raw["sections"] or []
    metrics: list[dict[str, Any]] = []
    topics: dict[str, dict[str, Any]] = {}

    for sec in sections:
        metrics.extend(extract_metrics(
            sec.get("text", ""),
            region_code=region_code,
            year=year,
            section=(sec.get("heading") or "")[:30],
            section_key=sec.get("key", ""),
        ))
        for topic, info in extract_topic_hits(sec.get("text", "")).items():
            slot = topics.setdefault(topic, {"hit_count": 0, "hits": []})
            slot["hit_count"] += info["hit_count"]
            slot["hits"].extend(info["hits"][:3])
            if len(slot["hits"]) > 8:
                slot["hits"] = slot["hits"][:8]

    import hashlib

    report = {
        "source_id": raw["source_id"],
        "region_code": region_code,
        "region_name": region_name,
        "level": level,
        "year": year,
        "report_date": None,
        "title": raw["title"],
        "url": raw["url"],
        "content": body,
        "content_hash": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "sections": [
            {"order": s.get("order"), "heading": s.get("heading"),
             "key": s.get("key"), "chars": s.get("chars", 0)}
            for s in sections
        ],
        "char_count": len(body),
    }
    topic_rows = [
        {"topic": name, "region_code": region_code, "year": year,
         "hit_count": info["hit_count"], "hits": info["hits"]}
        for name, info in topics.items()
    ]
    return report, metrics, topic_rows
