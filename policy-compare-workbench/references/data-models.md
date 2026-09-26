# 数据模型（5 张新表）

> 追加到 `storage/models.py`，`Base.metadata.create_all` 自动建表。

## PolicyTopic

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | Integer | PK, autoincrement | 主键 |
| name | String(200) | NOT NULL, UNIQUE | 主题名称 |
| category | String(100) | | 分类（如"民政保障"） |
| keywords | JSON | | 关键词列表（如 ["灵活就业", "社保补贴"]） |
| field_schema | JSON | | 字段 schema（如 [{"field_key": "subsidy_amount", "label": "补贴标准"}]） |
| status | String(20) | DEFAULT 'active' | active / archived |
| created_at | DateTime | DEFAULT now() | 创建时间 |
| updated_at | DateTime | DEFAULT now() on update | 更新时间 |

## TopicDocLink

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | Integer | PK, autoincrement | 主键 |
| topic_id | Integer | FK, NOT NULL | 关联主题 |
| announcement_id | Integer | FK, NOT NULL | 关联公告 |
| gov_level | String(50) | | 政府层级（如"国家部委"） |
| confidence | Integer | NOT NULL | 置信度 0-100 |
| status | String(20) | NOT NULL | auto / pending / confirmed / rejected |
| evidence | JSON | | 证据（如 {"title_hits": [...], "body_hits": [...]}） |
| created_at | DateTime | DEFAULT now() | 创建时间 |
| updated_at | DateTime | DEFAULT now() on update | 更新时间（用于 digest 水印） |

**唯一约束**：`(topic_id, announcement_id)`

## FieldExtraction

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | Integer | PK, autoincrement | 主键 |
| announcement_id | Integer | FK, NOT NULL | 关联公告 |
| topic_id | Integer | FK, NOT NULL | 关联主题 |
| field_key | String(100) | NOT NULL | 字段 key（如 "subsidy_amount"） |
| value | Text | | 提取值 |
| confidence | Integer | | 置信度 0-100 |
| evidence_text | Text | | 证据文本（原文摘录） |
| method | String(20) | DEFAULT 'rule' | 提取方法（rule / manual） |
| status | String(20) | DEFAULT 'pending' | auto / pending / revised |
| revised_value | Text | | 人工修正值 |
| created_at | DateTime | DEFAULT now() | 创建时间 |
| updated_at | DateTime | DEFAULT now() on update | 更新时间 |

**唯一约束**：`(announcement_id, topic_id, field_key)`

## TopicSubscription

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | Integer | PK, autoincrement | 主键 |
| user_id | String(100) | NOT NULL | 用户 ID（如 "local"） |
| topic_id | Integer | FK, NOT NULL | 关联主题 |
| regions | JSON | | 关注地区列表（如 ["北京", "上海"]） |
| enabled | Boolean | DEFAULT true | 是否启用 |
| created_at | DateTime | DEFAULT now() | 创建时间 |

**唯一约束**：`(user_id, topic_id)`

## UserNotice

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | Integer | PK, autoincrement | 主键 |
| user_id | String(100) | NOT NULL | 用户 ID |
| topic_id | Integer | FK | 关联主题（可空，系统通知） |
| notice_type | String(50) | NOT NULL | 通知类型（new_links / field_changes / digest） |
| message | Text | | 通知内容 |
| read | Boolean | DEFAULT false | 是否已读 |
| created_at | DateTime | DEFAULT now() | 创建时间 |

**索引**：`(user_id, read, created_at)`
