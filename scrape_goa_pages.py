"""Fetch the Goa listing pages by their pagination links and save the listings.

Opens https://www.99acres.com/property-in-goa-ffid, then -page-2, -page-3 ...
until 99acres' last results page (LAST_PAGE is only a safety cap),
reads the JSON each page embeds in window.__initialData__, and writes every
listing to one file. No API tokens are involved: these are ordinary pages.

Kept slow on purpose so 99acres doesn't flag the connection:
  * 20-40 s between pages,
  * at most PAGES_PER_RUN pages per run; the next run continues where this
    one stopped (progress is kept in state/goa_pages_progress.json),
  * refuses to start until MIN_GAP_MINUTES after the previous run finished
    (BLOCKED_GAP_MINUTES if that run hit the security check),
  * never runs at the same time as scrape_99acres.py / scrape_magicbricks.py
    (shares their run lock).

If 99acres shows its "Security checkup" page, the run stops and saves its
progress. Tick the box yourself in Chrome (or wait a few hours), then run again.

Run:
    .venv\\Scripts\\python.exe scrape_goa_pages.py              # next batch of pages
    .venv\\Scripts\\python.exe scrape_goa_pages.py --pages 3    # smaller batch
    .venv\\Scripts\\python.exe scrape_goa_pages.py --start 6    # continue from page 6
    .venv\\Scripts\\python.exe scrape_goa_pages.py --fresh      # start over at page 1
    .venv\\Scripts\\python.exe scrape_goa_pages.py --now        # don't wait for the gap

Unattended (one batch every 70 minutes): register_goa_pages_task.ps1. That runs
pythonw.exe, which has no console, so output goes to data\\goa_pages.log.
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

BASE_URL = "https://www.99acres.com/property-in-goa-ffid"
FIRST_PAGE = 1
LAST_PAGE = 200               # upper bound only; stops at 99acres' own last page (Goa ~140)
PAGES_PER_RUN = 10
MIN_GAP_MINUTES = 60          # minimum time between the end of one run and the next
BLOCKED_GAP_MINUTES = 180     # longer pause after a run that hit the security check
DELAY_SECONDS = (20.0, 40.0)
OUT_FILE = Path("goa_pages.json")
PROGRESS_FILE = config.STATE_DIR / "goa_pages_progress.json"
MARKERS = ("window.__initialData__", "window.__INITIAL_STATE__", "window.__PRELOADED_STATE__")
BLOCK_HINTS = ("captcha", "challenge-platform", "just a moment", "access denied")


class Blocked(Exception):
    pass


def page_url(page):
    return BASE_URL if page == 1 else f"{BASE_URL}-page-{page}"


def extract_initial_data(html):
    """Return the JSON object assigned to window.__initialData__ (or a fallback marker)."""
    for marker in MARKERS:
        i = html.find(marker)
        if i == -1:
            continue
        start = html.find("{", i)
        if start == -1:
            break
        obj, _ = json.JSONDecoder().raw_decode(html, start)
        return obj
    raise ValueError("no embedded __initialData__ JSON on this page")


def listings_from(data):
    """(listings, is_last_page) from a search page's initial data."""
    srp = data.get("srp") or {}
    listings = (srp.get("pageData") or {}).get("properties") or []
    return [p for p in listings if isinstance(p, dict)], bool(srp.get("isLastpage"))


def listing_key(p):
    for k in ("PROP_ID", "propertyId", "projectUnitId", "landingPageRelativeUrl"):
        if p.get(k):
            return f"{k}:{p[k]}"
    return json.dumps(p, sort_keys=True)


def fetch(session, url, page):
    resp = session.get(url, timeout=45, allow_redirects=True)
    head = resp.text[:5000].lower()
    hits = [h for h in BLOCK_HINTS if h in head]
    # A real results page carries the embedded JSON; a verification page doesn't.
    has_data = any(m in resp.text for m in MARKERS)
    redirected_to_check = "verifycaptcha" in str(resp.url).lower()
    if resp.status_code in (403, 417, 429) or redirected_to_check or (hits and not has_data):
        saved = Path(f"blocked_page_{page}.html")
        saved.write_text(resp.text, encoding="utf-8")
        raise Blocked(f"HTTP {resp.status_code}, {len(resp.text)} bytes, "
                      f"matched {hits or 'status code / redirect'} at {url}; page saved to {saved}")
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code} at {url}")
    return resp.text


def load_progress():
    """(next_page, finished_at epoch seconds or None, gap_minutes before the next run)."""
    try:
        saved = json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return FIRST_PAGE, None, MIN_GAP_MINUTES
    # Files written before finished_at existed: the file's own write time is when that run ended.
    finished_at = saved.get("finished_at") or PROGRESS_FILE.stat().st_mtime
    gap = BLOCKED_GAP_MINUTES if saved.get("blocked") else MIN_GAP_MINUTES
    return saved.get("next_page", FIRST_PAGE), finished_at, gap


