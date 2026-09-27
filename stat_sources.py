"""统计公报源注册——用于补齐政府工作报告里缺失的指标。

为什么不抓搜索引擎
  抓搜索结果违反其服务条款；结果不是可引用的一手来源；同样的问题今天和明天
  可能给不同答案，数字不可复现。统计公报是统计局每年发布的一手权威文件，
  逐年结构稳定、URL 可枚举、robots 与限速规则和报告采集完全一致。

覆盖面限制（实测）
  统计局官网的公报栏目通常只挂近几年。上海 tjj.sh.gov.cn/tjgb/ 首屏仅 14 条
  链接，而报告侧最大的缺口在 2009-2015（那几年报告只写增速不写总量）。
  所以补齐能覆盖多少要看栏目实际挂了几年，脚本会逐条报告，不要假设能补满。

收录依据是实际探查，不是推测：
  上海  tjj.sh.gov.cn/tjgb/            200，可枚举，链接形如
        /tjgb/20260330/e0772941e8e041eaaad2df850b44ef98.html
  广州  tjj.gz.gov.cn/stats_newtjyw/tjsj/index.html   200，可枚举，链接形如
        .../tjgb/qstjgb/content/post_XXXXXXX.html（"市统计公报"子栏目）
  武汉  未找到。「统计数据」导航是 href="#" 的 JS 驱动，四个候选路径全 404。
"""
from __future__ import annotations

STAT_SOURCES: list[dict] = [
    {
        "id": "st_shanghai",
        "name": "上海市国民经济和社会发展统计公报",
        "region_name": "上海",
        "region_code": "310000",
        "level": "省级",
        "domain": "tjj.sh.gov.cn",
        "url": "https://tjj.sh.gov.cn/tjgb/",
        "gov_level": "直辖市",
        # 只认这一种文件。同一栏目里还挂着"1%人口抽样调查主要数据公报"
        # "第五次全国经济普查公报"，那是另两类文件，年份和口径都不同。
        "title_must_contain": "国民经济和社会发展统计公报",
        "publisher": "上海市统计局",
        "check_robots": True,
        "request_interval_seconds": 3,
        "cadence_tier": "yearly",
    },
    {
        "id": "st_guangzhou",
        "name": "广州市国民经济和社会发展统计公报",
        "region_name": "广州",
        "region_code": "440100",
        "level": "市级",
        "domain": "tjj.gz.gov.cn",
        "url": "https://tjj.gz.gov.cn/stats_newtjyw/tjsj/index.html",
        "gov_level": "地市级",
        "title_must_contain": "国民经济和社会发展统计公报",
        "publisher": "广州市统计局",
        "check_robots": True,
        "request_interval_seconds": 3,
        "cadence_tier": "yearly",
    },
    {
        "id": "st_wuhan",
        "name": "武汉市国民经济和社会发展统计公报",
        "region_name": "武汉",
        "region_code": "420100",
        "level": "市级",
        "domain": "tjj.wuhan.gov.cn",
        "url": "",
        "gov_level": "地市级",
        "publisher": "武汉市统计局",
        "check_robots": True,
        "request_interval_seconds": 3,
        "cadence_tier": "yearly",
        # 待发现：首页「统计数据」是 href="#" 的 JS 导航，
        # /tjsj/、/tjsj/tjgb/、/xxgk/tjsj/、/tjgb/ 四个候选路径全部 404。
        "blocked_reason": "公报栏目未找到，统计数据导航为 JS 驱动",
    },
]
