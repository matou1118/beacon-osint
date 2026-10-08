"""全面体检：逐页检测功能可用性、交互元素、动效、数据状态"""
import asyncio, json
from playwright.async_api import async_playwright

URL = "http://localhost:8599"

PAGES = [
    "总览", "中国地图", "热频话题", "公告检索",
    "采集运营", "来源健康", "对比工作台", "关联确认",
    "字段复核", "知识图谱", "订阅中心", "进化仪表盘",
    "质量与备份", "备用区",
]

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            executable_path=r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
        )
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        await page.goto(URL, wait_until="networkidle", timeout=20000)
        await page.wait_for_timeout(2000)

        results = []
        for name in PAGES:
            try:
                # 展开所有折叠分组
                for _ in range(10):
                    exp = page.locator(".st-key-side details:not([open]) > summary").first
                    try:
                        await exp.click(timeout=1000)
                        await page.wait_for_timeout(200)
                    except:
                        break
                await page.wait_for_timeout(500)

                btn = page.locator(f".st-key-side button:has-text('{name}')").first
                await btn.click(timeout=5000)
                await page.wait_for_timeout(2000)

                r = await page.evaluate("""(pageName) => {
                    const html = document.documentElement;
                    const main = document.querySelector('.st-key-main-content') || document.querySelector('[data-testid="stMain"]');

                    // 1) 滚动
                    const canScroll = html.scrollHeight > html.clientHeight + 50;
                    const scrollDiff = html.scrollHeight - html.clientHeight;

                    // 2) 交互元素统计
                    const allBtns = main ? main.querySelectorAll('button').length : 0;
                    const inputs = main ? main.querySelectorAll('input, select, textarea').length : 0;
                    const links = main ? main.querySelectorAll('a').length : 0;
                    const expanders = main ? main.querySelectorAll('details').length : 0;
                    const tabs = main ? main.querySelectorAll('[role="tab"], .stTabs').length : 0;

                    // 3) 数据卡片
                    const cards = main ? main.querySelectorAll('.metric, .panel, .record-card, .heat-card, .module-card, [data-testid="stMetric"]').length : 0;
                    const emptyHints = main ? main.querySelectorAll('.empty').length : 0;

                    // 4) 动效检测
                    const animatedEls = main ? main.querySelectorAll('[style*="animation"], [class*="fade"], [class*="slide"], [class*="reveal"]').length : 0;
                    const hasTransition = main ? Array.from(main.querySelectorAll('*')).some(el => {
                        const cs = getComputedStyle(el);
                        return cs.transition && cs.transition !== 'all 0s ease 0s' && cs.transition !== '';
                    }) : false;

                    // 5) 错误检测
                    const errorTexts = main ? Array.from(main.querySelectorAll('*')).filter(el => {
                        const t = el.textContent || '';
                        return t.length < 200 && (t.includes('Traceback') || t.includes('Error') || t.includes('Exception') || t.includes('KeyError') || t.includes('AttributeError'));
                    }).map(el => (el.textContent || '').slice(0, 100)) : [];

                    // 6) 内容文本量
                    const textContent = main ? (main.textContent || '').trim() : '';
                    const textLen = textContent.length;
                    const hasContent = textLen > 100;

                    // 7) 布局检测：检查是否有重叠元素
                    const metrics = main ? main.querySelectorAll('[data-testid="stMetric"]') : [];
                    let overlapCount = 0;
                    for (let i = 0; i < metrics.length && i < 20; i++) {
                        const r1 = metrics[i].getBoundingClientRect();
                        for (let j = i + 1; j < metrics.length && j < 20; j++) {
                            const r2 = metrics[j].getBoundingClientRect();
                            if (r1.left < r2.right && r2.left < r1.right && r1.top < r2.bottom && r2.top < r1.bottom) {
                                if (r1.width > 50 && r2.width > 50) overlapCount++;
                            }
                        }
                    }

                    // 8) 检查 st-key-main-content 内的可见内容区域
                    const visibleCards = main ? Array.from(main.querySelectorAll('.metric, .panel, .record-card, .heat-card, [data-testid="stMetric"]')).filter(el => {
                        const r = el.getBoundingClientRect();
                        return r.width > 0 && r.height > 0;
                    }).length : 0;

                    return {
                        page: pageName,
                        canScroll, scrollDiff,
                        buttons: allBtns, inputs, links, expanders, tabs,
                        cards, visibleCards, emptyHints,
                        animatedEls, hasTransition,
                        errors: errorTexts.slice(0, 3),
                        textLen, hasContent,
                        overlapCount
                    };
                }""", name)

                # 评级
                issues = []
                if r["errors"]:
                    issues.append(f"有{len(r['errors'])}处错误文本")
                if not r["hasContent"]:
                    issues.append("内容为空")
                if r["overlapCount"] > 0:
                    issues.append(f"{r['overlapCount']}处元素重叠")
                if r["visibleCards"] == 0 and r["buttons"] == 0:
                    issues.append("无可见卡片或按钮")
                if r["emptyHints"] > 3:
                    issues.append(f"{r['emptyHints']}处空态提示")

                grade = "A" if not issues else ("B" if len(issues) == 1 else "C")
                status_emoji = {"A": "✅", "B": "⚠️", "C": "❌"}[grade]

                print(f"\n{'='*60}")
                print(f"{status_emoji} [{grade}] {name}")
                print(f"  滚动: {'可滚' if r['canScroll'] else '不超屏'} (diff={r['scrollDiff']})")
                print(f"  控件: 按钮{r['buttons']} 输入{r['inputs']} 链接{r['links']} 折叠{r['expanders']} 标签{r['tabs']}")
                print(f"  内容: 卡片{r['cards']}(可见{r['visibleCards']}) 空态{r['emptyHints']} 文本{r['textLen']}字")
                print(f"  动效: 动画元素{r['animatedEls']} 有过渡={r['hasTransition']}")
                if issues:
                    print(f"  问题: {'; '.join(issues)}")
                if r["errors"]:
                    for e in r["errors"][:2]:
                        print(f"    └ {e[:80]}")

                results.append({"page": name, "grade": grade, "issues": issues, **r})

            except Exception as e:
                msg = str(e).split("\n")[0][:80]
                print(f"\n❌ [F] {name} — {msg}")
                results.append({"page": name, "grade": "F", "error": msg})

        await browser.close()

        # 汇总
        grades = {}
        for r in results:
            g = r["grade"]
            grades[g] = grades.get(g, 0) + 1
        print(f"\n{'='*60}")
        print(f"体检汇总: {json.dumps(grades, ensure_ascii=False)}")
        print(f"  A(无问题): {grades.get('A',0)}  B(小问题): {grades.get('B',0)}  C(多问题): {grades.get('C',0)}  F(打不开): {grades.get('F',0)}")

        with open(r"D:\TVbox\BeaconOSINT\health_check_result.json", "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2, default=str)

asyncio.run(main())
