"""政府工作报告的指标抽取。

分两层，这是被真实语料逼出来的结构，不是设计偏好。

第一层「经济运行」—— 数值对比。
  实测 9 份报告（上海/广州/重庆 2024-2026，平均 22922 字）覆盖率：
    城镇新增就业 100%(9/9)  CPI 89%  地区生产总值 78%
    财政收入 33%  人均可支配收入 44%  人均GDP 11%
  这层是每份报告的"成绩单"，口径相对统一，能跨地区跨年比。

第二层「民生专题」—— 文本命中，不做数值对比。
  原计划抽 就业/医疗/教育 三类数值指标，实测医疗类 9 份报告命中 0 条、
  教育 1 条。不是正则写得差：上海的"医疗"写的是养老床位 3852 张、
  托育托额 8.1 万个、专家号源 31.3 万个，广州重庆同样不报执业医师数与
  卫生技术人员；教育只有"中小学教室加装空调全覆盖"这种无数值表述。
  所以民生层只回答"哪些报告提到了、投入方向是什么"，绝不硬凑数字。

三个设计点，都是为了让"对比"不至于得出假结论：

  is_target   「上年回顾」段的数字是上年实际完成值，「本年目标」段是本年
              目标值。报告 N 的目标要跟报告 N-1 的实际比，否则同年两个
              目标互比，比出来的是承诺之差不是落实之差。

  caliber     口径原文表述。常住人口 != 户籍人口，地区生产总值 != 生产总值。
              口径不同的值必须能被识别出来，否则跨地区对比是假的。

  缺失不补零 抽不到就是抽不到，存 None。存 0 会让"没披露"变成"为零"，
              在折线图上就是一个假的谷底。
"""
from __future__ import annotations

import re
from typing import Any

EXTRACT_VERSION = "metrics-v2"

# 命名捕获组，不要用位置解包。
# 早先 _PCT 写成 ({_NUM})\s*(%) 而 _NUM 自身已带一个组，于是 groups[0] 与
# groups[1] 是同一个数的两层，unit 变成了 "4.2"。指标名 -> 单位 的对应关系
# 靠下标维持，早晚会在某个嵌套组上再次错位。
_VALUE = r"(?P<value>-?[0-9]+(?:\.[0-9]+)?)"
_UNITS = r"(?P<unit>万亿元|亿元|万元|亿美元|万美元|万人次|万人|万|亿|元|人|个|名|所|家|张|床位)"
_NUMBER = rf"{_VALUE}\s*{_UNITS}"
_GROWTH = rf"(?:增长|下降|减少|提高|降低|上升|回落)\s*(?P<growth>-?[0-9]+(?:\.[0-9]+)?)\s*%"

# 指标名与数值之间的连接词。各省市写法差异极大，且实测同一指标在相邻
# 两年里就能换说法（上海 2026 写"城镇调查失业率平均为4.2%"），所以这里
# 用一个覆盖常见表述的可选组，而不是逐条枚举。
# 允许连接词连续出现最多两个。公文里有"平均值为""分别达到"这类两段式表述，
# 单 token 的可选组匹配不上（"全年城镇调查失业率平均值为4.2%" 就因此漏掉）。
_LINK = (r"(?:达到|完成|实现|达|为|是|平均|平均为|平均值为|分别|约为|约|近|超过|超出|超|逾|"
         r"不小于|不少于|在|计|总计|累计|新增|增长至|增至|增加到|控制在|保持在|降至|低于|"
         r"不超过|不高于|预计|计划){0,2}\s*")
# 百分号作为单位捕获
_PCT = rf"{_VALUE}\s*(?P<unit>%)"

