"""Verify the film opener: full frame visible + scroll (and only scroll) drives playback."""
import asyncio


async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        errors, missing = [], []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("requestfailed", lambda r: missing.append(r.url))
        page.on("response", lambda r: missing.append(f"404 {r.url}") if r.status == 404 else None)
        await page.goto("http://localhost:8899", wait_until="domcontentloaded")
        await page.wait_for_timeout(4000)

        state0 = await page.evaluate("""() => {
            const v = document.getElementById('heroVideo');
            return {t: +v.currentTime.toFixed(2), paused: v.paused,
                    loop: v.hasAttribute('loop'), autoplay: v.hasAttribute('autoplay'),
                    fit: getComputedStyle(v).objectFit,
                    fill: !!document.querySelector('.film-fill'),
                    pinned: !!document.querySelector('.pin-spacer')};
        }""")

        for _ in range(8):
            await page.mouse.wheel(0, 400)
            await page.wait_for_timeout(100)
        await page.wait_for_timeout(1500)
        mid = await page.evaluate("""() => {
            const st = ScrollTrigger.getAll().find(x => x.pin);
            return {t: +document.getElementById('heroVideo').currentTime.toFixed(2),
                    scrollY: Math.round(window.scrollY),
                    prog: st ? +st.progress.toFixed(3) : null};
        }""")

        # scroll back up — a scrubbed video REWINDS, a playing one keeps going
        for _ in range(8):
            await page.mouse.wheel(0, -400)
            await page.wait_for_timeout(100)
        await page.wait_for_timeout(1500)
        back = await page.evaluate("() => +document.getElementById('heroVideo').currentTime.toFixed(2)")
        await page.screenshot(path="film_check_final.png")
        await browser.close()

    print("at top:  ", state0)
    print("scrolled down ->", mid)
    print("scrolled up   -> t =", back)
    print("errors:", errors[:4] if errors else "none")
    print("missing files:", missing[:4] if missing else "none")
    scrubbed = state0["pinned"] and mid["t"] > 1 and back < mid["t"] - 0.5 and state0["t"] < 0.5
    print("VERDICT:", "PASS — pinned, scroll drives it both directions, full frame" if scrubbed else "FAIL")


asyncio.run(main())
