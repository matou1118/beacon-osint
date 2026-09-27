# 后端工作台开发需求书

> 交付对象：界面/工作台 agent　|　框架：全国（省/市/县）　|　本轮目标：**模块功能可用，不做 UI 设计**
> 业务流（采集/分析/摘要/指标）由另一方负责，你**只消费已提供的数据层，不要自己写查询**
>
> 生成时间：2026-09-27　|　数据基准：`data/beacon.db`（1885 条公告）

---

## 一、后端现状盘点（实测数字）

### 1.1 运行状态

| 项 | 状态 |
|---|---|
| Streamlit 进程 | **未运行** |
| 端口 8599 | **无监听** |
| 13 个视图路由 | `app.py:1514-1536` 全部接线完整 |
| 测试 | 268 passed / 288 subtests，含 13 视图冒烟 |

**结论：工作台不是「功能缺失」，是没启动 + 部分数据表为空。**

### 1.2 数据表清单（17 张，实测行数）

| 表 | 行数 | 供给模块 | 状态 |
|---|---|---|---|
| `announcements` | 1885 | 公告检索/中国地图/总览 | ✅ 有数据 |
| `field_extractions` | 5909 | 字段复核 | ⚠️ 有数据，但依赖 topic_id 关联 |
| `graph_nodes` / `graph_edges` / `graph_evidence` | 1993 / 8388 / 6982 | 知识图谱 | ✅ 有数据（纯结构） |
| `attachments` | 211 | 质量与备份 | ✅ 有数据 |
| `source_sync_state` | 142 | 来源健康/采集运维 | ✅ 有数据 |
| `policy_topics` | **1** | 对比工作台/关联确认 | ❌ 近乎空 |
| `topic_doc_links` | **1** | 关联确认 | ❌ 近乎空 |
| `heat_topics` | **0** | 热频话题 | ❌ 空 |
| `user_subscriptions` | **0** | 订阅中心 | ❌ 空 |
| `topic_subscriptions` | **0** | 订阅中心 | ❌ 空 |
| `match_results` | **0** | 订阅中心 | ❌ 空 |
| `notification_logs` | **0** | 订阅中心 | ❌ 空 |
| `push_preferences` | **0** | 订阅中心 | ❌ 空 |
| `user_notices` | **0** | 订阅中心 | ❌ 空 |
| `feedback` | **0** | 质量与备份 | ❌ 空 |

**空表原因不是工作台缺陷，而是喂它们的管线阶段没在跑。** `batch_collect` 只执行到
「采集 → 富化 → 印证 → 入库 → 字段抽取」即结束，不建专题、不做订阅匹配、不算热度、
不重建图谱、不发通知。视图侧不要为这件事改代码。

### 1.3 质量现状

**21 项体检，未达标 1 项：**

```
summary_rate              0.9968  ✓    chrome_residue_rate    0.4503  ✓
dropped_amount            0.0090  ✓    attachment_download_rate 0.1121 ✓
compare_coverage_rate     1.0000  ✓    collection_yield_rate  0.9278  ✓
region_coverage_rate      0.8327  ✓
corroboration_pass_rate   0.0000  ✗    ← 唯一未达标，需多源交叉印证，属业务流
```

**下列「可用性问题」尚未纳入体检，数值很糟，你必须知道：**

| 问题 | 占比 |
|---|---|
| `category_l1` = 其他/空 | **95%** |
| 摘要超过 600 字上限（`MAX_SUMMARY_CHARS` 是死常量，从未生效） | **22%** |
| 门户页/栏目页入库（含上一页/下一页/网站标识码） | **12%** |
| `amount` 是垃圾值（如 `20`、`2026`） | 6% |
| 数据集中在 2026 一年 | **78%** |
| 摘要可见性 | **0** —— `as_record`（`app.py:125`）没有 `summary` 字段，1879 条摘要工作台一条都看不到 |

### 1.4 数据时效

`source_sync_state` 最新 `last_success_at` 约 2 天前；工作台无任何「数据新鲜度」提示。

---

## 二、你要交付什么

### 2.1 分工红线

| 你负责 | 不归你 |
|---|---|
| 视图结构、模块内部组织、交互流程、图表类型选择 | 采集器、清洗、摘要、分类、指标抽取 |
| 调用已有数据层函数并渲染 | **自己写 SQL 查 `data/beacon.db`** |
| 新增视图文件（如 `work_report_view.py`） | 改 `storage/`、`extractors/`、`collectors/`、`scripts/` |
| 视图内缓存/预聚合（**不得落库**） | 改 `data_health.py` 的度量定义 |

