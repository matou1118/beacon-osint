"""⚠️ 已废弃，不要运行（2026-10-07）。

这个脚本会按 AGENTS.md §4.1 的**旧 12 值枚举**把 category_l1 归并：
  公示公告 → 其他、政策发布 → 行业政策、行政审批 → 其他 ……
跑一次就会把库里的 21 类塌成 12 类，**不可逆地毁掉分类粒度**。

为什么留着文件而不是删掉：有人可能从 git 历史或旧会话里再次看到它。
留着并让它自己拒绝运行，比默默删掉更安全——「找不到文件」会让人去找
替代方案，而「明确告诉你别跑」会让人先问为什么。

实际情况（2026-10-07 实测）：
  * AGENTS.md §-1 已规定 §4 在开发期不构成约束；
  * 实际标准是 config/categories.yaml 的 21 类 / 105 个二级分类，
    前端下拉、GET /api/categories、订阅匹配（rules/engine.py）只认它；
  * 库内 15091 条的 category_l1 **100% 落在这 21 类内**，0 越界，
    所以本脚本的前提（「有 10 个非法值」）已经不成立。

要校验分类一致性，跑这个（只读，红了就是真漂移）：
  python -m pytest tests/test_category_taxonomy.py
  python scripts/data_health.py            # 看 category_enum_violations

维护本文件的替代物：tests/test_category_taxonomy.py
  它同时比对 l1 和 l2 父子关系——2026-10-06 实测 989 条记录的 category_l2
  挂在分类表没有的子类下（比选采购 338、课题承担 226、能量电力 125…），
  而当时所有单测都是绿的，因为只查了 l1。

用法：
  python scripts/clean_category_enum.py          # 拒绝运行并说明原因
  python scripts/clean_category_enum.py --apply  # 同上（--apply 也拒绝）
"""
from __future__ import annotations

import sys


def main() -> int:
    print(__doc__)
    print("拒绝运行。要校验分类一致性请用：")
    print("  python -m pytest tests/test_category_taxonomy.py")
    print("  python scripts/data_health.py    # category_enum_violations")
    return 2


if __name__ == "__main__":
    sys.exit(main())
