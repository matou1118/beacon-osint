# 更新日志 / Changelog

本项目在 `v0.9.0-open` 之前使用内部版本号。本文件记录**灯塔采集链路**从首次通宵采集
到开源发布的完整演进。

Before `v0.9.0-open` this project used internal build numbers. This file records the
full evolution of the **lighthouse collection pipeline**, from the first overnight run
through the open-source release.

---

## v0.9.0-open — 2026-10-07 · 开源发布 / Open-source release

**首个公开版本。** 采集内核 + 只读前端界面，中英双语文档。

First public release. Collection core + read-only UI, bilingual documentation.

### 修复 / Fixes

这一轮集中修掉了多个「**报告可信度**」类缺陷——它们不报错，只让判断偏掉：

- **进程存活判定漏掉 `pythonw.exe`** — 无人值守链全由计划任务以 `pythonw` 拉起，
  按 `name='python.exe'` 查一个都看不见，于是「采集明明在跑」却报「watchdog 进程不在」，
  `--heal` 还会再拉一个 watchdog 抢锁。
- **时区基准错误导致时间数字全部虚高 8 小时** — `datetime.now()`（本地）减 UTC 时间戳。
  「零入库 157 小时」实际是 0.1 小时。共 4 处：`status_summary` 的「今日入库」按 UTC 日界切
  （本地早上入库的记录算到昨天，每天有 8 小时是错的）、`command_center` 的今日计数与 30 天趋势图、
  `reconcile_check._age_hours`。
- **`reconcile_check` 把「故意不翻的旧账」判成丢数据** — 拿 1 页列表的**总数**比库里**总数**。
  实测 `js_gxt` 报「列表 999 条、库内 484 条，丢了 515 条」，而位置 0..483 一条不差都在库里，
  515 条全在老尾。改为只看**头部**（列表页倒序，最新在最前），并把没入库的 URL 直接打出来自证。
- **`key_points` 列被两个产出方来回覆盖** — 采集写 `[summary]`（整块当一条），
  `resummarize.py` 写逐句列表，同一列两种形态互相覆盖，导致 196 条存着**过期摘要**。
- **新增导航页判据 `nav_page`** — 「联系我们」「网站地图」「关于我们」这类页面正文上千字
  （「网站地图」最长 11252 字），现有 5 条看正文的判据一个都拦不住，库内已有 50 条混入。
  判据按标题穷举词表 + 去站点后缀后完全相等，实测 50 条判死、0 误杀。

### 修复 / Fixes (continued)

- **目录型列表链接漏网** — 守卫只拦 `/index.html` 结尾，而政府站大量用裸目录做栏目地址
  （`/xxgk/tjsj/`、`/wzgl/rss/`），整批混进来当详情抓。A/B 实测：不加这道守卫，
  `sz_zjj` 真实公告入库 6 条变 0 条（目录链接挤占单源配额）。
- **列表藏在 `<script>` 里** — 浙大/南京系 CMS 的 `datastore` 模式把整段文章列表塞进
  `<script type="text/xml"><![CDATA[…]]>`，BeautifulSoup 按规范看不见。实测 `nhsa_gov` 页面
  原始 67 个 `<a href>` 只认出 21 个。修复后：`nhsa_gov` 21→46、`sd_swt` 4→49、`sd_gxt` 7→45、
  `sd_kjt` 12→75、`sd_fgw` 9→44、`sc_sd_gov` 6→46。
- **深翻游标** — `early stop after N known urls` 把「这个 URL 我已经有了」当成「这条线后面
  都不用看了」。列表页倒序，于是每轮重爬同一段已知内容、每轮在同一位置得出同一个结论。
  近 40 轮实测 **99% 的时间（4027/4087 秒）花在零产出访问上**。改为记住停在哪、下次从那里继续。

### 数据治理 / Data governance

- 空摘要 **1558 → 94**（10.3% → 0.62%），剩余 94 条全部带明确 `drop_reason`。
- `category_l2` 越界 **989 → 0**——根因是 `category_rules.yaml` 一直在产出
  `比选采购`/`课题承担`/`能源电力` 等 11 个子类，而权威表 `categories.yaml` 在一次搬迁中
  漏登记。后果比一级分类更隐蔽：一级照常能订阅，但订阅「能源电力」的用户一条都匹配不上。
  已补齐并加了 l2 守卫测试。