**唯一允许直接访问数据库的场景**：调 `Database` 类或 `collector_ops.py` 的既有函数。
需要新数据时提出需求，由业务流方提供函数。

### 2.2 立即要做的第一件事

```powershell
streamlit run app.py --server.port 8599 --server.address 127.0.0.1
```

工作台现在完全打不开，这是「用不了」的首因。先跑起来，再谈功能。

### 2.3 可直接调用的数据层函数（不要绕过）

```python
# collector_ops.py
load_registry()                    # 源注册表，按职能分组
summarize_registry(groups)         # 源状态汇总
read_progress()                    # 调度器进度快照
read_rounds(limit=None)            # 每轮采集台账
rounds_summary(rounds=None)        # 吞吐汇总 + 抽干识别
read_health()                      # 21 项体检 {metrics, findings}
region_breakdown(db_path, limit)   # 分省记录数
run_collection(limit, timeout)     # 触发一轮采集
provinces_without_enabled(groups)  # 有源但未启用的省份
skew_warning(summary)              # 省份倾斜告警
read_improve_log(limit)            # 规则自修正记录

# graph_service.py
graph_summary(db)                  # 节点/边/证据计数
graph_neighbors(db, node_id)       # 邻域查询
rebuild_graph(db, config_path)     # 重建

# compare_topics.py
fetch_announcements(db_path)       # 取全库记录
build_topics(records, include_junk)  # 真实数据自动发现专题
build_comparison(topic)            # 生成对比矩阵
is_portal_page(record) / is_document(record) / parse_amount(*values)
to_markdown / to_csv / to_excel

# storage/db.py —— Database 类
list_announcements(limit)          # 取公告
count_announcements()              # 总数
list_heat_topics(report_date, limit) / get_heat_topic(cluster_id)
upsert_heat_topics(topics, report_date)

# scripts/backup_db.py —— 备份，main() 返回 JSON
```

---

## 三、13 个模块的逐项需求

优先级：**P0 = 让模块有数据可显示**，P1 = 补功能缺口，P2 = 新增。

### P0-1 公告检索（已有，需补摘要可见）

**现状**：`render_announcements` → `render_record` 只显示 `key_points[:3]`，标为「要点」。
而 `key_points[0]` 与摘要 95% 完全相同 —— 摘要文字被当成要点显示了一部分，标签还是错的。

**要做的**：

1. `as_record`（`app.py:125`）增加 `"summary": (item.extra or {}).get("summary") or ""`
   —— **这是 2 行的关键缺口，摘要数据已在库里，只是没传到视图层**
2. `render_record` 分三层显示：摘要（完整）/ 关键要点（去掉与摘要重复的首条）/ 元信息
3. 右侧详情面板（`app.py:1188-1196`）加摘要全文 + `publish_date` / `amount` / `deadline`
4. 摘要为空时显示**丢弃原因**（`extra.summary_drop_reason`：`chrome_only` / `empty_body`），不要显示空白

**验收**：任取一条有摘要的记录，检索结果里能看到摘要全文，且明确标注为「摘要」而非「要点」。

### P0-2 采集运维（已加过一轮，你继续完善）

**已有分区**：源注册现状、采集进度、采集吞吐与产出（含折线 + 逐轮明细表 + 抽干告警）、
地区覆盖（含分省条形图）、数据体检（8 项卡片 + 未达标表 + 全量折叠面板）、规则自修正记录。

**要做的**：

1. **数据新鲜度告警**：顶部显示「最新入库时间」+ 距今天数；超过 N 天标黄。接口见 §5.1
2. **体检面板可下钻**：点任一指标 → 列出触发记录（至少给 id / 标题 / 源）
3. **空态处理**：`heat_topics`、`user_subscriptions` 等空表对应的模块要有明确空态文案

**验收**：能看出「数据最后一次更新是什么时候」和「库是否健康」，不需要看日志。

### P0-3 来源健康（有数据，需补失败原因可见）

**现状**：`source_sync_state` 142 行，有 `last_error` / `consecutive_failures` / `next_due_at`。

**要做的**：

1. 失败源清单，错误信息**可直接复制**
2. 每源显示：下次到期时间、连续失败次数、错误摘要（截断 120 字）
3. **省份倾斜提示**：`skew_warning()` 已有，要在页面顶部显著位置
4. 有源但未启用的省份：`provinces_without_enabled()` 已有

**验收**：一眼看出「哪个源在失败、为什么失败、什么时候再试」。

### P1-1 中国地图（有数据，功能缺口大）

