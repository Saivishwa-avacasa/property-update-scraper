"""Collect 99acres Goa search results by reading what the browser itself loads.

Opens your installed Chrome (visible), lets the page make its own requests,
and records the listings from two places:
  * responses from .../api-aggregator/srp/search that the page fetches, and
  * window.__initialData__ embedded in each results page (server-rendered).
It then clicks to the next results page, up to MAX_PAGES.

If a CAPTCHA or verification page appears, solve it yourself in the browser
window; the script waits CAPTCHA_WAIT_SECONDS for you.

Setup (once):
    .venv\\Scripts\\python.exe -m pip install playwright
Run:
    .venv\\Scripts\\python.exe scrape_srp_browser.py              # visible window
    .venv\\Scripts\\python.exe scrape_srp_browser.py --headless   # no window
"""
import json
import random
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

START_URL = "https://www.99acres.com/search/property/buy/goa?city=233"
API_MARKER = "api-aggregator/srp/search"
MAX_PAGES = 5
CAPTCHA_WAIT_SECONDS = 90
DATA_WAIT_SECONDS = 20
DELAY_SECONDS = (5.0, 10.0)   # same politeness gap as acres/config.py
OUT_FILE = Path("goa_srp_browser.json")
HEADLESS = "--headless" in sys.argv


def find_properties(obj, depth=0):
    """Return the first list stored under a 'properties' key, searching nested dicts."""
    if depth > 6 or not isinstance(obj, dict):
        return None
    props = obj.get("properties")
    if isinstance(props, list):
        return props
    for value in obj.values():
        found = find_properties(value, depth + 1)
        if found is not None:
            return found
    return None


def listing_key(p):
    for k in ("propertyId", "PROP_ID", "projectUnitId", "landingPageRelativeUrl"):
        if p.get(k):
            return f"{k}:{p[k]}"
    return json.dumps(p, sort_keys=True)


class Collector:
    def __init__(self):
        self.listings = {}          # key -> listing, keeps first-seen order
        self.api_responses = 0

    def add(self, props, source):
        new = 0
        for p in props or []:
            if isinstance(p, dict):
                key = listing_key(p)
                if key not in self.listings:
                    self.listings[key] = p
                    new += 1
        print(f"  {source}: {len(props or [])} listings ({new} new), total {len(self.listings)}")
        return new

    def on_response(self, response):
        if API_MARKER not in response.url or response.status != 200:
            return
        try:
            data = response.json()
        except Exception:
            return
        self.api_responses += 1
        self.add(find_properties(data), "API response")


def read_initial_data(page, collector):
    try:
        data = page.evaluate("() => window.__initialData__ || null")
    except Exception:
        return 0
    return collector.add(find_properties(data), "page __initialData__") if data else 0


def wait_for_listings(page, collector, before):
    """Wait until listings arrive; if none do, give the user time to solve a CAPTCHA."""
    deadline = time.time() + DATA_WAIT_SECONDS
    while time.time() < deadline:
        page.wait_for_timeout(1000)
        if len(collector.listings) > before:
            return True
    if read_initial_data(page, collector):
        return True
    if HEADLESS:
        return False
    print(f"  No listings yet. If a CAPTCHA is showing, solve it in the browser "
          f"(waiting {CAPTCHA_WAIT_SECONDS}s)...")
    time.sleep(CAPTCHA_WAIT_SECONDS)
    read_initial_data(page, collector)
    return len(collector.listings) > before


def click_next(page, current):
    """Scroll to the pagination and click the next page number.

    99acres renders pagination as
    <div class="Pagination__srpPageBubble ..."><a href="...-page-2">2</a>...</div>
    where the current page is an <a class="Pagination__active"> without href.
    """
    page.mouse.wheel(0, 20000)
    page.wait_for_timeout(1500)
    next_number = re.compile(rf"^\s*{current + 1}\s*$")
    candidates = [
        page.locator("div[class*='Pagination__srpPageBubble'] a[href]").filter(has_text=next_number),
        page.get_by_role("link", name=re.compile(r"^\s*next", re.I)),
        page.get_by_role("button", name=re.compile(r"^\s*next", re.I)),
        page.get_by_text(re.compile(r"^\s*next\s*[>›»]?\s*$", re.I)),
        page.get_by_role("link", name=str(current + 1), exact=True),
        page.get_by_text(str(current + 1), exact=True),
    ]
    for i, locator in enumerate(candidates):
        try:
            if locator.count():
                # Pagination links are unambiguous; for the loose text fallbacks
                # the bottom-most match is the pagination control.
                target = locator.first if i == 0 else locator.last
                target.scroll_into_view_if_needed(timeout=5000)
                print(f"  clicking: {target.get_attribute('href') or target.inner_text()}")
                target.click(timeout=5000)
                return True
        except PlaywrightTimeout:
            continue
    return False


def main():
    collector = Collector()
    with sync_playwright() as pw:
        # --headless: no window. Nobody can solve a CAPTCHA then, so a blocked
        # page simply ends the run with whatever was collected.
        browser = pw.chromium.launch(channel="chrome", headless=HEADLESS)
        page = browser.new_page()
        page.on("response", collector.on_response)

        print(f"Page 1: opening {START_URL}")
        page.goto(START_URL, wait_until="domcontentloaded", timeout=60000)
        if not wait_for_listings(page, collector, 0):
            print("No listings on page 1. Stopping.")

        for current in range(1, MAX_PAGES):
            if not collector.listings:
                break
            time.sleep(random.uniform(*DELAY_SECONDS))
            before = len(collector.listings)
            print(f"Page {current + 1}: clicking next")
            if not click_next(page, current):
                print("  Couldn't find a Next / page-number button. Stopping.")
                break
            try:
                page.wait_for_load_state("domcontentloaded", timeout=30000)
            except PlaywrightTimeout:
                pass
            if not wait_for_listings(page, collector, before):
                print("  No new listings arrived. Stopping.")
                break

        browser.close()

    OUT_FILE.write_text(
        json.dumps(list(collector.listings.values()), ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"Saved {len(collector.listings)} listings to {OUT_FILE} "
          f"({collector.api_responses} API responses seen).")


if __name__ == "__main__":
    main()
