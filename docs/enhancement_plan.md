# BeaconOSINT 能力增强完整方案

> 核心洞察：**有数据才有纯本地规则的分析对比和感知能力。**
> 自进化闭环的瓶颈不在规则调参，而在采集→清洗→摘要→对比→印证全链路的吞吐量。
> 本方案目标：把每一段管线的"产出量"提上去，让自进化有足够的样本做测量→迭代→保留。

---

## 一、现状诊断

### 1.1 管线吞吐量

| 阶段 | 产出 | 目标 | 差距 |
|------|------|------|------|
| 采集 | 527 条 / 57 源 | ≥2000 条 / ≥80 源 | 4× |
| 清洗 | 527 条（全量清洗） | 清洗后 ≥95% 可用 | 需审计 |
| 摘要 | 309 条有摘要 | 全量有摘要 | 62% |
| 抽取 | field_extractions = **0** | ≥500 条结构化字段 | 从零开始 |
| 对比 | match_results = **0** | ≥100 条匹配 | 从零开始 |
| 印证 | corroboration_pass_rate = **0.0** | ≥0.2 | 从零开始 |
| 话题 | heat_topics = **0** | ≥20 个热点 | 从零开始 |

### 1.2 自进化瓶颈

当前 14 个缺陷中 6 个是**数据依赖型**——不是规则不够好，而是数据不够多：

| 缺陷 | 当前值 | 目标 | 瓶颈 |
|------|--------|------|------|
| collection_yield_low | 0.0 | ≥0.3 | 采集器只抓列表页，不抓详情页 |
| attachment_pipeline_empty | 0.0 | ≥0.1 | 附件下载管线未接通 |
| compare_coverage_low | 0.0 | ≥0.3 | 对比引擎未跑 |
| corroboration_pass_low | 0.0 | ≥0.2 | 印证引擎未跑 |
| low_region_coverage | 0.40 | ≥0.50 | 9 省 blocked，需找替代源 |
| no_cross_level_programs | 0.0 | ≥1 | 缺市县级源 |

### 1.3 代码资产

| 模块 | 文件数 | 代码行 | 健康度 |
|------|--------|--------|--------|
| extractors/ | 10 | 1525 | 核心逻辑在，但 gliner/verification 未接通 |
| scripts/ | 7 | 1584 | self_improve 闭环在，但 discover_fix 覆盖窄 |
| root/ | 30 | 8553 | app.py 84KB 偏大，需拆分 |

---

## 二、增强方案总览

```
Phase A: 采集增强          Phase B: 清洗增强          Phase C: 摘要增强
  ↓                          ↓                          ↓
源探测 → 详情页抓取 →       HTML清洗 → 编码归一 →       结构化摘要 →
附件下载 → 增量调度          去重 → 字段抽取             事实句提取 → 多级摘要
  ↓                          ↓                          ↓
Phase D: 对比增强          Phase E: 印证增强          Phase F: 自进化增强
  ↓                          ↓                          ↓
字段匹配 → 版本追踪 →       多源交叉 → 置信度 →         新缺陷类型 →
话题聚类                    证据链                     discover_fix 扩展
  ↓                          ↓                          ↓
                    Phase G: 技能开发
                    5 个 Skill 固化能力
```

---

## 三、分阶段详细方案

### Phase A：采集增强（最高优先级）

> **原则**：没有数据，后面全是空跑。

#### A1. 详情页深度抓取
- **现状**：collector 只抓列表页摘要，不进详情页
- **目标**：每条记录有完整正文 + 原文链接
- **实现**：
  - 在 `collector_ops.py` 加 `fetch_detail_page(url)` 方法
  - 用 httpx + 选择器配置（sources.yaml 已有 CSS selector 字段）
  - 增量抓取：只抓 `source_sync_state.last_synced` 之后的新条目
- **缺陷对应**：`collection_yield_low`

#### A2. 附件下载管线
- **现状**：`attachment_download_rate = 0.0`
- **目标**：PDF/Word/Excel 附件下载 + 文本提取
- **实现**：
  - 新建 `extractors/attachment_downloader.py`
  - 下载：httpx 流式下载，存 `data/attachments/`
  - 提取：PDF 用 pdfplumber，Word 用 python-docx，Excel 用 openpyxl
  - 入库：提取文本写入 `announcements.attachment_text`
- **缺陷对应**：`attachment_pipeline_empty`

