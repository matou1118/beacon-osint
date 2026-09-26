---
name: policy-compare-workbench
description: |
  在现有 Streamlit 工作台上添加政策对比工作台，包含主题关联、字段对比、订阅通知。
  触发词：政策对比、对比工作台、字段复核、关联确认、订阅中心、政策订阅、政策通知。
allowed-tools: Bash(python:*) read write edit glob grep
---

# policy-compare-workbench

> 在 8599 Streamlit 工作台上做"主题 → 三级关联 → 字段对比 → 订阅通知"闭环。

## 核心原则

1. **复用优先** — 用现有 SQLite(SQLAlchemy)、`Announcement` 结构化列、`policy_themes.yaml` 18 个主题关键词、APScheduler，不重复造轮子
2. **最小充分架构** — 5 张新表 + 4 个新页面 + 1 个每日通知任务，不多不少
3. **幂等可回填** — 种子主题、关联回填、字段提取都是幂等操作，可重复执行
4. **测试保绿** — 现有 140 测试保持全绿，新增测试覆盖引擎/页面/调度

## 工作流

### Phase 1: 数据模型 + 引擎

```text
Step 1 — 在 storage/models.py 追加 5 张表（PolicyTopic, TopicDocLink, FieldExtraction, TopicSubscription, UserNotice）
Step 2 — 运行 Base.metadata.create_all 建表
Step 3 — 创建 policy_fields.py：规则字段提取（正则+关键词窗口，method=rule，置信度 0-100）
Step 4 — 创建 policy_relations.py：主题↔公告关联构建（标题关键词加权 + 正文命中，≥90 自动 / 60~90 待审 / <60 丢弃）
Step 5 — 创建 compare_engine.py：对比引擎纯函数，输出六种差异状态
Step 6 — 创建 seed_compare.py：种子主题 + 存量公告回填
```

### Phase 2: 页面

```text
Step 7 — 在 app.py 扩展 NAV_VIEWS 和 NAV_ICONS，加"对比工作台/关联确认/字段复核/订阅中心"
Step 8 — 创建 compare_view.py：主题选择 + 地区多选 → HTML 对比表，差异琥珀/未出台灰斜体/待确认蓝虚线/已废止划线/附加条件紫角标/一致折叠
Step 9 — 创建 admin_relations_view.py：关联确认（pending 列表+置信度条+确认/驳回）
Step 10 — 创建 admin_fields_view.py：字段复核（待复核字段+原文摘录+修正提交）
Step 11 — 创建 subscription_view.py：订阅表单 + 列表 + 未读通知
```

### Phase 3: 订阅通知

```text
Step 12 — 创建 compare_digest.py：每日对比新关联/字段变化 → 写 user_notices
Step 13 — 在 scheduler.py 挂每日任务（默认 8:00）
Step 14 — 在 app.py 顶栏显示未读角标
```

### Phase 4: 验证

```text
Step 15 — 运行 python -m compileall 编译
Step 16 — 运行全量 pytest，确保 140+ 测试全绿
Step 17 — 启动 8599 服务，HTTP 200 检查
```

## 决策规则

| 任务类型 | 执行方式 |
|----------|----------|
| 数据模型设计 | 按 references/data-models.md 追加 5 表，不修改现有表 |
| 字段提取 | 正则 + 关键词窗口，method=rule，不引入 ML 模型 |
| 关联构建 | 标题关键词加权 + 正文命中，阈值 90/60 可配置 |
| 页面渲染 | st.markdown(unsafe_allow_html) 渲染 HTML 对比表，st.expander 折叠一致项 |
| 通知 | 站内通知（user_notices），邮件留二期 |
| 调度 | APScheduler CronTrigger 每日 8:00，配置项 digest_hour/digest_minute 可调 |

## 数据模型

| 表 | 用途 | 关键列 |
|---|---|---|
| policy_topics | 对比主题 | name(唯一)/category/keywords(JSON)/field_schema(JSON)/status |
| topic_doc_links | 主题↔公告三级关联 | topic_id/announcement_id/gov_level/confidence/status(auto/pending/confirmed/rejected)/evidence(JSON) |
| field_extractions | 字段提取留痕 | announcement_id/topic_id/field_key/value/confidence/evidence_text/method/status(auto/pending/revised)/revised_value |
| topic_subscriptions | 主题订阅 | user_id/topic_id/regions(JSON)/enabled |
| user_notices | 站内通知 | user_id/topic_id/notice_type(new_region/update/revoke/field_change)/message/read |

## 安全约束

以下操作**禁止**：
- 修改现有 Announcement 表结构
- 在 seed_compare.py 中硬编码生产数据
- 在页面中执行危险操作（rm -rf、格式化等）

以下操作**优先**：
- 复用现有列（region_*/gov_level/amount/deadline/target_group/institution_name/key_points）
- 幂等操作（可重复执行不重复插入）
- 测试覆盖所有引擎函数

## 资源导航

| 资源 | 路径 | 用途 |
|------|------|------|
| 设计文档 | references/design.md | 完整设计规范 |
| 数据模型 | references/data-models.md | 5 张新表 schema |
| 种子主题 | references/seed-topics.md | 种子主题 + 字段 schema |
| 页面模板 | assets/page-templates/ | 4 个页面模板 |
