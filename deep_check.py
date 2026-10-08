"""深度诊断：采集运营按钮 + 页面内容来源 + 侧栏结构"""
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            executable_path=r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
        )
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        await page.goto("http://localhost:8599", wait_until="networkidle", timeout=20000)
        await page.wait_for_timeout(2000)

        # 展开所有折叠分组
        for _ in range(10):
            exp = page.locator(".st-key-side details:not([open]) > summary").first
            try:
                await exp.click(timeout=1000)
                await page.wait_for_timeout(200)
            except:
                break
        await page.wait_for_timeout(500)

        # 1) 检查采集运营按钮状态
        btn = page.locator(".st-key-side button:has-text('采集运营')").first
        count = await btn.count()
        print(f"采集运营按钮数量: {count}")
        if count > 0:
            visible = await btn.is_visible()
            enabled = await btn.is_enabled()
            box = await btn.bounding_box()
            print(f"  visible={visible} enabled={enabled} box={box}")

        # 2) 检查侧栏所有按钮的可见性
        all_btns = page.locator(".st-key-side button")
        total = await all_btns.count()
        print(f"\n侧栏按钮总数: {total}")
        for i in range(total):
            b = all_btns.nth(i)
            txt = await b.text_content()
            vis = await b.is_visible()
            box = await b.bounding_box()
            if txt and len(txt) < 30:
                print(f"  [{i}] '{txt}' visible={vis} box={box}")

        # 3) 检查 main-content 区域是否正确隔离
        main = page.locator(".st-key-main-content").first
        main_count = await main.count()
        print(f"\n.st-key-main-content 存在: {main_count}")

        # 4) 点击公告检索后检查内容区域
        btn2 = page.locator(".st-key-side button:has-text('公告检索')").first
        await btn2.click(timeout=5000)
        await page.wait_for_timeout(2000)

        info = await page.evaluate("""() => {
            const main = document.querySelector('.st-key-main-content');
            if (!main) return {error: 'no main-content'};
            const r = main.getBoundingClientRect();
            return {
                rect: {x: r.x, y: r.y, w: r.width, h: r.height},
                childCount: main.children.length,
                textLen: (main.textContent || '').length,
                cards: main.querySelectorAll('.record-card, .metric, .panel').length
            };
        }""")
        print(f"\n公告检索 main-content: {info}")

        await browser.close()

asyncio.run(main())
