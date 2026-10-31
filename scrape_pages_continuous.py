"""Collect 99acres listing pages continuously: one page every few minutes,
logging each page, until a target number of listings is reached.

Uses the same ordinary pagination links as scrape_goa_pages.py
(https://www.99acres.com/property-in-<state>-ffid, -page-2, -page-3 ...) and
the same files, so it continues where that script stopped:
    <slug>_pages.json                    listings (goa_pages.json for Goa)
    state\\<slug>_pages_progress.json     next page to fetch
States are worked through in order; when one reaches its last results page
the next one starts.

On a 99acres security check it pauses BLOCKED_GAP_MINUTES and then retries
the same page. It never tries to get past the check.

Every page is saved as soon as it arrives, so Ctrl+C loses nothing.
Holds the shared run lock while running, so scrape_99acres.py (daily 11:00)
and scrape_goa_pages.py wait or skip until this stops.

Run (cmd, from the project folder):
    .venv\\Scripts\\python.exe scrape_pages_continuous.py
    .venv\\Scripts\\python.exe scrape_pages_continuous.py --every 2 --target 18000 --states GA,UK,KA
Log (also printed in the window):  data\\pages_continuous.log
"""
import json
import math
import random
import sys
import time
from pathlib import Path

from curl_cffi import requests

from acres import config
from acres.runlock import acquire_run_lock
from scrape_goa_pages import (Blocked, extract_initial_data, fetch, listing_key, listings_from)

EVERY_MINUTES = 2.0            # gap between pages (each gap varies +-20%)
TARGET_LISTINGS = 18000        # stop when all chosen states together hold this many
STATES = "GA,UK,KA,TN,MH"      # order to work through (codes from acres/config.py)
BLOCKED_GAP_MINUTES = 180      # pause after a security check, then retry the same page
MAX_ERRORS_IN_A_ROW = 3        # other failures (timeouts, odd pages) before giving up
LAST_PAGE = 400                # safety cap; each state stops at 99acres' own last page
PER_PAGE = 25
LOG_FILE = config.DATA_DIR / "pages_continuous.log"


def arg(name, default, cast=str):
    if name in sys.argv:
        return cast(sys.argv[sys.argv.index(name) + 1])
    return default


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    if sys.stdout is not None:
        print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def sleep_until(when, why):
    if when > time.time():
        log(f"{why}; next request at {time.strftime('%H:%M:%S', time.localtime(when))}")
        time.sleep(when - time.time())


class StateRun:
    """Files and progress for one state's pagination."""

    def __init__(self, code):
        self.code = code
        self.slug = config.STATES[code]["slug"]
        self.base_url = f"{config.BASE}/property-in-{self.slug}-ffid"
        self.out_file = Path(f"{self.slug}_pages.json")
        self.progress_file = config.STATE_DIR / f"{self.slug}_pages_progress.json"
        self.collected = {}
        if self.out_file.exists():
            for p in json.loads(self.out_file.read_text(encoding="utf-8")):
                self.collected[listing_key(p)] = p
        saved = {}
        if self.progress_file.exists():
            try:
                saved = json.loads(self.progress_file.read_text(encoding="utf-8"))
            except ValueError:
                saved = {}
        self.next_page = saved.get("next_page", 1)
        self.blocked_at = saved.get("finished_at") if saved.get("blocked") else None
        self.total_pages = None

    def page_url(self, page):
        return self.base_url if page == 1 else f"{self.base_url}-page-{page}"

    @property
    def done(self):
        return self.next_page > LAST_PAGE

    def save(self, blocked=False):
        self.progress_file.parent.mkdir(parents=True, exist_ok=True)
        self.progress_file.write_text(json.dumps(
            {"next_page": self.next_page, "finished_at": time.time(), "blocked": blocked}),
            encoding="utf-8")

    def save_listings(self):
        tmp = self.out_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(list(self.collected.values()), ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(self.out_file)   # never leave a half-written file behind


def main():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    every = arg("--every", EVERY_MINUTES, float)
    target = arg("--target", TARGET_LISTINGS, int)
    codes = [c.strip().upper() for c in arg("--states", STATES).split(",") if c.strip()]
    unknown = [c for c in codes if c not in config.STATES]
    if unknown:
        log(f"unknown state code(s) {unknown}; choose from {sorted(config.STATES)}")
        return 1

    lock = acquire_run_lock(0)
    if lock is None:
        log("Another scraper (scrape_99acres / scrape_goa_pages) is running. Stop it first.")
        return 5

    runs = [StateRun(c) for c in codes]
    grand = lambda: sum(len(r.collected) for r in runs)  # noqa: E731
    log(f"==== start: 1 page every ~{every:g} min, target {target} listings, states {','.join(codes)}")
    for r in runs:
        state = "done" if r.done else f"next page {r.next_page}"
        log(f"  {r.code} ({r.slug}): {len(r.collected)} listings saved, {state}")

    session = requests.Session(impersonate="chrome")
    session.headers.update({"Accept-Language": "en-IN,en;q=0.9"})
    next_request = time.time()
    errors = 0

    try:
        for r in runs:
            if r.blocked_at:
                sleep_until(r.blocked_at + BLOCKED_GAP_MINUTES * 60,
                            f"{r.code}: last run hit the security check, pausing {BLOCKED_GAP_MINUTES} min")
            while not r.done and grand() < target:
                wait = next_request - time.time()
                if wait > 0:
                    time.sleep(wait)
                page = r.next_page
                try:
                    data = extract_initial_data(fetch(session, r.page_url(page), page))
                except Blocked as e:
                    r.save(blocked=True)
                    log(f"{r.code} page {page}: SECURITY CHECK ({e})")
                    next_request = time.time() + BLOCKED_GAP_MINUTES * 60
                    sleep_until(next_request, f"paused {BLOCKED_GAP_MINUTES} min, will retry page {page}")
                    continue
                except Exception as e:   # timeouts, connection resets, pages without JSON
                    errors += 1
                    log(f"{r.code} page {page}: error {errors}/{MAX_ERRORS_IN_A_ROW}: {e}")
                    if errors >= MAX_ERRORS_IN_A_ROW:
                        log("too many errors in a row, stopping")
                        return 1
                    next_request = time.time() + every * 60
                    continue
                errors = 0

                listings, is_last = listings_from(data)
                if r.total_pages is None:
                    count = ((data.get("srp") or {}).get("pageData") or {}).get("count")
                    r.total_pages = math.ceil(count / PER_PAGE) if count else None
                new = 0
                for p in listings:
                    key = listing_key(p)
                    if key not in r.collected:
                        r.collected[key] = p
                        new += 1
                r.next_page = page + 1
                finished = not listings or is_last or (page > 1 and not new)
                if finished:
                    r.next_page = LAST_PAGE + 1
                r.save_listings()
                r.save()
                next_request = time.time() + every * 60 * random.uniform(0.8, 1.2)
                of = f"/{r.total_pages}" if r.total_pages else ""
                log(f"{r.code} page {page}{of}: {len(listings)} listings ({new} new) | "
                    f"{r.code} total {len(r.collected)} | all {grand()}/{target} | "
                    f"next at {time.strftime('%H:%M:%S', time.localtime(next_request))}")
                if finished:
                    log(f"{r.code}: last results page reached ({len(r.collected)} listings)")
    except KeyboardInterrupt:
        log(f"stopped with Ctrl+C; {grand()} listings saved")
        return 0

    if grand() >= target:
        log(f"==== target reached: {grand()} listings")
    else:
        log(f"==== all chosen states finished: {grand()} listings (target was {target})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