def save_progress(next_page, finished_at=None, blocked=False):
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS_FILE.write_text(
        json.dumps({"next_page": next_page, "finished_at": finished_at, "blocked": blocked}),
        encoding="utf-8")


def minutes_to_wait(finished_at, gap_minutes=MIN_GAP_MINUTES, now=None):
    """Minutes left before another run may start (0 when it may start now)."""
    if finished_at is None:
        return 0
    elapsed = (now or time.time()) - finished_at
    return max(0, math.ceil((gap_minutes * 60 - elapsed) / 60))


def arg_value(name, default):
    if name in sys.argv:
        return int(sys.argv[sys.argv.index(name) + 1])
    return default


def main():
    if sys.stdout is None:   # pythonw.exe (scheduled task): no console, write a log instead
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        log = open(config.DATA_DIR / "goa_pages.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log
        print(f"==== {time.strftime('%Y-%m-%d %H:%M:%S')} start")
    lock = acquire_run_lock(0)
    if lock is None:
        print("Another scraper (scrape_99acres / scrape_magicbricks) is running. Try again later.",
              file=sys.stderr)
        return 5

    saved_next, finished_at, gap = load_progress()
    wait = minutes_to_wait(finished_at, gap)
    if wait and "--now" in sys.argv:
        print(f"--now: starting {wait} minutes before the {gap}-minute gap is over.")
    elif wait:
        ready = time.strftime("%H:%M", time.localtime(finished_at + gap * 60))
        print(f"The last run finished less than {gap} minutes ago. "
              f"Wait {wait} more minute{'s' if wait != 1 else ''} (until about {ready}), then run again.",
              file=sys.stderr)
        return 3
    if "--fresh" in sys.argv:
        saved_next = FIRST_PAGE
    first = arg_value("--start", saved_next)
    batch = arg_value("--pages", PAGES_PER_RUN)
    last = min(LAST_PAGE, first + batch - 1)
    if first > LAST_PAGE:
        print("All result pages are done. Use --fresh to start over.")
        return 0

    collected = {}   # key -> listing, in first-seen order
    if first > FIRST_PAGE and OUT_FILE.exists():
        for p in json.loads(OUT_FILE.read_text(encoding="utf-8")):
            collected[listing_key(p)] = p
        print(f"Continuing at page {first} with {len(collected)} listings from {OUT_FILE}")
    print(f"This run: pages {first}-{last}, {DELAY_SECONDS[0]:.0f}-{DELAY_SECONDS[1]:.0f}s apart")

    session = requests.Session(impersonate="chrome")
    session.headers.update({"Accept-Language": "en-IN,en;q=0.9"})

    pages_read = 0
    next_page = first
    status = 0
    for page in range(first, last + 1):
        if page > first:
            time.sleep(random.uniform(*DELAY_SECONDS))
        url = page_url(page)
        try:
            data = extract_initial_data(fetch(session, url, page))
        except Blocked as e:
            print(f"page {page}: 99acres showed its security check ({e}).", file=sys.stderr)
            print("Tick the box on 99acres in your own Chrome, or wait a few hours, "
                  "then run this script again; it continues from this page.", file=sys.stderr)
            status = 2
            break
        except (RuntimeError, ValueError) as e:
            print(f"page {page}: {e}. Stopping.", file=sys.stderr)
            status = 1
            break

        pages_read += 1
        next_page = page + 1
        listings, is_last = listings_from(data)
        if page == first:
            total = ((data.get("srp") or {}).get("pageData") or {}).get("count")
            print(f"99acres reports {total} results in total for this search")
        new = 0
        for p in listings:
            key = listing_key(p)
            if key not in collected:
                collected[key] = p
                new += 1
        print(f"page {page}: {len(listings)} listings ({new} new), total {len(collected)}")
        if not listings or is_last or (page > first and not new):
            print("No more pages.")
            next_page = LAST_PAGE + 1
            break

    save_progress(next_page, finished_at=time.time(), blocked=(status == 2))
    if pages_read:   # never overwrite earlier results with an empty run
        OUT_FILE.write_text(json.dumps(list(collected.values()), ensure_ascii=False, indent=2),
                            encoding="utf-8")
    print(f"Saved {len(collected)} listings ({pages_read} pages this run) to {OUT_FILE}")
    if status == 0 and next_page <= LAST_PAGE:
        ready = time.strftime("%H:%M", time.localtime(time.time() + MIN_GAP_MINUTES * 60))
        print(f"Next run continues at page {next_page}; it can start from about {ready}.")
    return status


if __name__ == "__main__":
    sys.exit(main())