**现状**：`announcements.region_province` 覆盖 83.3%、29 个省级区划。

**要做的**：

1. 地图着色指标**可切换**：公告数 / 摘要覆盖率 / 附件数 / 未达标指标数（现在单一维度）
2. 点省份 → 右侧列出该省公告，**可按 `category_l1` 二次筛选**
3. **必须处理 95% 未分类**：分类筛选器要包含「未分类」选项并显示数量，否则筛出来是空的
4. 缺地区归属的 315 条要有单独入口

**验收**：能回答「哪个省的公开信息最多」「哪个省的摘要质量最差」。

### P1-2 知识图谱（有数据但价值低）

**现状**：1993 节点 / 8388 边，**全部是结构性边** —— 节点只有
`announcement/category/organization/region/source`，边只有
`CLASSIFIED_AS / ISSUED_BY / LOCATED_IN / PART_OF / PUBLISHED_BY`。
也就是「这条公告属于哪个分类/地区/机构」。

**问题**：对政策对比几乎没用，因为所有东西最终都只挂在分类上，而分类 95% 是「其他」。

**要做的**：

1. 边类型筛选器（现在全量 8388 条混在一起）
2. 邻域查询：`graph_neighbors(db, node_id)` 已有接口，要能点节点展开
3. **诚实呈现**：页面显著位置提示「当前图谱为结构关系，概念关联层尚未构建」

**验收**：能从一个机构/地区出发，看到相关公告集合。

### P1-3 对比工作台（近乎空，需空态 + 降级）

**现状**：`policy_topics` 1 行、`topic_doc_links` 1 行。`compare_topics.py` 的
**真实数据自动发现**模式可用（不需要预建 topic）。

**要做的**：

1. 保留两种模式入口：**真实数据自动发现**（可用）/ 已登记专题（空态）
2. 显示「已排除 X 条垃圾」（`is_portal_page` / `is_document` / `parse_amount` 已在生效）
3. 导出：CSV/JSON/Markdown 可用；**Excel 入口必须置灰禁用**（`openpyxl` 未安装，`to_excel()` 返回 None）
4. 说明金额解析的保守性：已排除产能型假金额（`1000万吨/年`），新闻/访谈金额标「待确认」

**验收**：不建任何专题也能出对比结果；能导出。

### P1-4 关联确认 / 字段复核（数据依赖 topic）

**现状**：`field_extractions` 5909 行（有数据），但 `policy_topics` 1 行、
`topic_doc_links` 1 行，所以关联确认几乎无内容。

**要做的**：

1. 空态说明原因：「尚无待确认关联，请先运行专题构建」
2. 批量操作要能处理空集合而不报错（`bulk_decide` 已有）
3. 字段复核的抽取结果要显示 `evidence_text`（原文出处）—— 审计必需

### P2-1 订阅中心（6 张表全空，从零实现）

**现状**：`user_subscriptions`、`topic_subscriptions`、`match_results`、
`notification_logs`、`push_preferences`、`user_notices` **全部 0 行**。

功能链路：

```
配置订阅(地区/层级/分类/目标人群/行业/关键词)
  → 匹配引擎产出 match_results
  → 通知记录 notification_logs
  → 用户通知 user_notices
  → 推送偏好 push_preferences
```

**必须实现的字段**（对齐 `user_subscriptions` 15 列）：
`gov_levels`、`region_province`、`region_city`、`region_county`、`categories`、
`target_group`、`target_industry`

**要求**：全国框架 —— 订阅条件要支持省/市/县三级。数据重点在 9 个地区，但**不能写死**。

**验收**：能建订阅 → 看到匹配结果 → 看到通知记录。

### P2-2 热频话题（空表）

**现状**：`heat_topics` 0 行，`upsert_heat_topics()` / `list_heat_topics()` 接口已有。

**要做的**：

1. 空态 + 数据源说明（需运行热度计算阶段）
2. 列表字段：`heat_score`、`source_count`、`source_names`、`latest_publish`、`heat_reason`
3. 详情要能跳到构成该话题的公告列表

### P2-3 进化仪表盘（有数据，需补）

**现状**：`scripts/self_improve.py` 的尝试日志。

**要做的**：

1. 缺陷登记表（`config/defect_registry.yaml`）可视：缺陷 / 指标 / 当前值 / 阈值 / 修复方案数
2. 尝试历史：缺陷 / 修复 / 是否保留 / 前值 / 后值
3. **必须显示「已撤销」和「已保留」的区别** —— 修复机制会自动回滚失败的尝试，
   用户需要知道当前生效的是哪个版本