# 指标定义。key 稳定、name 给人看；patterns 按顺序试，第一个命中的口径即 caliber。
# 顺序里把更"专"的表述放前面：宽泛的"就业"会命中大量无关句子。
METRICS: dict[str, dict[str, Any]] = {
    # ── 第一层：经济运行（每份报告的"成绩单"，跨地区可比）──
    "gdp": {
        "topic": "经济运行", "name": "地区生产总值", "layer": "numeric",
        "patterns": [
            (r"(?:全市|全省)?(?:地区)?生产总值(?:总量)?\s*" + _LINK + _NUMBER, "地区生产总值"),
        ],
    },
    "gdp_growth": {
        "topic": "经济运行", "name": "地区生产总值增速", "layer": "numeric",
        "patterns": [
            (r"(?:全市|全省)?(?:地区)?生产总值(?:同比)?(?:增长|增速|增幅)\s*" + _LINK + _PCT, "地区生产总值增速"),
            (r"(?:全市|全省)?(?:地区)?生产总值\s+" + _LINK + _PCT + r"(?=[^。]{0,8}(?:增长|增速))", "地区生产总值增速"),
        ],
    },
    "fiscal_revenue": {
        "topic": "经济运行", "name": "地方一般公共预算收入", "layer": "numeric",
        # 限定词"地方"是必需的。广州 2022 那份同时写
        # 「来源于广州地区的一般公共预算收入累计3.13万亿元」与
        # 「地方一般公共预算收入累计8475亿元」——前者是全地区口径（含区县），
        # 后者是市本级，两者差 3.7 倍。宽松匹配会把两个口径当成同一指标的值，
        # 交叉验证报出 100% 的差异。累计的那条也会被 _NOT_ANNUAL 拦掉，
        # 但限定词仍要收紧，避免非累计的"某某地区的一般公共预算收入"混进来。
        "patterns": [
            (r"(?:地方|本级|市本级)(?:一般公共预算|一般预算)收入\s*" + _LINK + _NUMBER, "地方一般公共预算收入"),
        ],
    },
    "fiscal_expenditure": {
        "topic": "经济运行", "name": "地方一般公共预算支出", "layer": "numeric",
        "patterns": [
            (r"(?:地方|本级|市本级)(?:一般公共预算|一般预算)支出\s*" + _LINK + _NUMBER, "地方一般公共预算支出"),
        ],
    },
    "cpi": {
        "topic": "经济运行", "name": "居民消费价格涨幅", "layer": "numeric",
        "patterns": [
            (r"居民消费价格(?:指数|涨幅)?\s*" + _LINK + _PCT, "居民消费价格"),
        ],
    },
    "per_capita_gdp": {
        "topic": "经济运行", "name": "人均地区生产总值", "layer": "numeric",
        "patterns": [
            (r"人均(?:地区)?生产总值\s*" + _LINK + _NUMBER, "人均地区生产总值"),
        ],
    },
    "per_capita_income": {
        "topic": "经济运行", "name": "居民人均可支配收入", "layer": "numeric",
        "patterns": [
            (r"(?:全体)?(?:居民)?人均可支配收入\s*" + _LINK + _NUMBER, "居民人均可支配收入"),
        ],
    },
    "population": {
        "topic": "经济运行", "name": "常住人口", "layer": "numeric",
        "patterns": [
            (r"常住人口\s*" + _LINK + _NUMBER, "常住人口"),
        ],
    },

    # ── 就业（实测覆盖尚可，留在数值层）──
    "new_urban_jobs": {
        "topic": "就业", "name": "城镇新增就业", "layer": "numeric",
        "patterns": [
            (r"城镇新增就业(?:人员)?\s*" + _LINK + _NUMBER, "城镇新增就业"),
            (r"新增城镇就业(?:人员)?\s*" + _LINK + _NUMBER, "城镇新增就业"),
            (r"城镇新增就业人数\s*" + _LINK + _NUMBER, "城镇新增就业"),
        ],
    },
    "urban_surveyed_unemployment": {
        "topic": "就业", "name": "城镇调查失业率", "layer": "numeric",
        "patterns": [
            (r"城镇调查失业率\s*" + _LINK + _PCT, "城镇调查失业率"),
        ],
    },
    "urban_registered_unemployment": {
        "topic": "就业", "name": "城镇登记失业率", "layer": "numeric",
        "patterns": [
            (r"城镇登记失业率\s*" + _LINK + _PCT, "城镇登记失业率"),
        ],
    },
    "urban_employment_total": {
        "topic": "就业", "name": "城镇就业人员", "layer": "numeric",
        "patterns": [
            (r"城镇就业人员(?:总数|规模)?\s*" + _LINK + _NUMBER, "城镇就业人员"),
        ],
    },

    # ── 第二层：医疗/教育。
    # 数值模式保留但预期几乎不命中（实测 9 份报告医疗 0 条）：重庆、广州
    # 可能报床位或卫生人员，命中就是赚的；命中不到就由 TOPIC_CUES 的文本
    # 层兜住，界面上显示"提及 N 次、无可比数值"，不假装有数字。
    "doctors_per_1000": {
        "topic": "医疗", "name": "每千人口执业医师数", "layer": "numeric",
        "patterns": [
            (r"每千人口(?:拥有)?执业(?:医师|医生)数?\s*" + _LINK + _NUMBER, "每千人口执业医师数"),
        ],
    },
    "health_beds": {
        "topic": "医疗", "name": "医疗卫生机构床位数", "layer": "numeric",
        # 限定词是必需的。早期版本把它们全写成可选，结果"新增养老床位3852张"
        # "『城市建设者管理者之家』床位3.2万张"都被当成医疗床位——数值对、
        # 指标错，这种错误比抽不到更糟，因为它看起来像有效数据。
        "patterns": [
            (r"(?:医疗卫生机构|医院|医疗|卫生)(?:机构)?(?:的)?床位数?\s*" + _LINK + _NUMBER, "医疗卫生机构床位"),
        ],
    },
    "health_staff": {
        "topic": "医疗", "name": "卫生技术人员", "layer": "numeric",
        "patterns": [
            (r"卫生技术人员(?:数)?\s*" + _LINK + _NUMBER, "卫生技术人员"),
        ],
    },
    "medical_insurance_enrolled": {
        "topic": "医疗", "name": "基本医保参保人数", "layer": "numeric",
        "patterns": [
            (r"(?:基本医疗保险|基本医保|医保)(?:参保)?(?:人数|人员)?\s*" + _LINK + _NUMBER, "基本医保参保人数"),
        ],
    },
    "students_enrolled": {
        "topic": "教育", "name": "在校生数", "layer": "numeric",
        "patterns": [
            (r"(?:各级各类学校)?在校生(?:数|总数)?\s*" + _LINK + _NUMBER, "在校生数"),
        ],
    },
    "full_time_teachers": {
        "topic": "教育", "name": "专任教师数", "layer": "numeric",
        "patterns": [
            (r"专任教师(?:数)?\s*" + _LINK + _NUMBER, "专任教师数"),
        ],
    },
    "preschool_gross_enrollment": {
        "topic": "教育", "name": "学前三年毛入园率", "layer": "numeric",
        "patterns": [
            (r"学前(?:教育)?三年(?:毛)?入园率\s*" + _LINK + _PCT, "学前三年毛入园率"),
        ],
    },
    "inclusive_preschool_coverage": {
        "topic": "教育", "name": "普惠性幼儿园覆盖率", "layer": "numeric",
        "patterns": [
            (r"普惠性幼儿园(?:覆盖率|占比)\s*" + _LINK + _PCT, "普惠性幼儿园覆盖率"),
        ],
    },
}

