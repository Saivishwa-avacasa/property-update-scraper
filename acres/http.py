# acres/http.py
import json
import logging
import random
import time

from curl_cffi import requests as cr

from . import config
from .captcha_solver import solve_captcha_with_browser

log = logging.getLogger("acres.http")

class Blocked(Exception):
    """403 / 429 / 417 / captcha."""

class Gone(Exception):
    """Project page removed."""

class Site:
    def __init__(self, delay=(1.5, 3.5), allow_browser=True):
        self.delay = delay
        self.allow_browser = allow_browser
        self.requests_made = 0
        self.session = cr.Session(impersonate="chrome")
        self.session.headers.update({"Accept-Language": "en-IN,en;q=0.9"})
        self._warmed = False
        self.browser_mode = False # Kept for compatibility with scrape_99acres.py

    def close(self):
        pass

    def _get(self, url, **kw):
        if self.requests_made:
            time.sleep(random.uniform(*self.delay))
        self.requests_made += 1
        
        for attempt in range(3):
            try:
                resp = self.session.get(url, timeout=45, allow_redirects=True, **kw)
            except Exception as e:
                log.warning("request error %s: %s", url, e)
                time.sleep(10 * (attempt + 1))
                continue

            # Check for CAPTCHA or Blocks (403, 429, and 417 which happens when cookies fail)
            is_blocked = resp.status_code in (403, 429, 417)
            
            # Sometimes 99acres returns a 200 with a captcha page embedded
            if not is_blocked and resp.text:
                text_lower = resp.text[:5000].lower()
                if "captcha" in text_lower or "challenge-platform" in text_lower or "just a moment" in text_lower:
                    is_blocked = True
            
            if is_blocked and self.allow_browser:
                log.warning("⚠️ CAPTCHA detected on %s. Calling Laya Solver...", url)
                current_cookies = {c.name: c.value for c in self.session.cookies.jar}
                
                # Trigger the SeleniumBase + Laya bypass
                new_cookies = solve_captcha_with_browser(url, current_cookies)
                
                # Inject new cookies into curl_cffi
                for k, v in new_cookies.items():
                    self.session.cookies.set(k, v, domain=".99acres.com")
                
                # Warm up the homepage to register the new cookies
                log.info("🔥 Warming up homepage with new cookies...")
                try:
                    self.session.get("https://www.99acres.com/", timeout=20)
                except Exception:
                    pass

                # Retry the request immediately
                resp = self.session.get(url, timeout=45, allow_redirects=True, **kw)
                
                # If it's still returning 417 or 0 bytes, the bypass failed
                if resp.status_code == 200 and len(resp.text) > 1000:
                    log.info("✅ Successfully bypassed CAPTCHA and fetched page.")
                    return resp
                else:
                    raise Blocked(f"Bypass failed or returned empty page (HTTP {resp.status_code}) at {url}")
                    
            elif is_blocked:
                raise Blocked(f"HTTP {resp.status_code} at {url} (no browser allowed)")

            if resp.status_code >= 500:
                time.sleep(10 * (attempt + 1))
                continue
                
            return resp
            
        raise Blocked(f"Failed after 3 tries at {url}")

    def warm_up(self, url):
        """Kept for compatibility with existing CLI flags."""
        try:
            self._get(url)
        except Blocked as e:
            log.error("Warmup blocked: %s", e)

    def fetch_initial_data(self, url, warmup_url=None):
        """Fetches the page and extracts the initial data JSON blob (99acres specific)."""
        if not self._warmed and warmup_url:
            self.warm_up(warmup_url)
            self._warmed = True

        resp = self._get(url)
        if resp.status_code == 404:
            raise Gone(url)

        # 99acres embeds the data in window.__initialData__
        text = resp.text
        marker = "window.__initialData__"
        
        if marker not in text:
            # Fallback to other common markers just in case
            marker = "window.__INITIAL_STATE__"
            if marker not in text:
                marker = "window.__PRELOADED_STATE__"
                if marker not in text:
                    raise RuntimeError(f"no window.__initialData__ at {url} (HTTP {resp.status_code}, {len(text)} bytes). Check if page is blank or changed structure.")

        i = text.find(marker)
        # The JSON usually starts with an '=' or ':' after the marker
        start = text.find("{", i)
        if start == -1:
            raise RuntimeError(f"found marker {marker} but no JSON object at {url}")

        try:
            obj, _ = json.JSONDecoder().raw_decode(text, start)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"preloaded state JSON decode failed at {url}: {e}")
        
        return obj