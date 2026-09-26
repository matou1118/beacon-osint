# 自动采集器功能现状汇总

- 日期：2026-09-26
- 范围：`collectors/`、`scheduler.py`、`main.py` 采集链路，以及配套的源注册、清洗摘要、交叉印证、健康检查
- 口径：所有"已实现"均以真实数据或真实运行验证过；未验证的一律列入"未实现"
- 数据现状：`data/beacon.db` 387 条真实记录，测试 221 passed / 179 subtests

---

## 一、已实现

### 1. 采集策略（`collectors/html_list.py`）

按成本从低到高自适应降级，每一级失败都记录原因，不是只报"0 条"：

| 顺序 | 策略 | 触发条件 |
|---|---|---|
| 1 | RSS | 源配了 `rss_url` |
| 2 | crawl4ai | `render_mode: crawl4ai` |
| 3 | 静态 HTML | 默认 |
| 4 | 静态归档翻页 | 链接数不足 `max_items` |
| 5 | Playwright 渲染翻页 | 仍不足且 `playwright_fallback: true` |
| 6 | crawl4ai 兜底 | 仍为空或静态质量不达标 |

- 栏目页/首页识别：`_is_section_page()` 标题特征 + `_navigation_like()` URL 形态，命中抛 `SectionLandingError`，不混进公告集（实测 `section_page_rate` 0.0078）
- 链接白名单：`_link_allowed()` 同域限制，避免爬到站外
- 质量闸门：`_static_quality_ok()` 判定静态抓取是否值得采信

### 2. 真实浏览器渲染（`collectors/playwright_worker.py`）

- **worker 进程隔离**：`render_page` / `render_pages` 走独立进程。原因是 crawl4ai 调 `asyncio.run()` 后 Playwright sync API 无法再启动（"Sync API inside the asyncio loop"），该故障曾命中 145 个注册源中的 46 个
- 浏览器复用：worker 内常驻，`shutdown_renderer()` 收尾
- 分页：`NEXT_SELECTORS` + `PAGE_INPUT_SELECTORS` 两种策略，覆盖"下一页"按钮和页码输入框
- DOM 直提：`collect()` 走 `DEFAULT_EXTRACT_JS`，绕过 HTML 解析直接取列表

### 3. 合规与稳定性（`collectors/base.py`）

- **robots 强制**：请求前校验、重定向后对最终 URL 再校验一次；按 origin 缓存；404/410 视为允许，401/403 视为拒绝
- 域名白名单：请求前和重定向后都校验，防止越域
- 限速：`_wait_for_rate_limit()`
- URL 日期解析：`_date_from_url()` 覆盖多套政务站路径格式
- 保留期重算：`_retention_until_with_days()` 按最终 `retention_class` 重算 `retention_until`
- 内容去重：`compute_content_hash()`

### 4. 调度（`scheduler.py`）

- 层级开关 `level_switch`：国家部委/省/市开，县级与特区关；本次已把特区在配置层也关掉（原来只靠运行时开关，直接跑 `main.py` 会绕过）
- 分层抽样：`province_sample` / `city_sample` / `county_sample` 限制单次规模
- 进度快照：`data/runtime/collect_progress.json`，心跳 30 秒，长跑任务能看到实时进度
- 失败重试：`run_source_with_retry()`

### 5. 采集后的加工链路

- 清洗：位置感知的字体切换器处理、页脚区判定、面包屑、元数据内联串、字号残留
- 摘要：抽取式（逐字来自原文），`summary_refine.build_summary()` 做自我修正（压缩带、金额/时限/对象保护、联系方式尾巴裁剪）
- 分类：确定性规则 + 主管部门兜底映射
- 交叉印证：`rules/corroboration.py` 多源匹配 + `extractors/verification_gate.py` 媒体准入门控（排除政治类、要求含可比事实）
- 时间线：`extractors/timeline.py` / `policy_timeline.py`，新鲜度分层
- 回填：`scripts/resummarize.py` 幂等重算，摘要器改进后可安全重跑
- 体检：`scripts/data_health.py` 21 项指标 + 阈值，`--strict` 可作 CI 闸门
- 自我修正：`scripts/self_improve.py` 测量 → 应用 → 复测 → 保留/撤销，尝试历史写 `data/self_improve_log.jsonl`

### 6. 源注册

- 291 个政府源 + 9 个媒体源，`pending_review` 状态需探测通过才启用
- 栏目自动发现：从站点自身导航提取政策栏目 URL，146 个新栏目全部来自站点导航，无一猜测
- 分级开关生效验证：4 个特区源全部被丢弃，无泄漏

---

## 二、未实现

### 1. 采集器类型：代码齐备但零源在用

291 个源全部是 `access_method: static_html`，以下采集器**在生产中完全没有被使用**：

