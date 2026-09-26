# 对比工作台第一期设计

> 完整设计规范，详见项目 `docs/specs/2026-09-26-policy-compare-phase1-design.md`。

## 背景

现有工作台（Streamlit, localhost:8599）有 7 个页面。`policy_compare.py` 已是确定性对比引擎但仅为 CLI，未接入页面。本期把"主题 → 三级关联 → 字段对比 → 订阅通知"做成页面闭环。

## 复用与新增

**复用**：
- SQLite(SQLAlchemy)
- `Announcement` 现有结构化列（region_*/gov_level/amount/deadline/target_group/institution_name/key_points）
- `policy_themes.yaml` 18 个主题关键词
- APScheduler

**新增（M1 引擎层）**：
- `policy_fields.py`：规则字段提取
- `policy_relations.py`：主题↔公告关联构建
- `compare_engine.py`：对比引擎纯函数

**数据模型**：5 张新表（见 references/data-models.md）

## 页面（M2）

- `app.py`：扩展 NAV_VIEWS 和 NAV_ICONS
- `compare_view.py`：HTML 对比表，六种差异状态
- `admin_relations_view.py`：关联确认
- `admin_fields_view.py`：字段复核
- `subscription_view.py`：订阅中心

## 订阅通知（M3）

- `compare_digest.py`：每日对比新关联/字段变化 → 写 user_notices
- `scheduler.py`：挂每日任务（默认 8:00）

## 测试与验收

- 新增 `tests/test_policy_fields.py`、`tests/test_policy_relations.py`、`tests/test_compare_engine.py`
- 现有 140 测试保持全绿
- `python -m compileall` 通过
- 8599 服务 HTTP 200

## 二期不做

数据源管理、采集任务监控、邮件推送、对比历史快照、地区视图独立页。
