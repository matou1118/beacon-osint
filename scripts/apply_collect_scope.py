"""按 config 的 collect_scope 白名单，同步 config/sources.yaml 的启停。

为什么需要
  用户明确「只采部委/北京/上海/广州/深圳/成都/重庆/浙江/江苏/武汉」。
  原来 114 个 enabled 源里只有 34 个落在范围内，其余 80 个（四川/福建/河南/
  陕西/安徽/河北/山东/湖北/山西/内蒙/辽宁/吉林/黑龙江/海南/云南/西藏/青海/
  宁夏/新疆/天津/湖南/香港/澳门/广东省……）都在白跑，每轮还占 300~600 秒。

  范围写在 lighthouse/config/web_collector_config.yaml 的 collect_scope 里，
  本脚本据此改 sources.yaml 的 enabled/status，run.py 再按同一张表兜一道。
  两处都读同一个来源，不各写一份。

判据
  - include 命中任一条即采（gov_level 或 region 命中）
  - exclude 优先级更高：香港/澳门即使将来被别处匹配也排除
  - 只改 enabled/status 两个字段，notes 追加本次依据，保留原始备注
  - 先备份 sources.yaml；--apply 才落盘

用法
  python scripts/apply_collect_scope.py            # 预览
  python scripts/apply_collect_scope.py --apply    # 落盘
"""
from __future__ import annotations

import argparse
import collections
import shutil
import sys
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "config" / "sources.yaml"
SCOPE_CONFIG = ROOT / "lighthouse" / "config" / "web_collector_config.yaml"


def in_scope(source: dict, scope: dict) -> bool:
    include = scope.get("include") or {}
    gov_levels = {str(x) for x in (include.get("gov_level") or [])}
    regions = {str(x) for x in (include.get("region") or [])}
    if not gov_levels and not regions:
        return True                      # 没配范围 = 不限制
    excluded = {str(x) for x in (scope.get("exclude") or [])}
    province = str(source.get("region_province") or "")
    city = str(source.get("region_city") or "")
    if province in excluded or city in excluded:
        return False
    if str(source.get("gov_level") or "") in gov_levels:
        return True
    return bool(province in regions or city in regions
                or any(province.startswith(r) or city.startswith(r) for r in regions))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--show", type=int, default=20)
    args = parser.parse_args()

    cfg = yaml.safe_load(SCOPE_CONFIG.read_text(encoding="utf-8")) or {}
    scope = cfg.get("collect_scope") or {}
    if not scope:
        print("config 里没有 collect_scope，先补上再跑这个脚本")
        return 1

    data = yaml.safe_load(SOURCES.read_text(encoding="utf-8"))
    sources = data["sources"] if isinstance(data, dict) else data

    keep, drop, keep_stuck = [], [], []
    for source in sources:
        wanted = in_scope(source, scope)
        is_on = bool(source.get("status") == "enabled" and source.get("enabled"))
        if wanted and is_on:
            keep.append(source)
        elif wanted and not is_on:
            keep_stuck.append(source)
        elif not wanted and is_on:
            drop.append(source)

    include = scope.get("include") or {}
    print("配置范围：include gov_level=%s region=%s / exclude=%s"
          % (include.get("gov_level"), include.get("region"), scope.get("exclude")))
    print()
    print("范围内且已启用: %d" % len(keep))
    print("将停用:        %d" % len(drop))
    print("范围内但当前不可用（blocked 等，需要重探）: %d" % len(keep_stuck))
    for s in keep_stuck:
        print("   %-16s %-28s status=%s"
              % (s.get("id"), str(s.get("name"))[:26], s.get("status")))

    if drop:
        print()
        print("将停用的源（按省份）:")
        for prov, n in collections.Counter(
                str(s.get("region_province") or s.get("gov_level") or "?")
                for s in drop).most_common():
            print("   %-16s %d" % (prov, n))
        print()
        print("明细（前 %d）:" % args.show)
        for s in drop[:args.show]:
            print("   %-16s %-30s %s" % (s.get("id"), str(s.get("name"))[:28],
                                          s.get("region_province") or ""))

    if not drop:
        print("\n没有需要停用的源。")
        return 0

    if not args.apply:
        print("\n（预览模式，未写入。加 --apply 落盘）")
        return 0

    stamp = datetime.now().isoformat(timespec="seconds")
    backup = SOURCES.with_suffix(f".yaml.bak-scope-{datetime.now():%Y%m%dT%H%M%S}")
    shutil.copy2(SOURCES, backup)
    print("\n已备份 %s" % backup.name)

    for s in drop:
        # status 与 enabled 必须一起改。只改一个会留下
        # status=disabled / enabled=True 这类矛盾态，采集器按 enabled 过滤，
        # 配置看着停了实际还在跑。
        s["status"] = "disabled"
        s["enabled"] = False
        old = str(s.get("notes") or "").strip()
        s["notes"] = (old + f" | {stamp} 按 collect_scope 白名单停用（"
                             f"范围：部委/北京/上海/广州/深圳/成都/重庆/浙江/江苏/武汉）").strip()

    SOURCES.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=200),
        encoding="utf-8")

    total_on = sum(1 for s in sources
                   if s.get("status") == "enabled" and s.get("enabled"))
    bad = [s.get("id") for s in sources
           if (s.get("status") == "enabled") != bool(s.get("enabled"))]
    print("已停用 %d 个源；现在 enabled 源共 %d 个" % (len(drop), total_on))
    print("status/enabled 矛盾态:", bad or "无")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
