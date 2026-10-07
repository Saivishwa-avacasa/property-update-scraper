"""Static configuration: states, URLs, timings.

99acres treats each Indian state as a "city" in its search URL. The ids below
were read from the `filters.city` value of https://www.99acres.com/property-in-<state>-ffid.
`rera_state_code` is the state_code used in the existing `rera_projects` table
(E:\\Scraper-for-RERA), so the two datasets can be joined.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"          # logs, JSON dumps when the DB is off
STATE_DIR = ROOT / "state"        # cookies + Chrome profile (git-ignored)

BASE = "https://www.99acres.com"

STATES = {
    "TN": {"name": "Tamil Nadu",  "slug": "tamilnadu",   "city_id": "227", "rera_state_code": "TN"},
    "KA": {"name": "Karnataka",   "slug": "karnataka",   "city_id": "224", "rera_state_code": "KA"},
    "MH": {"name": "Maharashtra", "slug": "maharashtra", "city_id": "223", "rera_state_code": "MH"},
    "GA": {"name": "Goa",         "slug": "goa",         "city_id": "233", "rera_state_code": None},
    "UK": {"name": "Uttarakhand", "slug": "uttarakhand", "city_id": "250", "rera_state_code": None},
}

# availability=3 is the "New Launch" chip on the results page
# (facet CONSTRUCTION_AVAILABILITY: 1 = Under Construction, 2 = Ready to move, 3 = New Launch).
NEW_LAUNCH_AVAILABILITY = "3"


def search_url(state_code, page=1, keyword=True):
    s = STATES[state_code]
    q = (f"city={s['city_id']}&preference=S&budget_min=0"
         f"&availability={NEW_LAUNCH_AVAILABILITY}&res_com=R&isPreLeased=N")
    if keyword:
        # Same URL the "New Launch" panel produces in the browser. Without the
        # keyword 99acres returns a broader (less curated) list.
        q = f"city={s['city_id']}&keyword={s['slug']}&" + q.split("&", 1)[1]
    if page > 1:
        q += f"&page={page}"
    return f"{BASE}/search/property/buy/{s['slug']}?{q}"


# Politeness. Residential IP + a real browser fingerprint (curl_cffi) is what
# keeps us off the bot list; the delay keeps us off the rate limiter.
DELAY_SECONDS = (3.0, 6.5)
REQUEST_TIMEOUT = 45

# Defaults for a daily run
MAX_LISTING_PAGES = 40       # safety cap; TN new-launch is ~8-30 pages
MAX_DETAILS_PER_RUN = 60     # detail pages per state per run
RESCRAPE_AFTER_DAYS = 7      # re-check prices/dates weekly
MAX_ATTEMPTS = 3
