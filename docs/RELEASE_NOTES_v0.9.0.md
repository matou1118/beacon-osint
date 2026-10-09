# 灯塔 Beacon OSINT · v0.9.0-open

**首个公开版本 · First public release**

聚焦中国政务公共信息（Government Affairs Information）的采集、抽取与归档工具。
A collection, extraction and archiving toolkit for Chinese government-affairs information.

许可 / License：**非商用**（允许使用、修改、二次开发；禁止商业用途）
Non-commercial: use, modify and fork freely; **no commercial use**.

---

## 这个版本是什么 / What this release is

从国家部委、省级厅局到市级部门的公开栏目，持续抓取政策文件、通知公告、招标与补贴信息，
抽取结构化字段，生成可追溯摘要，归档成可检索、可比对的本地知识库。

Continuous collection from ministries, provincial departments and municipal bureaus:
extracts structured fields, produces traceable summaries, archives into a searchable
local knowledge base.

**它不做的事 / What it does not do**

不生成内容、不改写原文、不推测缺失字段。所有字段来自原文**带标签**的值；
摘要由原文事实字段拼装，不是模型改写。

It never generates content, never rewrites source text, never guesses missing fields.
Every field comes from a **labelled** value; summaries are assembled from factual fields.

---

## 核心设计 / Core design

**按成本逐级升级** —— 绝大多数政府站用静态 HTTP（~0.3 秒）就能拿到列表页；
只有当廉价路径**被证明走不通**，才升级到浏览器渲染（~10 秒）、Markdown 抽取（~30 秒）、
PDF 解析（~60 秒）。一上来就用最贵的手段是浪费，也对站点不友好。

Escalate by cost — only prove the cheap path fails before paying for the expensive one.

三条配套机制：

- **质量门禁** —— 区分「内容是垃圾」（值得升级渲染）与「内容好只是短」（**不要**升级）
- **深翻游标** —— 列表页倒序，最新在最前。记住 early stop 停在哪，下次从那里继续；
  已知 URL 用来**跳过**，不再用来**停止**
- **退避与熔断** —— 按源记录干 streak，阶梯退避，零产出自动熔断

---

## 本版本修复的缺陷 / Defects fixed in this release

集中修掉多个「**报告可信度**」类问题——它们不报错，只让判断偏掉。

- **进程存活判定漏掉 `pythonw.exe`** —— 无人值守链全由 `pythonw` 拉起，
  按 `python.exe` 查一个都看不见，于是「采集明明在跑」却报「已停」，`--heal` 还会再拉一个抢锁
- **时区基准错误，时间数字虚高 8 小时** —— 本地时间减 UTC 时间戳。「零入库 157 小时」
  实际是 0.1 小时。共 4 处
- **`reconcile_check` 把「故意不翻的旧账」判成丢数据** —— 拿 1 页总数比库里总数。
  实测某源报「丢了 515 条」，而位置 0..483 一条不差都在库里，515 条全在老尾
- **`key_points` 列被两个产出方来回覆盖** —— 采集写整块、`resummarize` 写逐句列表，
  导致 196 条存着**过期摘要**（摘要里的话在正文里找不到）
- **新增导航页判据 `nav_page`** —— 「联系我们」「网站地图」正文可达 11252 字，
  现有 5 条看正文的判据一个都拦不住
- **目录型列表链接漏网** —— 守卫只拦 `/index.html`，而政府站大量用裸目录做栏目地址。
  A/B 实测：不加这道守卫，某源真实公告入库 6 条变 0 条
- **列表藏在 `<script>` 里** —— CMS 的 `datastore` 模式把整段列表塞进
  `<script type="text/xml"><![CDATA[…]]>`，BeautifulSoup 按规范看不见。
  实测某源页面原始 67 个链接只认出 21 个
- **深翻游标** —— 原判据每轮重爬同一段已知内容。近 40 轮实测 **99% 的时间
  （4027/4087 秒）花在零产出访问上**

### 数据治理 / Data governance

| 指标 | 修前 | 修后 |
|---|---|---|
| `category_l2` 越界 | 989 (6.55%) | **0** |
| `category_l2` 为空 | 112 | **0** |
| 空摘要 `key_points=[]` | 1558 (10.3%) | **94 (0.62%)** |
| 附件下载率 | 0.0175 | **0.0473** |
| `publish_date` 覆盖率 | 0.9392 | **0.9676** |

`category_l2` 越界的根因是**规则表与权威表搬了一半**：`category_rules.yaml` 一直在产出
11 个子类，而 `categories.yaml` 漏登记。后果比一级分类更隐蔽——一级照常能订阅，
但订阅「能源电力」的用户一条都匹配不上。已补齐并加了 l2 守卫测试。

剩余 94 条空摘要全部带明确 `drop_reason`（`chrome_only` / `nav_chrome` / `empty_body`），
不再是有字段无内容的「假满」。

---

## 扩源 / Source expansion

- 新增 `scripts/probe_source_candidates.py` —— **探列表页而非首页**
  （现有脚本探的是首页，它自己的注释就承认「首页 200 不代表列表页有数据」）
- 候选池 77 个，通过率 13%
- 启用源 **147 → 155**，覆盖 33 个省级行政区
- 每个源都经过「探列表页 + 实跑抽取」双重验证才启用

---

## 目录 / Repository layout

```
collectors/     多来源采集器（HTML / API / RSS / 媒体）
lighthouse/     采集路由、质量门禁、字段抽取、退避熔断、无人值守调度
extractors/     正文抽取、摘要构建、附件下载、分类规则
storage/        SQLite 持久化、指纹去重
config/         sources.yaml（源清单）、categories.yaml（21 类 / 105 子类）
api/            FastAPI 只读接口
tests/          688 项测试
```

---

## 快速开始 / Quick start

```bash
git clone https://github.com/matou1118/beacon-osint.git
cd beacon-osint
pip install -r requirements.txt
playwright install chromium

streamlit run app.py                                    # 界面
python lighthouse/run.py --dry-run --sources moa_gov   # 试跑一轮采集
pytest tests lighthouse/tests                           # 688 项测试
```

---

## 平台 / Platform

Windows 为主要开发与运行环境（无人值守依赖任务计划程序与 `pythonw`）。
核心采集逻辑为纯 Python，**Linux/macOS 可运行**，但调度与进程组部分 Windows 专属。

---

## 数据与合规 / Data and compliance

- 本仓库**不包含**任何采集到的政务公告原文或数据库（见 `.gitignore`）
- 此类内容的著作权由**原始发布机关**决定，不适用本项目许可
- 使用者须自行遵守目标站点的 robots 协议、服务条款与适用法律
- 第三方组件（ECharts / three.js / 行政区划 GeoJSON）适用其各自许可，
  见 `THIRD_PARTY_NOTICES.md`。**注意**：GeoJSON 上游 README 提示商业部署需单独核实
  AntV L7 GISDATA 的数据条款——本项目采用非商用许可恰好规避该风险

---

**完整更新历史 / Full history**: [`CHANGELOG.md`](https://github.com/matou1118/beacon-osint/blob/master/CHANGELOG.md)
**工程约定与踩坑 / Engineering conventions**: [`AGENTS.md`](https://github.com/matou1118/beacon-osint/blob/master/AGENTS.md)