from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

import yaml

DEFAULT_RULES_PATH = Path(__file__).resolve().parents[1] / "config" / "category_rules.yaml"
DEFAULT_DEPARTMENT_PATH = Path(__file__).resolve().parents[1] / "config" / "department_map.yaml"
DEFAULT_FIELDS = ("title", "content", "key_points", "issuing_body", "functions", "tags")
DEFAULT_TITLE_WEIGHT = 3
DEFAULT_BODY_WEIGHT = 1
DEFAULT_SPECIFIC_BONUS = 2
NOISE_LIMIT = 600
# Process words ("申报"/"通知"/"公告") are shared by many categories, so a title
# carrying a concrete entity (补贴, 招聘, 招生, 津贴) must outweigh them. These
# categories define themselves by that entity, so they get a score bonus.
SPECIFIC_CATEGORIES = frozenset({
    "行业补贴", "养老与社区", "人才政策", "招生信息", "公务员考试", "公招信息", "军队招录",
})
# 部门归类的唯一落点：部门名只作为 category_l2（行业领域），一级分类固定为
# 「行业政策」。这样 classify() 的一级输出始终落在 config/categories.yaml
# 的一级枚举内。
DEPARTMENT_CATEGORY_L1 = "行业政策"
TITLE_WEIGHT = DEFAULT_TITLE_WEIGHT
BODY_WEIGHT = DEFAULT_BODY_WEIGHT
SPECIFIC_BONUS = DEFAULT_SPECIFIC_BONUS


def weights_from(config: Mapping[str, Any] | None) -> tuple[int, int, int]:
    scoring = (config or {}).get("scoring") or {}
    return (
        int(scoring.get("title_weight", DEFAULT_TITLE_WEIGHT)),
        int(scoring.get("body_weight", DEFAULT_BODY_WEIGHT)),
        int(scoring.get("specific_bonus", DEFAULT_SPECIFIC_BONUS)),
    )


def load_department_map(path: str | Path = DEFAULT_DEPARTMENT_PATH) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError("config/department_map.yaml must contain a mapping")
    return data


def classify_by_department(
    record: Mapping[str, Any], config: Mapping[str, Any] | None = None
) -> dict[str, str] | None:
    """Classify from the issuing body, which is far more reliable than the title.

    A ministry or bureau decides the business area, so "财政部关于记账式贴现国债
    发行" is finance regardless of the wording. Returns None when nothing
    matches so the caller can fall back to keyword rules.
    """
    rules = config if isinstance(config, dict) and config.get("department_map") else load_department_map()
    body = _normalize(record.get("issuing_body") or record.get("department") or "")
    if not body:
        return None
    for category, markers in (rules.get("department_map") or {}).items():
        for marker in markers or []:
            normalized = _normalize(marker)
            if normalized and normalized in body:
                # 一级固定为「行业政策」，部门名只作为二级（行业领域）。
                #
                # DEPARTMENT_CATEGORY_L1 常量和它上面的注释早就写明了这条设计
                #（「部门只决定哪个行业领域，不决定一级分类；一级输出必须落在
                # categories.yaml 的枚举内」），但这个 return 一直直接写了
                # 部门名当一级——**常量定义了却从没被引用**。
                #
                # 后果实测（test_category_taxonomy 一直在报这个）：
                #   classify_by_department(发文机关=财政部) 返回
                #   {'category_l1': '财政金融', ...}
                # 而「财政金融」不在 categories.yaml 的枚举里，它本来应该是
                #   {'category_l1': '行业政策', 'category_l2': '财政金融'}
                # 于是「按行业领域查」和「按一级分类查」两套口径对不上。
                return {"category_l1": DEPARTMENT_CATEGORY_L1,
                        "category_l2": str(category),
                        "matched_department": str(marker)}
    return None


# 通用歧义词：同一个词在不同语境下属于完全不同的类别。
#
# 「遴选」是最典型的：公务员遴选、事业单位遴选是**人**的遴选（公招信息），
# 而项目遴选、基地遴选、中心遴选是**项目/单位**的遴选（招标信息/商业机会）。
# 2026-10-03 实测 4 个用例，3 个被公招信息垄断：
#   关于遴选2026年度省级工程技术研究中心的通知        -> 公招信息（错）
#   关于遴选2026年度省社科基金项目申报单位的通知       -> 公招信息（错）
#   关于遴选基层医疗卫生机构人才的公告               -> 公招信息（错）
#   关于公开遴选事业单位工作人员的公告               -> 公招信息（对）
#
# 靠往词表里加词解决不了——「遴选」该归哪边取决于**同行里出现的是人还是项目**。
# 所以在这里显式列出两侧的上下文信号，靠共现判定。
AMBIGUOUS_TERM = "遴选"

