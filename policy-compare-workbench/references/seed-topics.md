# 种子主题 + 字段 Schema

> 种子主题与 Announcement 列天然映射的通用 Schema。

## 种子主题：灵活就业人员社保补贴

> **注意**：`field_schema` 应与 `policy_fields.py` 中的 `FIELD_SCHEMA` 保持一致，确保字段提取引擎与页面展示的字段定义统一。

```json
{
  "name": "灵活就业人员社保补贴",
  "category": "民政保障",
  "keywords": ["灵活就业", "社保补贴", "社会保险补贴", "灵活就业人员", "就业困难人员", "4050"],
  "field_schema": [
    {"field_key": "subsidy_amount", "label": "补贴标准"},
    {"field_key": "target_group", "label": "申报条件"},
    {"field_key": "deadline", "label": "申报截止"},
    {"field_key": "institution", "label": "责任机构"},
    {"field_key": "region", "label": "所属地区"},
    {"field_key": "key_measures", "label": "关键举措"}
  ]
}
```

## 字段与 Announcement 列映射

| field_key | 来源 | 说明 |
|---|---|---|
| subsidy_amount | amount + 正文 | 补贴标准 |
| target_group | target_group + 正文 | 申报条件 |
| deadline | deadline | 截止时间 |
| institution | institution_name | 责任机构 |
| region | region_province | 所属地区 |
| key_measures | key_points | 关键举措 |

## 关联规则

| 规则 | 权重 | 说明 |
|---|---|---|
| 标题关键词命中 | 30-60 | 每命中一个关键词 +30，最高 60 |
| 正文关键词命中 | 15-30 | 每命中一个关键词 +15，最高 30 |
| 分类匹配 | 10 | 分类一致 +10 |
| 政府层级加成 | 5 | 国家部委/国家级 +5 |

**阈值**：
- ≥90：自动确认（auto）
- 60-89：待审（pending）
- <60：丢弃