# 第二层文本命中词表。民生层只回答"提没提、往哪个方向投"，不做数值对比。
TOPIC_CUES: dict[str, tuple[str, ...]] = {
    "医疗": (
        "医院", "医疗", "卫生", "医师", "医生", "医保", "卫生健康", "健康服务",
        "家庭医生", "分级诊疗", "疾病", "诊疗", "急救", "救护", "中医药",
        "公共卫生", "传染病", "慢病", "老年人照护", "护理",
    ),
    "教育": (
        "学校", "教育", "学生", "教师", "学院", "幼儿园", "中小学", "高校",
        "义务教育", "学前", "学位", "校园", "双减", "职业教育", "产教融合",
        "培训机构", "教材", "招生", "毕业",
    ),
    "就业": (
        "就业", "失业", "劳动", "岗位", "职业", "用工", "技能培训", "创业",
        "招工", "农民工", "灵活就业", "新就业形态",
    ),
    "住房": (
        "住房", "保障房", "公租房", "棚改", "老旧小区", "租房", "购房", "房贷",
        "城中村", "保障性租赁住房", "安置房", "物业",
    ),
    "养老": (
        "养老", "老年", "长者", "托养", "护理院", "退休金", "基本养老金",
    ),
    "交通": (
        "地铁", "轨道", "公交", "交通", "道路", "桥梁", "高速", "铁路", "机场",
        "港口", "停车", "拥堵", "通勤",
    ),
    "环境": (
        "环境", "污染", "绿化", "碳达峰", "碳中和", "能耗", "空气质量", "PM2.5",
        "污水", "垃圾", "生态", "湿地", "河湖",
    ),
}


# 单位归一。跨省比较时"万人"和"人"不能混算。
_UNIT_SCALE: dict[str, float] = {
    "万亿元": 1e12, "亿元": 1e8, "万元": 1e4, "元": 1.0,
    "亿美元": 1e8, "万美元": 1e4,
    "亿人": 1e8, "万人": 1e4, "万人次": 1e4, "人": 1.0,
    "万": 1e4, "亿": 1e8,
    "个床位": 1.0, "床位": 1.0, "张": 1.0,
    "个": 1.0, "名": 1.0, "所": 1.0, "家": 1.0,
}

