# extraction-eval

## 触发

修改 `extractors/gliner_extractor.py`、`config/schema.yaml`、正则回退、分类规则或模型版本时使用。

## 目标

用可复现样本评估字段准确率、JSON 成功率、needs_review 和耗时。

## 工作流

1. 读取 `AGENTS.md`、`config/schema.yaml` 和现有测试。
2. 使用脱敏真实公告样本或 `tests/fixtures/` 做固定输入。
3. 分别记录模型原始结果和来源权威字段补全后的结果。
4. 评估 title、issuing_body、gov_level、category_l1、admission_sub_category、金额、期限和证据字段。
5. 记录 JSON 解析成功率、字段准确率、平均耗时和失败样本。
6. 只生成报告，不把模型结果直接推送给用户。

## 招生专项

招生信息必须检查 `institution_name`、`institution_type`、`admission_type`、`enrollment_count`、`tuition_info` 和 `employment_guarantee`；非法子类或缺失院校类型必须标记 `needs_review=1`。

## 红线

不把来源元数据覆盖后的准确率冒充模型准确率，不用模型补造数字、机构、日期或就业承诺。
