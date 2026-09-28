"""正文质量判据 + 字段拼装式摘要。

为什么这里不做「摘要算法」——实测依据
--------------------------------------
对全库 2492 条记录实测：

    摘要覆盖率            98%      不是「没有摘要」
    句中硬切              26.4%    在字符数处截断，不在句号停
    表格竖线倒灌           9.8%    整个表头行进了摘要
    正文原样开头           10.5%    等于没摘要
    公文结构句式覆盖       12.1%    「现将…通知如下」这类句式只占八分之一

病根是**选句**不是生成。id=2961 是《工作指引》+ 42 篇案例汇编共 71506 字，
摘要从第 40 篇里挑了「广外街道」「蓝梅采摘园 200 亩」，再把另一篇案例的
「268 万元」当成文件投资额拼在头部——**句子全部忠于原文，但拼起来答非所问**。

试过 TextRank 系（中心性）：在案例汇编里中心性最高的是每篇都重复的样板句
（「加强领导、健全机制」），不是「这份文件要做什么」的论断句。又试过 20 行
Lead-3 + 标题锚定，4 条样本里赢 2 平 1 输 1。**换算法不改变结论。**

所以这里把摘要重新定义为：**结构化字段 + 首段完整句的模板渲染**。
这样三条性质是构造上成立的，不依赖任何算法：

    1. 100% 在句号/分号处收尾   —— 不是字符数截断
    2. 100% 抄原文             —— 逐句复制，不生成
    3. 不含目录行/编号行/表格行 —— 构造时就被过滤

代价要认：它不是「读 200 字就懂这份 7 万字文件」。它是名片。
长文件靠 output/markdown.py 里的「关键要点」+ 完整原文承载。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

# 采集器抓回来时可能带 Markdown 链接/图片占位符（crawl4ai fit_markdown 的输出形态）
_MD_LINK = re.compile(r"!?\[[^\]]{0,60}\]\([^)]*\)")
# 面包屑：>_ [首页](url) > [政务公开](url) > ...
_CRUMB_LINE = re.compile(r"^[\s>_*]*\[[^\]]{1,20}\]\([^)]*\)[\s>_*]*$")
# 首行 "标题 | 列1 | 列2 |" 这类表头残留
_TABLE_ROW = re.compile(r"^[^|]{0,40}\|.*\|.*\|")
_WS = re.compile(r"[ \t　]+")


def load_config(path: str | None = None) -> dict[str, Any]:
    """读 YAML 配置。所有可调项都在那里，代码里不写死。"""
    import pathlib
    import yaml

    if path is None:
        path = str(pathlib.Path(__file__).resolve().parent / "config" / "web_collector_config.yaml")
    with open(path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


# ------------------------------------------------------------------ 质量判据

@dataclass
class Verdict:
    """一条正文通过 / 不通过质量门禁的判定结果。

    区分两件本来被混在一起的事——这是 bench 实测全库 2492 条后改的：

    * ``fatal`` —— 内容是垃圾或根本没内容。这才值得升级到更贵的渲染级
      （chrome / link_flood / toc_page / 短到连一个完整句都没有）。
    * ``fatal=False`` —— 内容是好的，只是短。**不要升级**：渲染要花 10-50 倍
      成本，而且不会让一篇 300 字的完整通告变长。直接接受并出摘要。
    """

    passed: bool
    reason: str = ""            # 不通过时的机器可读原因
    fatal: bool = False         # True=升级/丢弃；False=内容可用，只是短
    score: float = 0.0          # 0..1，仅用于报告和排序，不参与门禁
    signals: dict[str, Any] = field(default_factory=dict)


def _patterns(cfg: Mapping[str, Any], section: str, key: str) -> list[re.Pattern[str]]:
    raw = ((cfg.get("quality") or {}).get(key) or [])
    return [re.compile(p) for p in raw]


def judge(content: str, cfg: Mapping[str, Any]) -> Verdict:
    """判断一条正文能不能用。

    降级阶梯靠这个函数决定要不要升级到下一级渲染方式，所以判据必须是
    **正文质量**而不是「HTTP 返回 200」——政府栏目页返回 200 但正文是
    导航文字，这是最常见的假成功。
    """
    text = content or ""
    chars = len(text)
    limits = cfg.get("quality") or {}
    min_chars = int(limits.get("min_content_chars", 800))
    min_summary = int(limits.get("min_summary_chars", 200))
    max_chrome = float(limits.get("max_chrome_ratio", 0.03))
    max_link = float(limits.get("max_link_ratio", 0.25))
    max_toc = int(limits.get("max_toc_run", 8))

    chrome_res = _patterns(cfg, "chrome", "chrome_patterns")
    toc_res = _patterns(cfg, "chrome", "toc_patterns")

    # 站点框架文字**字符占比**。按比例不按次数：按次数（原来 max_chrome_hits: 0）
    # 会把 5833 字正文里偶然出现一个「网站地图」的正常文档也判死——实测全库时
    # 误杀了 61 条。真正要拦的是「满页都是导航」，那种占比在 20% 以上。
    chrome_chars = sum(len(m.group(0)) for r in chrome_res for m in r.finditer(text))
    chrome_hits = sum(1 for r in chrome_res if r.search(text))
    chrome_ratio = round(chrome_chars / chars, 4) if chars else 1.0

    # markdown 链接/图片占位符占掉的字符比例。
    # 这条专治「满屏面包屑」：data_health.py 的 CHROME_RE 看不见这种页面，
    # 但它从头到尾没有一个字是正文。
    link_chars = sum(len(m.group(0)) for m in _MD_LINK.finditer(text))
    link_ratio = round(link_chars / chars, 4) if chars else 1.0

    # 目录行连续长度
    toc_run = _longest_run(text.splitlines(), toc_res)

    signals = {
        "chars": chars,
        "chrome_hits": chrome_hits,
        "chrome_ratio": chrome_ratio,
        "link_ratio": link_ratio,
        "toc_run": toc_run,
    }

    # 垃圾内容：升级渲染有意义（DOM 里可能真有正文），全级失败就丢
    if chrome_ratio > max_chrome:
        return Verdict(False, "chrome", True, 0.0, signals)
    if link_ratio > max_link:
        return Verdict(False, "link_flood", True, 0.0, signals)
    if toc_run >= max_toc:
        return Verdict(False, "toc_page", True, 0.0, signals)

    # 短到连一个完整句都取不到：渲染也救不了，丢弃
    if chars < min_summary:
        return Verdict(False, "too_short", True, 0.0, signals)

    # 短但干净：内容可用，只是不到「值得花 50 倍成本升级渲染」的门槛。
    # passed=True / fatal=False —— 路由阶梯据此直接接受，不升级。
    if chars < min_chars:
        signals["below_escalate_bar"] = True
        return Verdict(True, "short_but_clean", False, 0.4, signals)

    # 通过。分数只用于报告里排序，不参与门禁
    score = min(1.0, round(min(chars / 2000.0, 1.0) * (1.0 - link_ratio), 4))
    return Verdict(True, "", False, score, signals)


def _longest_run(lines: list[str], res: list[re.Pattern[str]]) -> int:
    best = run = 0
    for line in lines:
        stripped = line.strip()
        if stripped and any(r.search(stripped) for r in res):
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


# ------------------------------------------------------------------ 正文清洗

def strip_noise(text: str) -> str:
    """去掉采集器带回来的框架残留：markdown 链接、图片占位符、面包屑行。"""
    lines: list[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _CRUMB_LINE.match(stripped):
            continue
        if _TABLE_ROW.match(stripped) and stripped.count("|") >= 2:
            continue
        cleaned = _MD_LINK.sub(" ", stripped)
        cleaned = _WS.sub(" ", cleaned).strip()
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def split_sentences(text: str, terminators: str = "。！？；") -> list[str]:
    """按终止标点切句，**保留终止符**。

    这是「绝不在字符数处硬切」的基础：拼摘要时整句取用，
    摘要的最后一个字符必然是标点。

    公文还有一种断句方式：冒号引出编号小节，
    「现将有关事项通知如下：一、支持方向。二、申报条件。」
    只按句号切的话这一整段会变成一个 300+ 字的「超长句」而被 max_sentence_chars
    丢掉——实测全库有 140 条卡在这里。所以冒号后紧跟编号标记时也在那里断。
    """
    out: list[str] = []
    buf = ""
    for index, ch in enumerate(text or ""):
        buf += ch
        if ch in terminators or (ch == "：" and _STARTS_SECTION(buf, (text or "")[index + 1:])):
            sentence = buf.strip()
            if sentence:
                out.append(sentence)
            buf = ""
    tail = buf.strip()
    if tail:
        out.append(tail)          # 末尾没有标点的残句也返回，由调用方决定要不要用
    return out


# 冒号后面紧跟这些就算「引出编号小节」，在那里断句
_SECTION_MARK = re.compile(r"^\s*(?:[一二三四五六七八九十]{1,3}[、．.]|"
                           r"[（(][一二三四五六七八九十]{1,3}[)）]|"
                           r"\d{1,2}[、．.]|第[一二三四五六七八九十]{1,3}[条章节部分])")


def _STARTS_SECTION(buffer: str, rest: str) -> bool:
    """buffer 末尾是冒号，且**紧跟其后**的是编号小节标记。

    必须看 rest（冒号后面的原文）而不是 buffer 内部：逐字符扫到冒号时，
    「一、」还没读进来，只看 buffer 尾部永远匹配不上。
    """
    head, sep, _tail = buffer.rpartition("：")
    if not sep or len(head) < 8:
        # 太短的片段不按冒号断，否则「时间：2026年」这种会被切碎
        return False
    return bool(_SECTION_MARK.match(rest))


# ------------------------------------------------------------------ 字段抽取

def extract_fields(content: str, cfg: Mapping[str, Any]) -> dict[str, Any]:
    """从正文里抽四个字段：发布机构、日期、金额、截止。

    金额宁可置空也不猜——实测旧摘要把「第 40 篇案例的 268 万元」当成整个
    文件的投资额，这种错误比缺字段有害得多。

    发布机构的正则只作**兜底**。权威值来自 ``config/sources.yaml`` 的
    ``name``（那是逐源人工登记过的），由调用方通过 extra_facts 传进来。
    正则抽机构名不可靠：``[一-龥]{2,20}?(?:厅|局|委|部)`` 在
    「为了便于公民、法人及时、准确获取四川省商务厅的政府信息」这种句子里
    会抽出「准确获取四川省商务厅」——把动词和状语一起吃进来了。
    """
    text = content or ""
    junk_res = [re.compile(p) for p in
                ((cfg.get("summary") or {}).get("amount_junk_patterns") or [])]

    out: dict[str, Any] = {}

    # 发布机构兜底正则。锚到句首或标点后，避免从句子中间起匹配把动词吃进来。
    org = re.search(r"(?:^|[。；，、（(])([一-龥]{2,10}?(?:人民政府|厅|局|委|部|办公室|中心|集团|署))"
                    r"(?=发布|印发|关于|按照|根据|将|$|[，,])", text)
    if org:
        out["issuing_body"] = org.group(1)

    # 发布日期
    date = re.search(r"(20\d{2})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})", text)
    if date:
        out["publish_date"] = f"{date.group(1)}-{int(date.group(2)):02d}-{int(date.group(3)):02d}"

    # 金额：必须带货币单位或「万元/亿元」这类量词，纯数字不算
    amount = re.search(r"(\d[\d,，.]*\s*(?:亿元|万元|万美元|美元|元))", text)
    if amount:
        candidate = amount.group(1).strip()
        bare = re.sub(r"[^\d.]", "", candidate)
        if bare and not any(r.search(bare) for r in junk_res):
            out["amount"] = candidate

    # 截止时间
    deadline = re.search(r"((?:截止|截至|于)\s*20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)", text)
    if deadline:
        out["deadline"] = deadline.group(1)

    return out


# ------------------------------------------------------------------ 摘要

def _prefix(title: str, facts: Mapping[str, Any]) -> str:
    """摘要前缀：〔机构〕〔日期〕发布〔标题〕。都是原文字段，不生成。"""
    title_text = _clean(title)
    if not title_text:
        return ""
    bits: list[str] = []
    org = str(facts.get("issuing_body") or "").strip()
    # 标题里已经带了机构名就别再前缀一遍。实测出现过
    # 「四川省商务厅2025-07-30发布行政规范性文件 - 四川省商务厅。」这种
    # 同一个机构名出现两次的摘要。
    if org and org not in title_text:
        bits.append(org)
    if facts.get("publish_date"):
        bits.append(str(facts["publish_date"]))
    lead = "".join(bits)
    return f"{lead}发布{title_text}。" if lead else f"发布{title_text}。"


def summarize_table(content: str, cfg: Mapping[str, Any],
                    max_pairs: int = 6) -> str:
    """从管道分隔的表格里取「键：值。」摘要。

    政务公告里有一大类根本没有散文——整页是表格：招投标中标结果、环评受理
    清单、新化学物质登记、财政预算表。实测全库 165 条，占「出不了摘要」的大头。
    它们恰恰是「重大项目」最相关的内容，丢掉代价太大。

    取第一行做表头、第二行做数据，按列配对。列名太泛的（序号/备注/合计）
    和值太长的（长句）跳过——摘要要的是能扫读的关键字段，不是把表格抄一遍。

    实测真实形态（id=86，生态环境部环评公示）：换行符 0 个、竖线 48 个，
    切出 49 个单元格：
        ['', '序号', '项目名称', ..., '承诺文件', '', '1', '某输电工程', ...]
    采集器把整张表压成**一行**，行与行之间靠**空单元格**分隔。所以不能按行解析，
    得按单元格流解析：第一段非空 = 表头，第二段非空 = 第一行数据，按列配对。
    """
    cells = [cell.strip() for cell in (content or "").replace("\n", "|").split("|")]
    header: list[str] = []
    data: list[str] = []
    in_data = False
    for cell in cells:
        if not cell:
            # 空单元格 = 换行。表头后的第一个空表示「下面是数据行」，
            # 数据行之后的空表示「这张表到头了，收工」。
            if header and not in_data:
                in_data = True
            elif data:
                break
            continue
        (data if in_data else header).append(cell)
    if not header or not data:
        return ""

    skip = {"序号", "备注", "合计", "小计", "无", "/", "-", "来源", "链接"}
    parts: list[str] = []
    for name, value in zip(header, data):
        name, value = name.strip(), value.strip()
        if not name or not value or name in skip or len(name) > 12:
            continue
        if len(value) > 60 or value in ("无", "/", "-", "—"):
            continue
        parts.append(f"{name}：{value}。")
        if len(parts) >= max_pairs:
            break
    return "".join(parts)


def build_summary(title: str, content: str, cfg: Mapping[str, Any],
                  extra_facts: Mapping[str, Any] | None = None) -> tuple[str, str]:
    """生成摘要。返回 (摘要, 空摘要时的原因)。

    摘要 = 〔结构化字段前缀〕 + 〔首段前几个完整句〕

    三条硬约束由构造保证，不靠事后校验：
      1. 最后一个字符必然是终止标点（除非一个句子都取不到）
      2. 每个字都抄自原文，没有生成
      3. 目录行、编号行、表格行在候选阶段就被剔除了
    """
    limits = cfg.get("summary") or {}
    max_chars = int(limits.get("max_chars", 300))
    window = int(limits.get("lead_window_sentences", 8))
    min_sent = int(limits.get("min_sentence_chars", 15))
    max_sent = int(limits.get("max_sentence_chars", 230))
    terminators = str(limits.get("terminators", "。！？；"))
    use_prefix = bool(limits.get("prefix_facts", True))
    toc_res = _patterns(cfg, "chrome", "toc_patterns")

    body = strip_noise(content or "")
    sentences = split_sentences(body, terminators)
    title_key = "".join(_clean(title).split())

    facts = dict(extract_fields(content or "", cfg))
    facts.update({k: v for k, v in (extra_facts or {}).items() if v not in (None, "")})
    prefix = _prefix(title, facts) if use_prefix else ""

    # 只在首段窗口里找，且必须以终止符收尾 —— 宁可少取，不切半个字
    candidates: list[str] = []
    for sentence in sentences[:window]:
        stripped = sentence.strip()
        if not (min_sent <= len(stripped) <= max_sent):
            continue
        if stripped[-1] not in terminators:
            continue
        if any(r.search(stripped) for r in toc_res):
            continue
        # 正文第一行通常就是标题本身。摘要前缀已经带了一次标题，再取一遍
        # 就变成「发布XX。XX\n正文…」——同一句话连着出现两遍。
        # 这条影响每一条记录，不是个别 case。
        if title_key and _same_text(stripped, title_key):
            continue
        candidates.append(stripped)

    if not candidates:
        # 整页都是表格的公告（招投标、环评受理清单、标准物质登记……）没有一句散文，
        # 但它们恰恰是「重大项目」最相关的内容。从表头取键值对来摘要：
        #   序号|项目名称|建设单位|受理日期  +  1|某项目|某公司|2026-9-23
        #   -> 「项目名称：某项目。建设单位：某公司。受理日期：2026-9-23。」
        # strip_noise 会把表格行整行删掉，所以这里用原始 content。
        table = summarize_table(content or "", cfg)
        if table:
            return (prefix + table).strip()[:max_chars + 120], ""

        # 连表格都取不出值 = 正文里没有可摘要的内容（典型是纯导航索引页：
        # 全是「首页/打印/收藏」这种无终止标点的短条目）。
        # 这种情况下**不要**返回只剩标题的前缀——一条 18 字的「发布XXX。」比
        # 没有摘要更糟，它看起来像有摘要，实际零信息。宁可标 skipped。
        return "", "no_usable_sentence"

    # 逐句累加，超预算就停 —— 保证结尾落在句号上
    picked: list[str] = []
    budget = max_chars - len(prefix)
    for sentence in candidates:
        if len(sentence) > budget:
            break
        picked.append(sentence)
        budget -= len(sentence)
        if budget <= 30:
            break
    if not picked:
        picked = [candidates[0][:max_chars]]

    return (prefix + "".join(picked)).strip(), ""


def _same_text(sentence: str, title_key: str, tolerance: float = 0.85) -> bool:
    """判断一个句子是不是标题的复述。

    政务页面的正文第一行几乎总是标题的复制（有时带「- 站名」后缀、有时带
    多余空格或换行）。摘要把标题前缀和这句一起留下，就变成同一句话出现两遍。
    用字符集合的重合度判断，避免因为标点/空格差异就漏判。
    """
    key = "".join(sentence.split())
    if not key or not title_key:
        return False
    short, long_ = (key, title_key) if len(key) <= len(title_key) else (title_key, key)
    if not short:
        return False
    return len(set(short) & set(long_)) / len(set(short)) >= tolerance


def _clean(value: Any) -> str:
    return _WS.sub(" ", str(value or "")).strip()