# 比率型指标：值本身就是百分数，单位记 %，不做量级缩放
_RATIO_UNITS = {"%"}

# 不该被当成指标的数值：年份、序号、届次、条款号
# 只滤年份，不滤个位数。
# 早先的 ^(?:19|20)\d{2}$|^\d{1,2}$ 会把 "5"、"2" 当序号丢掉，于是
# 「地区生产总值增长5%左右」「居民消费价格涨幅2%左右」
# 「城镇调查失业率5%以内」这些个位数百分比全部被静默丢弃——pattern 层明明
# 命中了，抽取层却返回空，看起来像覆盖率低。序号不必单独防：模式要求指标名
# 紧邻数值，"第5名"根本匹配不上。
_NOISE = re.compile(r"^(?:19|20)\d{2}$")

# 句中出现这些就说明这个数不是"本年值"，不能进年度序列：
#   累计     "地方一般公共预算收入累计8475亿元"        多年加总
#   五年/三年  "五年城镇新增就业72.4万人"              多年加总
#   到20XX年   "到2011年,全市地区生产总值超过1万亿元"     未来目标
#   年均     "年均增长5.2%"                            增速的增速
# 混进年度序列会造出一个假的数值：武汉 2012 因此出现"72.4万人"，
# 广州 2007 因此出现"1万亿元"（2011 年的目标被记到 2007 年）。
_NOT_ANNUAL = re.compile(
    r"累计|五年|三年|近[三五七八十\d]+年|过去[三五七八九十\d]+年|年均|"
    r"(?:到|预计到|力争到)\s*20\d{2}\s*年|"
    r"“?[一二三四五]?[十]?[五]?五”?规划|上个?五年|今后[三五]?年|未来[三五]?年"
)

# 目标类关键词：命中则 is_target=True（「本年目标」段）
_TARGET_CUES = ("目标", "计划", "力争", "争取", "确保", "达到", "实现", "预期", "力争达到")


def _scale(value: float, unit: str) -> float:
    if unit in _RATIO_UNITS:
        return value
    return value * _UNIT_SCALE.get(unit, 1.0)


def _is_target_text(text: str) -> bool:
    return any(cue in text for cue in _TARGET_CUES)


