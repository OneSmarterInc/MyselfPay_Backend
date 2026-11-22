"""Explore public, read-only service selectors when the HTML has no matching price."""
from http.client import HTTPException
import os
import re
import time
from urllib.parse import urlsplit

from lxml import html

from .menus import payment_section
from .prices import EXAM, parse_value
from .public_fetch import fetch_public


def needs_browser(content, rows, url):
    if isinstance(content, bytes) and content.lstrip().startswith(b"%PDF-"):
        return False
    doc = html.document_fromstring(content)
    # Never start Chromium just to retry an HTTP rejection. Use it for observed
    # interactive controls whose options have not already produced a numeric row.
    labels = {" ".join(n.text_content().split()).casefold() for n in
              doc.xpath("//select/option|//*[@role='tab']") if EXAM.search(n.text_content())
              and payment_section(n) != "insured"}
    observed = {r["exam"].casefold() for r in rows if r["amount_cents"] is not None and r["payment_context"]}
    if labels - observed and doc.xpath("//script"):
        return True
    return bool(not observed and re.search(r"pric|self-pay|cash-pay", url, re.I)
                and doc.xpath("//*[@id='root' or @id='app' or @id='__next'][not(normalize-space())]")
                and doc.xpath("//script[@src]"))


# Selectors are restricted to the nearest section explicitly headed Self-pay/Cash.
# Accept only a visible, independently labelled result for the chosen exam.
OBSERVE = """([label, selector]) => {
  const norm = s => s.trim().replace(/\\s+/g, ' ').toLowerCase();
  const visible = e => e.checkVisibility({checkOpacity:true, checkVisibilityCSS:true});
  const out = [];
  const scope = e => {
    for (let a=e.parentElement; a; a=a.parentElement) {
      const titles=[...a.querySelectorAll(':scope > h1,:scope > h2,:scope > h3,:scope > h4')].map(h=>norm(h.innerText));
      if (titles.some(t=>/\\binsur(?:ed|ance)\\b/.test(t))) return '';
      const title=titles.find(t=>/^(self[- ]pay|cash(?:[- ]pay)?)( (prices?|pricing|rates?))?$/.test(t));
      if (title) return a;
    }
    return '';
  };
  for (const h of document.querySelectorAll('h2,h3,h4,strong,[data-exam]')) {
    if (!visible(h) || norm(h.innerText)!==norm(label) || h.closest('[role=tab],button,a,select')) continue;
    const cash=scope(h);
    const control = document.querySelector('[data-myselfpay-control="'+selector+'"]');
    if (!control || !cash || scope(control)!==cash) continue;
    for (let a=h.parentElement; a && a!==document.body; a=a.parentElement) {
      const t=a.innerText.trim();
      if (t.length>650 || a.querySelector('select,[role=tablist]')) break;
      if (!/you pay|estimated total cost|cash price|self[- ]pay price/i.test(t)) continue;
      const amounts=[...a.querySelectorAll('h2,h3,h4,strong,output,[data-price],.price')].filter(visible)
        .map(e=>e.innerText.trim()).filter(x=>/^\\$[\\d,]+(?:\\.\\d{2})?$/.test(x));
      if (new Set(amounts).size===1) out.push([amounts[0],cash.querySelector(':scope > h1,:scope > h2,:scope > h3,:scope > h4').innerText,t]);
      break;
    }
  }
  return out.length===1 ? out[0] : null;
}"""


def explore(content, url, known, fetch_asset=fetch_public):
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError:
        return [], "Interactive service menu found. Install the browser dependencies listed in the scraper guide."

    deadline = time.monotonic() + 35
    attempts, results, cache = 0, [], {}
    types = {"script": {"text/javascript", "application/javascript", "application/x-javascript"},
             "stylesheet": {"text/css"}, "xhr": {"application/json", "text/html", "text/plain"},
             "fetch": {"application/json", "text/html", "text/plain"}}

    def serve(route):
        nonlocal attempts
        req = route.request
        if req.method == "GET" and req.url == url and req.resource_type == "document":
            return route.fulfill(body=content, content_type="text/html")
        if (req.method != "GET" or req.resource_type not in types or time.monotonic() >= deadline
                or urlsplit(req.url).netloc != urlsplit(url).netloc or not req.url.startswith("https://")):
            return route.abort()
        key = (req.resource_type, req.url)
        if key not in cache:
            if attempts >= 20:
                return route.abort()
            attempts += 1
            try:
                cache[key] = fetch_asset(req.url, 1500000, origin=url, formats=types[req.resource_type])
            except (OSError, ValueError, UnicodeError, HTTPException):
                return route.abort()
        body, _, mime = cache[key]
        return route.fulfill(body=body, content_type=mime)

    try:
        with sync_playwright() as pw:
            options = {}
            if os.environ.get("MYSELFPAY_CHROMIUM"):
                options["executable_path"] = os.environ["MYSELFPAY_CHROMIUM"]
            # The dead proxy ensures any unhandled browser traffic cannot go direct.
            with pw.chromium.launch(proxy={"server": "http://127.0.0.1:9"},
                                    args=["--force-webrtc-ip-handling-policy=disable_non_proxied_udp"],
                                    **options) as browser:
                ctx = browser.new_context(service_workers="block", accept_downloads=False)
                ctx.route("**/*", serve)
                ctx.route_web_socket("**/*", lambda ws: ws.close())
                page = ctx.new_page()
                page.set_default_timeout(2200)
                page.goto(url, wait_until="domcontentloaded", timeout=15000)
                controls = page.locator("select,[role=tab]")
                controls.first.wait_for(state="attached", timeout=2500)
                remaining = 24
                for seq, control in enumerate(controls.all()[:35]):
                    if remaining <= 0 or time.monotonic() >= deadline:
                        break
                    if not control.is_visible():
                        continue
                    control.evaluate("(e,n) => e.dataset.myselfpayControl=n", str(seq))
                    native = control.evaluate("e => e.tagName === 'SELECT'")
                    labels = control.locator("option").all_text_contents() if native else [control.inner_text()]
                    for index, raw in enumerate(labels):
                        label = " ".join(raw.split())
                        if not EXAM.search(label) or label.casefold() in known:
                            continue
                        if remaining <= 0 or time.monotonic() >= deadline:
                            break
                        remaining -= 1
                        try:
                            if native:
                                control.select_option(index=index)
                            else:
                                control.click()
                            page.wait_for_load_state("networkidle", timeout=2500)
                            result = page.wait_for_function(OBSERVE, arg=[label,str(seq)], timeout=1400).json_value()
                            rate, section, evidence = result
                            value = parse_value(rate)
                            if value and page.url == url:
                                if "estimated total cost" in evidence.lower():
                                    value = (*value[:2], "estimate")
                                results.append((label, evidence, value, section))
                                known.add(label.casefold())
                        except Error:
                            continue
    except Error as exc:
        detail = "Chromium is unavailable. Run python -m playwright install chromium." if "executable" in str(exc).lower() else "The page did not expose a readable interactive price."
        if "BrowserType.launch" in str(exc) and "executable" not in str(exc).lower():
            detail = "Chromium could not start on this machine."
        return results, detail
    return results, f"Checked interactive service options; {len(results)} additional prices."
