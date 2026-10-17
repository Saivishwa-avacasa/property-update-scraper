"""Static configuration for Magicbricks MagicHomes.

MagicHomes (https://www.magicbricks.com/magichomes) is Magicbricks' new-projects
section: "All 28122 Projects in India". The page loads them from

    /mbproject/newProjectCards?&pageNo=<n>&magichome=Y          (all India, 20 cards/page)
    /mbproject/newProjectCards?&pageNo=<n>&city=<city id>      (one city)

The scheduled run uses the all-India feed (feed code "IN"). The per-state city
lists below are kept for one-off runs (--state TN) and for RERA matching.
City ids were
read from searchBean.city on https://www.magicbricks.com/new-projects-<slug>
(Oct 2026). An unknown slug silently falls back to Chennai (5196), so new
cities must be checked before they are added here.
"""
from acres.config import ROOT, DATA_DIR, STATE_DIR  # noqa: F401  (shared folders)

BASE = "https://www.magicbricks.com"
CARDS_URL = BASE + "/mbproject/newProjectCards"

STATES = {
    "TN": {"name": "Tamil Nadu", "rera_state_code": "TN", "cities": {
        "5196": "Chennai", "5216": "Coimbatore", "5420": "Madurai", "5798": "Trichy",
        "5194": "Chengalpattu", "5872": "Vellore", "5801": "Tirunelveli", "5666": "Salem",
        "1000539": "Hosur"}},
    "KA": {"name": "Karnataka", "rera_state_code": "KA", "cities": {
        "3327": "Bangalore", "3493": "Mysore", "3475": "Mangalore", "3334": "Belgaum",
        "1200171": "Hubli"}},
    "MH": {"name": "Maharashtra", "rera_state_code": "MH", "cities": {
        "4320": "Mumbai", "4378": "Pune", "4341": "Navi Mumbai", "4327": "Nagpur",
        "4442": "Thane", "4354": "Palghar", "4339": "Nashik", "1200023": "Raigad",
        "4139": "Aurangabad", "4274": "Kolhapur"}},
    "GA": {"name": "Goa", "rera_state_code": None, "cities": {"2669": "Goa"}},
    "UK": {"name": "Uttarakhand", "rera_state_code": None, "cities": {
        "6608": "Dehradun", "6625": "Haridwar", "6654": "Nainital", "6663": "Rishikesh"}},
}

INDIA = "IN"    # the all-India MagicHomes feed

# detail page stateDesc -> state code; codes TN/KA/MH match rera_projects.state_code
STATE_CODES = {
    "andhra pradesh": "AP", "arunachal pradesh": "AR", "assam": "AS", "bihar": "BR",
    "chhattisgarh": "CG", "goa": "GA", "gujarat": "GJ", "haryana": "HR", "himachal pradesh": "HP",
    "jharkhand": "JH", "karnataka": "KA", "kerala": "KL", "madhya pradesh": "MP",
    "maharashtra": "MH", "manipur": "MN", "meghalaya": "ML", "mizoram": "MZ", "nagaland": "NL",
    "odisha": "OD", "orissa": "OD", "punjab": "PB", "rajasthan": "RJ", "sikkim": "SK",
    "tamil nadu": "TN", "telangana": "TS", "tripura": "TR", "uttar pradesh": "UP",
    "uttarakhand": "UK", "uttaranchal": "UK", "west bengal": "WB",
    "andaman and nicobar islands": "AN", "chandigarh": "CH", "dadra and nagar haveli": "DN",
    "daman and diu": "DD", "delhi": "DL", "new delhi": "DL", "jammu and kashmir": "JK",
    "jammu & kashmir": "JK", "ladakh": "LA", "lakshadweep": "LD", "puducherry": "PY", "pondicherry": "PY",
}
RERA_STATE_CODES = {"TN", "KA", "MH"}   # states present in rera_projects

# psmStatus on the cards. 10066 and 10068 were confirmed against project pages;
# the project page's own status text wins when the details are scraped.
STATUS_CODES = {10066: "Under Construction", 10067: "New Launch", 10068: "Ready to Move"}

# Politeness. Magicbricks serves plain curl_cffi requests without a captcha.
DELAY_SECONDS = (1.5, 3.5)
REQUEST_TIMEOUT = 45

MAX_LISTING_PAGES = 1600         # all-India feed is ~1,407 pages; a city at most ~185
MAX_DETAILS_PER_RUN = 1500       # project pages per daily run (~1.5 h)
# Prices, status, dates and units are re-checked every day from the list itself
# (see db.save_listing); the full project page only needs an occasional refresh.
RESCRAPE_AFTER_DAYS = 30
MAX_ATTEMPTS = 3

FULL_MAX_LISTING_PAGES = 1600
FULL_MAX_DETAILS = 100000
