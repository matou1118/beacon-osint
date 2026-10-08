"""把 detail_pattern 从「只认旧 URL 形态」修成「新旧都认」。

2026-10-02 实测（miit_gov）
---------------------------
站点 HTTP 200、渲染后 47304 字符、页面里有 10 条真实文件链接，但
``Ladder.list_links()`` 一路 static -> render -> markdown 三级全部返回
「0 links」。逐层拆开才看到真因不在渲染，而在**抽链那一步**：

* 渲染后的 HTML 里确实有 10 条 ``/zwgk/zcwj/wjfb/tz/art/2026/art_<hash>``
* ``collector._is_allowed_url()`` 对它们返回 True（域名没被拦）
* 但配置里的 ``detail_pattern`` 是 ``/\\d{6}/t\\d{8}_\\d+\\.s?html?$`` ——
  **工信部旧站的 URL 形态**。新版 CMS 的 ``/art/<年>/art_<hash>`` 匹配不上，
  于是 ``_extract_links`` 的三级放宽（容器 -> 整页 -> a[href]）逐级试完
  仍然全被这条正则滤光，返回 0 条。

``_extract_links`` 那三级放宽救不了这种情况：它们放宽的是**选择器**，
不是**详情页判据**。选择器放到最宽（全页 a[href]）也照样过不了
detail_pattern 那一关。所以症状是「三级都试了还是 0 条」，很容易被误判成
「站点是 JS 渲染的、渲染也拿不到」——实际渲染早就拿到了。

同一个 pattern 还被 6 个源共用（mof / miit / mee / mot / moa / mct），
其中 mof_gov、mee_gov、moa_gov 在库里有历史记录，说明**旧形态仍然有效**
（那是旧文章的 URL），不能只留新的也不能只留旧的，必须两种都收。

为什么是脚本而不是手改 YAML
---------------------------
pattern 以 ``$`` 结尾，而 PowerShell 会把 ``$`` 当变量吃掉——手敲进去的
正则到文件里就不是原来那个了。这里用 Python 读写，避免第二次踩坑。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "config" / "sources.yaml"

# 共用这套旧 pattern 的源（实测见上）
TARGETS = {"mof_gov", "miit_gov", "mee_gov", "mot_gov", "moa_gov", "mct_gov"}

# CMS 新版形态：/art/<4 位年>/art_<16 位以上 hex>，有的还带 .html
NEW_FORM = r"|/art/[0-9]{4}/art_[0-9a-f]{16,}"

NOTE = ("2026-10-02: 站点改版成 CMS，真实链接是 /art/2026/art_<hash>，"
        "旧 pattern 匹配不到，_extract_links 把整站滤成 0 条链接"
        "（miit_gov 实测 0 -> 11 条）。新旧两种形态都收。")


def main() -> int:
    doc = yaml.safe_load(SOURCES.read_text(encoding="utf-8"))
    sources = doc.get("sources") or []
    changed: list[str] = []
    for source in sources:
        sid = str(source.get("id"))
        if sid not in TARGETS:
            continue
        pattern = str(source.get("detail_pattern") or "")
        if not pattern:
            continue
        if "art_" in pattern:
            # 已经改过了。仍要规范化一次：上一版把新形态直接拼在末尾，
            # \\$ 只约束了新形态那一支，旧链接会全部失配（实测 old=False）。
            # 幂等很重要——改配置的脚本必须能反复跑。
            base = NEW_FORM.lstrip("|")
            if pattern.startswith("(?:"):
                continue                      # 已经是分组形式
            if not pattern.endswith(base):
                continue
            stem = pattern[:-len(base)].rstrip("|")
            source["detail_pattern"] = "(?:%s|%s)\\$" % (stem, base)
            changed.append(sid + "(规范化)")
            continue
        if not pattern.endswith("$"):
            print("跳过 %s：pattern 不是以 $ 结尾，不敢自动改：%r" % (sid, pattern))
            continue
        # 必须把新形态放进**分组**再跟旧形态并起来，否则末尾的 \\$ 会只
        # 约束新形态那一支：\.s?html?|/art/...  会被解析成
        # （\.s?html?）或（/art/...）——实测旧链接立刻全部失配。
        source["detail_pattern"] = "(?:%s|%s)\\$" % (pattern[:-1], NEW_FORM.lstrip("|"))
        source["detail_pattern_fixed"] = NOTE
        changed.append(sid)

    if not changed:
        print("没有需要修正的源（可能已经改过了）")
        return 0

    SOURCES.write_text(
        yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print("修正 %d 个源的 detail_pattern：%s" % (len(changed), ", ".join(changed)))
    return 0


def verify() -> int:
    """改完必须验：旧形态和新形态都要匹配得上。"""
    doc = yaml.safe_load(SOURCES.read_text(encoding="utf-8"))
    cases = {
        "new": "/zwgk/zcwj/wjfb/tz/art/2026/art_7d2e760b4be94217b8f55caec840b30d.html",
        "old": "/zwgk/t20260102_12345.html",
        "old_shtml": "/jigou/xxs/t20260102_1.shtml",
        "index": "/index.html",
    }
    bad = 0
    for source in doc.get("sources") or []:
        if str(source.get("id")) not in TARGETS:
            continue
        pattern = str(source.get("detail_pattern") or "")
        if not pattern:
            continue
        rx = re.compile(pattern)
        got = {k: bool(rx.search(v)) for k, v in cases.items()}
        ok = got["new"] and (got["old"] or got["old_shtml"]) and not got["index"]
        print("%-10s %s  new=%s old=%s index=%s"
              % (source.get("id"), "OK" if ok else "BAD",
                 got["new"], got["old"] or got["old_shtml"], got["index"]))
        bad += 0 if ok else 1
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())