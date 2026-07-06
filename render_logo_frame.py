"""Render the Adapix mark onto a dark 1920x1080 frame for Higgsfield image-to-video."""
import asyncio
from pathlib import Path

svg_markup = Path("website/adapix_mark.svg").read_text(encoding="utf-8")
# strip the xml declaration so it can be inlined
if svg_markup.startswith("<?xml"):
    svg_markup = svg_markup[svg_markup.index("?>") + 2:]

OUT = Path("adapix_logo_frame.png").resolve()

HTML = f"""
<!doctype html><html><head><style>
  html,body{{margin:0;width:1920px;height:1080px;background:#0b0b1c;
    display:flex;align-items:center;justify-content:center;overflow:hidden}}
  svg{{width:860px;height:auto;
    filter:drop-shadow(0 0 50px rgba(124,58,237,.55))
           drop-shadow(0 0 110px rgba(34,211,238,.35));}}
</style></head><body>{svg_markup}</body></html>
"""


async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1920, "height": 1080})
        await page.set_content(HTML)
        await page.wait_for_timeout(800)
        await page.screenshot(path=str(OUT))
        await browser.close()
    print(f"saved {OUT}")


asyncio.run(main())
