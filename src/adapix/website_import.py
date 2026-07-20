"""Import business info from the owner's website.

The owner pastes their site URL; we fetch a few key pages, hand the text to
Claude, and get back the structured facts the wizard would have asked for —
name, phone, hours, description, services with prices, FAQ-style knowledge.
The result is staged in the org profile blob under data["website_import"]
(status: running | ready | failed | applied) so the dashboard can show a
review reminder until the owner applies it. Applying MERGES: it only fills
fields the owner hasn't set and appends services/knowledge that aren't
already there — it never overwrites something taught by hand.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from datetime import datetime
from html.parser import HTMLParser

MAX_PAGES = 4
MAX_CHARS_PER_PAGE = 12_000
INTERESTING = re.compile(r"about|service|pricing|price|contact|faq|hours|menu", re.I)


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.links: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"):
            self._skip += 1
        if tag == "a":
            href = dict(attrs).get("href") or ""
            self.links.append(href)

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.parts.append(data.strip())


def _assert_public_url(url: str) -> None:
    """SSRF guard: only fetch public http(s) hosts. Blocks a logged-in user
    from making the server fetch its own cloud metadata endpoint
    (169.254.169.254), localhost, or any private/internal address and read
    the response back through the import preview."""
    import ipaddress
    import socket

    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("only http/https URLs can be imported")
    host = parsed.hostname
    if not host:
        raise ValueError("invalid URL")
    if parsed.port is not None and parsed.port not in (80, 443):
        raise ValueError("only ports 80 and 443 are allowed")
    # Resolve EVERY address the host maps to and reject if ANY is non-public
    # (a hostname can resolve to both a public and a private A record).
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        raise ValueError("could not resolve host")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise ValueError("that address isn't publicly reachable")


def _fetch(url: str) -> tuple[str, list[str]]:
    _assert_public_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; AdapixImport/1.0)"})
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read(1_500_000).decode("utf-8", errors="replace")
    p = _TextExtractor()
    try:
        p.feed(raw)
    except Exception:
        pass
    return " ".join(p.parts)[:MAX_CHARS_PER_PAGE], p.links


def fetch_site_text(url: str) -> str:
    """Homepage plus up to a few same-domain pages that look informative."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    base = urllib.parse.urlparse(url)
    text, links = _fetch(url)
    pages = [f"=== PAGE: {url} ===\n{text}"]
    seen = {url.rstrip("/")}
    for href in links:
        if len(pages) >= MAX_PAGES:
            break
        full = urllib.parse.urljoin(url, href)
        parsed = urllib.parse.urlparse(full)
        if parsed.netloc != base.netloc:
            continue
        full = full.split("#")[0].rstrip("/")
        if full in seen or not INTERESTING.search(parsed.path or ""):
            continue
        seen.add(full)
        try:
            t, _ = _fetch(full)
            pages.append(f"=== PAGE: {full} ===\n{t}")
        except Exception:
            continue
    return "\n\n".join(pages)


EXTRACT_PROMPT = """\
You are reading the public website of a small business. Extract ONLY facts that
actually appear in the text — never invent, guess, or embellish. Leave a field
empty ("" or []) when the site doesn't state it.

Return STRICT JSON (no markdown fence, no commentary) with exactly this shape:
{
  "business_name": "",
  "owner_name": "",
  "phone": "",
  "hours": "",
  "description": "",            // 1-3 sentences, in the business's own words where possible
  "services": [ {"name": "", "price": "", "details": ""} ],
  "knowledge": [ {"q": "", "a": ""} ]   // FAQ-style facts customers ask about
}
"""


def extract_business_info(site_text: str, settings) -> dict:
    import anthropic
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    resp = client.messages.create(
        model=settings.adapix_model,
        max_tokens=2000,
        system=EXTRACT_PROMPT,
        messages=[{"role": "user", "content": site_text[:48_000]}],
    )
    raw = resp.content[0].text.strip()
    raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.M).strip()
    return json.loads(raw)


def run_import(org_id: str, url: str) -> None:
    """Background task: fetch -> extract -> stage. Writes status transitions
    into the org profile so the UI can poll."""
    from sqlalchemy.orm import Session
    from .config import Settings
    from .db import get_engine
    from .api.app_routes import _load_org_profile_data, _save_org_profile_data

    def _set(record: dict) -> None:
        with Session(get_engine()) as s:
            data = _load_org_profile_data(s, org_id)
            data["website_import"] = {**(data.get("website_import") or {}), **record}
            _save_org_profile_data(s, org_id, data)
            s.commit()

    try:
        site_text = fetch_site_text(url)
        if len(site_text) < 200:
            raise ValueError("The site returned almost no readable text")
        info = extract_business_info(site_text, Settings())
        _set({"url": url, "status": "ready", "data": info, "error": "",
              "at": datetime.utcnow().isoformat() + "Z"})
    except Exception as e:
        _set({"url": url, "status": "failed", "error": str(e)[:300],
              "at": datetime.utcnow().isoformat() + "Z"})


def apply_import(org_id: str) -> dict:
    """Merge the staged extraction into the taught profile. Fill-only for
    scalar fields; append-only (deduped by name/question) for lists."""
    import secrets
    from sqlalchemy.orm import Session
    from .db import get_engine
    from .api.app_routes import _load_org_profile_data, _save_org_profile_data

    with Session(get_engine()) as s:
        data = _load_org_profile_data(s, org_id)
        imp = data.get("website_import") or {}
        if imp.get("status") != "ready":
            raise ValueError("no import ready to apply")
        info = imp.get("data") or {}

        practice = dict(data.get("practice") or {})
        filled = []
        for src, dst in (("business_name", "name"), ("owner_name", "owner"),
                         ("phone", "phone"), ("hours", "hours")):
            if (info.get(src) or "").strip() and not (practice.get(dst) or "").strip():
                practice[dst] = info[src].strip()
                filled.append(dst)
        data["practice"] = practice
        if (info.get("description") or "").strip() and not (data.get("description") or "").strip():
            data["description"] = info["description"].strip()
            filled.append("description")

        services = list(data.get("services") or [])
        have = {(sv.get("name") or "").strip().lower() for sv in services}
        added_services = 0
        for sv in info.get("services") or []:
            name = (sv.get("name") or "").strip()
            if name and name.lower() not in have:
                services.append({"id": secrets.token_hex(4), "name": name,
                                 "price": (sv.get("price") or "").strip(),
                                 "details": (sv.get("details") or "").strip(),
                                 "pricing_type": "one_time", "billing_period": "month",
                                 "term_length": ""})
                have.add(name.lower())
                added_services += 1
        data["services"] = services

        kb = list(data.get("knowledge_base") or [])
        have_q = {(k.get("q") or "").strip().lower() for k in kb}
        added_kb = 0
        for k in info.get("knowledge") or []:
            q, a = (k.get("q") or "").strip(), (k.get("a") or "").strip()
            if q and a and q.lower() not in have_q:
                kb.append({"id": secrets.token_hex(4), "q": q, "a": a})
                have_q.add(q.lower())
                added_kb += 1
        data["knowledge_base"] = kb

        data["website_import"] = {**imp, "status": "applied",
                                  "applied_at": datetime.utcnow().isoformat() + "Z"}
        _save_org_profile_data(s, org_id, data)
        s.commit()
        return {"filled": filled, "services_added": added_services, "knowledge_added": added_kb}
