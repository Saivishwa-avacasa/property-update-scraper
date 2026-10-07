"""HTTP layer for 99acres.

Findings that shaped this module (Oct 2026):

* Plain `requests` gets 403 even from a home IP. `curl_cffi` with a Chrome TLS
  fingerprint gets through.
* The first response of a fresh session is a tiny "loader" shell. Once the
  session has 99acres' cookies, the same URL returns the full server-rendered
  page with `window.__initialData__ = {...}` (150-900 KB of JSON). Everything
  we need is in that JSON; no HTML parsing.
* Search/listing pages additionally sit behind a "warden" reCAPTCHA. A real,
  headed Chrome passes the invisible challenge on its own; headless Chrome gets
  "Access Denied". Cookies from that Chrome session make curl_cffi pass too.
  So: run headed Chrome only when a captcha shows up, export its cookies, and
  do the actual scraping with curl_cffi.
"""
import json
import logging
import random
import time
from pathlib import Path

from curl_cffi import requests as cr

from . import config

log = logging.getLogger("acres.http")


class Blocked(Exception):
    """Captcha / 403 that we could not get past. Stop this state for today."""


class Gone(Exception):
    """404 - project removed from 99acres."""


def extract_initial_data(html):
    """Return the window.__initialData__ object, or None if the page has none."""
    marker = "window.__initialData__"
    i = html.find(marker)
    if i < 0:
        return None
    start = html.find("{", i)
    if start < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(html, start)
    except json.JSONDecodeError as e:
        log.warning("initialData JSON decode failed: %s", e)
        return None
    return obj


def classify(resp):
    text = resp.text or ""
    if resp.status_code == 404:
        return "gone"
    if resp.status_code in (403, 429):
        return "blocked"
    if "verifycaptcha" in resp.url or "verifycaptcha" in text[:20000]:
        return "captcha"
    if "__initialData__" in text:
        return "data"
    if 'data-label="LOADER_PAGE"' in text:
        return "loader"
    if "Access Denied" in text[:3000]:
        return "blocked"
    return "unknown"


class Site:
    """One polite session against 99acres with cookie persistence and
    automatic captcha warm-up through a headed Chrome."""

    def __init__(self, state_dir=None, delay=config.DELAY_SECONDS, allow_browser=True):
        self.state_dir = Path(state_dir or config.STATE_DIR)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.cookie_file = self.state_dir / "cookies.json"
        self.delay = delay
        self.allow_browser = allow_browser
        self.warmed_this_run = False
        self.requests_made = 0
        self.session = cr.Session(impersonate="chrome")
        self.session.headers.update({"Accept-Language": "en-IN,en;q=0.9"})
        self._load_cookies()

    # ── cookies ────────────────────────────────────────────────────────────
    def _load_cookies(self):
        if not self.cookie_file.exists():
            return
        try:
            for c in json.loads(self.cookie_file.read_text(encoding="utf-8")):
                self.session.cookies.set(c["name"], c["value"], domain=c.get("domain", ".99acres.com"))
            log.info("loaded cookies from %s", self.cookie_file)
        except Exception as e:  # corrupt file: start clean
            log.warning("could not load cookies: %s", e)

    def _save_cookies(self, cookies):
        self.cookie_file.write_text(json.dumps(cookies, indent=1), encoding="utf-8")

    # ── fetching ───────────────────────────────────────────────────────────
    def _sleep(self):
        if self.requests_made:
            time.sleep(random.uniform(*self.delay))

    def _get(self, url):
        self._sleep()
        self.requests_made += 1
        return self.session.get(url, timeout=config.REQUEST_TIMEOUT, allow_redirects=True)

    def fetch_initial_data(self, url, warmup_url=None):
        """GET `url` and return its __initialData__ dict.

        Raises Gone on 404, Blocked when even a browser warm-up can't get past
        the captcha."""
        for attempt in range(4):
            resp = self._get(url)
            kind = classify(resp)
            log.debug("%s -> %s %s (%d bytes)", url, resp.status_code, kind, len(resp.text or ""))
            if kind == "data":
                data = extract_initial_data(resp.text)
                if data is not None:
                    return data
                kind = "unknown"
            if kind == "gone":
                raise Gone(url)
            if kind == "loader" and attempt == 0:
                continue  # cookies are set now, second request gets the SSR page
            if kind in ("captcha", "blocked", "loader", "unknown"):
                if not self.warmed_this_run and self.allow_browser:
                    self.warm_up(warmup_url or url)
                    continue
                raise Blocked(f"{kind} at {url}")
        raise Blocked(f"gave up after retries at {url}")

    # ── captcha warm-up with a real Chrome ─────────────────────────────────
    def warm_up(self, url, wait_seconds=180):
        """Open `url` in a headed Chrome (persistent profile), wait until the
        page carries __initialData__ (the invisible reCAPTCHA usually passes by
        itself; if a visible one shows, a person at the PC can solve it), then
        copy the cookies into the curl session."""
        self.warmed_this_run = True
        log.warning("captcha/blocked - warming up cookies with Chrome: %s", url)
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise Blocked("playwright not installed; run: pip install playwright") from e

        profile = self.state_dir / "chrome_profile"
        with sync_playwright() as pw:
            try:
                ctx = pw.chromium.launch_persistent_context(
                    str(profile), channel="chrome", headless=False,
                    viewport={"width": 1366, "height": 850}, locale="en-IN",
                    args=["--disable-blink-features=AutomationControlled"],
                )
            except Exception as e:
                raise Blocked(f"could not launch Chrome: {e}") from e
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                deadline = time.time() + wait_seconds
                ok = False
                while time.time() < deadline:
                    try:
                        if page.evaluate("!!(window.__initialData__ && (window.__initialData__.srp || window.__initialData__.projectDetailState))"):
                            ok = True
                            break
                    except Exception:
                        pass
                    # The captcha redirect drops the query string and lands on a
                    # 400 page; just navigate again once the cookie is set.
                    if "verifycaptcha" not in page.url and ("400" in page.title() or page.url.rstrip("/") != url.rstrip("/")):
                        try:
                            page.goto(url, wait_until="domcontentloaded", timeout=60000)
                        except Exception:
                            pass
                    time.sleep(2)
                cookies = ctx.cookies()
            finally:
                ctx.close()
        if not ok:
            raise Blocked("Chrome warm-up did not get past the captcha in time")
        for c in cookies:
            self.session.cookies.set(c["name"], c["value"], domain=c.get("domain", ".99acres.com"))
        self._save_cookies(cookies)
        log.info("warm-up ok, %d cookies saved", len(cookies))
