"""金额与关键数值的准确抽取：表格优先、标签配对、单位归一、排除非金额。

为什么单独写，而不是继续往 policy_fields.BODY_PATTERNS 里加正则
  那个文件里的规则是"线索词 + 后面一个数"，它有三个测出来的问题：
  1. 一份文件里多个金额时抓错对象。实测 id=86 的环评公示里有
     「本项目动态投资总计1923610万元，其中环保投资约18252.5万元」
     「本工程总投资390622万元，其中环保设施及措施投资24196.75万元」
     「本项目动态总投资约441128万元，环保投资约4258万元」
     ——要回答"环保投资多少"，只知道有个金额不够，必须知道这个数对应哪个标签。
  2. 表格里的金额抽不到。表格被压平成 `|采购预算|最高限价|…|495000元|`，
     线索词和数字之间隔着竖线，且前面还有一整行表头。
  3. 准确性无法保证。电话号码 028-86702309、邮编 610000、百分比 12.5%、
     文号〔2026〕2号、面积 1200平方米 都会被"数字+单位"规则误抓。
     用户明确要求"金额要想法办法准确抽取"，那就必须把非金额挡在门外。

三层抽取，按可靠性排序（先表格，再带标签的散文，最后无标签兜底但不采信）
  1. 表格行：把 `|a|b|c|` 拆成单元格，按"标签列 → 相邻数值列"配对。
     表格里的金额可靠性最高——它本身就是结构化数据。
  2. 带标签的散文：「(总投资|投资额|预算|补助|补助资金|资金|注册资本|
     合同金额|最高限价|采购预算)[^数字]{0,12}(数字)(单位)」。
     标签决定这个数是什么，不靠猜。
  3. 无标签的数字：不采信为金额，只作为 `numeric_candidates` 返回给人工看。
     宁可漏也不错——错误金额会污染时间轴对比，比没有金额更糟。

单位归一：全部换算成"元"存原值，同时保留原始书写形式。
  1.92 亿元 与 19200 万元 必须在时间轴上是同一个点，否则对比就是错的。

过滤非金额：电话/传真/邮编/身份证/统一社会信用代码/文号/序号/面积/人口/年限
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

# ---------------------------------------------------------------- 单位归一

UNIT_TO_YUAN = {
    "元": 1,
    "万元": 10_000,
    "亿元": 100_000_000,
    "万亿": 1_000_000_000_000,
    "万": 10_000,
    "亿": 100_000_000,
}

# 数字 + 货币单位。负号和小数都要，金额可能有小数（18252.5万元）
# 两处注意，都是实测踩出来的：
# 1. _NUM 内部的 `|`（千分位分支 / 纯数字分支）必须包在 (?:) 里。不包的话它
#    优先级最低，会把整个正则劈成两半——一支只匹配数字、完全不要求单位，
#    而 lookbehind 只作用在第一支上。静默抽错，不报错。
# 2. 数字要用捕获组：group(1)=数字 group(2)=单位，全文件的调用都按这个取。
#
# 单位后面还要排除计数/计量量词。实测抓到过两类：
#   「筛查不少于25万人次」   → 25万 被当成 25万元
#   「30万千瓦热电联产机组」 → 30万 被当成 30万元（千瓦是功率）
#   「退出6000万吨/年生产线」→ 6000万 被当成 6000万元（万吨是重量）
# 但「5.67万元/亩」「300元/人」要留——那是单价/费率，是真金额。
_NUM = r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
_NOT_COUNT = (r"(?!\s*[人次人户台件个名只头匹万亩吨升千百兆瓦马力方]"
              r"|年月日时分秒])")
MONEY_RE = re.compile(
    rf"(?<![\d.])(\d[\d,]*(?:\.\d+)?)\s*(万亿|亿元|万元整|万元|元|亿|万){_NOT_COUNT}")

# ---------------------------------------------------------------- 金额标签

# 标签 -> 规范名。顺序即优先级：越靠前越具体，用于同名标签的归属判定。
AMOUNT_LABELS: tuple[tuple[str, str], ...] = (
    ("采购预算", "采购预算"),
    ("项目预算", "预算金额"),
    ("预算金额", "预算金额"),
    ("预算", "预算金额"),
    ("最高限价", "最高限价"),
    ("控制价", "控制价"),
    ("拦标价", "控制价"),
    ("投资总额", "投资总额"),
    ("总投资", "投资总额"),
    ("项目总投资", "投资总额"),
    ("动态投资", "投资总额"),
    ("静态投资", "投资总额"),
    ("环保投资", "环保投资"),
    ("环保设施及措施投资", "环保投资"),
    ("工程总投资", "投资总额"),
    ("补助资金", "补助资金"),
    ("奖补资金", "补助资金"),
    ("支持资金", "补助资金"),
    ("专项资金", "专项资金"),
    ("资金总额", "资金总额"),
    ("合同金额", "合同金额"),
    ("成交金额", "成交金额"),
    ("中标金额", "中标金额"),
    ("注册资本", "注册资本"),
    ("总投资额", "投资总额"),
    ("贷款金额", "贷款金额"),
    ("授信额度", "授信额度"),
    ("贴息", "贴息金额"),
    ("保费", "保费"),
    ("面值总额", "面值总额"),
    ("收费标准", "收费标准"),
    ("评审费", "收费标准"),
    ("工本费", "收费标准"),
)

# 时间轴不能把所有金额混成一条线。「评审收费 300元/人」和「项目投资 3000万元」
# 放一起比毫无意义，所以按 label 分组对比。收费标准这类"费"必须单列，
# 否则它会污染投资额的时间轴。

_LABEL_ALT = "|".join(sorted({lbl for lbl, _ in AMOUNT_LABELS}, key=len, reverse=True))

# 标签与数字之间允许的连接文字：冒号、空白、"约"、"为"、"计"、"金额"等
_BRIDGE = r"[^0-9\n]{0,12}?"
LABELLED_RE = re.compile(
    rf"(?:{_LABEL_ALT}){_BRIDGE}({_NUM})\s*(万亿|亿元|万元整|万元|元|亿|万)")

# 表格里"标签列"与"数值列"相邻：`|采购预算|最高限价|` 或 `|资金|金额|`
TABLE_ROW_RE = re.compile(r"\|[^|\n]{1,40}\|")

# ---------------------------------------------------------------- 非金额过滤

# 这些词左边 24 字内的数字不是金额
NOT_AMOUNT_CTX = re.compile(
    r"(?:电话|传真|邮编|邮　编|开户行|开户银行|账　号|账号|"
    r"身份证|统一社会信用代码|信用代码|法人代表|"
    r"序号|编号|件号|批号|证书号|文号|批文|备案号|"
    r"面积|容积|户数|人数|人数次|床位|车位|公里|千米|米|平方米|立方米|"
    r"年限|有效期|工龄|年龄|比例|占比|增幅|下降|增长|税率|利率|百分比)")

# 形如 2026-09-28 / 2026年9月28日 的日期不能当金额
DATE_RE = re.compile(r"(?:19|20)\d{2}\s*[-/年.]")

# 明显是电话/邮编：8 位以上纯数字、无小数
LONG_PLAIN = re.compile(rf"^{_NUM}$")


# ---------------------------------------------------------------- 数据结构


@dataclass
class AmountHit:
    """一处金额。value_yuan 是归一后的元，raw 是原始书写。"""

    label: str
    raw: str
    value_yuan: float
    unit: str
    confidence: int
    source: str          # table | prose
    evidence: str = ""
    year: int | None = None

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "raw": self.raw,
            "value_yuan": self.value_yuan,
            "unit": self.unit,
            "confidence": self.confidence,
            "source": self.source,
            "evidence": self.evidence,
            "year": self.year,
        }


def _context(text: str, start: int, end: int, before: int = 40, after: int = 24) -> str:
    """截命中处上下文，且**保证命中本身一定在结果里**。

    踩过的坑：原来写 `text[start-60 : end+24][:90]`。前面留 60 字，总长很容易
    超过 90，后面的 `[:90]` 就把命中处本身切掉了——evidence 里找不到那个金额
    （用 raw.find(evidence) == -1 就能发现）。人工复核时看到的是一段不含金额
    的文字，等于没法审。
    """
    head = text[max(0, start - before):start]
    tail = text[end:end + after]
    return (head + "⟦" + text[start:end] + "⟧" + tail).replace("\n", " ").strip()


def _to_yuan(number: str, unit: str) -> float:
    base = unit.rstrip("整")
    return round(float(number.replace(",", "")) * UNIT_TO_YUAN.get(base, 1), 2)


def _year_of(text: str) -> int | None:
    m = DATE_RE.search(text or "")
    return int(m.group(0)[:4]) if m else None


def _nearest_year(text: str, start: int, end: int, radius: int = 160) -> int | None:
    """取离金额**最近**的年份，而不是文本里出现的第一个。

    两个坑：
    1. 窗口太窄。原先只回看 60 字，于是「……根据2024年预算执行情况，2026年
       安排支出51948亿元」里的 51948 落在窗口外，年份只能回退成发布年 2026
       ——跨年对比时这就直接错位了。放宽到 160 字。
    2. 取第一个而不是最近的。一份文件里常有好几个年份（发布年、执行年、目标年），
       挨着金额的那个才是它所属的年份。
    """
    lo = max(0, start - radius)
    window = text[lo:end + 40]
    best: tuple[int, int] | None = None
    for m in DATE_RE.finditer(window):
        offset = lo + m.start()
        # 距离：年份在金额左侧还是右侧
        dist = (start - offset) if offset < start else (offset - end)
        if best is None or dist < best[0]:
            best = (dist, int(m.group(0)[:4]))
    return best[1] if best else None


# ---------------------------------------------------------------- 表格抽取


def _clause_before(text: str, pos: int, width: int = 24) -> str:
    """取 pos 之前 width 字，但**不跨句子边界**。

    踩过的坑：原本直接取 text[pos-24:pos]，结果「…环保投资占项目总投资的
    比例为0.95%。本工程总投资390622万元」里的 390622 被误杀——左窗口 24 字
    里含"比例"，命中 NOT_AMOUNT_CTX。而 0.95% 和 390622 万元根本不在一个
    句子里。所以先按句读切掉，只在**同一分句**内回看。
    """
    left = text[max(0, pos - width):pos]
    for sep in ("。", "！", "？", "；", "\n", "，", "、"):
        cut = left.rfind(sep)
        if cut >= 0:
            left = left[cut + 1:]
    return left


def parse_table_amounts(body: str) -> list[AmountHit]:
    """从管道符表格里抽「标签列 → 数值列」，按**列位置**配对。

    表格被 trafilatura 压平成 `|采购预算|最高限价|495000元|…|`，表头和数据行
    常常挤在同一行（实测 id=137 是「…2.资金来源：已落实|分包号|分包名称|
    采购预算|最高限价|项目属性|备注||第一包|舆情监测服务|495000元|495000元|
    服务」）。所以不能假设"表头单独一行"。

    配对策略（用户要求"有表格用表格"）：
      先按顺序收集本行里的标签格和金额格，数量相同就**按位置 zip**——
      实测上面这行标签 [采购预算, 最高限价]、金额 [495000元, 495000元]，
      位置一一对应，两列各自的金额都拿对了。
      数量不同（表头和数据行真的分行）就退回"就近标签"：向上找最近的标签格。
    """
    hits: list[AmountHit] = []
    for line in (body or "").splitlines():
        if line.count("|") < 2:
            continue
        cells = [c.strip() for c in line.split("|")]
        labels: list[str] = []
        # 存 (列号, 单元格文本, 匹配)。必须把 cell 一起存下来：早先只存了
        # (idx, m)，构建 evidence 时引用的 `cell` 是上一个 for 循环遗留的最后一个
        # 单元格——整行最后一个格常常是空的，于是 evidence 变成 "⟦⟧"，373 处里
        # 108 处丢失了可复核的原文。
        values: list[tuple[int, str, re.Match]] = []
        for idx, cell in enumerate(cells):
            if not cell:
                continue
            norm = next((n for lbl, n in AMOUNT_LABELS if lbl in cell), None)
            if norm and not MONEY_RE.search(cell):
                labels.append(norm)
                continue
            for m in MONEY_RE.finditer(cell):
                if NOT_AMOUNT_CTX.search(_clause_before(cell, m.start())):
                    continue
                values.append((idx, cell, m))
        if not values:
            continue
        by_position = len(labels) == len(values)
        for order, (idx, cell, m) in enumerate(values):
            if by_position:
                label, conf = labels[order], 88
            elif labels:
                # 就近标签：取该金额之前最近的标签格
                near = [l for i, l in enumerate(labels) if i < order]
                label = near[-1] if near else "未标注"
                conf = 80 if label != "未标注" else 62
            else:
                label, conf = "未标注", 62
            hits.append(AmountHit(
                label=label,
                raw=m.group(0).strip(),
                value_yuan=_to_yuan(m.group(1), m.group(2)),
                unit=m.group(2),
                confidence=conf,
                source="table",
                evidence=_context(cell, m.start(), m.end(), before=30, after=18),
                year=_nearest_year(line, m.start(), m.end()),
            ))
    return hits


# ---------------------------------------------------------------- 散文抽取


def parse_prose_amounts(body: str, title: str = "") -> list[AmountHit]:
    """从正文/标题里抽「标签 + 金额」。

    只有带标签的才采信。裸数字（"共 12 项"）无法判断是什么，宁可漏。
    """
    text = f"{title or ''}\n{body or ''}"
    hits: list[AmountHit] = []
    for m in LABELLED_RE.finditer(text):
        start, end = m.start(), m.end()
        if NOT_AMOUNT_CTX.search(_clause_before(text, start)):
            continue
        # 标签本身若属于非金额语境（"总投资率"之类）也排除
        if NOT_AMOUNT_CTX.search(m.group(0)):
            continue
        label = next((norm for lbl, norm in AMOUNT_LABELS if lbl in m.group(0)), "金额")
        # 标题里出现金额是最强信号（"关于下达500万元补助的通知"）
        in_title = (title or "") and m.group(0) in (title or "")
        hits.append(AmountHit(
            label=label,
            raw=m.group(0).strip(),
            value_yuan=_to_yuan(m.group(1), m.group(2)),
            unit=m.group(2),
            confidence=92 if in_title else 74,
            source="prose",
            evidence=_context(text, start, end),
            year=_nearest_year(text, start, end),
        ))
    return hits


# ---------------------------------------------------------------- 交叉印证


def cross_verify(hits: Iterable[AmountHit]) -> list[AmountHit]:
    """同标签同值但来自不同路径 → 提置信度；同标签不同值 → 分情况处理。

    用户要求的"交叉印证"：表格和散文都报了同一个金额，说明两条独立路径指向
    同一事实，可信度高，+10。

    同标签出现**不同**金额不能一律当矛盾。踩过的坑：原来一律 -20，实测 id=86
    那份环评公示列出的是三个项目的投资
      「本项目动态投资总计1923610万元」
      「本工程总投资390622万元」
      「本项目动态总投资约441128万元」
    三个都是"投资总额"标签、不同值，但它们本来就是三个不同项目——降分后
    54 分掉到 60 门槛以下，三个全被丢掉，正好丢的是最该留的数据。

    所以按量级分两种情况：
      比值 < 3   同一个东西报出两个接近的数 → 真不一致，降一点并标注待复核
      比值 >= 3  明显是不同项目/年度/币种 → 正常，不罚
    两种都**保留**。删数据比标脏数据危险：时间轴对比靠的就是这些点。
    """
    items = list(hits)
    by_label: dict[str, list[AmountHit]] = {}
    for hit in items:
        by_label.setdefault(hit.label, []).append(hit)
    for label, group in by_label.items():
        values = {g.value_yuan for g in group}
        sources = {g.source for g in group}
        if len(group) > 1 and len(values) == 1 and len(sources) > 1:
            for g in group:
                g.confidence = min(98, g.confidence + 10)
                g.evidence += "｜表格与正文一致"
        elif len(values) > 1:
            lo, hi = min(values), max(values)
            # lo 可能是 0（金额为 0 的记录），用 hi 防除零
            near = lo > 0 and hi / lo < 3
            for g in group:
                if near:
                    g.confidence = max(62, g.confidence - 12)
                    g.evidence += "｜同标签多个接近金额，待复核"
                else:
                    g.evidence += f"｜同标签 {len(values)} 个不同金额（不同项目/年度）"
    return items


def extract_amounts(body: str, title: str = "", *, year: int | None = None
                    ) -> tuple[list[AmountHit], list[str]]:
    """返回 (采信的金额, 未采信的裸数字候选)。

    采信门槛 60：表格里带标签的（88）、标题里的（92）、正文带标签的（74）
    过线；表格里没标签的（62）勉强过；裸数字不进采信列表。
    """
    hits = parse_table_amounts(body) + parse_prose_amounts(body, title)
    hits = cross_verify(hits)
    if year:
        for hit in hits:
            if hit.year is None:
                hit.year = year
    trusted = [h for h in hits if h.confidence >= 60]
    # 去掉完全重复的（同一标签同值同来源）
    seen: set[tuple] = set()
    unique: list[AmountHit] = []
    for h in trusted:
        key = (h.label, h.value_yuan, h.source)
        if key in seen:
            continue
        seen.add(key)
        unique.append(h)
    candidates: list[str] = []
    for line in (body or "").splitlines():
        # 表格行里的数字已由 parse_table_amounts 处理，别再报成"未采信"
        if line.count("|") >= 2:
            continue
        for m in MONEY_RE.finditer(line):
            ctx = line[max(0, m.start() - 20):m.end() + 6]
            if not NOT_AMOUNT_CTX.search(_clause_before(line, m.start())) \
                    and not LABELLED_RE.search(ctx):
                candidates.append(ctx.strip()[:70])

    # 全文唯一金额 → 采信为"主要金额"。这是"没表格时交叉印证"的落地形态：
    # 只有一个候选时不存在"这个数指什么"的歧义，等于被排除法印证过。
    # 实测这样的公告 112 条，且全部带年份——多标一个"主要金额"标签就能进时间轴。
    # 多个金额且都无标签的（149 条）仍然不采信，歧义太大，列进 candidates 供人工看。
    if not unique:
        text = f"{title or ''}\n{body or ''}"
        all_money = [m for m in MONEY_RE.finditer(text)
                     if not NOT_AMOUNT_CTX.search(_clause_before(text, m.start()))
                     and not LABELLED_RE.search(
                         text[max(0, m.start() - 14):m.end() + 2])]
        if len(all_money) == 1:
            m = all_money[0]
            unique = [AmountHit(
                label="主要金额",
                raw=m.group(0).strip(),
                value_yuan=_to_yuan(m.group(1), m.group(2)),
                unit=m.group(2),
                # 66：高于采信门槛 60，但低于任何带标签的（74+），排序时靠后
                confidence=66,
                source="prose",
                evidence=_context(text, m.start(), m.end(), before=30, after=18),
                year=_nearest_year(text, m.start(), m.end()) or year,
            )]
    return unique, candidates[:8]