### P2-4 质量与备份（有数据，需补）

**要做的**：

1. 备份：调 `scripts/backup_db.py`（返回 JSON，含路径/大小/保留策略）
2. 附件：211 条，按 `extract_status` 筛选（成功/失败）
3. 反馈：`feedback` 0 行，需空态 + 收集入口
4. **质量标记筛选**：见 §5.2，本轮最实用的新增

### P2-5 总览（需重构信息架构）

**现状**：展示公告数/源数/质量分。

**要做的**：总览要回答四个问题，每个都要有数字支撑：

1. **有什么** —— 库存（总量/地区/层级/分类分布）
2. **新不新** —— 数据新鲜度 + 最近 7 天新增趋势
3. **干不干净** —— 21 项体检达标数 + **5 项可用性问题**（§1.3）
4. **在做什么** —— 当前采集轮次进度

---

## 四、新增模块（政府工作报告主线，数据到达后实现）

> ⚠️ **现在不要建。** 4 个地区 ~70 篇报告和指标表还没采到。现在建就是盲建，无法验证。
> 数据到达后再按本节实现。

### 4.1 目标

政府工作报告**升为主线**，现有公告降为辅线。理由：报告有（地区 × 年份）矩阵、天然可比、
能出数字；而公告 78% 集中一年、95% 未分类。

**不做摘要** —— 实测一份 45565 字的北京市报告产出 4636 字「摘要」（全文 10%），
22% 记录摘要超上限。报告改做**指标化 + 专题对照**。

### 4.2 数据契约（业务流方提供）

```
work_reports         id, region_code, region_name, level, year, report_date,
                     title, url, content, content_hash, sections(JSON),
                     char_count, fetched_at, created_at, updated_at

work_report_metrics  report_id, region_code, year, metric_key, metric_name,
                     value, unit, growth_value, growth_unit,
                     is_target(True=本年目标/False=上年实际),
                     caliber(口径原文表述), section, evidence_text,
                     extract_version

work_report_topics   report_id, topic, region_code, year, hit_count
```

**关键字段解释（实现时必须理解）：**

- **`is_target`** —— 「上年回顾」段的数字是上年**实际**完成值，「本年目标」段是本年**目标**值。
  这个字段让同一指标能拼成连贯年度序列：报告 N 的目标 vs 报告 N-1 的实际。
  **跨年对比正确性依赖它。**
- **`caliber`（口径）** —— `常住人口` ≠ `户籍人口`，`地区生产总值` ≠ `生产总值`。
  **口径不一致的指标不参与自动跨地区对比，只在图上标出。** 这是防止「假的对比」的关键。

### 4.3 首批覆盖（实测确认可枚举）

| 地区 | 归档页 | 篇数 | 坑 |
|---|---|---|---|
| 上海 | `shanghai.gov.cn/nw12336/` | 20（2007-2026） | 链接文字带「（20XX年）」 |
| 广州 | `gz.gov.cn/zwgk/zjgb/zfgzbg/` | 20（2007-2026） | `/zwgk/zfgzbg/` 是 404 |
| 重庆 | `cq.gov.cn/zwgk/zfxxgkml/zfgzbg/` | 15（2006-2026） | 首屏只到 2013，更早需翻页 |
| 武汉 | `wuhan.gov.cn/zwgk/zwgk_wl/zfgb/` | 15（2010-2026） | **正式面包屑栏目是 JS 加载（0 命中），必须用镜像栏目** |

成都/湖北 412（WAF），浙江无归档栏目 —— 本轮不做。

### 4.4 首批专题与指标

- **就业**：城镇新增就业、城镇登记失业率、就业人员数
- **医疗**：每千人口执业医师数、医疗卫生机构床位数、卫生技术人员、医保参保人数
- **教育**：在校生数、专任教师数、教育经费、学前三年毛入园率、普惠性幼儿园覆盖率

**已知缺口**：实测上海报告中 `居民人均可支配收入`、`常住人口`、`PM2.5`、`空气优良天数`
正则命中 **0**。界面要能显示「该地区该年未披露」，而不是显示 0。

### 4.5 你要做的三个模块

**A. 报告库**

- 列表：地区 / 年份 / 字数 / 指标命中数
- 筛选：地区（全国框架，本轮 4 地有数据）、年份区间、字数区间
- 详情：**分节展示**（回顾 / 规划 / 目标与任务），不要整篇平铺
- 4 万字的报告必须分节 + 折叠，否则不可读

**B. 指标对照**

