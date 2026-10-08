import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True, executable_path=r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
        page = await b.new_page(viewport={"width":1440,"height":900})
        await page.goto("http://localhost:8599", wait_until="networkidle", timeout=20000)
        await page.wait_for_timeout(2000)
        for _ in range(10):
            exp = page.locator(".st-key-side details:not([open]) > summary").first
            try:
                await exp.click(timeout=1000)
                await page.wait_for_timeout(200)
            except:
                break
        await page.wait_for_timeout(500)
        btns = await page.locator(".st-key-side button").all_text_contents()
        print("侧栏按钮文本:")
        for i,t in enumerate(btns):
            print(f'  [{i}] "{t}"')
        await b.close()

asyncio.run(main())
