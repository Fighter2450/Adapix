"""Steal-this-template cards (notes-app look — a NEW structural layout) and a
founder-note card. Rendered 1080x1080 @2x."""
from playwright.sync_api import sync_playwright
import pathlib
base = pathlib.Path(__file__).parent

TMPL = """<!doctype html><html><head><meta charset="utf-8"><style>
*{margin:0;padding:0;box-sizing:border-box}
body{width:1080px;height:1080px;font-family:'Segoe UI',sans-serif;background:linear-gradient(150deg,#12102b,#1c1140 55%,#0d1b33);display:flex;align-items:center;justify-content:center;position:relative;overflow:hidden}
.tape{position:absolute;top:74px;left:50%;transform:translateX(-50%) rotate(-2deg);background:rgba(167,139,250,.25);border:1px solid rgba(167,139,250,.4);color:#e9e5ff;font-size:22px;font-weight:800;letter-spacing:.18em;text-transform:uppercase;padding:12px 34px;border-radius:6px;z-index:3}
.note{width:820px;background:#fffbea;border-radius:6px;padding:64px 62px 56px;transform:rotate(1.1deg);box-shadow:0 40px 90px rgba(0,0,0,.5);position:relative}
.note::before{content:'';position:absolute;top:0;left:0;right:0;height:56px;background:repeating-linear-gradient(90deg,transparent,transparent 6px,rgba(0,0,0,.02) 6px,rgba(0,0,0,.02) 7px);border-bottom:1px solid #f1e9c8}
.label{font-size:22px;font-weight:800;letter-spacing:.14em;text-transform:uppercase;color:#b45309;margin-bottom:26px}
.msg{font-size:37px;line-height:1.5;color:#1f2937;font-weight:600}
.msg .blank{color:#b45309;border-bottom:3px dashed #d97706;padding:0 4px}
.why{margin-top:38px;padding-top:30px;border-top:2px dashed #e7dcb0;font-size:24px;color:#6b7280;line-height:1.5}
.foot{position:absolute;bottom:64px;left:0;right:0;text-align:center;font-size:23px;color:#8a87b0;font-weight:600;z-index:3}
.foot b{color:#c4b5fd}
</style></head><body>
<div class="tape">__TAPE__</div>
<div class="note"><div class="label">__LABEL__</div><div class="msg">__MSG__</div><div class="why">__WHY__</div></div>
<div class="foot">Free follow-up template · from the team at <b>Adapix</b> · adapixai.com</div>
</body></html>"""

CARDS = [
    ("tmpl_revival", "Steal this text", "The quiet-quote revival",
     "&ldquo;Hi <span class=blank>[name]</span>, it's <span class=blank>[you]</span> from <span class=blank>[business]</span> &mdash; still want that <span class=blank>[job]</span> done? Happy to lock in a day this week if you're ready.&rdquo;",
     "Why it works: it's specific, it's easy to answer, and it assumes the job is still on &mdash; because half the time, it is. They just got busy."),
    ("tmpl_missedcall", "Steal this text", "The missed-call text-back",
     "&ldquo;Hi, this is <span class=blank>[you]</span> at <span class=blank>[business]</span> &mdash; sorry I missed you just now! What can I help with? I can usually text quicker than I can call back.&rdquo;",
     "Why it works: sent within 5 minutes, it catches them before they dial the next company on the list. Speed beats polish."),
    ("tmpl_dayafter", "Steal this text", "The day-after-quote text",
     "&ldquo;Thanks again for having me out yesterday, <span class=blank>[name]</span>. That quote's good for 30 days &mdash; any questions on it, just text me here.&rdquo;",
     "Why it works: it opens the door without pushing. The 30-day line adds a gentle clock, and &ldquo;text me here&rdquo; keeps the reply friction at zero."),
]

FOUNDER = """<!doctype html><html><head><meta charset="utf-8"><style>
*{margin:0;padding:0;box-sizing:border-box}
body{width:1080px;height:1080px;font-family:Georgia,'Times New Roman',serif;background:#0e0c22;position:relative;overflow:hidden;display:flex;align-items:center}
body::before{content:'';position:absolute;width:800px;height:800px;border-radius:50%;background:radial-gradient(circle,rgba(244,114,182,.13),transparent 60%);top:-300px;left:-200px}
.inner{position:relative;z-index:2;padding:0 110px}
.pre{font-family:'Segoe UI',sans-serif;font-size:21px;font-weight:800;letter-spacing:.22em;text-transform:uppercase;color:#f472b6;margin-bottom:36px}
p{font-size:41px;line-height:1.55;color:#e8e6f5;margin-bottom:30px}
p b{color:#fff}
.sig{margin-top:26px;font-style:italic;font-size:33px;color:#a78bfa}
.sub{font-family:'Segoe UI',sans-serif;font-size:22px;color:#8a87b0;margin-top:10px}
</style></head><body><div class="inner">
<div class="pre">A note from the founder</div>
<p>This week Adapix stopped being a tool and started being an <b>employee</b>.</p>
<p>It now files a morning report, books appointments from a text reply, and studies its own results to learn when <b>your</b> customers actually respond.</p>
<p>Built by one founder, shipped every week.</p>
<div class="sig">&mdash; Rocco, founder</div>
<div class="sub">adapixai.com</div>
</div></body></html>"""

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1080, "height": 1080}, device_scale_factor=2)
    for key, tape, label, msg, why in CARDS:
        html = TMPL.replace("__TAPE__", tape).replace("__LABEL__", label).replace("__MSG__", msg).replace("__WHY__", why)
        f = base / f"{key}.html"; f.write_text(html, encoding="utf-8")
        pg.goto(f.as_uri()); pg.wait_for_timeout(700)
        pg.screenshot(path=str(base / f"{key}.png")); print(key)
    f = base / "founder_note.html"; f.write_text(FOUNDER, encoding="utf-8")
    pg.goto(f.as_uri()); pg.wait_for_timeout(700)
    pg.screenshot(path=str(base / "founder_note.png")); print("founder_note")
    b.close()
