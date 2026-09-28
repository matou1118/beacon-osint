# 灯塔采集工具 lighthouse

5 级采集路由 + 正文质量门禁 + 字段拼装式摘要。读现有 `config/sources.yaml`，
产出对齐 `data/markdown/<source_id>/` 现有约定，**主项目零集成改动即可消费**。

## 它解决什么

对全库 2492 条记录实测，旧摘要的三个硬伤：

| 指标 | 旧 | 新 |
|---|---|---|
| 句中硬切（不在句号停） | 26.7% | **0.0%** |
| 表格竖线倒灌 | 9.8% | **0.5%** |
| 空摘要 | 49 | 8 |

病根是**选句**不是生成。`id=2961` 是《工作指引》+ 42 篇案例汇编共 71506 字，
旧摘要从第 40 篇里挑了「广外街道」「蓝梅采摘园 200 亩」，再把另一篇案例的
「268 万元」当成文件投资额拼在头部——句子全部忠于原文，但拼起来答非所问。

试过 TextRank 系（中心性）：案例汇编里中心性最高的是每篇都重复的样板句。
试过 20 行 Lead-3 + 标题锚定：4 条样本赢 2 平 1 输 1。**换算法不改变结论。**

所以摘要在这里被重新定义为**结构化字段 + 首段完整句的模板渲染**，三条性质由构造保证：
100% 落在终止标点、100% 抄原文不含目录/编号/表格行、0 依赖 0 算法。

代价要认：它是**名片**不是全文摘要。长文件靠 markdown 里的「关键要点」+ 完整原文承载。

## 路由阶梯

顺序即降级顺序，先命中先返回。**判据是正文质量，不是 HTTP 成功**——
政府栏目页返回 200 但正文是导航文字，是最常见的假成功。

| 级 | 实现 | 成本 |
|---|---|---|
| 1 rss | 源配了 `rss_url` 才走 | 1x |
| 1 api | 源配了 `list_endpoint` 且拿到 ≥3 条 | 1x |
| 2 static | httpx + trafilatura（主力） | 3x |
| 3 render | cloakbrowser + playwright（独立 worker 进程） | 30x |
| 4 markdown | crawl4ai fit_markdown（最后手段） | 50x |
| 5 pdf | PyMuPDF 抽附件 | 10x |

每级出的正文都过 `quality.judge()`：垃圾（chrome / 面包屑 / 目录页）就降级；
**短但干净直接接受，不升级**——渲染 30-50 倍成本也不会让一篇 300 字的完整通告变长。

## 一条命令的业务流

```bash
python lighthouse/run.py --all --limit 10 --max-minutes 25 --ingest --full
```

```
sources.yaml (145 源)
   ↓ 抢 collect.lock（和主项目通宵采集互斥）
   ↓ 每源翻 archive_pages 页、每源最多 max_items 条、单源 300 秒预算、整轮 25 分钟硬时限
列表页 → 详情 URL（栏目页守卫 / 翻页 / 早停）
   ↓
5 级降级阶梯（判据 = 正文质量，不是 HTTP 成功）
   ↓
quality.judge 门禁：不过就丢弃，理由进影子报告
   ↓
quality.build_summary  ← 字段拼装，不是摘要算法
   ├─→ data/markdown/<source_id>/*.md      给人看的交付物
   ├─→ data/runtime/lighthouse/collected-*.jsonl   机器读的旁路
   └─→ storage/db.py upsert → announcements 表   项目立刻可见
   ↓
scripts/pipeline.py --from refill   （--full：refill→amounts→classify→dedup→audit）
```

`--ingest` 是闭环的关键一环：`data/markdown/` 本身是**单向导出**，没有任何程序
把它读回数据库。不加 `--ingest`，工作台、对比、API 全都看不到新数据。

## 出差 / 无人值守

三条命令。`start` 一条就够，另外两条是查看和停止。

```powershell
# 起（看门狗会自动拉起调度器，调度器崩了也拉回来）
python lighthouse\watchdog.py --rounds 16 --minutes 30 --deadline 07:30

# 看（一行摘要）
python lighthouse\status.py

# 看详细（逐轮入库数、自迭代修了什么）
python lighthouse\status.py --full

# 停（当前轮跑完才退出，不丢已采到的数据）
python lighthouse\status.py --stop
python lighthouse\watchdog.py --stop
```

进程结构：

```
watchdog.py          只做三件事：spawn、探活、记日志
  └─ overnight.py    轮次循环 + 自迭代
       └─ run.py     一轮采集
            ├─ repair.py    从真实链接反推 detail_pattern
            └─ promote.py   实测能采到 → 恢复 enabled
```

**日志**（都在 `data/runtime/lighthouse/`）：

