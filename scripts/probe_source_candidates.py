"""探候选新源：**只探列表页**，能取到像公告的详情链接才算数。

为什么不用 scripts/probe_candidates.py
------------------------------------
它探的是**首页**——它自己的 docstring 就写了「首页 200 不代表列表页有数据」，
但 CANDIDATES 里那 13 个 URL 全是 `https://www.xxx.gov.cn/` 这种首页。
首页能取到「像公告的链接」只说明导航里有通知入口，不代表列表页有数据。
拿它的结论去加源，加进来的就是一批零产出的源。

2026-10-07 实测的教训：本项目已经踩过好几个「配了但一条没采到」的源
（nhc_gov / cd_gov / cd_mohrss_gov / cd_samr_gov / cd_st_gov），
成都政府和卫健委是**站点级 JS 反爬**（全域 HTTP 412，`$_ts` 挑战），
换 URL、升 markdown 级、开 playwright 都拿不到。加源前必须先探列表页。

判据（三条都要过，缺一条就是坏源）
------------------------------------
1. HTTP 200
2. 列表页取到 >= ``--min-hits`` 条**像详情页**的链接
3. 取到的链接里有 >= ``--min-distinct-host`` 个不同 host（防止全是导航外链）

政治成本：这是逐个候选站发请求，所以默认并发 4、带 ``--sleep``、只探不写配置。
候选列表要人工扩充——这里不给自动发现，那等于对全网乱扫。

用法：
    python scripts/probe_source_candidates.py --list
    python scripts/probe_source_candidates.py --min-hits 10
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
import yaml  # noqa: E402

SOURCES = ROOT / "config" / "sources.yaml"

# 候选 = (id, 名称, 层级, 列表页URL)。
# 选取依据：与本项目现有可用源同构的**通知公告/政策文件栏目页**，
# 而不是首页。省级门户里已配的（川/粤/浙/陕/苏/皖/闽/赣/鲁/鄂/湘…）不再重复。
CANDIDATES: list[tuple[str, str, str, str]] = [
    # ── 部委（通知公告/政策文件）──────────────────────────────
    ("nhsa_gov", "国家医保局", "national",
     "https://www.nhsa.gov.cn/col/col104/index.html"),
    ("cnipa_gov", "国家知识产权局", "national",
     "https://www.cnipa.gov.cn/col/col61/index.html"),
    ("mnr_tzgg", "自然资源部", "national",
     "https://www.mnr.gov.cn/dt/ywbb/"),
    ("nea_policy", "国家能源局-政策", "national",
     "https://www.nea.gov.cn/zc/index.htm"),
    ("samr_tzgg", "市场监管总局-通知公告", "national",
     "https://www.samr.gov.cn/zw/zfxxgk/tzgg/"),
    ("mot_tzgg", "交通运输部-通知公告", "national",
     "https://www.mot.gov.cn/zhengcejiedu/tzgg/"),
    ("miit_tzgg", "工信部-运行监测", "national",
     "https://www.miit.gov.cn/jgsj/yxjs/index.html"),
    ("mee_gzgg", "生态环境部-公告公告", "national",
     "https://www.mee.gov.cn/xxgk2018/xxgk/xxgk06/"),
    ("mohrss_gz", "人社部-政策文件", "national",
     "https://www.mohrss.gov.cn/xxgk2020/fdzdgknr/zcfg/"),
    ("mof_gk", "财政部-公告", "national",
     "https://www.mof.gov.cn/gkml/caizhenggg/"),
    ("mohurd_gz", "住建部-政策", "national",
     "https://www.mohurd.gov.cn/gongkai/zhengce/index.html"),
    ("mps_notice", "公安部-公告", "national",
     "https://www.mps.gov.cn/n2254314/n2254409/index.html"),
    ("moda_notice", "民政部-公告", "national",
     "https://www.mca.gov.cn/n152/n165/index.html"),
    ("nda_notice", "国家数据局-政策发布", "national",
     "https://www.nda.gov.cn/sjj/zwgk/zcfb/index.html"),
    ("stats_notice", "国家统计局-最新发布", "national",
     "https://www.stats.gov.cn/sj/zxfb/"),
    ("nfra_notice", "金融监管总局", "national",
     "https://www.nfra.gov.cn/cn/view/pages/ItemList.html"),
    ("pbc_notice", "人民银行-公告", "national",
     "https://www.pbc.gov.cn/goutongjiaoliu/113456/113469/index.html"),
    ("mem_gk", "应急管理部-公开信息", "national",
     "https://www.mem.gov.cn/gk/zfxxgkpt/fdzdgknr/gknr/"),
    ("forest_notice", "国家林草局-工作动态", "national",
     "https://www.forestry.gov.cn/lc/gzdt/index.html"),
    ("mnr_sj", "自然资源部-数据", "national",
     "https://www.mnr.gov.cn/sj/"),
    ("sastind", "国防科工局-通知", "national",
     "https://www.sastind.gov.cn/n16/n181/index.html"),
    ("cnsa_notice", "国家航天局-通知", "national",
     "https://www.cnsa.gov.cn/n6758823/n6758838/index.html"),
    ("moe_tzgg", "教育部-政策文件", "national",
     "http://www.moe.gov.cn/jyb_xxgk/moe_1777/"),
    ("most_tzgg", "科技部-通知公告", "national",
     "https://www.most.gov.cn/xxgk/tzgg/"),
    ("samr_gg", "市场监管总局-公告", "national",
     "https://www.samr.gov.cn/zw/zfxxgk/fdzdgknr/gg/"),
    # ── 省级垂管单位 ──────────────────────────────────────
    ("gd_samr", "广东省市场监管局", "provincial",
     "https://amr.gd.gov.cn/zwgk/tzgg/index.html"),
    ("zj_samr", "浙江省市场监管局", "provincial",
     "https://zjamr.zj.gov.cn/col/col1229057138/index.html"),
    ("sc_samr2", "四川省市场监管局-公告", "provincial",
     "https://scjgj.sc.gov.cn/scjgj/c107080/list.shtml"),
    ("hb_samr", "湖北省市场监管局", "provincial",
     "https://scjg.hubei.gov.cn/fbjd/tzgg/"),
    ("hn_samr", "湖南省市场监管局", "provincial",
     "https://amr.hunan.gov.cn/xxgk/tzgg/index.html"),
    ("fj_samr", "福建省市场监管局", "provincial",
     "https://scjgj.fujian.gov.cn/xxgk/tzgg/"),
    ("jx_samr", "江西省市场监管局", "provincial",
     "https://amr.jiangxi.gov.cn/col/col32097/index.html"),
    ("sd_samr", "山东省市场监管局", "provincial",
     "https://amr.shandong.gov.cn/col/col9660/index.html"),
    ("sx_samr", "山西省市场监管局", "provincial",
     "https://scjgj.shanxi.gov.cn/zfxxgk/zfxxgkzl/fdzdgknr/tzgg/"),
    ("hlj_samr", "黑龙江省市场监管局", "provincial",
     "https://scjgj.hlj.gov.cn/szfmh/tzgg/"),
    ("jl_samr", "吉林省市场监管厅", "provincial",
     "https://scjg.jl.gov.cn/xxgk/tzgg/"),
    ("ln_samr", "辽宁省市场监督管理局", "provincial",
     "https://scjg.ln.gov.cn/scjg/xxgk/fdzdgknr/tzgg/"),
    ("yn_samr", "云南省市场监督管理局", "provincial",
     "https://amr.yn.gov.cn/tzgg/"),
    ("gz_samr", "贵州省市场监督管理局", "provincial",
     "https://amr.guizhou.gov.cn/zwgk/xxgkml/tzgg/"),
    ("gx_samr", "广西市场监督管理局", "provincial",
     "https://scjdglj.gxzf.gov.cn/zwgk/tzgg/"),
    ("nmg_samr", "内蒙古市场监督管理局", "provincial",
     "https://amr.nmg.gov.cn/zw/tzgg/"),
    ("xj_samr", "新疆市场监督管理局", "provincial",
     "https://scjgj.xinjiang.gov.cn/tzgg/index.htm"),
    ("qh_samr", "青海省市场监督管理局", "provincial",
     "https://scjgj.qinghai.gov.cn/xxgk/tzgg/"),
    ("gs_samr", "甘肃省市场监督管理局", "provincial",
     "https://scjgj.gansu.gov.cn/tzgg/"),
    # ── 市级（现有覆盖最薄，优先补）──────────────────────
    ("su_gxw", "苏州市工业和信息化局", "municipal",
     "https://gxj.suzhou.gov.cn/szgyj/tzgg/index.shtml"),
    ("sz_fgw", "深圳市发展和改革委员会", "municipal",
     "https://fgw.sz.gov.cn/xxgk/tzgg/index.html"),
    ("gz_fgw", "广州市发展和改革委员会", "municipal",
     "https://fgw.gz.gov.cn/zwgk/tzgg/index.html"),
    ("hz_fgw2", "杭州市发展和改革委员会", "municipal",
     "https://fgw.hangzhou.gov.cn/col/col1228796629/index.html"),
    ("nj_fgw", "南京市发展和改革委员会", "municipal",
     "https://fgw.nanjing.gov.cn/xxgk/tzgg/"),
    ("cs_fgw", "长沙市发展和改革委员会", "municipal",
     "https://fgw.changsha.gov.cn/zwgk/tzgg/"),
    ("hf_fgw", "合肥市发展和改革委员会", "municipal",
     "https://fgw.hefei.gov.cn/zwgk/tzgg/"),
    ("cd_fgw2", "成都市发展和改革委员会", "municipal",
     "https://fgw.chengdu.gov.cn/cdfgw/c106080/list.shtml"),
    ("wh_fgw", "武汉市发展和改革委员会", "municipal",
     "https://fgw.wuhan.gov.cn/zwgk/tzgg/"),
    ("jn_fgw", "济宁市发展和改革委员会", "municipal",
     "https://fgw.jining.gov.cn/zwgk/tzgg/"),
    ("qd_fgw", "青岛市发展和改革委员会", "municipal",
     "https://fgw.qingdao.gov.cn/zwgk/tzgg/"),
    ("zz_fgw", "郑州市发展和改革委员会", "municipal",
     "https://fgw.zhengzhou.gov.cn/tzgg/"),
    ("sz_samr", "深圳市市场监督管理局", "municipal",
     "https://amr.sz.gov.cn/xxgk/tzgg/index.html"),
    ("gz_samr2", "广州市市场监督管理局", "municipal",
     "https://scj.gz.gov.cn/zwgk/tzgg/index.html"),
    ("hz_samr", "杭州市市场监督管理局", "municipal",
     "https://scjg.hangzhou.gov.cn/col/col1228840914/index.html"),
    ("nj_samr", "南京市市场监督管理局", "municipal",
     "https://scjgj.nanjing.gov.cn/njsscjj/tzgg/"),
    ("wh_samr", "武汉市市场监督管理局", "municipal",
     "https://scjgj.wuhan.gov.cn/tzgg/"),
    ("cs_samr", "长沙市市场监督管理局", "municipal",
     "https://scjgj.changsha.gov.cn/xxgk/tzgg/"),
    ("hf_samr", "合肥市市场监督管理局", "municipal",
     "https://scjgj.hefei.gov.cn/tzgg/"),
    ("sy_fgw", "沈阳市发展和改革委员会", "municipal",
     "https://fgw.shenyang.gov.cn/zwgk/tzgg/"),
    ("dl_fgw", "大连市发展和改革委员会", "municipal",
     "https://fgw.dl.gov.cn/zwgk/tzgg/"),
    ("xm_fgw", "厦门市发展和改革委员会", "municipal",
     "https://fgw.xm.gov.cn/zwgk/tzgg/"),
    ("nb_fgw", "宁波市发展和改革委员会", "municipal",
     "https://fgw.ningbo.gov.cn/xxgk/tzgg/"),
    ("wx_fgw", "无锡市发展和改革委员会", "municipal",
     "https://fgw.wuxi.gov.cn/zwgk/tzgg/"),
    ("fz_fgw", "福州市发展和改革委员会", "municipal",
     "https://fgw.fuzhou.gov.cn/zwgk/tzgg/"),
    ("xm2_gxw", "厦门市工业和信息化局", "municipal",
     "https://gxj.xm.gov.cn/zwgk/tzgg/"),
    ("qz_fgw", "泉州市发展和改革委员会", "municipal",
     "https://fgw.quanzhou.gov.cn/zwgk/tzgg/"),
    # ── 其它条线的市级（能源/住建/交通，公告量大）────────
    ("sz_sthj", "深圳市生态环境局", "municipal",
     "https://meeb.sz.gov.cn/xxgk/tzgg/index.html"),
    ("gz_sthj", "广州市生态环境局", "municipal",
     "https://gjj.gz.gov.cn/zwgk/tzgg/index.html"),
    ("sz_zjj", "深圳市住房和建设局", "municipal",
     "https://zjj.sz.gov.cn/xxgk/tzgg/index.html"),
    ("gz_zjj", "广州市住房和城乡建设局", "municipal",
     "https://zfcj.gz.gov.cn/zwgk/tzgg/index.html"),
    ("sz_jtys", "深圳市交通运输局", "municipal",
     "https://jtys.sz.gov.cn/xxgk/tzgg/index.html"),
    ("gz_jtys", "广州市交通运输局", "municipal",
     "https://jtj.gz.gov.cn/zwgk/tzgg/index.html"),
]
# 像详情页：路径里有内容/文章标识，或带日期的数字串（政府站常见 detail_12345.html）
DOC_URL = re.compile(r"/(?:content|art|info|detail|article|notice)[/_-]|"
                     r"(?:/\d{6,}|_(\d{6,})\.s?html)", re.I)


def probe(item: tuple[str, str, str, str], timeout: int) -> dict:
    sid, name, level, url = item
    started = time.time()
    result = {"id": sid, "name": name, "level": level, "url": url,
              "status": 0, "hits": 0, "hosts": 0, "seconds": 0.0,
              "error": "", "sample": ""}
    try:
        response = httpx.get(url, timeout=timeout, follow_redirects=True,
                             trust_env=False,
                             headers={"User-Agent": "Mozilla/5.0"})
        result["status"] = response.status_code
        if response.status_code >= 400:
            return result
        hrefs = re.findall(r'<a[^>]+href=["\']([^"\']+)["\']', response.text, re.I)
        hits = [h for h in hrefs if DOC_URL.search(h)]
        result["hits"] = len(hits)
        result["hosts"] = len({urlparse(h).netloc for h in hits if h})
        if hits:
            result["sample"] = hits[0][:80]
    except Exception as exc:                                # noqa: BLE001
        result["error"] = type(exc).__name__
    result["seconds"] = round(time.time() - started, 1)
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", action="store_true", help="只打印候选清单")
    ap.add_argument("--min-hits", type=int, default=10,
                    help="列表页至少要取到多少条像详情页的链接（默认 10）")
    ap.add_argument("--min-hosts", type=int, default=1,
                    help="这些链接至少要跨几个 host（默认 1，防全是外链）")
    ap.add_argument("--timeout", type=int, default=25)
    ap.add_argument("--sleep", type=float, default=0.3,
                    help="每个候选之间的秒数（对政府站友好）")
    ap.add_argument("--workers", type=int, default=4,
                    help="并发数。别开大：这是打政府站。")
    args = ap.parse_args()

    if args.list:
        for sid, name, level, url in CANDIDATES:
            print("  %-14s %-22s %-11s %s" % (sid, name, level, url))
        print("\n共 %d 个候选" % len(CANDIDATES))
        return 0

    print("探 %d 个候选列表页（判据: HTTP200 + >=%d 条像详情页的链接）\n"
          % (len(CANDIDATES), args.min_hits))
    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for result in pool.map(lambda c: probe(c, args.timeout), CANDIDATES):
            rows.append(result)
            time.sleep(args.sleep)
            verdict = ("可用" if (result["status"] == 200
                                 and result["hits"] >= args.min_hits
                                 and result["hosts"] >= args.min_hosts)
                       else "不可用")
            print("  %-14s HTTP%-4s 命中%4d 跨host%2d %5.1fs  %s %s"
                  % (result["id"], result["status"], result["hits"],
                     result["hosts"], result["seconds"], verdict,
                     result["error"] or result["sample"]))

    ok = [r for r in rows
          if r["status"] == 200 and r["hits"] >= args.min_hits
          and r["hosts"] >= args.min_hosts]
    print("\n可用 %d / %d:" % (len(ok), len(CANDIDATES)))
    for r in ok:
        print("  %-14s %-22s %-11s 命中%d  %s"
              % (r["id"], r["name"], r["level"], r["hits"], r["url"]))

    # 已知局限（2026-10-07 实测，别在报告里假装没有）
    #
    # 1) 通过率 13%（10/77）。大量 HTTP 404 是 URL 猜错——要拿到真 URL 得先
    #    读站点导航，那是另一件更大的事。HTTP 0（ConnectError）是本机网络
    #    到不了那些域名。
    #
    # 2) **「可用」不等于干净。** 实跑抽取会发现列表页**自身的栏目链接**混进来
    #    当详情抓：stats_notice 头两条是「RSS订阅 - 国家统计局」「局领导」，
    #    sz_zjj 是「统计信息 -深圳市住房和建设局网站」。quality.py 的 nav_page
    #    判据只认穷举词表+几个形态，正则兜不住无限多的中文栏目名。
    #    这是结构性缺陷，不是配词表能解决的——见下。
    #
    # 3) 结构性修法是**URL 层过滤**：列表链接先过 detail_pattern 再抓。
    #    但 2026-10-07 实测只有 52% 的源配了 detail_pattern，覆盖不到一半，
    #    现在开就是半吊子（要么对没配的源放过、要么误杀真链接）。
    #    等补齐 pattern 覆盖率再做。
    print("\n（可用 ≠ 能入库，也不等于干净。已知混入列表页自身栏目链接，"
          "见本文件末尾说明。）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())