#### A3. 动态页面支持
- **现状**：57 源全是 `static_html`，JS 渲染站点抓不到
- **目标**：支持 5+ 个 JS 渲染源
- **实现**：
  - 在 sources.yaml 加 `access_method: dynamic_js`
  - 用 playwright 或 crawl4ai 做后备渲染引擎
  - 在 collector 中按 `access_method` 分流
- **缺陷对应**：`collection_yield_low` (fix: switch_to_crawl4ai)

#### A4. 增量调度优化
- **现状**：scheduler 全量跑，慢且重复
- **目标**：每源独立增量，断点续跑
- **实现**：
  - `source_sync_state` 表已有，加 `last_item_hash` 字段做去重
  - scheduler 按源并发，每源完成后立即写 sync_state
  - 失败源自动降级到下次重试

---

### Phase B：清洗增强

#### B1. HTML 清洗加固
- **现状**：`clean_summarize.py` 有基础规则，但 chrome 残留、导航栏残留仍有
- **目标**：`chrome_residue_rate < 0.01`
- **实现**：
  - 扩展 `SWITCHER_PATTERNS` 和 `TAIL_ZONE` 规则
  - 加 `extractors/html_sanitizer.py`：用 lxml 做 structure-aware 清洗
  - 自进化驱动：discover_fix 从失败样本提取新残留模式

#### B2. 编码归一
- **现状**：部分源 GBK/GB2312，统一转 UTF-8
- **实现**：
  - 在 collector 加 `detect_encoding(content)` → chardet
  - 统一转 UTF-8，记录原始编码到 metadata

#### B3. 去重管线
- **现状**：无显式去重，可能同源同文重复入库
- **实现**：
  - 新建 `scripts/dedup.py`
  - 标题 SimHash + 正文 minhash 双重去重
  - 跨源去重：不同源发同一文，保留最早源 + 标记 corroborated

#### B4. 字段抽取接通
- **现状**：`field_extractions = 0 行`，gliner_extractor.py 存在但未接通
- **目标**：≥500 条结构化字段
- **实现**：
  - 在 pipeline 中加 `extract_fields(record)` 步骤
  - 调用 `gliner_extractor.py` 提取：金额、日期、文号、机构、政策类型
  - 结果写入 `field_extractions` 表
- **缺陷对应**：`compare_coverage_low`（有字段才能对比）

---

### Phase C：摘要增强

#### C1. 结构化事实句提取
- **现状**：摘要是一段压缩文本，无结构
- **目标**：每条摘要标注 5 类事实句（金额、日期、对象、范围、期限）
- **实现**：
  - 在 `summary_refine.py` 加 `extract_fact_sentences(text)` → dict
  - 事实句类型：amount、deadline、target、scope、duration
  - 写入 `announcements.fact_sentences` (JSON)
- **缺陷对应**：`corroboration_pass_low`（有事实句才能印证）

#### C2. 多级摘要
- **现状**：只有一级摘要
- **目标**：三级（brief ≤50字 / detailed ≤200字 / full ≤500字）
- **实现**：
  - `build_summary(text, level='brief|detailed|full')`
  - brief：核心事实句拼接
  - detailed：当前摘要逻辑
  - full：保留全部事实句 + 关键上下文

#### C3. 摘要质量自检
- **现状**：dropped_amount 已修，但其他事实句丢失未监控
- **实现**：
  - 在 `data_health.py` 加 `dropped_fact_rate` 指标
  - 对比原文事实句 vs 摘要事实句，丢失率 > 5% 告警
  - 写入 defect_registry.yaml 新缺陷类型

---

### Phase D：对比增强

#### D1. 字段级对比引擎
- **现状**：`compare_engine.py` 存在但 `match_results = 0`
- **目标**：≥100 条匹配
- **实现**：
  - 接通 `field_extractions` → `compare_engine` 管线
  - 对比维度：同文号、同标题 SimHash、同金额+同日期、同政策对象
  - 匹配结果写入 `match_results` 表
- **缺陷对应**：`compare_coverage_low`

#### D2. 版本追踪
- **现状**：同一政策的不同版本（征求意见稿→正式稿→修订稿）无关联
- **实现**：
  - 在 `policy_compare.py` 加 `track_versions(record)`
  - 匹配规则：同标题根 + 版本关键词（征求意见/正式/修订/废止）
  - 写入 `graph_edges` (type=version_of)

#### D3. 话题聚类
- **现状**：`heat_topics = 0`
- **目标**：≥20 个热点话题
- **实现**：
  - 新建 `scripts/topic_cluster.py`
  - 用 TF-IDF + KMeans 或 DBSCAN 做话题聚类
  - 每日跑一次，写入 `heat_topics` 表
  - 仪表盘加"热点话题"模块