# 「遴选」的语境信号：出现在这些词附近，多半不是招人
_AMBIGUOUS_PROJECT_CTX = (
    "研究中心", "工程中心", "实验室", "基地", "平台", "示范", "试点",
    "项目", "课题", "申报单位", "承担单位", "承担", "入选单位", "资质",
    "产业链", "联合体", "协同", "示范点", "创建单位", "建设单位",
)
# 「遴选」的语境信号：出现在这些词附近，多半是招人
_AMBIGUOUS_PERSON_CTX = (
    "工作人员", "事业单位", "人员", "干部", "公务员", "考生", "职工",
    "岗位", "教师", "医护", "会计", "人才", "编制",
)


def _resolve_ambiguous_ambiguity(title: str) -> str | None:
    """「遴选」这类歧义词按上下文定归属，返回正确的一级分类名或None。

    判据是**共现**：同行里同时出现「项目/课题/基地」→ 是遴选项目；
    出现「工作人员/编制/岗位」→ 是遴选人。两侧都没有就不判，交给词表兜底
    （保持既有行为，只在能判的时候改判）。
    """
    if AMBIGUOUS_TERM not in title:
        return None
    if any(ctx in title for ctx in _AMBIGUOUS_PROJECT_CTX):
        return "招标信息"
    if any(ctx in title for ctx in _AMBIGUOUS_PERSON_CTX):
        return "公招信息"
    return None


def _normalize(value: Any) -> str:
    if isinstance(value, (list, tuple, set)):
        value = " ".join(str(item) for item in value)
    return re.sub(r"\s+", "", str(value or "")).casefold()


def load_category_rules(path: str | Path = DEFAULT_RULES_PATH) -> dict[str, Any]:
    """读规则文件。同一个路径在进程内只读一次。

    之前每次调用都重读 YAML，而 classify() 对每条记录都调它——实测 1984 条
    记录花了 56.7 秒，约 28.6 ms/条，绝大部分时间花在重复读盘和 re 解析上，
    真正打分只占零头。reclassify.py 那一轮就要跑两分钟。

    缓存以 (路径, mtime_ns) 为键：改了配置文件 mtime 会变，自动重载，
    不用担心改了规则却用旧的。长驻进程用 reload_category_rules() 主动清。
    """
    key = str(path)
    try:
        stamp = Path(path).stat().st_mtime_ns
    except OSError:
        stamp = 0
    cached = _RULES_CACHE.get(key)
    if cached is not None and cached[1] == stamp:
        return cached[0]
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict) or not isinstance(data.get("categories"), dict):
        raise ValueError("config/category_rules.yaml must contain a categories mapping")
    _RULES_CACHE[key] = (data, stamp)
    return data


def reload_category_rules() -> None:
    """清空规则缓存。改了配置又想在长驻进程里立刻生效时用。"""
    _RULES_CACHE.clear()


def industry_l2_names() -> list[str]:
    """category_l2 里属于「行业门类」的子类名（行业统计的唯一口径）。

    按 config/category_rules.yaml 的父子结构判定：父分类是 行业政策 /
    行业补贴 的才是行业，其余（公示/名单/办法/通知…）是文件形态，必须排除
    —— 否则「公示」会以行业身份混进每个省，图表变成「各省发了多少公示」。
    刻意不手写白名单：手写会漏掉只在 fallback_categories 里的
    「商务贸易」「工业制造」。
    """
    try:
        rules = load_category_rules()
    except (OSError, ValueError, yaml.YAMLError):
        return []
    out: list[str] = []
    for table in (rules.get("categories") or {}, rules.get("fallback_categories") or {}):
        for parent, spec in table.items():
            if isinstance(spec, dict) and parent in ("行业政策", "行业补贴"):
                out.extend(str(s) for s in (spec.get("subcategories") or {}))
    return sorted(set(out))


# path -> (已解析的规则, 文件 mtime_ns)
_RULES_CACHE: dict[str, tuple[dict[str, Any], int]] = {}


def body_text(content: Any, limit: int = NOISE_LIMIT) -> str:
    return _normalize(content)[:limit]


