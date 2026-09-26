# 对比工作台第一期设计（8599 工作台功能改造）

日期：2026-09-26 · 状态：已确认（用户选定"对比工作台优先"）

## 背景

现有工作台（Streamlit, localhost:8599）有 7 个页面（总览/中国地图/公告检索/来源健康/知识图谱/热频话题/质量与备份）。`policy_compare.py` 已是确定性对比引擎但仅为 CLI，未接入页面。本期把"主题 → 三级关联 → 字段对比 → 订阅通知"做成页面闭环。

## 复用与新增

复用：SQLite(SQLAlchemy)、`Announcement` 现有结构化列（region_*/gov_level/amount/deadline/target_group/institution_name/key_points）、`policy_themes.yaml` 18 个主题关键词、`policy_compare.py` 的打标与金额规则、APScheduler。

新增（M1 引擎层，平铺根目录，遵循项目现有风格）：
- `policy_fields.py`：规则字段提取（正则+关键词窗口，method=rule，置信度 0-100）
- `policy_relations.py`：主题↔公告关联构建（标题关键词加权 + 文号/正文命中），置信度 ≥90 自动确认 / 60~90 待审 / <60 丢弃
- `compare_engine.py`：对比引擎纯函数，输出六种差异状态

数据模型（`storage/models.py` 追加 5 表，`Base.metadata.create_all` 自动建表）：
| 表 | 用途 | 关键列 |
|---|---|---|
| policy_topics | 对比主题 | name(唯一)/category/keywords(JSON)/field_schema(JSON)/status |
| topic_doc_links | 主题↔公告三级关联 | topic_id/announcement_id/gov_level/confidence/status(auto/pending/confirmed/rejected)/evidence(JSON) |
| field_extractions | 字段提取留痕 | announcement_id/topic_id/field_key/value/confidence/evidence_text/method/status(auto/pending/revised)/revised_value |
| topic_subscriptions | 主题订阅 | user_id/topic_id/regions(JSON)/enabled |
| user_notices | 站内通知 | user_id/topic_id/notice_type(new_region/update/revoke/field_change)/message/read |

种子主题与 Announcement 列天然映射的通用 Schema：subsidy_amount(补贴标准←amount+正文)、target_group(申报条件←target_group+正文)、deadline(截止时间←deadline)、institution(责任机构←institution_name)、key_measures(关键举措←key_points)、region(地区←region_province)。

## 页面（M2）

`app.py`：`NAV_VIEWS` 追加「对比工作台/关联确认/字段复核/订阅中心」，`NAV_ICONS` 补 比/联/核/订；顺手删除重复的 NAV_ICONS 定义（app.py 1267 与 1278 重复）；`main()` 分发。

- `compare_view.py`：主题选择 + 地区多选 → HTML 对比表（st.markdown unsafe_allow_html），差异琥珀/未出台灰斜体/待确认蓝虚线/已废止划线/附加条件紫角标/一致折叠（st.expander）；差异统计条；导出 CSV/JSON（st.download_button）
- `policy_admin.py`：关联确认（pending 列表+置信度条+确认/驳回）；字段复核（待复核字段+原文摘录+修正提交）
- `subscription_view.py`：订阅表单 + 列表 + 未读通知

## 订阅通知（M3）

`subscription_engine.py`：每日对比新关联/字段变化 → 写 user_notices；scheduler.py 挂每日任务；对比工作台顶栏显示未读角标。

## 测试与验收

- 新增 `tests/test_policy_fields.py`、`tests/test_policy_relations.py`、`tests/test_compare_engine.py`
- 现有 140 测试保持全绿；`python -m compileall` 通过；8599 服务 HTTP 200
- 验收：对比工作台选主题出对比表、六态齐现；关联/字段可确认修正；订阅后生成通知

## 二期不做

数据源管理、采集任务监控、邮件推送、对比历史快照、地区视图独立页。