| 采集器 | 实现位置 | 状态 |
|---|---|---|
| 开放数据 API | `collectors/api/base.py` 通用引擎 + `api/open_data.py` 薄子类 | 已实现并有测试，0 源 |
| 法律法规库 API | 同上 + `api/legal_db.py` | 已实现，0 源 |
| 教育资源 API | 同上 + `api/education_api.py` | 已实现，0 源 |
| RSS | `collectors/rss/rss_collector.py` | 已实现，0 源（`rss_url` 全为空） |
| 附件/文件直下 | `collectors/download.py` | 已实现，0 源（`download_url` 全为空） |

API 三个 kind 共用 `api/base.py` 的通用 JSON 采集引擎（`_request_json` / `_items` / `_item_record`），子类只设 `api_kind`；`tests/test_api_collectors.py` 有覆盖。也就是说这条通路**可用但没人接**，不是没写。

### 2. 附件与 PDF 正文：完全没走通

- `storage/documents.py` 有下载 + robots 校验 + OCR 文本抽取（`ingest_public_document`），但只被 `DownloadCollector` 调用，而没有源使用它
- 实测库里 **附件类 URL（.pdf/.doc/.xls）记录为 0 条**
- 后果：大量政策正文实际在 PDF 附件里，这部分内容目前完全采不到

### 3. crawl4ai 在生产中未启用

- 76 个源配了 `render_mode: static_then_crawl4ai`，但 **enabled 的 50 个源里没有任何一个用 crawl4ai**（28 个 `static`、22 个未设置）
- 媒体源 9 个全部没有 `playwright_fallback`，即媒体站若为 JS 渲染则完全采不到

### 4. 专项（跨年+跨层级）比对：没有可用数据

- 检出政策专名 47 个，但 `cross_level_programs = 0`
- 原因：省份覆盖只有 6 个，同一专项无法在多层出现
- `policy_programs` 没有持久化表，专项结果目前只在内存里算

### 5. 县级/市级：注册了但没数据

- 市级 enabled 源仅 4 个，县级 1 个，且县级开关当前关闭
- 库内县级记录 5 条

### 6. 探测报告不产出中间结果

- `scripts/probe_sources.py` 全有全无：182 个源跑完才写一个 JSON，中途失败或被中断则一个结果都不留
- 本次已跑 1 小时以上、仍未落盘

### 7. 国务院政策库：未接入

- `sousuo.www.gov.cn/search-gov/data` 返回 200、`groupMap` 有约 6219 条统计，但 `listVO` 始终为空，疑前端签名/会话门控
- 浏览器渲染只能看到导航和 facet，未渲染出政策列表
- 按 §1.7 不重造签名、不绕过访问控制，因此未接入

---

## 三、要改进

### P0 — 影响数据可信度

**1. 列表页文本节点被当成正文（根因在采集，不在清洗）**

- `mee.gov.cn`：25/25 条摘要带导航残留，渠道面包屑 `要闻动态>公示公告>公示>公示` 与标题**无分隔符粘连**，正则切不断
- `moa_gov` 58%、`sc_samr_gov` 58%、`cnenergy` 80% 有同类残留
- `sc_mohurd_gov` 部分记录整篇是元数据标签簇（`索引号：主题分类：发布机构：…`），不是文章
- 已做的缓解：识别到纯导航正文时**置空摘要**（空摘要比导航栏摘要诚实），并记录 `summary_drop_reason: nav_chrome`
- 待做：列表页应抓详情页正文，而不是把列表页文本当详情；这是采集器改动，不是正则能解决的

**2. robots 拒绝导致 9 个省零覆盖，且不可绕过**

- 54 个源因 robots.txt 被拒（江西 7、重庆 7、湖南 6、山东 5、浙江 5、江苏 4…）
- 按 §1.7 不绕过，正确做法是**换该省其他允许的 URL**（政务服务网、政策发布栏目），而不是解封
- 9 个省当前完全没有 enabled 源：上海、内蒙古、吉林、山东、广西、江西、湖南、甘肃、贵州、辽宁、重庆、西藏、黑龙江

**3. 源分布严重倾斜**

- 四川一家占 63 个源（12 个 enabled），而省级 enabled 源共 39 个
- 后果：全国覆盖上不去，地图与分省统计不可用（`region_coverage_rate` 0.217）
- 待做：探测通过后**按省限额提升**，避免新发现的栏目继续加剧倾斜

### P1 — 影响历史完整性

**4. 近 10 年出现空档年份**

- `history_year_coverage` 0.3，缺 2017–2023
- 已验证：`archive_pages` 6→12、`max_items` 60→120 **均无改善**，瓶颈不在翻页深度
- 真正原因：很多源只提供"最新 N 条"，历史在年度归档栏目里，那些栏目 URL 尚未注册
- 待做：按年注册归档栏目源（`defect_registry.yaml` 的 `add_year_archive_sources` 卡在需要人工/Agent 确认 URL）

