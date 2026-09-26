---
name: beacon-evolve
description: 灯塔项目（BeaconOSINT）的采集→清洗→摘要→时间线自我修正工作流。当需要排查采集失败、提升数据质量、扩展专项对比、让规则自我迭代，或出现"测试通过但数据不可用"时使用。基于 data_health.py 度量、defect_registry.yaml 修复、self_improve.py 自动验证，不凭感觉调参。
---

# 灯塔规则自我修正

## 何时用

- 采集量上不去、某个源反复失败
- 摘要/分类/字段抽取质量下降
- 需要扩展省/市/县源或做专项对比
- 单元测试全过但数据实际不可用（本项目踩过的坑）

## 铁律

**单元测试通过 ≠ 数据可用。** 本项目 122 项测试全绿时，中位正文只有 298 字、58 条不足 100 字——清洗层把 807 字正文截成 37 字。

每次汇报前必须跑 `python scripts/data_health.py`，并明确报告 12 项指标。**不达标就直说**，不要用"测试通过"代替。

## 工作流

### 1. 先度量，不猜

```bash
python scripts/data_health.py            # 12 项指标 + 数据分层 + 缺失年份
python scripts/data_health.py --json     # 机器可读
```

`summary_audit_error` / `program_extract_error` / `timeline_error` 非空说明某个抽取器抛异常被吞了——**先修它，指标才有意义**。

### 2. 定位归因，别把工程缺陷当技术壁垒

参考本项目的教训：145 个省源探测失败时，我最初报"85 个被反爬挡住"，实际归因是

| 真实原因 | 数量 |
|---|---|
| Playwright sync/async 冲突（我的 bug） | 46 |
| 探测器只试首条链接误判（我的 bug） | 40 |
| 站点真反爬 412 | **6** |

**遇到大面积失败，先分清"谁的锅"再决定对策。**

### 3. 修完跑自动验证

```bash
python scripts/self_improve.py --status     # 待处理队列
python scripts/self_improve.py --iterate    # 试一个修复：应用→复测→保留或撤销
```

引擎会：选一个超标指标 → 取未用过的修复 → 应用 → 全量复测 → 目标指标改善**且**无其他指标退化才保留。尝试历史记在 `data/self_improve_log.jsonl`。

引擎的"撤销"结论往往是真信息。例如"archive_pages 6→12 无改善"证明瓶颈不在翻页深度。

### 4. 记录新缺陷

发现新的可测量缺陷时，加到 `config/defect_registry.yaml`：

```yaml
  - id: <缺陷名>
    metric: <必须是 data_health.THRESHOLDS 里的指标>
    severity: high|medium|low
    description: <一句话>
    fixes:
      - id: <修复名>
        apply:
          kind: set_constant | scale_config | none
          # set_constant: target 用斜杠（extractors/x.py），name 必须真实存在
```

`tests/test_self_improve.py` 会校验 metric 存在、fix 合法、目标常量存在——**改 registry 后必须跑这个测试**。

⚠️ **不要用 `yaml.safe_dump` 回写 YAML 配置**，会重排并破坏结构。用编辑工具直接改。

## 本项目已踩过的坑（别重犯）

| 坑 | 症状 | 正确做法 |
|---|---|---|
| 无条件按页脚截断字体切换器 | 807 字正文 → 37 字 | 位置感知：<1/3 取后，>2/3 取前 |
| 页头/页尾同形标签（`网站地图`）首次出现就截 | 27 条正文被砍 | 取尾部区的最后一个 |
| `分享到：` 在标题下、正文上 | 第一财经 1680 字被砍 | 分享类仅尾部区截断 |
| 按 `、` 切分 | "各省、自治区"被切三段，条款腰斩 | 顿号不是句末标点 |
| 压缩时从尾部 pop | 金额和截止日期被删 | 删最弱句 + 保护高信号句 |
| 只认 `当前位置`面包屑 | 新华网 `首页 > 新闻` 全被当页脚 | 多形态面包屑 |
| `build_summary(text, 'title')` 把 title 传给 limit | 选句逻辑静默失效 | 关键字参数 |
| 迭代引擎把目标指标算成"回归" | 有效修复被自我否决 | 排除正在优化的指标 |
| Playwright sync 与 crawl4ai asyncio 同进程 | 46 源报 "Sync API inside asyncio loop" | worker 进程隔离 |
| 栏目页判死整源 | 40 源误判（首页链接排首位） | 专用异常 + 试多个候选 |

## 关键文件

| 文件 | 作用 |
|---|---|
| `scripts/data_health.py` | 12 项指标体检，exit 1 on fail |
| `scripts/self_improve.py` | 自我迭代引擎（应用→验证→保留/撤销） |
| `config/defect_registry.yaml` | 缺陷登记表（修复库） |
| `extractors/clean_summarize.py` | 位置感知清洗 |
| `extractors/summary_refine.py` | 摘要自我修正（6 类缺陷 + audit） |
| `extractors/program_extract.py` | 专项名抽取（跨年+跨层判定） |
| `extractors/timeline.py` | 新鲜度分层 + 时间线 |
| `policy_timeline.py` | 时间线 CLI |
| `collectors/playwright_worker.py` | 渲染 worker（含分页） |

## 边界

- **不装任何外部采集工具**（CloakBrowser / Firecrawl / XCrawl）。AGENTS.md §1.7：长期公共产品不走规避手段，信任靠数据交叉印证。
- **摘要必须是原文逐字抽取**，不得改写、不得用模型。AGENTS.md §1.6。
- 改 AGENTS.md 需要项目负责人点名条款，Agent 不得自行放宽（§1 维护方式）。
