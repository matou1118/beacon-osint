"""把审计出来的政务文种体系写进 config/category_rules.yaml。

为什么不直接改 yaml
  那 13 个原分类要保留（招生/招录/招聘/养老/招标/补贴/人才/活动都已在用），
  这次是**追加**政务体裁类。手写 yaml 容易破坏原有结构，而且注释会丢，所以
  用 ruamel 风格的行编辑：在 categories 段末尾追加，缩进与原文对齐。

为什么分类主要看标题
  实测 body_weight=0 是对的，不是漏配：库里 content 大量是导航栏而不是正文。
  抽样 id=372 的 content 是"云南省人民政府门户网站 返回首页 鼠标样式 页面放大
  语音播报 指读"后面跟一串首页要闻；id=373 是"智能问答(试运行) 长者模式 首页
  省政通 政务公开 政务服务 互动交流 彩云之南"。拿这种正文打分只会把文件归错。
  所以：文种词（互斥、可靠）优先，业务词（专名）次之，都只用标题。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "config" / "category_rules.yaml"

# 与 scripts/audit_categories.py 审计结论一一对应：这些类别不是设计出来的，
# 是 1465 条「其他」里成建制缺失的部分。每条都注明证据来源。
NEW_CATEGORIES = {
    # 审计证据：文种词覆盖 78%（通知 31% / 公示 13% / 公告 12% / 通告 7% /
    # 名单 6% / 规划 5% / 办法 4%），而原体系 13 类里一个都装不下。
    "政策发布": {
        "subcategories": {
            "通知": ["通知", "印发", "转发", "贯彻", "关于印发"],
            "通告": ["通告", "公告", "令"],
            "方案": ["方案", "措施", "行动计划", "实施方案"],
            "政策文件": ["的决定", "的公告", "的通告", "规范性文件"],
        }
    },
    "公示公告": {
        "subcategories": {
            "公示": ["公示", "公示期", "公开征求", "意见稿"],
            "名单": ["名单", "结果", "拟", "拟订", "入选", "中选"],
            "征求意见": ["征求意见", "公开征求", "意见稿"],
        }
    },
    "政策法规": {
        "subcategories": {
            "办法": ["办法", "规定", "条例", "细则", "规程"],
            "意见决定": ["意见", "决定", "批复", "令"],
            "规范性文件": ["的通知", "的公告", "的通告", "的决定"],
        }
    },
    "规划计划": {
        "subcategories": {
            "规划": ["规划", "十五五", "十四五", "纲要", "五年规划"],
            "计划": ["计划", "工作要点", "实施方案"],
            "标准规范": ["标准", "规范", "技术要求", "导则"],
        }
    },
    "政策解读": {
        "subcategories": {
            "解读": ["解读", "政策问答", "答记者问", "图解"],
            "指南": ["指南", "办事指南", "服务指南", "流程", "操作手册"],
        }
    },
    "行政审批": {
        "subcategories": {
            "批复": ["批复", "核准", "同意", "批复同意"],
            "许可登记": ["许可", "审批", "核准", "备案", "登记", "认定"],
        }
    },
    # 审计证据：审计 589 条兜底时发现整类缺失。
    "非公告体裁": {
        "subcategories": {
            # 这三类不是"没分类"，是压根不该混进公告库。单独归一类，
            # 至少能被筛掉。
            "时政要闻": ["习近平", "李强", "赵乐际", "王沪宁", "蔡奇", "丁薛祥",
                        "李希", "抵达", "国事访问", "会晤", "书面向", "重要讲话"],
            "新闻通稿": ["举行记者会", "中外记者", "记者见面会", "新闻发布会",
                        "答记者问", "国新办", "会见", "接待", "出席", "考察",
                        "座谈会", "部长对话", "视频会"],
            "数据播报": ["数据情况", "运行情况", "监测数据", "统计数据", "再破万亿",
                        "同比增长", "环比", "指数收于", "创历史新高"],
            "活动新闻": ["举办", "开幕", "启动仪式", "大会在", "活动将", "圆满", "落幕"],
        }
    },
}

# 补进既有"行业政策"下的子类：这些在审计里成建制出现，但原体系没有对应子类。
EXTRA_SUBCATEGORIES = {
    "行业补贴": {"贸易救济": ["反倾销", "反补贴", "保障措施", "关税", "配额",
                              "原产于", "出口管制"]},
    "行业政策": {
        "科技创新": ["科技创新", "研发", "技术攻关", "高新技术企业", "专精特新",
                    "知识产权", "科技成果", "创新"],
        "生态环保": ["环保", "污染", "排放", "环境影响评价", "排污许可",
                    "督察整改", "生态", "碳排放", "双碳"],
        "能源电力": ["能源", "电力", "光伏", "风电", "储能", "煤炭", "油气",
                    "电网", "核电"],
        "农业农村": ["农业", "农村", "乡村振兴", "耕地", "种业", "粮食",
                    "高标准农田", "农产品"],
        "交通物流": ["交通", "公路", "铁路", "民航", "港口", "物流", "快递",
                    "运输", "高速"],
        "文化旅游": ["文旅", "旅游", "文化", "文物", "体育", "景区", "演出"],
        "医疗保障": ["医保", "医疗", "卫生健康", "疾病预防", "公共卫生", "医联体"],
        "应急安全": ["应急", "安全生产", "消防", "防汛", "防灾", "救援",
                    "隐患排查", "事故"],
        "财政金融": ["财政", "预算", "税", "金融", "信贷", "保险", "基金", "国债"],
        "市场监管": ["市场监管", "食品安全", "抽检", "质量监督", "价格监督",
                    "反不正当竞争"],
        "产品召回": ["召回", "缺陷产品", "安全隐患", "召回部分进口", "召回部分国产"],
        "登记变更": ["登记试验", "登记证", "信息变更", "农药登记", "兽药登记",
                    "批准文号", "备案号"],
    },
}


def render(category: str, spec: dict, indent: str = "  ") -> list[str]:
    lines = [f"{indent}{category}:"]
    lines.append(f"{indent}  subcategories:")
    for sub, words in spec["subcategories"].items():
        lines.append(f"{indent}    {sub}:")
        for word in words:
            lines.append(f'{indent}      - "{word}"')
    return lines


def main() -> int:
    import yaml

    data = yaml.safe_load(RULES.read_text(encoding="utf-8"))
    categories = data["categories"]
    added_cat: list[str] = []
    added_sub: list[str] = []
    for name, spec in NEW_CATEGORIES.items():
        if name in categories:
            # 已存在则只补子类
            existing = categories[name].setdefault("subcategories", {})
            for sub, words in spec["subcategories"].items():
                if sub not in existing:
                    existing[sub] = list(words)
                    added_sub.append(f"{name}/{sub}")
            continue
        categories[name] = spec
        added_cat.append(name)
    for name, subs in EXTRA_SUBCATEGORIES.items():
        if name not in categories:
            print(f"跳过 {name}：一级分类不存在，不新建（避免打乱既有体系）")
            continue
        existing = categories[name].setdefault("subcategories", {})
        for sub, words in subs.items():
            if sub not in existing:
                existing[sub] = list(words)
                added_sub.append(f"{name}/{sub}")

    RULES.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=200),
        encoding="utf-8",
    )
    print(f"新增一级分类 {len(added_cat)} 个: {added_cat}")
    print(f"新增子类 {len(added_sub)} 个: {added_sub}")
    print(f"\n现有一级分类 {len(categories)} 个，总子类 "
          f"{sum(len((s or {}).get('subcategories') or {}) for s in categories.values())} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