**5. 缺 `cross_level_programs` 与 `region_coverage` 的闭环**

- `defect_registry.yaml` 里这两项的修复都是 `kind: none` + `requires: source_expansion`，自我修正循环无法自动应用
- 循环已正确报告"没有可用修复"——它需要新的候选修复项，而不是继续空转

### P2 — 工程与可维护性

**6. 探测脚本需要中间结果与断点续跑**

- 建议：每源完成即追加写 JSONL，末尾再汇总；被中断也能保留已完成部分
- 建议：加 `--source` 批量与 `--status` 过滤，支持只探测某状态的源

**7. 媒体源无法渲染**

- 9 个媒体源全部没有 `playwright_fallback`
- 已停用 `xinhua_policy`（指向新华网时政频道，违反 §1.6），`gov_cn` 从 `/lianbo/`（要闻联播）改到 `/zhengce/`
- 待做：给媒体源加渲染能力，或只登记静态可采的媒体栏目

**8. 采集链路缺回归用的真实样本**

- 现有测试用的是合成样本，真实站点的畸形 HTML 没进 fixture
- 已把真实样本固化为回归测试：`tests/test_clean_summarize.py::RealStoreDefectTests` 覆盖 `mee` 面包屑粘连、政务详情页元数据串、`sc_mohurd` 标签簇、`cnenergy` 导航栏四类
- 待做：`mee`/`moa`/`sc_samr` 这类粘连仍靠"识别为脏就置空"兜住，采集器本体修好前，这些源的摘要会持续偏少

**9. 已修但值得记录的同类缺陷**

- `storage/models.py` 曾有整块重复类定义（`PolicyTopic` 等 5 个类各定义两次），导致所有 `import main` 崩溃
- `collectors/html_list.py` 曾有重复的 `_playwright` 方法（旧实现被桩覆盖，17 行死代码拖着 Playwright sync API 依赖）
- 两处都已清理。Python 静默保留最后一个定义，单测看不见这类问题——已补 `tests/test_no_duplicate_defs.py`（AST 扫描全项目重复定义 + 语法检查），防止同类编辑事故复发

---

## 四、当前体检数据（2026-09-26）

| 指标 | 值 | 阈值 | 状态 |
|---|---|---|---|
| median_content_chars | 804 | ≥800 | OK |
| under_200_chars_rate | 0.0465 | ≤0.10 | OK |
| chrome_residue_rate | 0.323 | ≤0.60 | OK |
| amount_rate | 0.225 | ≥0.15 | OK |
| date_rate | 0.561 | ≥0.45 | OK |
| summary_rate | 0.992 | ≥0.90 | OK |
| publish_date_rate | 0.995 | ≥0.90 | OK |
| section_page_rate | 0.0078 | ≤0.05 | OK |
| current_tier_rate | 0.979 | ≥0.10 | OK |
| summary_compression_high | 0.048 | ≤0.15 | OK |
| summary_breadcrumb_rate | 0.0 | ≤0.01 | OK |
| summary_toolbar_rate | 0.0 | ≤0.01 | OK |
| dropped_amount | 0.0026 | ≤0.02 | OK |
| **region_coverage_rate** | **0.217** | ≥0.50 | **FAIL** |
| **cross_level_programs** | **0** | ≥1 | **FAIL** |
| **history_year_coverage** | **0.30** | ≥0.50 | **FAIL** |

库内构成：387 条记录 / 全部有原文 URL / 附件类 URL 0 条 / 媒体源记录 15 条 / 含金额 31 条 / 有截止时间 14 条 / 交叉印证通过 5 条 / 层级 国家部委 283·省级 79·国家级 20·县级 5 / 省份覆盖 6 个

采集器与源相关计数：政府源 291（enabled 50）/ 媒体源 9（enabled 5）/ blocked 57 / pending_reprobe 36 / pending_review 146 / 其中 robots 拒绝 54

---

## 五、结论

采集器的**骨架和合规底线是可靠的**：robots 强制校验、域名白名单、worker 进程隔离、自适应降级、确定性抽取式摘要、指标化体检、自我修正闭环，都在真实数据上跑通并留下数字。

真正的短板不在采集代码，而在**源**：291 个源里只有 50 个在跑，附件/PDF 通路完全没接，历史年份靠"最新 N 条"拿不到，9 个省因 robots 拒绝而零覆盖。这三条直接导致 `region_coverage_rate`、`cross_level_programs`、`history_year_coverage` 三项不达标。

优先级：**先换源（合规前提下扩大省份与历史覆盖）→ 再接附件/PDF 通路 → 最后修列表页正文提取**。第一项已就绪（146 个栏目探测中），第三项是采集器本体的唯一硬伤。
