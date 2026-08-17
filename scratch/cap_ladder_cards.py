"""Capture the morning-brief and learned cards from the live local app."""
from playwright.sync_api import sync_playwright

BASE = "http://localhost:3000"
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1400, "height": 1000}, device_scale_factor=2)
    pg = ctx.new_page()
    pg.goto(BASE + "/login")
    pg.request.post(BASE + "/auth/login", form={"email": "demo@adapix.test", "password": "demo12345"})
    pg.goto(BASE + "/app")
    pg.wait_for_selector("#home-brief", state="visible", timeout=15000)
    pg.wait_for_timeout(1500)
    pg.locator("#home-brief").screenshot(path="scratch/shot_brief.png")
    print("brief captured")
    pg.click('[data-view="analytics"]')
    pg.wait_for_selector("#an-learning", state="visible", timeout=15000)
    pg.wait_for_timeout(1800)
    pg.locator("#an-learning").screenshot(path="scratch/shot_learning.png")
    print("learning captured")
    b.close()
