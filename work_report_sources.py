"""政府工作报告源注册。

单独成文件而不是并进 config/sources.yaml：那些源是公告类（列表页→详情页），
政府工作报告的归档栏目结构不同（栏目页本身就是年表，链接文字带年份），
走同一套 list_selector / detail_pattern 会互相干扰。

收录依据是实际探查结果，不是推测。四个坑都在注释里，改配置前先看这里：

  上海  链接文字是「龚正市长在…政府工作报告（2025年）」，且正文含
        &emsp;&emsp; 缩进实体——不 decode 会让标题正则全部失配
        （2025/2024 两份曾因此分出 0 节）。
  广州  /zwgk/zfgzbg/ 是 404，正确路径是 /zwgk/jjgb/zfgzbg/ 之外的
        /zwgk/zjgb/zfgzbg/。
  重庆  首屏只到 2013，更早年份在 index_1..N 的分页里。
  武汉  正式面包屑栏目 /zwgk/xxgk/ghjh/zfgzbg/ 是 JS 加载（静态抓取 0 命中），
        必须用镜像栏目 /zwgk/zwgk_wl/zfgb/。且 2016-2018 三份的 URL 年份段
        都是 /202003/，所以年份只能从链接文字取。

未收录：成都、湖北（全站 412 WAF，已停止尝试）、浙江（省门户无该栏目）。
"""
from __future__ import annotations

WORK_REPORT_SOURCES: list[dict] = [
    {
        "id": "wr_shanghai",
        "name": "上海市政府工作报告",
        "region_name": "上海",
        "region_code": "310000",
        "level": "省级",
        "domain": "www.shanghai.gov.cn",
        "url": "https://www.shanghai.gov.cn/nw12336/",
        "gov_level": "直辖市",
        "check_robots": True,
        "request_interval_seconds": 3,
        "cadence_tier": "yearly",
    },
    {
        "id": "wr_guangzhou",
        "name": "广州市政府工作报告",
        "region_name": "广州",
        "region_code": "440100",
        "level": "市级",
        "domain": "www.gz.gov.cn",
        "url": "https://www.gz.gov.cn/zwgk/zjgb/zfgzbg/",
        "gov_level": "地市级",
        "check_robots": True,
        "request_interval_seconds": 3,
        "cadence_tier": "yearly",
    },
    {
        "id": "wr_chongqing",
        "name": "重庆市政府工作报告",
        "region_name": "重庆",
        "region_code": "500000",
        "level": "省级",
        "domain": "cq.gov.cn",
        "url": "https://cq.gov.cn/zwgk/zfxxgkml/zfgzbg/",
        "gov_level": "直辖市",
        "check_robots": True,
        "request_interval_seconds": 3,
        "cadence_tier": "yearly",
        # 受阻，不采集：https://cq.gov.cn/robots.txt 返回 403（WAF 拦截页），
        # 读不到 robots 就无法确认是否允许抓取。合规底线是不绕过 WAF，所以
        # 采集器的 robots 检查会正确地抛 PermissionError。
        # 裸 HTTP 探针（不查 robots）确实能拿到归档页，但那正是要避免的：
        # 绕过 robots 检查拿到的数据不该进库。恢复条件是该站提供可读 robots。
        "blocked_reason": "robots.txt 返回 403，无法确认抓取许可",
    },
    {
        "id": "wr_wuhan",
        "name": "武汉市政府工作报告",
        "region_name": "武汉",
        "region_code": "420100",
        "level": "市级",
        "domain": "www.wuhan.gov.cn",
        # 正式栏目 /zwgk/xxgk/ghjh/zfgzbg/ 由 JS 加载，静态抓取 0 命中
        "url": "https://www.wuhan.gov.cn/zwgk/zwgk_wl/zfgb/",
        "gov_level": "地市级",
        "check_robots": True,
        "request_interval_seconds": 3,
        "cadence_tier": "yearly",
    },
]