---

### Phase E：印证增强

#### E1. 多源交叉印证引擎
- **现状**：`corroboration_pass_rate = 0.0`
- **目标**：≥0.2
- **实现**：
  - 新建 `extractors/corroboration_engine.py`
  - 对每条记录的事实句，在其他源中搜索匹配
  - 匹配规则：同金额（±5%）+ 同日期（±7天）= 高置信印证
  - 写入 `graph_evidence` (type=corroboration)
- **缺陷对应**：`corroboration_pass_low`

#### E2. 置信度评分
- **现状**：无置信度体系
- **实现**：
  - 每条记录加 `confidence_score` 字段
  - 评分：单源 0.3 + 双源印证 0.7 + 三源+ 0.9
  - 仪表盘展示置信度分布

#### E3. 证据链可视化
- **现状**：graph_evidence 有 2888 行但无可视化
- **实现**：
  - 在 `graph_service.py` 加 `build_evidence_chain(record_id)`
  - 仪表盘加"证据链"视图：记录→印证源→置信度

---

### Phase F：自进化增强

#### F1. 新缺陷类型注册
- **现状**：14 个缺陷，但新管线需要新指标
- **新增**：
  - `field_extraction_rate`：字段抽取覆盖率
  - `topic_cluster_rate`：话题聚类覆盖率
  - `version_track_rate`：版本追踪覆盖率
  - `confidence_avg`：平均置信度
  - `dedup_rate`：去重率
- **实现**：写入 `defect_registry.yaml`，在 `data_health.py` 加测量

#### F2. discover_fix 扩展
- **现状**：discover_fix 只处理 3 种缺陷（chrome_residue, dropped_amount, short_bodies）
- **目标**：覆盖全部 14+ 缺陷类型
- **实现**：
  - 为每种缺陷写根因分析器
  - 从失败样本提取共性模式 → 生成修复候选
  - 候选写入 `data/discover_candidates.jsonl`，需人工审核

#### F3. 进化仪表盘增强
- **现状**：有折线图+回滚链
- **新增**：
  - 管线吞吐量漏斗图（采集→清洗→摘要→抽取→对比→印证）
  - 每阶段转化率标注
  - 顽固缺陷的"建议人工干预"按钮

---

### Phase G：技能开发（固化能力为可复用 Skill）

> 将重复性高、规则化强的能力固化为 Skill，让自进化可以自动调用。

| 优先级 | 技能名称 | 对应 Phase | 描述 |
|--------|---------|-----------|------|
| P0 | **source-probe-onboarding** | A | 探测新政府源：查 robots→试抓→配选择器→写 sources.yaml |
| P0 | **cross-source-corroboration** | E | 多源交叉印证引擎：事实句匹配→置信度→证据链 |
| P1 | **archive-crawler-configurator** | A | 分析政府站结构→自动生成翻页规则+CSS 选择器 |
| P1 | **evolution-report-generator** | F | 定期生成"本周进化摘要"：改了什么、保留了什么、卡在哪 |
| P2 | **data-quality-auditor** | B | 深扫 SQLite：重复、缺失、编码、格式不一致→修复报告 |

#### 技能开发计划

**Skill 1: source-probe-onboarding**
```
输入：政府网站 URL
流程：
  1. 查 robots.txt → 判断可抓性
  2. 试抓列表页 → 识别 CMS 类型（党政机关/通用 CMS）
  3. 自动推断 CSS 选择器（列表项/标题/日期/链接）
  4. 试抓 3 个详情页 → 验证选择器
  5. 生成 sources.yaml 条目 → 人工确认后写入
输出：sources.yaml 新条目 + 探测报告
```

**Skill 2: cross-source-corroboration**
```
输入：一批已抽取事实句的记录
流程：
  1. 按事实句类型分组（金额/日期/文号/对象）
  2. 跨源搜索匹配事实句
  3. 计算置信度（单源/双源/多源）
  4. 构建证据链 → 写入 graph_evidence
  5. 更新 corroboration_pass_rate
输出：印证结果 + 证据链 + 置信度更新
```

**Skill 3: archive-crawler-configurator**
```
输入：政府网站 URL
流程：
  1. 分析站点结构 → 识别归档栏目
  2. 探测翻页方式（页码/URL pattern/JS 加载）
  3. 生成 archive_pages 配置
  4. 试抓 1 年历史数据 → 验证
输出：archive 配置 + 历史覆盖报告
```