| 文件 | 内容 |
|---|---|
| `overnight.log` | 每轮的完整输出 |
| `watchdog.log` | 拉起/重启记录 |
| `status.json` | 机器可读状态，`status.py` 读它 |
| `shadow-*.json` | 每轮的逐源结果 + 路由分布 + 丢弃原因 |
| `collected-*.jsonl` | 采集到的记录，入库的数据源 |

**自迭代做了什么、不会做什么**

每轮结束后自动跑一次，只做有证据的动作：

- `no_links` 的源 → `repair.py` 反推 `detail_pattern` → 立刻复采验证 →
  采到数据才 `promote.py` 恢复 `enabled`
- 三道闸：推导形态必须与列表页**不同形**、**≥5 条链接**、**至少两段路径**；
  `repair` 不改人工登记字段；`promote` 只认实测报告
- 每步都备份 `sources.yaml`，全程行级手术（145 个人工字段零丢失）
- 推不出来的源保持原样，不猜

**什么时候会自己停**

- 到达 `--deadline`
- 连续两轮零入库（源采空了，继续空转没意义）
- 收到 `--stop`

## 跑法

```bash
# 先 dry-run 看报告，不写 markdown
python lighthouse/run.py --all --limit 20 --dry-run

# 正式采 + 直接入库
python lighthouse/run.py --all --limit 10 --max-minutes 25 --ingest

# 重新过一遍新门禁（已入库的 URL 会全部被跳掉，必须加 --fresh）
python lighthouse/run.py --all --limit 20 --fresh --ingest

# 单源（逗号分隔或重复 --source 都行）
python lighthouse/run.py --source mof_gov,mee_gov --limit 5

# 复探被 robots/WAF 挡住的源
python lighthouse/run.py --status blocked,pending_reprobe --limit 3 --dry-run

# 用影子报告挑出「复探成功」的源，改回 enabled（保注释的行级手术）
python lighthouse/promote.py --from-report --apply

# 全库新旧摘要 A/B；--apply 把新摘要写回全部记录（自动备份）
python lighthouse/bench.py
python lighthouse/bench.py --apply

# 单独入库（不重新采集）
python lighthouse/ingest.py --dry-run
python lighthouse/ingest.py
```

抢主项目同一把锁 `data/runtime/collect.lock`。通宵采集正在跑时本工具直接退出，
不会和它并行写库。

## 输出

* `data/markdown/<source_id>/<source_id>_<hash>.md` —— 完整 markdown，含规则摘要、
  关键要点、结构化字段表、原文
* `data/markdown/<source_id>/index.md` —— 该源索引
* `data/runtime/lighthouse/shadow-<时间戳>.json` —— 影子报告：路由分布、丢弃原因分布、
  **每条被丢的记录试过哪一级、每一级为什么没过**

## 验收

```bash
python -m pytest lighthouse/tests -q     # 9 项
python lighthouse/bench.py               # 新旧全库 A/B
```

## 依赖说明

* **已装即可**：`crawl4ai` `cloakbrowser` `trafilatura` `httpx` `pymupdf` `pyyaml`
  （都在主项目 requirements.txt 里）
* **本机需执行过一次**：`python -m playwright install chromium`

  没装的话第 4 级（crawl4ai）会抛
  `Executable doesn't exist at .../ms-playwright/chromium_headless_shell-...`。
  实测本机 `ms-playwright` 目录原本是空的（只有 `.links`），这就是 24 个源被标
  blocked 的真凶——不是站点拦，是我们自己的浏览器没装。

## 文件

```
lighthouse/
  run.py                 CLI 入口 + 进度（心跳/ETA/在跑哪些源）+ 逐源分类 + 编排
  routes.py              5 级路由阶梯 + 串行渲染锁 + 容错列表采集器
  quality.py             质量门禁 + 字段抽取 + 摘要（散文/表格两条路）
  ingest.py              旁路 JSONL → announcements 表
  promote.py             源状态恢复（行级手术，保人工登记字段）
  repair.py              从真实链接反推 detail_pattern（自迭代用）
  bench.py               新旧摘要全库 A/B；--apply 写回
  report.py              多轮统计总结
  status.py              无人值守状态查询 / 优雅停止
  overnight.py           轮次循环 + 自迭代
  watchdog.py            调度器看门狗
  overnight.ps1          旧的 PowerShell 驱动（无自迭代，留作参考）
  config/web_collector_config.yaml    全部可调参数，改这里不改代码
  tests/test_lighthouse.py            最小可运行检查
```

传输层（HTTP / robots.txt / 限速 / cloakbrowser worker / crawl4ai 适配）复用
`collectors/`，不复制。复制一份等于制造分叉，未来的 bug 要修两遍。