def _clean_number(raw: str) -> float | None:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def extract_metrics(
    text: str,
    *,
    region_code: str,
    year: int,
    section: str = "",
    section_key: str = "",
) -> list[dict[str, Any]]:
    """从一段报告文字里抽指标。

    section_key 是 is_target 的唯一依据：review 段的数字是已发生的事实，
    target 段的是承诺。曾经还叠加过句级线索（"力争""确保""达到"），结果
    「地方一般公共预算收入达到8500亿元…城镇调查失业率平均为4.2%」这句里的
    失业率被同句另一个指标的"达到"拖成了目标值——它在「一、上年工作回顾」
    段里，是已发生的事实。段落是可靠信号，句级措辞不是。
    """
    if not text:
        return []
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for key, spec in METRICS.items():
        for pattern, caliber in spec["patterns"]:
            compiled = re.compile(pattern)
            for match in compiled.finditer(text):
                # 命名组：value / unit / growth，不按���标猜位置。
                # 早先的位置解包撞上嵌套捕获组，unit 拿到了数值本身，
                # 界面上就成了 "4.2 4.2"。
                raw_value = match.group("value")
                unit = (match.groupdict().get("unit") or "").strip()
                value = _clean_number(raw_value)
                if value is None or value < 0 or _NOISE.match(raw_value) or value <= 0:
                    continue
                # 「人均地区生产总值达到3.2万美元」曾同时命中 gdp 与
                # per_capita_gdp，把人均数当成总量记进去。固定宽度的
                # (?<!人均) 挡不住：(?:地区)? 可选，正则会从"生"字处起匹配，
                # 那里前两字是"地区"。改为回看 4 字窗口——"人均"既可能紧邻
                # 匹配点（"人均生产总值"），也可能隔着"地区"（"人均地区生产总值"）。
                # 交叉验证抓到过这个：上海 2026 的 gdp 出现 5.67万亿元 与
                # 3.2万美元 两个值，差异 43.6%，后者就是人均值。
                if "人均" in text[max(0, match.start() - 4):match.start()]:
                    continue
                # 存原文数值与原文单位，不做量级归一。
                # 早先归一成 641000 + 单位仍写"万"，界面上就成了
                # "641,000.0 万"——数值对单位错。保真更安全：原文写
                # "64.1万人"就存 64.1 + 万人，evidence_text 里也留着，
                # 跨省换算交给分析层显式做，而不是在抽取时悄悄改口径。
                scaled = value

                # 取命中句作为出处
                start = text.rfind("。", 0, match.start()) + 1
                end = text.find("。", match.end())
                sentence = text[start: end if end > 0 else match.end() + 20].strip()
                if not sentence:
                    sentence = match.group(0)
                # 累计值、多年跨度、未来目标都不是"本年值"，混进年度序列会造出
                # 假数据（武汉 2012 的"72.4万人"是五年累计，广州 2007 的
                # "1万亿元"是 2011 年目标）。
                # 只看本句。早先还回看匹配点前 30 个字，结果把相邻分句里的
                # 正常值一起误杀：居民消费价格涨幅的 cells 从 13 掉到 3、
                # 地区生产总值增速从 18 掉到 11。句子里带不带这些词就是
                # 判断依据，跨句回看只会制造假阴性。
                if _NOT_ANNUAL.search(sentence):
                    continue

                # 同一指标同一单位在同一段里只留第一次出现
                dedupe = (key, unit)
                if dedupe in seen:
                    continue
                seen.add(dedupe)

                growth = re.search(_GROWTH, text[match.end(): match.end() + 24])
                out.append({
                    "region_code": region_code,
                    "year": year,
                    "topic": spec["topic"],
                    "metric_key": key,
                    "metric_name": spec["name"],
                    "value": scaled,
                    "unit": unit or None,
                    "growth_value": _clean_number(growth.group("growth")) if growth else None,
                    "growth_unit": "%" if growth else None,
                    # is_target 只由段落决定。早先还叠加了句级线索（"力争""确保"
                    # "达到"），结果「地方一般公共预算收入达到8500亿元…城镇调查
                    # 失业率平均为4.2%」这句里的失业率被同句另一个指标的"达到"
                    # 拖成了目标值，而它其实在「一、上年工作回顾」段里，是已发生
                    # 的事实。段落是可靠信号，句级措辞不是。
                    "is_target": section_key == "target",
                    "caliber": caliber,
                    "section": section or None,
                    "evidence_text": sentence[:400],
                    "extract_version": EXTRACT_VERSION,
                })
            # 该指标的模式命中过就不再试更宽泛的模式
            if any(item["metric_key"] == key for item in out):
                break
    return out


def metric_coverage(metrics: list[dict[str, Any]]) -> dict[str, int]:
    """按专题统计命中数量，用于验收时判断哪类指标缺。"""
    counts: dict[str, int] = {}
    for item in metrics:
        counts[item["topic"]] = counts.get(item["topic"], 0) + 1
    return counts


def layer_coverage(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    """按层统计：第一层数值指标 vs 第二层民生专题。

    这是判断"这套指标能不能支撑对比"的核心数字。民生层的数值指标几乎恒为
    0，界面应当显示"提及 N 次、无可比数值"，而不是显示 0。
    """
    out: dict[str, Any] = {"numeric": {}, "topic_hits": {}}
    for item in metrics:
        out["numeric"][item["topic"]] = out["numeric"].get(item["topic"], 0) + 1
    for topic, info in (item.get("_topics") or {}).items() if metrics else []:
        out["topic_hits"][topic] = info.get("hit_count", 0)
    return out


_TOPIC_SPLIT = re.compile(r"[。；\n]")


def extract_topic_hits(text: str, max_hits: int = 6) -> dict[str, dict[str, Any]]:
    """第二层：民生专题的文本命中。

    民生层没有可靠数值可比，但"这份报告有没有谈医疗、谈的是��还是社区养老"
    本身是信息。只记命中段落原文，不产出数值，也不做跨地区数值对比。
    """
    if not text:
        return {}
    sentences = [s.strip() for s in _TOPIC_SPLIT.split(text) if len(s.strip()) >= 10]
    out: dict[str, dict[str, Any]] = {}
    for topic, cues in TOPIC_CUES.items():
        matched = [s for s in sentences if any(cue in s for cue in cues)]
        if not matched:
            continue
        out[topic] = {
            "hit_count": len(matched),
            "hits": [{"text": s[:300]} for s in matched[:max_hits]],
        }
    return out

