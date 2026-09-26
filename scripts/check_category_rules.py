from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from extractors.category_rules import classify, load_category_rules
from extractors.regex_fallback import RegexFallbackExtractor


def main() -> int:
    rules = load_category_rules()
    cases = [
        ({"title": "关于2026年高职单招报名工作通知"}, "招生信息", "高职单招"),
        ({"title": "军队院校招生政治考核工作公告"}, "军队招录", "军队院校招生"),
        ({"title": "2026年度人民警察招录公告"}, "公务员考试", "警察招录"),
        ({"title": "省市场监管局关于开展产品质量监督抽查的通告"}, "行业政策", "监管政策"),
        ({"title": "市人社局事业单位公开招聘工作人员公告"}, "公招信息", "事业单位招聘"),
        ({"title": "四川省2026年普通高等学校招生政策解读"}, "招生信息", "招生政策解读"),
        ({"title": "关于印发养老服务补贴发放办法的通知"}, "养老与社区", "高龄津贴"),
        ({"title": "某单位食堂食材采购项目竞争性磋商公告"}, "招标信息", "货物采购"),
        ({"title": "关于开展适老化改造试点工作的通知"}, "养老与社区", "适老化改造"),
        ({"title": "2026年省级重点项目申报通知"}, "商业机会", "项目申报"),
        ({"title": "关于举办第十八届中国国际博览会的公告"}, "商业机会", "展会信息"),
        ({"title": "关于开展招商引资项目合作的通知"}, "商业机会", "招商引资"),
        ({"title": "四川省2026年度考试录用公务员公告"}, "公务员考试", "省考招录"),
        ({"title": "市人社局事业单位公开招聘工作人员公告"}, "公招信息", "事业单位招聘"),
        ({"title": "某企业面向社会招工岗位公告"}, "招聘信息", "招聘信息"),
        ({"title": "会议通知"}, "其他", "未分类"),
    ]
    failures = 0
    for record, expected_l1, expected_l2 in cases:
        got = classify(record, rules)
        ok = got["category_l1"] == expected_l1 and got["category_l2"] == expected_l2
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {record['title'][:24]:<26s} -> {got['category_l1']}/{got['category_l2']}")
    html = '<html><head><title>省市场监管局关于开展产品质量监督抽查的通告</title></head><body>2026年9月25日 产品质量 监督抽查 通告</body></html>'
    extracted = RegexFallbackExtractor().extract(html, source={"name": "省市场监管局", "gov_level": "省级"})
    assert extracted["category_l1"] == "行业政策", extracted["category_l1"]
    assert extracted["category_l2"] == "监管政策", extracted["category_l2"]
    print("ok   regex_fallback 下钻分类")
    print("FAIL" if failures else "all passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
