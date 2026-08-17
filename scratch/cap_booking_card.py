"""Capture the 'Booked by Adapix' Home card and frame it as a square poster."""
from playwright.sync_api import sync_playwright
import pathlib, base64
base = pathlib.Path(__file__).parent
BASE = "http://localhost:3000"
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1400, "height": 1000}, device_scale_factor=2)
    pg = ctx.new_page()
    pg.goto(BASE + "/login")
    pg.request.post(BASE + "/auth/login", form={"email": "demo@adapix.test", "password": "demo12345"})
    pg.goto(BASE + "/app")
    pg.wait_for_selector("#home-bookings", state="visible", timeout=15000)
    pg.wait_for_timeout(1200)
    pg.locator("#home-bookings").screenshot(path=str(base / "shot_booking.png"))
    print("booking card captured")
    img64 = base64.b64encode((base / "shot_booking.png").read_bytes()).decode()
    html = """<!doctype html><html><head><meta charset="utf-8"><style>
*{margin:0;padding:0;box-sizing:border-box}
body{width:1080px;height:1080px;font-family:'Segoe UI',sans-serif;background:#081120;position:relative;overflow:hidden;display:flex;flex-direction:column;justify-content:center;padding:0 84px}
body::before{content:'';position:absolute;width:900px;height:900px;border-radius:50%;background:radial-gradient(circle,rgba(74,222,128,.14),transparent 60%);top:-350px;right:-300px}
.bubble{position:relative;z-index:2;align-self:flex-start;background:#1f2a44;color:#dbe4f5;font-size:31px;padding:22px 30px;border-radius:26px 26px 26px 8px;max-width:640px}
.bubble.me{align-self:flex-end;background:linear-gradient(135deg,#34d399,#0ea5e9);color:#04121f;border-radius:26px 26px 8px 26px;margin-top:20px;font-weight:600}
.arrow{position:relative;z-index:2;text-align:center;font-size:44px;color:#4ade80;margin:34px 0 26px}
.shot{position:relative;z-index:2;width:100%;border-radius:20px;border:1px solid rgba(74,222,128,.35);box-shadow:0 26px 70px rgba(0,0,0,.55)}
.cap{position:relative;z-index:2;margin-top:36px;font-size:38px;font-weight:800;color:#fff;text-align:center}
.cap em{font-style:normal;color:#4ade80}
.foot{position:absolute;bottom:56px;left:0;right:0;text-align:center;font-size:22px;color:#7d8bab;font-weight:600}
.foot b{color:#a7f3d0}
</style></head><body>
<div class="bubble">Yeah let's do it &mdash; when could you come out?</div>
<div class="bubble me">I have Monday Aug 17 at 7am, Tuesday at 12pm, or Wednesday at 3pm &mdash; which works best?</div>
<div class="arrow">&darr;</div>
<img class="shot" src="data:image/png;base64,__IMG__">
<div class="cap">A text reply became a <em>booked job.</em> Nobody picked up a phone.</div>
<div class="foot">Real screen from <b>Adapix</b> &mdash; the AI that follows up, books &amp; reports back · adapixai.com</div>
</body></html>""".replace("__IMG__", img64)
    f = base / "feature_booking.html"; f.write_text(html, encoding="utf-8")
    pg2 = b.new_page(viewport={"width": 1080, "height": 1080}, device_scale_factor=2)
    pg2.goto(f.as_uri()); pg2.wait_for_timeout(800)
    pg2.screenshot(path=str(base / "feature_booking.png"))
    print("poster rendered")
    b.close()
