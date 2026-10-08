"""从真实库里构建**高置信**分类基准（golden set）。

为什么��要它
------------
``tests/test_synthetic_data.py`` 用的是合成标题，门禁 L1 >=95% / L2 >=80%。
但 2026-10-03 实测发现：**合成基线的分布和真实标题不同**——合成标题多是
「关于印发XX的通知」这种模板句，真实标题里「公示/名单/批复」才是主信号。
所以我在合成基线上改了三次分类规则，三次都被门禁打回（L1 76.2% / 41.2%），
而其中至少一次改动（把「通知」加进强文种）在真实语料上确实是改好了的。

也就是说：**合成门禁绿 ≠ 真实分类对**。缺一个真实语料的门禁。

怎么保证这个基准本身是干净的
----------------------------
这是关键——2026-10-03 我已经因为「拿未验证的集合当基准」栽了三次
（见原则 stage|baseline_is_dirty）。所以这里只收**文种互斥、抽读验证过**
的类别：

* ✅ 公示公告/公示 —— 抽读 4/4 正确
* ✅ 政策法规/办法 —— 抽读 4/4 正确
* ❌ 公示公告/名单 —— **有歧义**：「关于…名单的公示」既算公示也算名单，
  取决于 STRONG_DOC_TYPES 的优先级顺序。这不是噪声，是设计问题，
  在没定论之前不能当基准。

「政策发布/通知」同样不收：通知的归类依赖主题（SPECIFIC_CATEGORIES 的加分
机制），单看「的通知」判断不了。

用法
----
    python scripts/build_golden_set.py --sample 200
    python -m pytest tests/test_golden_classify.py -q
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "golden" / "category_golden.json"
DB = ROOT / "data" / "beacon.db"

# 只收文种互斥、且 2026-10-03 抽读验证过 4/4 正确的组合。
# 见模块docstring 里为什么「名单」和「通知」被排除。
GENRES: list[tuple[str, str, str]] = [
    ("公示公告", "公示", r"公示(?!期)"),
    ("政策法规", "办法", r"(办法|规定|条例|细则)$"),
]


def build(sample: int, seed: int = 20261003) -> dict:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    # 排除 needs_review：被标记的记录标题本身可能是栏目页，会污染基准。
    titles = [r[0] for r in con.execute(
        "SELECT title FROM announcements "
        "WHERE COALESCE(needs_review, 0) = 0 AND title IS NOT NULL AND trim(title) <> ''")]
    con.close()

    # ★ 收样时就把栏目页剔掉，别指望测试去发现。
    #
    # 2026-10-03 实测：首版基准里混进了「公示公告-陕西省发改委」——标题就是
    # 栏目名 + 机构名，和前面抽样读出来的导航垃圾同一类。它匹配「公示」正则，
    # 于是被当成「确认是公示公告」的高置信样本，实际是目录页。
    # 这类标题**必须**在构建阶段排除，否则基准自己就脏了
    # （又是 stage|baseline_is_dirty）。
    def _is_section_page(title: str) -> bool:
        t = re.sub(r"\s+", "", title)
        if len(t) <= 16 and re.search(r"-\S{2,}(厅|局|委|政府|发改委|网)$", t):
            return True
        # 「栏目名 - 机构名」整体就是栏目
        return len(t) <= 20 and bool(re.match(r"^\S{2,8}-\S{2,10}(厅|局|委|政府)$", t))

    rng = random.Random(seed)
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for l1, l2, pattern in GENRES:
        pool = [t for t in titles
                if re.search(pattern, t) and t not in seen
                and len(t) >= 14 and not _is_section_page(t)]
        rng.shuffle(pool)
        for title in pool[:sample]:
            seen.add(title)
            out.append({"title": title, "category_l1": l1, "category_l2": l2})
    return {
        "built_at": "2026-10-03",
        "note": "高置信子集：只含文种互斥且抽读验证过的组合。"
                "名单/通知 因歧义或需主题信息，刻意不收。",
        "threshold": {"category_l1": 0.95, "category_l2": 0.80},
        "cases": out,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="构建真实语料分类基准")
    ap.add_argument("--sample", type=int, default=120,
                    help="每个文种采多少条")
    args = ap.parse_args(argv)

    data = build(args.sample)
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    print("已写入 %s：%d 条用例（%d 个文种 × %d）"
          % (GOLDEN, len(data["cases"]), len(GENRES), args.sample))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())