def record_text(record: Mapping[str, Any], fields: tuple[str, ...] = DEFAULT_FIELDS) -> str:
    return _normalize(" ".join(_normalize(record.get(field)) for field in fields))


def score_category(text: str, spec: Mapping[str, Any]) -> tuple[int, str]:
    """Return (weighted hits, best subcategory).

    Within a category, a subcategory holding more distinct hits is the more
    specific match, so it wins over one that merely matched first.
    """
    best_subcategory = ""
    best_hits = 0
    best_span = 0
    score = 0
    for subcategory, keywords in (spec.get("subcategories") or {}).items():
        hits = 0
        span = 0
        for keyword in keywords or []:
            normalized = _normalize(keyword)
            if normalized and normalized in text:
                hits += 1
                span = max(span, len(normalized))
        if not hits:
            continue
        score += hits
        # Specificity beats hit count: a title naming "中央机关及其直属机构"
        # is a national exam even if it also contains the bare word "公务员",
        # which would otherwise drag it into the provincial-exam bucket.
        if span > best_span or (span == best_span and hits > best_hits):
            best_hits, best_span, best_subcategory = hits, span, str(subcategory)
    return score, best_subcategory


# 强文种词：出现即定 category_l1，不再和主题词比大小。
# 审计 1465 条兜底记录时按词频统计得出，覆盖 78%：
#   通知 31% / 公示 13% / 公告 12% / 通告 7% / 名单 6% / 规划 5% / 办法 4%。
# 排在前面的是"形态最明确"的：一份文件只会是一种公示/名单/批复。
# 通知/公告故意不放这里——它们太泛，一份招标公告也是"公告"，
# 强判会把招标信息全推进"政策发布/通知"。这类交给主题计分。
STRONG_DOC_TYPES = (
    ("征求意见", ("征求意见", "公开征求", "意见稿")),
    ("批复", ("批复", "核准批复", "批复同意")),
    # ★ 「公示」必须排在「名单」前面（2026-10-03 有真实基准背书）。
    #
    # 真实基准上 L2 最大的单一错分是「公示 -> 名单」29 条，原因是
    # 「关于……名单的公示」同时命中两个词，而 STRONG_DOC_TYPES 是**按顺序**
    # 返回第一个命中的，所以「名单」赢了。
    #
    # 这个歧义是可以判定的，不需要回避：
    #   「关于XX名单的公示」= 一份**公示**，内容恰好是名单 —— 文种是公示。
    #   「XX名单」单独出现     = 一份**名单**。
    # 包含关系是单向的：公示里可以有名单，名单里不会有公示。
    # 文种（genre）比内容描述更可靠——这也是 STRONG_DOC_TYPES 存在的理由。
    #
    # 构建基准时我一度把「名单」整类排除（因为有歧义），这里修正为：
    # 歧义不在类别本身，而在**优先级顺序**。改顺序后两个都能收。
    ("公示", ("公示", "公示期", "公示名单")),
    ("名单", ("名单", "入选名单", "拟录用名单", "中选名单", "结果名单")),
    ("解读", ("解读", "政策问答", "答记者问", "图解")),
)


def _strong_doc_type(title: str, rules: Mapping[str, Any]) -> tuple[str, str] | None:
    """标题里出现强文种词就返回 (一级, 二级)，否则 None。"""
    for subcategory, keywords in STRONG_DOC_TYPES:
        if not any(_normalize(keyword) and _normalize(keyword) in title
                   for keyword in keywords):
            continue
        # 找子类名要**兼容两种形状**。
        #
        # 2026-10-03 实测（这次有真实语料证据，不是凭直觉）：建了 240 条真实
        # 标题的高置信基准（scripts/build_golden_set.py，只收文种互斥且抽读
        # 验证过的组合），当前分类器在这上面 **L1 只有 71.7%**，而合成基线是
        # >=95%。最大单一错分是「公示公告 -> 活动通知」17 条。
        #
        # 根因：「公示」这个子类只存在于 ``fallback_categories``，而这里只遍历
        # ``rules["categories"]``——于是函数按自己的保护逻辑「子类名不存在就不判」
        # 退回，**公示这条最强的文种信号根本没被认领**，标题只能靠主题词碰运气，
        # 落到「活动通知」「行业政策」「人才政策」这些主题类里。
        #
        # 为什么之前试过一次又撤回：那次我写成「同时查两层」，而 classify() 传的
        # 是**扁平**的 merged_rules（20 个分类名直接做键，压根没有
        # rules["categories"] 这个键），于是走到 fallback 分支、遍历顺序整个变了，
        # 把合成基线打到 41.2%。—— 不是「跨层」这个方向错，是**没适配调用方
        # 实际传进来的结构**。这正是原则 stage|one_fix_must_cover_all_paths
        # 的另一面：修之前得先确认「实际调用方传的是什么」。
        #
        # 现在两种形状都兼容：嵌套（有 categories / fallback_categories 键）按
        # 两层查，扁平（合并过的）直接遍历自身。先主分类后兜底，维持既有优先级。
        if "categories" in rules or "fallback_categories" in rules:
            groups = (rules.get("categories") or {},
                      rules.get("fallback_categories") or {})
        else:
            groups = (rules,)
        for group in groups:
            for category, spec in group.items():
                if not isinstance(spec, dict):
                    continue
                subs = spec.get("subcategories") or {}
                if subcategory in subs:
                    return str(category), subcategory
    return None