- 指标 × 地区 × 年份的**三维表**（可导出）
- 多地区同指标**趋势折线**
- 地区 × 年份**热力表**
- **口径不一致的单元格必须视觉区分**，并在图例说明
- 缺失值显示「未披露」，**绝不显示 0**

**C. 专题看板**

- 就业/医疗/教育三个专题切换
- 每个专题：命中段落 + 段落内数值 + 年度趋势
- 只呈现**事实与差异，不做政策判断、不给建议**（合规底线）

---

## 五、需要业务流方提供的接口（先按此开发，接口到达后直接接）

### 5.1 数据新鲜度

```python
def data_freshness(db_path: str = "data/beacon.db") -> dict[str, Any]
# 返回: last_ingest_at, last_success_at, hours_since_ingest,
#       latest_publish_date, stale(bool)
```

### 5.2 质量标记（本轮最实用）

```python
def quality_flags(db_path: str = "data/beacon.db") -> dict[str, Any]
# 每条记录返回 flag:
#   portal_page          门户页/栏目页（链接密度 + 上一页/下一页/共N条/网站标识码）
#   summary_over_length  摘要超 600 字
#   uncategorized        category_l1 为 其他/空
#   bogus_amount         amount 明显非金额（年份/小数字）
# 支持: quality_flags(filter_by=["uncategorized"], region="四川省")
```

**用途**：工作台需要一个「只用干净数据」的开关。当前 233 条门户页 + 416 条超长摘要 +
1773 条未分类混在一起，不筛就得出错误结论。

### 5.3 主题词频 / 共现（后期）

```python
def term_frequency(region=None, year_range=None, top=50) -> list[dict]
def co_occurrence(region=None, year_range=None, min_count=3) -> list[dict]
```

基于词同现网络（参考 `Chinese_Government_Report_Analysis` 的方法，**借思路不抄代码**，
该仓库无 LICENSE 文件）。

### 5.4 检索用摘要（公告辅线）

```python
def brief_summary(announcement_id: int) -> str
# 检索列表用的单句摘要（≤50字），与 work_reports 的指标层无关
```

---

## 六、验收标准

### 6.1 通用

1. **13 个视图全部可渲染，零异常**（`tests/test_view_smoke.py` 13/13 必须通过）
2. **无空表显示空白页** —— 每个模块都要有明确空态 + 原因 + 下一步动作
3. **每个数字可溯源** —— 能从页面上的数字追到具体记录 id
4. **全国框架** —— 省/市/县三级可筛选，不得把 9 个重点地区写死
5. **不写 SQL** —— 只调 `Database` / `collector_ops.py` / 待提供的接口函数

### 6.2 本轮必须达成

- [ ] 工作台能启动，8599 可访问
- [ ] 公告检索**能看到摘要全文**且标注为「摘要」
- [ ] 空态：热频话题 / 订阅中心 / 关联确认 / 对比工作台（已登记模式）
- [ ] 总览四问（有什么/新不新/干不干净/在做什么）都有数字
- [ ] 采集运维显示数据新鲜度
- [ ] 质量标记筛选可用
- [ ] 13/13 冒烟测试通过

### 6.3 数据到达后

- [ ] 报告库：4 地 × 15 年可浏览，分节展示
- [ ] 指标对照：三维表 + 趋势折线 + 热力表，口径不一致有视觉区分
- [ ] 专题看板：就业/医疗/教育三专题

---

## 七、明确不做

1. **不重搭框架** —— 13 个视图已接线且测试通过，重建会丢界面工作
2. **不改主题/配色/布局** —— 本轮只谈功能
3. **不自己建表** —— 表结构由业务流方定
4. **不在视图里做重计算** —— 指标计算、词频、共现都在业务流侧，视图只渲染
5. **不建政府工作报告模块，直到数据到达** —— 盲建无法验证

---

## 八、交付物

```
work_report_view.py     政府工作报告三模块（数据到达后）
quality_view.py         质量标记与筛选（依赖 §5.2）
<现有 *_view.py 修改>   摘要可见、空态、新鲜度
tests/test_view_content.py  补充内容断言
```

**协作约定**：任何需要新数据接口的地方，在视图里写
`# TODO(接口待提供): <签名>` 并继续用空态渲染，不要阻塞。接口到达后替换调用点即可。

---

## 附：合规底线（所有模块适用）

- 遵守 robots，不绕过登录、验证码、签名或访问控制
- 不使用代理池、指纹伪装或反检测手段
- 只呈现**事实与差异**，不做政策判断、不给决策建议
- 媒体印证只允许行业/民生/经济内容，政治内容不得进入印证池
