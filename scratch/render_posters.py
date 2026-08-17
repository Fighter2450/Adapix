from playwright.sync_api import sync_playwright
import pathlib
base = pathlib.Path(__file__).parent
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1080, "height": 1080}, device_scale_factor=2)
    for name in ("poster_brief", "poster_learning"):
        pg.goto((base / f"{name}.html").as_uri())
        pg.wait_for_timeout(900)
        pg.screenshot(path=str(base / f"{name}.png"))
        print(name, "rendered")
    b.close()