- `l2` 为空 **112 → 0**，`未分类` 归位。
- 附件下载率 **0.0175 → 0.0473**——`lighthouse` 链路把附件链接存进 `extra` 却从不下载。
- `publish_date` 覆盖率 **0.9392 → 0.9676**——重新抓页面补日期（存库正文被截断到几百字，
  日期在模板区被裁掉了）。
- `scripts/clean_category_enum.py` 加弃用标记：它按 AGENTS.md §4.1 的**旧 12 值枚举**清洗，
  跑一次会把 21 类塌成 12 类，不可逆毁掉分类粒度。

### 扩源 / Source expansion

- 新增 `scripts/probe_source_candidates.py`——**探列表页而非首页**。现有 `probe_candidates.py`
  探的是 `https://www.xxx.gov.cn/` 这类首页，它自己的 docstring 就承认「首页 200 不代表列表页有数据」。
- 候选池 77 个，通过率 13%；启用源 147 → 155。

---

## v1.4-winflags — 2026-09-29 · 全局窗口标志 / Global window flags

修桌面弹窗：把 `CREATE_NO_WINDOW` 提升为全局子进程策略（`lighthouse/winflags.py`），
不再在每个 spawn 点单独处理。

Fixed desktop popups by promoting `CREATE_NO_WINDOW` to a global subprocess policy
(`lighthouse/winflags.py`) instead of handling it at each spawn site.

## v1.3-nowin — 2026-09-29 · 渲染进程无窗口 / Headless render worker

修弹窗闪现：渲染 worker 进程也用 `CREATE_NO_WINDOW`。

Fixed popups flashing: the render worker process now also uses `CREATE_NO_WINDOW`.

## v1.2-schtasks — 2026-09-29 · 计划任务启动 / Scheduled-task startup

无人值守：消除子进程弹窗，改用 Windows 任务计划程序 + `pythonw` 启动。
15 分钟重复触发，重启后自动恢复采集。

Unattended operation: removed subprocess popups, switched to Windows Task Scheduler with
`pythonw`. A 15-minute repeating trigger restores collection after reboot.

## v1.1-detached — 2026-09-29 · 子进程脱离进程组 / Detached subprocesses

无人值守：每一级子进程都 `DETACHED_PROCESS`，关闭控制台时不会被一并杀死。

Unattended operation: every subprocess level detached so they survive console close.

## v1.0-lighthouse — 2026-09-29 · 灯塔采集链路 / The lighthouse collection pipeline

项目核心。**5 级采集路由 + 质量门禁 + 字段拼装摘要 + 无人值守自迭代。**

The core of the project.

- **5 级采集路由**（`lighthouse/routes.py`）：`static → render → markdown → pdf`，
  逐级升级而非一上来就用最贵的手段。
- **质量门禁**（`lighthouse/quality.py`）：`chrome` / `link_flood` / `toc_page` / `listing_page`
  / `short_but_clean`；区分「内容是垃圾」（值得升级渲染）与「内容好只是短」（**不要**升级）。
- **字段拼装摘要**（`lighthouse/quality.py:476`）：摘要前缀由原文事实字段拼成，
  不生成、不改写。
- **无人值守自迭代**（`watchdog.py` + `overnight.py`）：看门狗不设截止，一直守到被停掉。
- **退避与熔断**（`run.py:SourceBackoff`）：按源记录干 streak，阶梯退避，零产出自动熔断。

## v1.0-lighthouse-complete — 2026-09-29 · 备份工具 / Backup tooling

SQLite 在线备份（`Connection.backup()`，不阻塞写入）+ 源码打包 + 元数据。

Online SQLite backup (`Connection.backup()`, non-blocking for writers) + source packaging + metadata.

## collect-20260927 — 2026-09-27 · 首次通宵采集 / First overnight run

存档首夜采集结果与统计报告。库内量从 0 起跳到千级，其中 3487 条为单日首次全量灌库。

Archived the first overnight run's results and statistics. The database grew from zero
into the thousands, including a single-day initial bulk load of 3487 records.

> **一条仍然成立的经验 / A lesson that still holds**
> 10-03 单日 3487 条是**首次全量灌库**的产物，不可重复。此后每天的新增量只有几十到几百条。
> 把「日增量大」当成常态去调参，改的是数字，产出没变——只是把预算烧在已采干的源上。
>
> The 3487-record day was a **one-time initial bulk load** and is not repeatable. Steady-state
> daily intake afterwards is only tens to hundreds. Tuning parameters to chase "daily volume"
> changes the numbers, not the output — it just burns budget on drained sources.