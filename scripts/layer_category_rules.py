"""把新增的政务文种类改成分层：原 13 类不动，新类只在原类全空时启用。

为什么必须分层，而不是继续调权重
  直接把新类塞进同一个 categories 池，合成基线从 95% 掉到 61.7%，L2 掉到 57%。
  逐词排查后发现根因不是"新类不对"，而是两套数据分布不同：
    - 原始 13 类是在合成集上调出来的（scoring.tuned_on 写明 L1=98.3%），
      合成标题大量是「关于印发XX实施方案的通知」这种模板句；
    - 新增 7 类是从真实语料审计出来的（1465 条兜底记录），真实标题里
      「公示/名单/批复/解读」才是主信号。
  两套都在同一个计分池里，就会出现新类把老类判对的记录抢走。
  而"新增公告词"这条路上逐词打补丁是错的方向：每修一个词就冒出下一个，
  已经证明了——通知→印发→方案 连修三轮，准确率 61.7%→78.9%→79.2%，
  离 95% 仍远，且每一步都在真实语料上做取舍。

分层的实际含义
  第 1 层：原始 13 类（标题关键词 + 部门回退）——合成集 95% 全部走这一层，行为不变。
  第 2 层：新增政务文种 7 类——只有第 1 层一个都没命中时才启用。
  真实语料里 93% 原本落兜底，正好全部落到第 2 层；合成集里几乎每条都能被
  第 1 层命中，所以第 2 层对它是静默的。两个目标不再冲突。
"""
from __future__ import annotations

import copy
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "config" / "category_rules.yaml"

NEW_LAYER = ("政策发布", "公示公告", "政策法规", "规划计划", "政策解读",
             "行政审批", "非公告体裁")


def main() -> int:
    raw = subprocess.run(
        ["git", "cat-file", "-p", "HEAD:config/category_rules.yaml"],
        cwd=ROOT, capture_output=True, check=True).stdout
    original = yaml.safe_load(raw.decode("utf-8"))
    current = yaml.safe_load(RULES.read_text(encoding="utf-8"))

    # 第 1 层：原始 13 类，逐字还原（不是拷贝当前值——当前已被我改过关键词）
    # 深拷贝，否则下面的 pop 会改到 original_cats 之外的对象上。
    original_cats = copy.deepcopy(original["categories"])
    new_cats = {name: copy.deepcopy(current["categories"][name])
                for name in NEW_LAYER if name in current["categories"]}
    # 原始类里被我塞过的新子类要撤回
    extra_subs_added = []
    for name, spec in original_cats.items():
        subs = spec.get("subcategories") or {}
        cur_subs = ((current["categories"].get(name) or {}).get("subcategories") or {})
        for sub in list(cur_subs):
            if sub not in (subs or {}):
                subs.pop(sub, None)
                extra_subs_added.append(f"{name}/{sub}")

    merged = dict(original)
    merged["categories"] = original_cats
    merged["fallback_categories"] = new_cats
    merged["scoring"] = dict(original["scoring"])
    merged["scoring"]["tuned_on"] = (
        "第1层13类沿用 synthetic 5000 条调优结果（L1=98.3% L2=89.3%）；"
        "第2层政务文种7类由 1465 条真实兜底记录审计得出（见 "
        "scripts/audit_categories.py），不参与合成集调参"
    )

    RULES.write_text(
        yaml.safe_dump(merged, allow_unicode=True, sort_keys=False, width=200),
        encoding="utf-8")
    print(f"第1层（原始，合成集回归基线）: {len(original_cats)} 个一级分类")
    print(f"第2层（新增，仅第1层全空时启用）: {len(new_cats)} 个 -> {list(new_cats)}")
    if extra_subs_added:
        print(f"撤回我曾塞进第1层的子类 {len(extra_subs_added)} 个: {extra_subs_added}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