**Skill 4: evolution-report-generator**
```
输入：self_improve_log.jsonl + data_health 指标
流程：
  1. 汇总本周进化动作（尝试/保留/撤销）
  2. 对比本周 vs 上周指标变化
  3. 列出顽固缺陷 + 建议人工干预
  4. 生成 Markdown 报告
输出：evolution_report_YYYYWW.md
```

**Skill 5: data-quality-auditor**
```
输入：SQLite 数据库
流程：
  1. 重复检测（标题 SimHash + 正文 minhash）
  2. 字段缺失审计（必填字段空值率）
  3. 编码异常检测（乱码/混合编码）
  4. 日期格式一致性
  5. 生成修复建议
输出：quality_audit_report.md + 修复 SQL 脚本
```

---

## 四、实施路线图

### Sprint 1（2 周）：采集 + 清洗
| 任务 | Phase | 产出 |
|------|-------|------|
| 详情页深度抓取 | A1 | 527→1500 条有正文 |
| 附件下载管线 | A2 | attachment_download_rate ≥ 0.1 |
| 编码归一 | B2 | 0 条乱码 |
| 去重管线 | B3 | dedup_rate ≥ 0.95 |
| 字段抽取接通 | B4 | field_extractions ≥ 500 |

### Sprint 2（2 周）：摘要 + 对比
| 任务 | Phase | 产出 |
|------|-------|------|
| 事实句提取 | C1 | 每条有 5 类事实句 |
| 多级摘要 | C2 | brief/detailed/full 三级 |
| 字段级对比 | D1 | match_results ≥ 100 |
| 版本追踪 | D2 | version_of 边 ≥ 50 |

### Sprint 3（2 周）：印证 + 话题
| 任务 | Phase | 产出 |
|------|-------|------|
| 交叉印证引擎 | E1 | corroboration_pass_rate ≥ 0.2 |
| 置信度评分 | E2 | 全量有 confidence_score |
| 话题聚类 | D3 | heat_topics ≥ 20 |
| 证据链可视化 | E3 | 仪表盘可查 |

### Sprint 4（2 周）：自进化 + 技能
| 任务 | Phase | 产出 |
|------|-------|------|
| 新缺陷类型注册 | F1 | 19 个缺陷 |
| discover_fix 扩展 | F2 | 覆盖全部缺陷 |
| 仪表盘漏斗图 | F3 | 管线可视化 |
| Skill: source-probe-onboarding | G | 可用 |
| Skill: cross-source-corroboration | G | 可用 |

### Sprint 5（1 周）：技能收尾
| 任务 | Phase | 产出 |
|------|-------|------|
| Skill: archive-crawler-configurator | G | 可用 |
| Skill: evolution-report-generator | G | 可用 |
| Skill: data-quality-auditor | G | 可用 |

---

## 五、验收标准

| 指标 | 当前 | Sprint 1 后 | Sprint 3 后 | 最终 |
|------|------|------------|------------|------|
| announcements | 527 | ≥1500 | ≥2000 | ≥3000 |
| field_extractions | 0 | ≥500 | ≥1500 | ≥2500 |
| match_results | 0 | 0 | ≥100 | ≥300 |
| corroboration_pass_rate | 0.0 | 0.0 | ≥0.2 | ≥0.3 |
| collection_yield_rate | 0.0 | ≥0.3 | ≥0.4 | ≥0.5 |
| attachment_download_rate | 0.0 | ≥0.1 | ≥0.2 | ≥0.3 |
| region_coverage_rate | 0.40 | ≥0.50 | ≥0.55 | ≥0.60 |
| heat_topics | 0 | 0 | ≥20 | ≥50 |
| 自进化保留率 | 8% | ≥15% | ≥20% | ≥25% |

---

## 六、风险与对策

| 风险 | 概率 | 对策 |
|------|------|------|
| 政府站反爬升级 | 中 | 多策略后备（static→dynamic→RSS），降级不阻塞 |
| 附件下载量大占磁盘 | 高 | 按 retention_days 自动清理，大附件只存文本不存原文件 |
| gliner 模型加载慢 | 中 | 懒加载 + 进程内缓存，批量抽取 |
| 自进化误保留回归修复 | 低 | 每次保留前检查全量指标，有回归立即撤销 |
| 技能开发周期超估 | 中 | P0 技能先做，P1/P2 可延后 |