def _best_match(title: str, body: str, categories: Mapping[str, Any],
                title_weight: int, body_weight: int,
                specific_bonus: int) -> tuple[str, str, int]:
    """在给定的一组分类里找最高分。返回 (一级, 二级, 分数)。"""
    best_category = ""
    best_subcategory = ""
    best_score = 0
    for category, spec in categories.items():
        if not isinstance(spec, dict):
            continue
        title_score, title_sub = score_category(title, spec)
        body_score, body_sub = score_category(body, spec)
        score = title_score * title_weight + body_score * body_weight
        if title_score and category in SPECIFIC_CATEGORIES:
            score += specific_bonus
        if score > best_score:
            best_category = str(category)
            best_subcategory = title_sub or body_sub
            best_score = score
    return best_category, best_subcategory, best_score


def _news_or_political(title: str, rules: Mapping[str, Any]) -> tuple[str, str] | None:
    """时政通稿/新闻/活动体裁。命中就返回 (一级, 二级)。

    只看标题。判别力强且特征明确：领导人姓名 + 国事动作，或记者会/发布会/座谈会
    这类活动词。放在第 1 层之前，因为第 1 层的关键词是单字级（"国"、"空军"），
    会被地名、机构名、场所名误触发。
    """
    # 优先复用第 1 层已有的「新闻动态」，它在合成基线里就是时政/播报的归属。
    target = (rules.get("新闻动态") or {}).get("subcategories") or {}
    if not target:
        target = (rules.get("非公告体裁") or {}).get("subcategories") or {}
    if not target:
        return None
    has_leader = any(h in title for h in
                     ("习近平", "李强", "赵乐际", "王沪宁", "蔡奇", "丁薛祥", "李希"))
    has_action = any(h in title for h in
                     ("抵达", "国事访问", "会晤", "书面向", "会见", "接待",
                      "出席", "考察", "记者会", "记者见面会", "新闻发布会",
                      "座谈会", "部长对话", "视频会"))
    if has_leader and has_action and "时政要闻" in target:
        return "新闻动态", "时政要闻"
    # 第 1 层「新闻动态」只有 时政要闻/行业动态/数据播报/媒体报道 四个子类，
    # 没有「新闻通稿」。活动/会议类归「行业动态」——它的词表本来就含
    # 座谈会/在京举行/圆满落幕 这类。
    ordered = [name for name in ("时政要闻", "行业动态", "数据播报", "媒体报道")
               if name in target]
    ordered += [name for name in ("新闻通稿", "活动新闻", "数据播报", "时政要闻")
                if name in target and name not in ordered]
    # 活动/会议特征词由这里自己持有，不指望第 1 层词表覆盖。
    # 实测「国新办就…举行中外记者见面会」里命中的是"举行"，而第 1 层
    # 「新闻动态/行业动态」的词表是"新闻发布会""在京举行"——词形对不上，
    # 结果一个子类都没命中，整条掉到第 2 层的"非公告体裁/新闻通稿"。
    activity = any(h in title for h in
                   ("举行", "举办", "召开", "开幕", "启动", "在京", "出席",
                    "记者会", "记者见面会", "新闻发布会", "座谈会", "论坛",
                    "大会", "部长对话", "视频会", "调研", "考察"))
    if activity and not any(w in title for w in
                            ("的通知", "的公告", "的通告", "的批复", "的公示")):
        for preferred in ("行业动态", "新闻通稿", "活动新闻"):
            if preferred in target:
                return "新闻动态", preferred
    for subcategory in ordered:
        keywords = target.get(subcategory) or []
        hits = [_normalize(k) for k in keywords if _normalize(k) and _normalize(k) in title]
        if len(hits) >= 2 and not any(
                w in title for w in ("的通知", "的公告", "的通告", "的批复")):
            return "新闻动态", subcategory
    return None


