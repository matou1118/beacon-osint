"""把 2026-09-30 手工修复的问题沉淀成**可执行原则**。

## 为什么需要这个脚本

今天我手工修了 6 类问题（404 死链烧预算、link_flood 误杀、robots 终态、
prune 自毁退避闸、watchdog 自杀、正文丢换行）。每一条都是「系统下次应该
自己发现并处理」的，但修复只存在于代码里，**没有一条进过原则表**。

所以闭环是断的：问题被人工修掉 → 原则表里没有经验 → 同样的问题下次再犯 →
再人工修一遍。`principles.apply_principles()` 甚至没有调用方。

这个脚本把每条修复写成一条原则（scope_type + failure_type + best_action），
之后 `principle_applier.apply()` 遇到同类问题就会自动执行对应动作。

**注意**：best_action 必须映射到 `principle_applier.ACTIONS` 里的真实动作。
之前表里全是 `auto_observe`（=什么都不做），utility 永远是 0，系统学不到东西。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# ── 今天修的问题 → 原则 ───────────────────────────────────────────────
# 每条：key 的 scope_type|failure_type、best_action、以及为什么这么定。
#
# best_action 的选法（关键）：必须是 principle_applier.ACTIONS 里的真实动作，
# 且必须是「对这个 failure_type 真正有效」的那个。选 observe_only 等于没修。
PRINCIPLES = [
    {
        "key": "stage|utc_columns_need_utc_thresholds",
        "scope_type": "stage",
        "failure_type": "utc_columns_need_utc_thresholds",
        "best_action": "discover_fix",
        "why": "created_at / fetched_at 存的是 **UTC**，所以任何拿它们做区间比较的"
               "阈值也必须是 UTC；本地时间只允许出现在给人看的输出里。"
               "2026-10-05 实测踩了两次，同一根因："
               "overnight_health.py 用 datetime.now()-WINDOW（本地）去比 created_at（UTC），"
               "本机 UTC+8，于是「最近 14 小时」实际只覆盖 6 小时——"
               "6~14 小时前那批产出整段漏掉，体检脚本报出来的数字偏小；"
               "collection_report.py 按 substr(created_at,1,13) 分桶那是 UTC 小时，"
               "却当本地小时展示，本地 20:00 采的 422 条被标成 12:00。"
               "这类 bug 不报错、只让数字偏小或偏早，看起来像「采集系统不行」——"
               "事实上我据此误判过一次「入库延迟 7 小时」。诊断特征："
               "数字随 UTC 偏移量成比例偏差，且没有任何报错。"
               "另外 SQLite 的 datetime('now') 返回 UTC，与之比较天然正确；"
               "只有 Python 侧 datetime.now() 才是地雷。",
    },
    {
        "key": "runtime|unattended_needs_writable_stdio",
        "scope_type": "runtime",
        "failure_type": "unattended_needs_writable_stdio",
        "best_action": "discover_fix",
        "why": "pythonw.exe 被计划任务拉起时 Windows 不分配控制台，sys.stdout 就是 "
               "None，于是 log() 里第一个 print() 直接抛 AttributeError，"
               "进程在写第一行日志之前就死。2026-10-05 实测："
               "LastTaskResult=2、State=Ready、日志零新增，看着像「正常跑完没活干」；"
               "而同一时刻手动那条 python.exe 链完全正常，所以「采集一直没断」是假象，"
               "计划任务其实从没起来过，机器一重启就断链。"
               "对照组：python.exe 存活 45 秒+；pythonw 加 -RedirectStandardError 存活；"
               "pythonw 无重定向启动即退。凡是无人值守入口，第一件事是保证 stdio 可写"
               "（winflags.ensure_stdio），不能只在计划任务里套 cmd 重定向。",
    },
    {
        "key": "runtime|scheduled_task_health_needs_log_delta",
        "scope_type": "runtime",
        "failure_type": "scheduled_task_health_needs_log_delta",
        "best_action": "discover_fix",
        "why": "判断调度链是否真的在跑，进程存活和 State=Running 都不够——"
               "被 kill 后重拉、或日志写到别处，都会显示「在跑」而实际零产出。"
               "2026-10-05 实测的判据组合：Get-ScheduledTaskInfo 的 LastTaskResult"
               "（0x41301=正在运行，0=已退出，2=启动即退）+ 日志文件末尾时间戳是否有增量。"
               "LastTaskResult=2 且日志零新增 = 启动即死，不是「没事干」。",
    },
    {
        "key": "stage|route_order_is_not_exclusion",
        "scope_type": "stage",
        "failure_type": "route_order_is_not_exclusion",
        "best_action": "discover_fix",
        "why": "采集等级里的 routes 表达的是**尝试顺序**（「静态拿不到，优先走渲染」），"
               "不是**允许集合**。2026-10-05 实测踩过：把它实现成过滤（只保留 routes 里的层），"
               "ndrc_gov 立刻变成丢弃原因 {'render:too_short': 4}、0 条入库，"
               "因为它只剩渲染一条路而渲染恰好拿不到正文——26 个 T2 源会被静默废掉。"
               "正确做法只做「提到最前面」，其余层保留兜底；"
               "等级是推断、配置是事实，宁可多试两层也不能让源因推断错误而采不到数据。",
    },
    {
        "key": "stage|cumulative_health_signal_must_exclude_marked",
        "scope_type": "stage",
        "failure_type": "cumulative_health_signal_must_exclude_marked",
        "best_action": "discover_fix",
        "why": "重复正文数、404 页数这类**累加型**健康信号，必须只统计干净记录。"
               "2026-10-05 实测：needs_review 只加标记、不减计数，于是「把源修好了」"
               "永远不会反映到等级上——sc_nx_gov 有 401 条正常记录，只因历史上采到过 "
               "1 条 404 页面就被判 T5 不可采，从此降级到 30 天探活一次；"
               "11 个 T3/T5 里 5 个是这类假警报。反向也要单独统计："
               "「干净 0 条且脏记录 10+」是「只产垃圾」的有效信号，"
               "不能和「从没产出过」混为一谈，后者不该判形态可疑。",
    },
    {
        "key": "stage|test_import_side_effects_pollute_suite",
        "scope_type": "stage",
        "failure_type": "test_import_side_effects_pollute_suite",
        "best_action": "discover_fix",
        "why": "测试文件在模块作用域 import 重型依赖会污染整个 pytest 进程。"
               "2026-10-05 实测：tests 顶层 import home_dashboard → 拉进 streamlit → "
               "重配 logging/stdout → 后面 4 个用 subprocess 跑真实脚本再拿中文断言的测试"
               "（pipeline_lock / proxy_guard / view_smoke / principle_loop，共 6 个用例）"
               "全部拿到乱码而失败，而单跑任何一个都是绿的。"
               "诊断特征：单跑绿、全量红，且失败断言是「找不到中文字符串」。"
               "修法是把重导入放进函数体（延迟到真正需要时）。",
    },
    {
        "key": "source_id|dead_urls",
        "scope_type": "source_id",
        "failure_type": "dead_urls",
        "best_action": "widen_stale",
        "why": "列表页翻页后拿到过期/错域名链接，14+ 条 404 逐条走三级升级，"
               "烧光 300 秒预算产出 0 条（mct_gov）。动作是放宽早停，"
               "让死链不阻塞后面的真内容。",
    },
    {
        "key": "source_id|link_flood",
        "scope_type": "source_id",
        "failure_type": "link_flood",
        "best_action": "deepen_pages",
        "why": "满屏链接有两种：真垃圾页（零正文）和正文短的真公告"
               "（江苏站群 20 条，crawl4ai 把导航也写成 markdown 链接）。"
               "漏网的正���要靠深翻才能拿到更多同类样本来判断。",
    },
    {
        "key": "source_id|drained_sources",
        "scope_type": "source_id",
        "failure_type": "drained_sources",
        "best_action": "widen_stale",
        "why": "采干是「当前回访周期内没有新公告」，不是「翻得不够深」——"
               "实测 mee_gov 翻到 30 页已有 2022~2026 四年 37 条，"
               "mem_gov 深度 30 拿到 489 条/2020-2026（收益封顶点），"
               "再加深度也不出内容。正确动作是放宽已知 URL 早停，"
               "让长列表被完整翻完而不是扫到 N 条已知就停。",
    },
    {
        "key": "source_id|deep_backfill_needed",
        "scope_type": "source_id",
        "failure_type": "deep_backfill_needed",
        "best_action": "deepen_pages",
        "why": "真正的「历史没采完」才该加深翻页。判据是库内最老记录仍比 5 年早"
               "（用户要求部委/省市采 5 年以上）。实测 mem_gov 深度 4→30："
               "80→489 条，年份 2026→2020-2026，196 秒/源。",
    },
    {
        "key": "stage|skipped_marked_visited",
        "scope_type": "stage",
        "failure_type": "skipped_marked_visited",
        "best_action": "loosen_backoff",
        "why": "「被时限/预算砍掉」不等于「访问过」。2026-10-02 实测：145 源跑"
               "30 分钟预算，只有 36 个真抓，剩下 97 个被 collect_source 以"
               "skipped=True 退回，但回灌退避的守卫只挡了崩溃、没挡跳过，"
               "于是 record(produced=False) 把它们 last_epoch 写成「刚刚」，"
               "全部推进 8 小时退避——下一轮直接报「145 个源全部在回访间隔」，"
               "而它们一条都没真抓过。信号形态：退避表条目数 == 配置源数，"
               "且各条 visits/dry/zero 形状完全一致（说明是同一次批量写入，"
               "不是各源独立演化）。动作是松开这些源的退避并清掉误标，"
               "让它们本轮就能被采。代码侧已修 run.py 的守卫并加了回归测试。",
    },
    {
        "key": "stage|archive_depth_uniform",
        "scope_type": "stage",
        "failure_type": "archive_depth_uniform",
        "best_action": "widen_stale",
        "why": "翻页深度被 raise_*_below 无条件下抬，导致按产出分档失效。"
               "2026-10-02 实测：想分 C 档（库内>=100 条的高产源）深翻 12 页、"
               "B 档其余 3 页保持快节奏，但 raise_archive_pages_below=30 把"
               "**每一档**都算成 30 页，B 档的 3 形同虚设——而每源 30 页正是"
               "「145 源 / 30 分钟预算只跑完 36 个」的慢源根因。"
               "信号形态：with_defaults 算出的 archive_pages 对所有源都相同，"
               "且等于 raise_* 地板值。动作是松开深度地板、按源显式值走。",
    },
# ── 2026-10-02：质量门禁与「测试基准」两个坑 ────────────────────────
    #
    # 这一批的共同点：**我拿一个自己没验证过的集合当「正确基线」**。
    # 145 源采回 1112 条，其中 126 条是纯导航（标题=栏目名、正文=登录注册
    # 无障碍阅读）。我拿剩下 986 条当「正常记录」当基准，调阈值调了三轮：
    # 误杀率 23.2% → 10.4% → 13.7%，全部超标，全部撤回了。
    #
    # 为什么调不出来：那 986 条**本身也混着栏目页**（"政府信息公开制度_
    # 重庆市商务委员会"就是目录页，只是恰好抽到日期所以进了库）。用
    # 脏数据当干净基准，阈值永远在两个错误之间来回摆。
    {
        "key": "stage|baseline_is_dirty",
        "scope_type": "stage",
        "failure_type": "baseline_is_dirty",
        "best_action": "discover_fix",
        "why": "拿未人工确认的集合当「正确基线」去调阈值，必然调不出来。"
               "2026-10-02 实测：145 源采回 1112 条，126 条是纯导航垃圾；"
               "剩下 986 条被当正常基线，据此给 listing_page 加句读密度判据，"
               "三轮误杀 23.2%/10.4%/13.7%，全部超标撤回。根因是那 986 条"
               "**自己也不干净**（目录页恰好带日期就混过了 publish_date 门）。"
               "判据：任何调阈值的动作，必须先有**人工确认过的小样本**"
               "（几十条足够）当正例/反例，样本本身要有出处记录。"
               "在拿到干净样本前，正确动作是**不动阈值、只打 needs_review**"
               "（可逆），而不是猜一个数字改生产。",
    },
    {
        "key": "source_id|navigation_boilerplate",
        "scope_type": "source_id",
        "failure_type": "navigation_boilerplate",
        "best_action": "discover_fix",
        "why": "「三道质量门全过」不代表是正文。2026-10-02 实测 126 条"
               "纯导航入库：标题是栏目名（『处罚/强制 - 湖北省人民政府门户"
               "网站』），正文是『本站PC版在1920*768以上…EN FR 无障碍阅读 登录 "
               "注册』。逐条看门禁为什么都放过——日期列表判据要 ≥12 个目录式"
               "日期，它一个没有；chrome_ratio 门限 3% 但 chrome_patterns 只"
               "命中 2 个词共 6 字（占比 1.1%），因为导航词永远列不全"
               "（这页还有 EN/FR/注册/登录/分辨率/移动端都不在表里）；"
               "min_content_chars 800 而它有 623…实际过了 800。"
               "**信号分离度实测很高**：句末标点密度中位 0.45（垃圾）"
               "vs 9.77（正文），差 21 倍零重叠；短行占比 0.77 vs 0.30。"
               "所以信号是够的，缺的是干净基准（见 baseline_is_dirty）。"
               "动作不是调阈值，是先要人工样本。",
    },
    {
        "key": "source_id|budget_exhausted",
        "scope_type": "source_id",
        "failure_type": "budget_exhausted",
        "best_action": "raise_budget",
        "why": "深度 30 抓列表就要 ~196 秒（JS 分页必须走渲染），加上详情抓取，"
               "300 秒预算会在列表阶段就把源砍掉。实测 6 个源因此零产出。",
    },
# ── 2026-10-03：抽查方法本身（比结论更重要） ───────────────────
    #
    # 这一条记的不是「哪条记录是垃圾」，而是**怎么抽查才不会被自己的假设骗**。
    # 一天前我在同一件事上栽了：拿「有 publish_date 的记录」当正常基准，
    # 拿「没有的」当垃圾，调三轮阈值全错。根因是那批"正常"里自己就混着
    # 目录页（目录页恰好抽到日期就混过了），基准是脏的。
    {
        "key": "stage|sample_before_threshold",
        "scope_type": "stage",
        "failure_type": "sample_before_threshold",
        "best_action": "discover_fix",
        "why": "改任何质量阈值之前，先抽 20~30 条**人眼读过**的样本，"
               "分成「垃圾 / 真内容」两堆，数清比例；样本本身要连来源和判定"
               "理由一起记下来。**没有这个步骤就不要动阈值。**"
               "2026-10-03 反例：那天用统计量代替抽样（句读密度、短行占比、"
               "chrome 占比），三轮误杀 23.2%/10.4%/13.7%——因为"
               "「正常基准」是自己没验证过的集合。2026-10-03 正例（同一批数据，"
               "换了方法）：先抽 14 条看标题和正文首段，立刻分出「真实公告、"
               "有机构名、有跨年份」；再对 150 条缺日期的逐条看内容，"
               "发现 149 条是转载新闻稿、正文写「新华社北京9月18日电」这种"
               "**没有年份的相对日期**，抽取器不敢猜而已——不是垃圾。"
               "结论：垃圾 1 条 / 150（0.7%），而如果按「缺日期=垃圾」的"
               "假设去清，就会误删 149 条真数据。"
               "**缺日期只是线索，不是判据。**",
    },
    {
        "key": "stage|date_missing_is_not_garbage",
        "scope_type": "stage",
        "failure_type": "date_missing_is_not_garbage",
        "best_action": "discover_fix",
        "why": "「缺 publish_date」绝不能当垃圾判据，只能当**抽样线索**。"
               "2026-10-03 实测：某轮入库 1721 条里150 条缺日期，看起来像"
               "10% 的脏数据，逐条看下来 149 条是完整正文（陕西省外商投资"
               "平台转载的商务/财政新闻），只是正文用「新华社北京9月18日电」"
               "这种无年份的相对日期——按日期抽不全才留空。"
               "同日还有 1 条**真垃圾**（某省商务厅「下载中心」页，"
               "markdown 表格有「发布日期」表头但整页一个日期都没有），"
               "特征完全不同：表头空表格 + 无任何日期 + 标题就是机构名。"
               "所以判别顺序是：先看标题像不像公告 → 再看正文有没有实质内容 →"
               "最后才看日期；**日期只排最后**。真正该盯的是"
               "「表头空表格」这类结构性指纹，它比缺日期干净得多。",
    },
    {
        "key": "stage|relative_date_could_be_resolved",
        "scope_type": "stage",
        "failure_type": "relative_date_could_be_resolved",
        "best_action": "widen_stale",
        "why": "转载新闻稿的日期写「新华社北京9月18日电」，**没有年份**，"
               "因为转载发生在采集当天，年份可以由抓取时间推出（年份相同）或"
               "由正文里其他完整日期补齐。2026-10-03 实测这一类 149 条——"
               "是当日新增里最大的一块可恢复信息，丢掉它们等于让这批记录"
               "永远无法按年份筛选（而用户的核心诉求正是 5 年以上历史）。"
               "动作方向：放宽早停/加深抽取，把「抓取年份 + 无年份月日」"
               "作为一种可接受的推断，并**在字段里标明是推断值**，"
               "不要和页面上明确写出的日期混为一谈。",
    },

# ── 2026-10-03 抽查：方法与发现 ────────────────────────────────
    #
    # 这次抽查的**方法**比结论重要。同一批数据（1721 条），我用统计量
    # 调阈值和用「抽样+人眼读+跨记录比对」得出的结论完全不同。
    {
        "key": "stage|sample_before_threshold",
        "scope_type": "stage",
        "failure_type": "sample_before_threshold",
        "best_action": "discover_fix",
        "why": "改任何质量阈值前，先抽 20~30 条**人眼读过**的样本，分成"
               "「垃圾/真内容」两堆数清比例，样本连来源和判定理由一起记下来。"
               "**没做这步就不要动阈值。** "
               "2026-10-03 反例：用句读密度/短行占比/chrome 占比当判据，"
               "三轮误杀 23.2%/10.4%/13.7%——因为「正常基准」是自己没验证过的"
               "集合，那批「有日期的正常记录」里自己就混着目录页。"
               "可执行工具：scripts/sample_audit.py（抽样 + 指纹统计 + "
               "--record 落盘成基准），见 sample_verdicts.json。",
    },
    {
        "key": "stage|date_missing_is_not_garbage",
        "scope_type": "stage",
        "failure_type": "date_missing_is_not_garbage",
        "best_action": "discover_fix",
        "why": "「缺publish_date」绝不能当垃圾判据，只能当**抽样线索**。"
               "2026-10-03 实测：某轮 1721 条里 150 条缺日期，看着像 10% 脏数据，"
               "逐条读内容发现 149 条是完整正文的转载新闻稿（陕西省外商投资平台"
               "转载商务/财政新闻），只是正文写「新华社北京9月18日电」这种"
               "**没有年份的相对日期**，抽取器不敢猜而已；真垃圾只有 1 条"
               "（某省商务厅「下载中心」页，markdown 表格声明了「发布日期」"
               "表头但整页一个日期都没有）。"
               "按「缺日期=垃圾」去清，会误删 149 条真数据。"
               "判别顺序：先看标题像不像公告 → 再看正文有没有实质内容 →"
               "**日期排最后**。",
    },
    {
        "key": "stage|duplicate_body_across_records",
        "scope_type": "stage",
        "failure_type": "duplicate_body_across_records",
        "best_action": "discover_fix",
        "why": "★ 判垃圾最可靠的信号不是「这条像不像垃圾」，而是"
               "**同一个源下多条文案的正文一模一样**。这是跨记录的关系信号，"
               "不受单条长度/比例失真的影响，而所有内容统计量都会被短正文带偏。"
               "2026-10-03 实测：1721 条里 379 条（22%）正文是重复模板，"
               "最大一处 350 条同源（sd_rst）正文全是同一段"
               "「索引 服务 日一二三四五六最新发布…」，而**标题全部是真实"
               "公告标题、日期也正常**——所以任何单条判据（缺日期/句读密度/"
               "标题形态）都抓不到它。根因是详情页抓取失败后退回了列表页"
               "导航：链接找对了、正文取错了，属于「抓取成功但取错内容」，"
               "在逐源结果里显示为 ok，完全看不出来。",
    },
    {
        "key": "source_id|detail_body_is_list_nav",
        "scope_type": "source_id",
        "failure_type": "detail_body_is_list_nav",
        "best_action": "discover_fix",
        "why": "「标题对、日期对、正文是列表页导航」= 详情页抓取失败并退回"
               "列表页。2026-10-03 实测 sd_rst 一轮 598 条里 350 条（58%）"
               "如此。特征：正文以「索引/服务/日一二三四五六/最新发布」开头、"
               "带一串「标题 + 日期」的列表项、却没有任何句读。"
               "它在逐源结果里算 **ok**（链接拿到了、日期抽到了），"
               "所以常规监控完全看不见。"
               "正确动作是**抓详情页时校验正文与列表页是否雷同**，"
               "雷同就当详情页失败重取（换渲染级），而不是收一条"
               "「标题很好正文是导航」的记录。",
    },

    {
        "key": "stage|validate_extraction_against_title",
        "scope_type": "stage",
        "failure_type": "validate_extraction_needed",
        "best_action": "discover_fix",
        "why": "★ 正文抽取**必须校验结果**，不能直接采信——而校验要拿「句读」"
               "当主判据，标题只能当加强项。2026-10-03 实测（sd_rst 一轮598 条"
               "里 350 条正文是导航）：页面完好（55KB、HTTP 200、正文齐全），"
               "但 trafilatura 选错 DOM 区域，抽到 483 字符的日历组件——因为"
               "正文在 `<div class=show>`，而 show 不含 content/article"
               "之类常见词，正文定位打分输了。失败完全静默：页面能抓、标题能抽、"
               "日期能抽，逐源结果记为 ok。"
               "**两个反直觉的点，都踩过**："
               "(1)「正文必须含标题片段」不能当独立否决项——trafilatura 的 txt"
               "模式不把 <h1> 算进正文，干净结果里根本没有标题，用它判错会把"
               "好页面扔掉（test_content_excludes_site_chrome 现场抓到）。"
               "(2)「挑最长的那份」也不行——favor_recall 档比干净档长（301 vs "
               "293 字符）却多带了页脚。"
               "正确顺序：先看句读（导航没句子），再看标题片段（够长时加强），"
               "全不过就升级渲染级，不要硬塞已知混着 chrome 的内容。"
               "实现见 BaseCollector._extract_body / _body_looks_wrong。",
    },
    {
        "key": "stage|locate_body_by_structure_not_class",
        "scope_type": "stage",
        "failure_type": "locate_body_by_structure_not_class",
        "best_action": "discover_fix",
        "why": "★ 定位正文块要靠**结构**，不要靠 class 名硬编码。"
               "2026-10-03 实测：各站正文容器 class 五花八门——show / art / "
               "TRS_Editor / warp_list，加白名单只会变成「一直往下加」。"
               "而结构特征稳定得多：**句读密度高 + 链接占比低**。实测"
               "sc_jx_gov 正文 2001 字句读 15.99/千字链接占比 0%，"
               "sc_xz_gov 3658 字 11.48/千字 0%；导航条则几乎 100% 是链接、"
               "句读接近 0。实现见 BaseCollector._auto_locate_body。"
               "配套两条纪律：(1) 白名单只当**加速命中**项，不作唯一依据；"
               "(2) 定位失败就**返回空**让上层升级渲染级，绝不硬塞一份已知"
               "混着导航的内容——实测 sc_gs_gov 是 HTTP 412、静态 HTML 全空，"
               "这种情况自动定位本来���解，正确行为是让渲染级接手。",
    },
    {
        "key": "stage|one_fix_must_cover_all_paths",
        "scope_type": "stage",
        "failure_type": "one_fix_must_cover_all_paths",
        "best_action": "discover_fix",
        "why": "★ 修一个逻辑前先数清它有**几条调用路径**，逐条确认都走同一处；"
               "只改一条等于没修全，而且没修的那条恰好常常最需要修。"
               "2026-10-03 实踩：正文抽取加固（校验+ 自动定位）只做在 "
               "`BaseCollector.parse` 里，而 `Ladder._extract_html`（渲染级）"
               "**自己又调了一次 trafilatura**——分叉了。结果静态级修好了、"
               "渲染级照旧抽到全站导航，一轮下来 87 条重复正文。"
               "而受影响的 sc_gs_gov / sc_jx_gov / sc_xz_gov 都是配了 "
               "playwright_fallback 的源——**静态是 HTTP 412、只能走渲染**，"
               "也就是「只有渲染级能救」的那批。我恰好只修了救不了它们的那条路。"
               "原注释还写着「这和静态级用的是同一个函数」，曾经为真、"
               "改完就不成立了，而注释不会自动提醒你它已经过期。"
               "纪律：动共享逻辑前 grep 出所有调用点；发现分叉就合并到一处，"
               "并在测试里断言「所有路径都走同一个函数」（已加 "
               "test_render_path_uses_the_validated_extractor）。",
    },
{
        "key": "stage|no_sentence_marks_is_not_garbage",
        "scope_type": "stage",
        "failure_type": "no_sentence_marks_is_not_garbage",
        "best_action": "discover_fix",
        "why": "★ 修正我自己在 2026-10-03 早先写下的判断：「正文无句读」"
               "**不能**当垃圾判据。库里 2111 条（15.4%）正文 >=120 字而无句读，"
               "逐条抽读 10 条的结果是 **8 条是真数据、2 条是垃圾**。"
               "真数据长这样：生态环境部关于某年9 月环评文件受理情况的公示，"
               "正文是 markdown 表格「序号 | 项目名称 | 建设单位 | 受理日期」——"
               "**表格型公告**，数据价值很高（项目名、企业、受理日期），"
               "只是表格本来就没有句子。"
               "垃圾长这样：标题「政务联播 - 四川省人民政府网站」或「公告公示」，"
               "正文是 /col/col1738/index.html 这种栏目索引路径。"
               "所以判别式是「**表格 vs 栏目索引路径**」，不是「有没有句读」。"
               "顺带记一条工具教训：/col/colNNN/index.html 这个指纹也不干净，"
               "它会命中「关于开展某项目征集的通知」这类真公告（正文里带了栏目链接）。"
               "**没有单一信号能分，必须抽样读。**",
    },
# ── 2026-10-03：分类体裁词的三次失败尝试 ──────────────────────
    #
    # 背景：库 13682 条、L1 有 21 个分类、「其他」占 15.2%。抽样读标题发现
    # 明确的错分：「关于开展…科普统计调查的**通知**」被分成「新闻动态/数据播报」
    #（"调查""统计"命中了新闻的数据词表，而真正的文种信号"通知"没人认领）。
    # 我试了三种改法，**三次都把合成基线打坏**，最后全部撤回。
    {
        "key": "stage|genre_word_must_be_mutually_exclusive",
        "scope_type": "stage",
        "failure_type": "genre_word_must_be_mutually_exclusive",
        "best_action": "discover_fix",
        "why": "★ 想把一个词加进 STRONG_DOC_TYPES 之前，先问它**互斥吗**。"
               "强文种的收录标准是「一份文件只有一个文种」：征求意见/批复/名单/"
               "公示/解读 互斥，所以出现即定调安全。"
               "而「通知」**几乎出现在每一份公文标题里**——2026-10-03 实测把它"
               "加进强文种，L1 准确率从 >=95% 直接掉到 **41.2%**，因为所有标题"
               "都落进「政策发布/通知」，具体行业类（商业机会/行业补贴/人才政策）"
               "被整体吃掉。它确实修好了几条，但那是拿 53 个百分点的回归换的。"
               "判据：**一个词在真实标题里的出现率高到接近全体，它就不是区分信号**。"
               "「关于XX的通知」的正确归类要靠主题加具体实体判定"
               "（SPECIFIC_CATEGORIES 的加分机制就是干这个的）。",
    },
    {
        "key": "stage|fix_rules_measure_against_synthetic_baseline",
        "scope_type": "stage",
        "failure_type": "fix_rules_measure_against_synthetic_baseline",
        "best_action": "discover_fix",
        "why": "★ 动分类规则前后必须跑 ``tests/test_synthetic_data.py``，"
               "它有 L1 >=95% / L2 >=80% 的准确率门禁。2026-10-03 三次改动的"
               "准确率轨迹：76.2%（同分让体裁优先）→ 41.2%（通知进强文种）→ "
               "**测试红了才停下**。其中「同分让体裁优先」当时我觉得逻辑很漂亮"
               "（主题词不互斥、文种互斥，同分该让文种赢），实测把基线打到 76.2%"
               "——分层设计的初衷就是让两套分布各走各的，用 >= 把它们又揉回"
               "同一池子等于废掉分层。"
               "**规则改动不是「看起来更合理」，是「门禁还绿」。**"
               "真要改两层之间的优先级，得先有真实语料门禁，不能只靠合成基线。",
    },
    {
        "key": "stage|constant_defined_but_never_used",
        "scope_type": "stage",
        "failure_type": "constant_defined_but_never_used",
        "best_action": "discover_fix",
        "why": "「定义了却没被引用」的常量是**沉默的设计漂移**：注释和常量名都在"
               "声明一条规则，实现却没遵守，而且没有任何报错。"
               "2026-10-03 实测：``DEPARTMENT_CATEGORY_L1 = \"行业政策\"`` 连同"
               "注释「部门只决定行业领域，不决定一级分类」明明白白写着，而 "
               "``classify_by_department`` 直接把部门名当一级返回——"
               "于是「财政部」这个发文机关产出的记录一级分类是「财政金融」，"
               "可它不在 categories.yaml 的一级枚举里。"
               "「按行业领域查」和「按一级分类查」两套口径长期对不上，"
               "tests/test_category_taxonomy 为这一条红了很久。"
               "查法：``grep -n '<常量名>'`` 若只有定义处、没有使用处，就是它。",
    },
# ── 2026-10-03：真实语料基准 vs 合成基准（分类的独立验证） ──────
    #
    # 这是分类这件事的收尾：三次改分类被合成门禁打回，第四次靠真实数据证明
    # 那些改动是对的。关键不是"第四次成功"，而是**合成门禁根本量不到真问题**。
    {
        "key": "stage|synthetic_baseline_cannot_gate_accuracy",
        "scope_type": "stage",
        "failure_type": "synthetic_baseline_cannot_gate_accuracy",
        "best_action": "discover_fix",
        "why": "★ 合成基线测的是**自洽性**，不是准确率，别拿它当准确率门禁。"
               "2026-10-03 实测：``tests/test_synthetic_data.py`` 的标签来自 "
               "``scripts/generate_synthetic_data.py`` 的**随机拼词**——"
               "「市北京市人民政府关于职业院校培训的公告」"
               "「关于岳麓区许可公示项目的通知」（标签商业机会，可标题里写着公示）。"
               "它拿分类器去对词组打分，于是真实标题上明显更正确的改法"
               "（公示优先于名单、强文种跨层查找）都被它以「合成 L1 掉到 92.5% < 95%」"
               "拒绝——而那 92.5% 全是被随机标签冤枉的。"
               "**两套门禁并存，含义不同**：合成=生成器与分类器没脱节；"
               "真实（tests/test_golden_classify.py，240 条生产库标题）=准确率。"
               "不要拿合成的准确率去否决真实数据上的改进。",
    },
    {
        "key": "stage|golden_set_must_exclude_section_pages",
        "scope_type": "stage",
        "failure_type": "golden_set_must_exclude_section_pages",
        "best_action": "discover_fix",
        "why": "★ 基准文件自己也会有栏目页，必须在**构建阶段**剔掉，"
               "别指望下游测试发现。2026-10-03 实测：首版 "
               "tests/golden/category_golden.json（从生产库按「公示」正则抽 240 条）"
               "里混进了「公示公告-陕西省发改委」——标题就是栏目名+机构名，"
               "和抽样读出来的导航垃圾同一类。它匹配「公示」正则，于是被当成"
               "「确认是公示公告」的高置信样本。"
               "后果很直接：剔脏之前测出 L2 94.2%，剔脏之后真值是 **75.4%**，"
               "差了 19 个点。**差点又拿脏基准得出错误结论**——今天第四次。"
               "所以 scripts/build_golden_set.py 里加了 ``_is_section_page()``，"
               "并且 tests/test_golden_classify.py 里有一条断言专门拦"
               "「市北京市」这类拼词标题，防止合成数据混进来。",
    },
    {
        "key": "stage|ambiguity_is_an_ordering_bug_not_noise",
        "scope_type": "stage",
        "failure_type": "ambiguity_is_an_ordering_bug_not_noise",
        "best_action": "discover_fix",
        "why": "「看起来有歧义」的时候，先检查它是不是**优先级顺序**问题，"
               "而不是把整类数据排除。2026-10-03 实例：真实基准上 L2 最大单一"
               "错分是「公示 -> 名单」29 条，「关于XX名单的公示」同时命中两个词，"
               "而 ``STRONG_DOC_TYPES`` 按顺序返回第一个命中的，于是名单赢了。"
               "但这个歧义**可以判定**：包含关系是单向的——公示里可以有名单，"
               "名单里不会有公示，所以「公示」是文种、「名单」是内容，"
               "把公示提到名单之前即可。L2 **75.4% -> 87.5%（+12 点）**。"
               "我一开始的处理是把「名单」整类排除出基准（当作有歧义），"
               "那是回避不是解决，而且会让基准变小、代表性变差。"
               "先问「这个歧义能不能判」，判得了就是顺序 bug。",
    },# ── 2026-10-03：通用歧义词靠上下文，不靠词表 ──────────────────
    #
    # 「其他」占 18% 时抽读 16 条，读出三类东西：真垃圾（404 页面、栏目页）、
    # 真被埋没的、以及**通用歧义词**。这一批记的是第三类的解法。
    {
        "key": "stage|ambiguous_term_needs_context_not_keywords",
        "scope_type": "stage",
        "failure_type": "ambiguous_term_needs_context",
        "best_action": "discover_fix",
        "why": "★ 一个词出现在 4 个以上类别里，说明它**不是区分信号**，"
               "往词表里加词解决不了——要判的是**共现**。"
               "2026-10-03 实测「遴选」：它同时在 公务员考试/公招信息/招标信息/"
               "活动通知 四个类的词表里，于是公招信息靠 specific_bonus 独占，"
               "4 个用例 3 个错：「遴选工程技术研究中心」「遴选社科基金项目申报单位」"
               "「遴选基层医疗卫生机构人才」全被判成「事业单位招聘」。"
               "判据是共现：同行出现 项目/课题/基地/研究中心/承担单位 → 遴选的是**项目**"
               "（招标信息）；出现 工作人员/编制/岗位/医护 → 遴选的是**人**（公招信息）。"
               "两侧都没有就不判、交回词表兜底。实现见 "
               "``_resolve_ambiguous_ambiguity``。",
    },
    {
        "key": "stage|check_for_missing_genre_in_the_fallback_bucket",
        "scope_type": "stage",
        "failure_type": "missing_genre_in_fallback_bucket",
        "best_action": "discover_fix",
        "why": "「其他」类里堆着的常常不是「分不出来」，而是**词表里根本没有这个体裁**。"
               "2026-10-03 实测：「其他」1775 条里含「比选」的**193 条（11%）**、"
               "含「承担单位」74 条、含「课题」118 条——「比选」是政府采购里独立的"
               "高频体裁，而 config/category_rules.yaml 的招标信息里只有"
               "工程招标/货物采购/服务采购/资质预警四个子类，**一个都没覆盖**。"
               "补了两个子类（比选采购/课题承担，11 个词）之后，"
               "「其他」18% -> 16.6%，招标信息下新增 244 + 222 条。"
               "排查手法：对「其他」做词频，找**高频但不在任何词表里**的词——"
               "那些就是缺失的体裁，比调阈值有效得多。",
    },
    {
        "key": "stage|page_not_found_in_db_is_unambiguous",
        "scope_type": "stage",
        "failure_type": "page_not_found_in_db",
        "best_action": "discover_fix",
        "why": "有些缺陷**不需要抽样判断**，指纹是确定的："
               "「Page Not Found / 找不到网页 / 404 Not Found / 页面不存在」"
               "出现在标题或正文开头 —— 这就是 404 页面，进了库就是错的。"
               "2026-10-03 实测 34 条（hk_gov 28 / tj_gov 5 / sc_nx_gov 1），"
               "已全部打 needs_review（可逆）。"
               "和「无句读」「短标题」那类启发式不同，这类指纹**没有假阳性**，"
               "可以直接判。区分标准就一条：会不会误杀真数据？"
               "「页面不存在」不会；「标题里没有机构名」会。",
    },# ── 2026-10-05：采集源等级制度（三次判据错误的教训） ────────────
    #
    # 用户要求「按可采集/可绕过/不可采/久未更新分等级，自动采集按等级用不同方法」。
    # 落地为 scripts/source_tiers.py 的 T0~T5 六档，每档有**不同的路由和间隔**。
    # 但真正值得记的不是设计，而是**前两版判据都把大批健康源误判了**。
    {
        "key": "stage|tier_by_evidence_not_by_intent",
        "scope_type": "stage",
        "failure_type": "tier_by_evidence_not_by_intent",
        "best_action": "discover_fix",
        "why": "★ 给任何东西分级时，判据必须建立在**实测产出**上，不能用"
               "「配置里声明了什么」。2026-10-05 实测两连击："
               "(1) T2「需渲染」第一版判据是配置里的 ``playwright_fallback=True``"
               "——结果 143 个源里 130 个被判 T2，因为**所有源都配了 True**"
               "（当初为兜底统一开的），这字段零区分度；实证里只有 37 个源"
               "真的靠渲染出的货更多。改成「渲染采到的记录占比 >= 60%」后，"
               "分布立刻正常（T2=22）。"
               "(2) 更狠的一次：「连续零产出轮数」当降频判据，143 个源里 132 个"
               "被判「长眠」。根因是 daily 档**一天回访 3 次**，"
               "「连续 4 轮」实际只是 1.3 天没更新；而且退避阶梯把间隔越拉越长，"
               "**轮数和时长之间没有稳定关系**。"
               "改用「距上次入库多少天」后：同一时刻按周统计，126 个源是"
               "**本周内刚产出过**的——同一批数据、同一时刻，两个判据给出"
               "完全相反的结论。判死尤其要小心，必须**两个信号同时成立**"
               "（久没产出 且 连着多轮零产出），否则长假/站点改版会误杀。",
    },
    {
        "key": "stage|tier_must_change_behaviour",
        "scope_type": "stage",
        "failure_type": "tier_must_change_behaviour",
        "best_action": "discover_fix",
        "why": "★ 等级制度如果不是「不同等级用不同方法」，就只是贴标签。"
               "落地时必须同时改三处，否则等于没做："
               "(1) **回访间隔**：``SourceBackoff.is_sleeping_for`` 读等级表，"
               "取「等级下限」与「退避间隔」的较大者——等级只降频、**不放行**，"
               "所以被判 T4 的源不会因为退避表刚好到期就被高频回访。"
               "(2) **路由**：T2 跳过静态尝试直接走渲染（静态被 WAF 挡时，"
               "先试静态再降级是白花一次请求和一份超时预算）。"
               "(3) **深度/条数上限**：T3/T4/T5 递减，别在死源上挖深页。"
               "还要保证**等级表缺失时能降级**：它是优化不是前置依赖，"
               "读不到就退回原行为（测试test_missing_tier_file_does_not_break_"
               "collection 钉住这条）。",
    },
    {
        "key": "stage|refuse_to_inflate_numbers_with_garbage",
        "scope_type": "stage",
        "failure_type": "refuse_to_inflate_with_garbage",
        "best_action": "discover_fix",
        "why": "★ 为了「新增了 N 个源」这类好看的数字去收垃圾，和修过的所有"
               "东西相反。2026-10-05 扩源实测：13 个候选省市门户里 7 个首页可取到"
               "链接，但端到端验证时长沙/南昌两个站**取到的全是栏目页**"
               "（渲染后南昌那32 条「详情页」全是 css/js 资源）。"
               "我可以用一个宽松的 detail_pattern 把栏目页收进来，数量立刻好看——"
               "但收进来的是「政府信息公开指南 政府信息公开制度 法定主动公开内容…」"
               "这种导航词条，正是当天花大力气修掉的那类垃圾。所以**停下不加了**。"
               "同理，第一次建分类基准时首版混进了栏目页，测出 L2 94.2%——"
               "剔除后真值 75.4%，**差 19 个点**，差点又得出错误结论。"
               "判据：宁可交付一个小而干净的集合，也不要一个大而脏的。",
    },# ── 2026-10-05：借 Easel 的三个方法 + 两个静默失败 ──────────────
    #
    # 调研浙大 REAL Lab 的开源项目 ZJU-REAL/Easel（社媒内容工作台），
    # 按「它有什么、我们没有」来找可借鉴的。领域上零交集（它做内容创作与
    # 平台发布，我们做政策情报采集），但架构层面撞车，且有 3 个方法可直接用。
    # 记录这次的实际收获和踩的两个坑。
    {
        "key": "stage|borrow_patterns_not_code",
        "scope_type": "stage",
        "failure_type": "borrow_patterns_not_code",
        "best_action": "discover_fix",
        "why": "★ 借鉴外部项目要挑「**我们没有的方法**」，而不是「能合并的代码」。"
               "2026-10-05 实测 Easel 与本项目**领域零交集**：grep 验证其README，"
               "「政府/政策/情报/OSINT/爬虫/采集」各0 次，"
               "「数据库/溯源/去重/交叉验证」也各 0 次——它是社媒内容创作"
               "与发布工具。但有三个方法值得抄："
               "(1) ``skill-content-gap-analysis``（分析赛道空白、找蓝海机会）"
               "-> 我们缺「**该采而没采的是什么**」。当天扩源试了 13 个门户"
               "花了几小时，而这个工具 5 秒就指出「市级源仅 4 个 vs 省级 112 个、"
               "31 个省只有 <=2 个源、部委只覆盖 13/44」——"
               "**这些正是我盲试时本该先看的**。"
               "(2) ``skill-account-diagnosis`` 的「病因→证据→处方」结构"
               "-> 我们原本逐源给 status/reason，看不出「1 个病因表现为 7 处」，"
               "也不知道先修哪个。归并后可按影响面排序。"
               "(3) ``skill-topic-evaluator`` 的多维打分"
               "-> 我们的 T0-T5 评的是**健康度**不是**价值**，"
               "「这条政策值不值得多挖」目前没有评分维度。"
               "注意：它的存储层没有数据库、归因是**营销归因**（播放量）"
               "不是信源溯源——这两点它比我们弱，不要照搬。",
    },
    {
        "key": "stage|guessed_key_is_silent_failure",
        "scope_type": "stage",
        "failure_type": "guessed_key_is_silent_failure",
        "best_action": "discover_fix",
        "why": "★ Python 里**猜错字典键名是静默失败**：`d.get(\"records\", 0)` "
               "永远返回 0，代码照跑、结论全错、**不报任何错**。"
               "2026-10-05 实测两次，同一个坑："
               "(1) scripts/remedy.py 读 ``source_doctor.json`` 时猜键"
               "``diagnoses``，实际是 ``results`` -> 读到空列表，"
               "输出退化成「只有 1 类病因」我还以为数据没问题。"
               "(2) 归并病因时用 ``evidence.get(\"records\", 0) == 0`` "
               "判「从未产出」，而 evidence 里**根本没有 records 字段**"
               "（只有 collectable/enabled/wait_hours）-> 条件恒成立，"
               "**34 个有 23~264 条记录的源全被误判成从未产出**。"
               "纪律：任何**硬结论**（判废、判死、判脏）必须查真实数据源"
               "（库、文件系统），不能信别处的报告摘要；"
               "而报告类脚本读不到条目时必须**报错退出**，不能返回空列表继续跑——"
               "空结果比崩溃危险，因为它看起来像「没问题」。"
               "（这次两处都已改成：查真实库 + 取不到就 SystemExit。）",
    },
    {
        "key": "stage|check_output_is_not_empty_means_working",
        "scope_type": "stage",
        "failure_type": "check_output_not_empty",
        "best_action": "discover_fix",
        "why": "★ 新脚本跑完必须检查**输出是不是真反映了数据**，"
               "而不是只看「没报错」。2026-10-05：remedy.py 第一次跑出"
               "「6 个源有问题、1 类病因」，看起来完全正常；"
               "实际是 34 个源被误判、真实病因根本没读到。"
               "判别方法很便宜——**和另一份数据源对一下**："
               "我拿库里的真实记录数一对（mof_gov 23 条、moe_gov 264 条），"
               "立刻发现矛盾。汇报数字前问一句「这个数和我另一处看到的一致吗」，"
               "比通读代码快得多。今天靠这一下抓到两个静默错误。",
    },{
        "key": "stage|add_dimension_dont_overload_existing",
        "scope_type": "stage",
        "failure_type": "add_dimension_dont_overload_existing",
        "best_action": "discover_fix",
        "why": "★ 发现现有字段混编了多种语义时，**加正交的新维度**，"
               "不要改老字段。2026-10-05 实测：``category_l1`` 混编了**体裁**"
               "（公示公告/政策法规/政策发布…共 64%）和**主题**（行业政策/"
               "商业机会…共 34%），后果是「公示公告」里科技政策的公示和"
               "农业补贴的公示混在一起，无法回答「各省科技创新政策趋势」。"
               "正确做法是加一个与体裁**正交**的一等维度：「这条是什么体裁」"
               "和「这条讲什么行业」是两个独立问题，塞进一个字段必然丢一个。"
               "改老字段会破坏成刊/统计/工作台里所有按它过滤的查询。"
               "配套：二级分类本身也混着行业门类和体裁词（公示/名单/办法），"
               "**必须按父分类拆开**——依据是 category_rules.yaml 的父子结构，"
               "不是手写白名单（实测手写会漏掉只存在于 fallback_categories 的"
               "商务贸易/工业制造，漏掉 380+281 条）。",
    },
    {
        "key": "stage|form_is_not_topic",
        "scope_type": "stage",
        "failure_type": "form_is_not_topic",
        "best_action": "discover_fix",
        "why": "★★ 同一类错误连犯三次，收敛成一条：**文件形态 ≠ 内容主题**。"
               "2026-10-05 建行业维度时三次踩中："
               "(1) 拿「目录」「指南」当商务贸易的关键词 → 37+23 条实际是"
               "「应急管理部关于修改《危险化学品目录》…的通知」，那是应急安全；"
               "(2) 拿「民政」当民政社会的关键词 → 1380 条实际是"
               "「陕西省人民政府**民政厅**关于…」——**发文机关名**，"
               "于是该部门发的所有文件都被归到那个行业；"
               "(3) 优先读机构字段 institution_name → 实测那字段"
               "**只有 27 条有值（0.2%）**且全是招标代理公司，早已失效；"
               "机关名其实就写在标题首部（「XX厅关于…的通知」）。"
               "判据：必须区分三类东西——**发文机关**（谁发的，走机构表）、"
               "**文件形态**（体裁词，不进主题表）、**内容主题**（真行业）。"
               "混在一起，跨省对比就全是废数据——比缺数据更坏，"
               "因为它看起来像结论。",
    },
    # ── 2026-10-02：整夜零入库的三个真故障 ───────────────────────────
    #
    # 这一批的共同点是「系统看起来完全健康」：进程活着、退出码 0、日志没有
    # 一行报错。前两次我是靠人肉翻日志才发现的，写成散文没人看，所以这次
    # 全部进机器。
    {
        "key": "stage|no_production",
        "scope_type": "stage",
        "failure_type": "no_production",
        "best_action": "loosen_backoff",
        "why": "库长时间不增长，而进程活着、日志正常——最难发现的一种停摆。"
               "2026-10-01 整夜实测：34 个源全被退避闸挡住，一夜零入库、"
               "12 小时只完成 1 轮，watchdog 每轮都报「正常完成（无源可采）」。"
               "根因是当时的退避倍率乘在 cadence 墙钟下限上：daily floor 从 2h "
               "提到 8h 时没同步重算，退避从 6 小时变成 24 小时。"
               "**该根因已于 2026-10-02 从结构上修掉**（阶梯改成绝对秒数的 "
               "BACKOFF_LADDER，不再和 floor 相乘），所以本原则现在针对的是"
               "「阶梯本身太陡」——采干的源退避涨到 96 小时太久，动作是前移阶梯。",
    },
    {
        "key": "stage|write_locked",
        "scope_type": "stage",
        "failure_type": "write_locked",
        "best_action": "retry_write",
        "why": "Windows 上 Path.replace() 替换正被读取的文件会抛 WinError 5"
               "（打开的文件带共享锁，而 replace 需要删除+重命名；POSIX 上"
               "rename 覆盖打开的文件没问题，所以这个坑只在 Windows 出现）。"
               "2026-10-01 整夜实测：守卫脚本每 20 分钟读一次 source_backoff.json，"
               "采集器写回时撞锁 → 调度器一起就退 → 看门狗连续重启 62 次。"
               "动作是重试并降级为覆盖写，而不是让整轮崩掉。",
    },
    {
        "key": "stage|all_gated",
        "scope_type": "stage",
        "failure_type": "all_gated",
        "best_action": "loosen_backoff",
        "why": "「可采源为 0」是零入库最常见的原因。退避闸把「采干」误当成"
               "「源坏了」，于是越采越少、越少越退避，形成正反馈锁死。"
               "实测 34/34 全被挡住时采集等于停摆，但没有任何报错。",
    },
]


# 动作 → 实现函数的映射，必须与 principle_applier.ACTIONS 一致。
# 写成断言而不是注释：注释会过期，断言不会（2026-10-01 就因为
# 「动作名在文档里写了但 ACTIONS 表里没有」而静默不执行）。
def _assert_actions_exist() -> None:
    from lighthouse.principle_applier import ACTIONS

    required = {"loosen_backoff", "retry_write"}
    missing = required - set(ACTIONS)
    if missing:
        raise SystemExit(
            "seed_principles 引用了 ACTIONS 里没有的动作：%s\n"
            "这些原则会「应用成功」但什么都不做——和 auto_observe 一样是假动作。"
            % sorted(missing))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    from lighthouse import principle_applier, principles

    # 先验一遍 best_action 是否都在 ACTIONS 表里 —— 映射不到等于白写，
    # 这正是之前 auto_observe 的问题。
    unknown = [p["key"] for p in PRINCIPLES
               if p["best_action"] not in principle_applier.ACTIONS
               or principle_applier.ACTIONS[p["best_action"]] is None]
    if unknown:
        print("以下原则的 best_action 不可执行（等于 observe_only）：")
        for k in unknown:
            print("   %s" % k)
        print()
        print("ACTIONS 可用值: %s" % sorted(
            k for k, v in principle_applier.ACTIONS.items() if v))
        return 1

    path = principles.PRINCIPLES_FILE
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        existing = []

    by_key = {p.get("key"): p for p in existing}
    added, updated = [], []
    for spec in PRINCIPLES:
        row = {
            "key": spec["key"],
            "scope_type": spec["scope_type"],
            "failure_type": spec["failure_type"],
            "best_action": spec["best_action"],
            "utility_score": 1.0,
            "apply_count": 0,
            "success_count": 0,
            "fail_count": 0,
            "last_ts": time.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            "retired": False,
            "actions": [{"action": spec["best_action"], "utility": 1.0,
                         "count": 0, "success": 0, "fail": 0,
                         "last_ts": time.strftime("%Y-%m-%dT%H:%M:%S+00:00")}],
            "why": spec["why"],
        }
        if spec["key"] in by_key:
            row["apply_count"] = by_key[spec["key"]].get("apply_count", 0)
            updated.append(spec["key"])
        else:
            added.append(spec["key"])
        by_key[spec["key"]] = row

    merged = list(by_key.values())
    print("现有原则 %d 条" % len(existing))
    print("新增 %d 条，更新 %d 条" % (len(added), len(updated)))
    for spec in PRINCIPLES:
        print("  %-34s -> %s" % (spec["key"], spec["best_action"]))
    if not args.apply:
        print("\n（预览模式，未写库。加 --apply 落盘）")
        return 0

    principles.save_principles(principles.merge_principles(merged))
    print("\n已写入 %s" % path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())