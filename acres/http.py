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
        self.browser_mode = False
        self._pw = None
        self._ctx = None
        self._page = None
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

        Fast path: curl_cffi. When 99acres answers with the captcha, the run
        switches to "browser mode": every remaining page is loaded in a headed
        Chrome (which passes the invisible reCAPTCHA by itself) and the JSON is
        read from the live page. Slower (~5 s/page) but it does not depend on
        copying cookies around.

        Raises Gone on 404, Blocked when even Chrome can't get through."""
        if self.browser_mode:
            return self._browser_fetch(url)
        for attempt in range(3):
            try:
                resp = self._get(url)
            except Exception as e:  # curl error / timeout: try once more, then browser
                log.warning("request error %s: %s", url, e)
                if attempt == 0:
                    continue
                return self._fallback_to_browser(url, f"request error {e}")
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
            return self._fallback_to_browser(url, kind)
        return self._fallback_to_browser(url, "retries exhausted")

    def _fallback_to_browser(self, url, reason):
        if not self.allow_browser:
            raise Blocked(f"{reason} at {url} (browser fallback disabled)")
        if not self.browser_mode:
            log.warning("%s at %s - switching to browser mode for the rest of this run", reason, url)
            self.browser_mode = True
        return self._browser_fetch(url)

    # ── browser mode ───────────────────────────────────────────────────────
    def _browser(self):
        """Lazily start a headed Chrome with the persistent profile."""
        if self._ctx is not None:
            return self._ctx
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise Blocked("playwright not installed; run: pip install playwright") from e
        self._pw = sync_playwright().start()
        profile = self.state_dir / "chrome_profile"
        try:
            self._ctx = self._pw.chromium.launch_persistent_context(
                str(profile), channel="chrome", headless=False,
                viewport={"width": 1366, "height": 850}, locale="en-IN",
                args=["--disable-blink-features=AutomationControlled"],
            )
        except Exception as e:
            self._pw.stop()
            self._pw = None
            raise Blocked(f"could not launch Chrome: {e}") from e
        self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        return self._ctx

    def _browser_fetch(self, url, wait_seconds=config.CAPTCHA_WAIT_SECONDS):
        self._browser()
        page = self._page
        self._sleep()
        self.requests_made += 1
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            raise Blocked(f"browser navigation failed: {e}") from e
        if resp is not None and resp.status == 404:
            raise Gone(url)
        deadline = time.time() + wait_seconds
        renavigated = False
        asked = False
        while time.time() < deadline:
            if "verifycaptcha" in page.url and not asked:
                asked = True
                self._ask_human(page, wait_seconds)
            try:
                data = page.evaluate(
                    "(window.__initialData__ && (window.__initialData__.srp || window.__initialData__.projectDetailState)) ? window.__initialData__ : null")
            except Exception:
                data = None
            if data:
                self._export_cookies()
                return data
            # The captcha redirect drops the query string and lands on a 400 page
            # with the same path, so compare the full URL, not just the path.
            cur = page.url
            if "verifycaptcha" not in cur and cur != url and not renavigated:
                renavigated = True
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                except Exception:
                    pass
            elif "verifycaptcha" not in cur and renavigated and "400" in (page.title() or ""):
                break
            time.sleep(1.5)
        if "verifycaptcha" in page.url:
            raise Blocked(f"captcha not passed in {wait_seconds}s at {url} (a person at the PC can solve it once)")
        raise Blocked(f"page did not render data at {url}")

    def _ask_human(self, page, wait_seconds):
        """99acres shows a 'tick the box' reCAPTCHA. Only a person can do that:
        bring Chrome to the front, beep, and wait. Once ticked, the profile keeps
        the cookie and the next runs usually go straight through."""
        log.warning("99acres is asking for a CAPTCHA. Please tick the box in the Chrome window and click "
                    "Proceed. Waiting up to %d minutes.", wait_seconds // 60)
        try:
            page.bring_to_front()
        except Exception:
            pass
        try:
            import winsound
            for _ in range(3):
                winsound.Beep(880, 250)
                time.sleep(0.15)
        except Exception:
            pass

    def _export_cookies(self):
        """Keep the curl session's cookie jar in step with Chrome, so the next
        run can start on the fast path again."""
        try:
            cookies = self._ctx.cookies()
            for c in cookies:
                self.session.cookies.set(c["name"], c["value"], domain=c.get("domain", ".99acres.com"))
            self._save_cookies(cookies)
        except Exception as e:
            log.debug("cookie export skipped: %s", e)

    def close(self):
        if self._ctx is not None:
            try:
                self._ctx.close()
            except Exception:
                pass
            self._ctx = None
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:
                pass
            self._pw = None

    # ── captcha warm-up with a real Chrome ─────────────────────────────────
    def warm_up(self, url):
        """Load `url` in the headed Chrome once (passes the captcha), save the
        cookies for the fast path, close Chrome. Used by --warm-cookies."""
        self.browser_mode = True
        try:
            self._browser_fetch(url)
            log.info("warm-up ok, cookies saved to %s", self.cookie_file)
        finally:
            self.close()
            self.browser_mode = False