def classify(record: Mapping[str, Any], config: Mapping[str, Any] | None = None) -> dict[str, str]:
    rules = config if isinstance(config, dict) and config.get("categories") else load_category_rules()
    defaults = rules.get("defaults") or {}
    title_weight, body_weight, specific_bonus = weights_from(rules)
    title = _normalize(record.get("title"))
    body = body_text(record.get("content"))

    # 文种（文件形态）先判，且优先于主题。
    #
    # 为什么必须分两层计分，而不是把文种词混进同一个池子比大小：
    # "生态环境部关于...拟作出的建设项目环境影响评价文件审批意见的公示" 里，
    # 「公示」只命中 1 个关键词，而「环境影响评价」「生态」在生态环保下命中 2 个，
    # 乘上 title_weight 后主题分更高——结果同源同类的三条公示被拆成
    # 「公示公告/公示」和「行业政策/监管政策」两个类，用户按公示查就漏了。
    #
    # 判据：文种词互斥且可靠（一份文件只有一个文种），主题词不互斥
    # （一条通知可以同时讲项目、科技、资金）。所以文种出现时直接定调。
    merged_rules = dict(rules["categories"])
    merged_rules.update(rules.get("fallback_categories") or {})

    # 时政/新闻/活动体裁先拦一道，再进第 1 层。
    #
    # 这不是新增判别力，是补一个第 1 层既有的漏洞：「军队招录/军队院校招生」的
    # 关键词里有「空军」，于是「习近平抵达美国安德鲁斯空军基地发表书面讲话」被判成
    # 军校招生；「国新办就…举行中外记者见面会」会被「国」字命中。
    # 这些是时政通稿，压根不是任何一种招录文件。
    #
    # 为什么不改原配置去掉「空军」：那 13 类是合成基线 95% 的依赖，动它要重新调参。
    #
    # 归到第 1 层的「新闻动态/时政要闻」而不是第 2 层的「非公告体裁」：第 1 层本来
    # 就有这个子类，tests/test_extractors.py 也明确期望时政落在这里。另建一个同名
    # 类会让同一批记录在两个地方各有一份，无法统一查询。
    # 歧义词按上下文定归属。先于新闻判定，因为「遴选…基地的通知」
    # 不会被新闻词命中，但它会被公招信息抢走。
    ambiguous = _resolve_ambiguous_ambiguity(title)
    if ambiguous:
        return {"category_l1": ambiguous, "category_l2": ""}

    news_hit = _news_or_political(title, merged_rules)
    if news_hit is not None:
        return {"category_l1": news_hit[0], "category_l2": news_hit[1]}

    doc_hit = _strong_doc_type(title, merged_rules)
    if doc_hit is not None:
        return {"category_l1": doc_hit[0], "category_l2": doc_hit[1]}

    # 第 1 层：原始 13 类。合成基线 95% 全部依赖这一层，行为必须保持不变。
    category, subcategory, score = _best_match(
        title, body, rules["categories"], title_weight, body_weight, specific_bonus)
    if score > 0:
        return {"category_l1": category, "category_l2": subcategory}

    # 第 2 层：政务文种。真实语料里 93% 原本落兜底，正好全部由这一层接管。
    # 分层而不是调权重，是因为两套数据的分布不同（详见 config/category_rules.yaml
    # 的 scoring.tuned_on）：合成标题多是"关于印发XX的通知"这种模板句，
    # 真实标题里"公示/名单/批复/产品召回"才是主信号。放进同一个池子会互相抢。
    fallback = rules.get("fallback_categories") or {}
    if fallback:
        category, subcategory, score = _best_match(
            title, body, fallback, title_weight, body_weight, specific_bonus)
        if score > 0:
            return {"category_l1": category, "category_l2": subcategory}

    # The issuing body is the stronger signal, but only when the keyword rules
    # found nothing: a title like "公务员招录公告" from 人社厅 is an exam notice,
    # not a department bucket.
    by_department = classify_by_department(record)
    if by_department:
        return by_department
    return {
        "category_l1": str(defaults.get("category_l1") or "其他"),
        "category_l2": str(defaults.get("category_l2") or "未分类"),
    }
