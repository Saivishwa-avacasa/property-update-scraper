"""HTTP layer for Magicbricks.

Findings (Oct 2026): plain curl_cffi with a Chrome fingerprint gets every page
and the cards API without a captcha. Project pages server-render a
`window.SERVER_PRELOADED_STATE_ = {...}` JSON blob that holds the whole project
(header, RERA, units, amenities, developer, locality ratings); the scraper reads
that instead of parsing HTML.
"""
import json
import logging
import random
import time

from curl_cffi import requests as cr

from . import config

log = logging.getLogger("mb.http")


class Blocked(Exception):
    """403 / 429 / captcha. Stop this state for today."""


class Gone(Exception):
    """Project page removed."""


def extract_state(html):
    marker = "window.SERVER_PRELOADED_STATE_"
    i = html.find(marker)
    if i < 0:
        return None
    start = html.find("{", i)
    try:
        obj, _ = json.JSONDecoder().raw_decode(html, start)
    except json.JSONDecodeError as e:
        log.warning("preloaded state JSON decode failed: %s", e)
        return None
    return obj


class Site:
    def __init__(self, delay=config.DELAY_SECONDS):
        self.delay = delay
        self.requests_made = 0
        self.session = cr.Session(impersonate="chrome")
        self.session.headers.update({"Accept-Language": "en-IN,en;q=0.9"})
        self._warmed = False

    def _get(self, url, **kw):
        if self.requests_made:
            time.sleep(random.uniform(*self.delay))
        self.requests_made += 1
        last = None
        for attempt in range(3):
            try:
                resp = self.session.get(url, timeout=config.REQUEST_TIMEOUT, allow_redirects=True, **kw)
            except Exception as e:  # network hiccup: back off and retry
                last = e
                log.warning("request error %s: %s", url, e)
                time.sleep(10 * (attempt + 1))
                continue
            if resp.status_code in (403, 429):
                raise Blocked(f"HTTP {resp.status_code} at {url}")
            if resp.status_code >= 500:
                last = f"HTTP {resp.status_code}"
                time.sleep(10 * (attempt + 1))
                continue
            return resp
        raise Blocked(f"{last} at {url} after 3 tries")

    def _warm(self):
        """One visit to /magichomes gets the session cookies the API expects."""
        if not self._warmed:
            self._get(config.BASE + "/magichomes")
            self._warmed = True

    def cards(self, city_id, page):
        """One page of cards: city_id None = the all-India MagicHomes feed."""
        self._warm()
        where = f"city={city_id}" if city_id else "magichome=Y"
        url = f"{config.CARDS_URL}?&pageNo={page}&{where}"
        resp = self._get(url, headers={"Accept": "application/json",
                                       "Referer": config.BASE + "/magichomes"})
        try:
            return resp.json()
        except Exception as e:
            if "captcha" in (resp.text or "").lower():
                raise Blocked(f"captcha at {url}") from e
            raise RuntimeError(f"cards API did not return JSON at {url}: {resp.text[:200]!r}") from e

    def project_state(self, url):
        resp = self._get(url)
        if resp.status_code == 404:
            raise Gone(url)
        text = resp.text or ""
        state = extract_state(text)
        if state is None:
            if "captcha" in text[:20000].lower() or "access denied" in text[:3000].lower():
                raise Blocked(f"captcha / access denied at {url}")
            raise RuntimeError(f"no preloaded state at {url} (HTTP {resp.status_code})")
        if not (state.get("projectDetailData") or {}).get("prjMobileBean"):
            raise Gone(f"{url} (page has no project data)")
        return state
