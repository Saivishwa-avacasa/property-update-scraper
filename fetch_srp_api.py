"""Fetch 99acres Goa search results (SRP) from the api-aggregator endpoint.

Replays the request copied from the browser's DevTools ("Copy as cURL").
That request is a GET with no body, so this script sends a GET too.

Secrets are read from environment variables, never stored in this file:

    RA_COOKIE              the full cookie string (value of curl's -b)
    RA_APITOKEN            the "apitoken" header
    RA_AUTHORIZATIONTOKEN  the "authorizationtoken" header
    RA_DEVICEID            the "deviceid" header

Both tokens are short-lived (apitoken ~2 min, authorizationtoken ~10 min),
so copy fresh values from the browser right before running. When they
expire the server rejects the request and the script stops.

Usage (PowerShell):
    $env:RA_COOKIE = "..."; $env:RA_APITOKEN = "..."
    $env:RA_AUTHORIZATIONTOKEN = "..."; $env:RA_DEVICEID = "..."
    python fetch_srp_api.py
"""
import json
import os
import sys
import time
from pathlib import Path

from curl_cffi import requests

URL = "https://www.99acres.com/api-aggregator/srp/search"
FIRST_PAGE = 1
LAST_PAGE = 20
DELAY_SECONDS = 2.0
OUT_DIR = Path("srp_pages")

BASE_PARAMS = {
    "platform": "DESKTOP",
    "moduleName": "GRAILS_SRP",
    "workflow": "GRAILS_SRP",
    "page_size": "25",
    "search_type": "QS",
    "reraType": "RERA",
    "city": "233",          # Goa
    "preference": "S",      # buy
    "res_com": "R",         # residential
    "rera_type": "RERA",
    "seoUrlType": "DEFAULT",
    "recomGroupType": "VSP",
    "pageName": "SRP",
    "groupByConfigurations": "true",
    "lazy": "true",
}

STATIC_HEADERS = {
    "accept": "*/*",
    "accept-language": "en-GB,en;q=0.7",
    "pagename": "SRP",
    "platform": "desktop",
    "priority": "u=1, i",
    "referer": "https://www.99acres.com/search/property/buy/goa?city=233&keyword=goa&preference=S&res_com=R",
    "sec-ch-ua": '"Brave";v="155", "Chromium";v="155", "Not(A:Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "sec-gpc": "1",
    "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"),
}


def required_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(f"Set the {name} environment variable (see the docstring).")
    return value


def parse_cookie_string(raw):
    """Turn 'a=1; b=2' into a dict. Later duplicates win, as in the browser."""
    cookies = {}
    for part in raw.split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name:
            cookies[name] = value
    return cookies


def fetch_page(session, page):
    params = dict(BASE_PARAMS, page=str(page))
    resp = session.get(URL, params=params, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"page {page}: HTTP {resp.status_code}: {resp.text[:300]}")
    try:
        return resp.json()
    except ValueError:
        raise RuntimeError(f"page {page}: response is not JSON: {resp.text[:300]}")


def main():
    headers = dict(STATIC_HEADERS)
    headers["apitoken"] = required_env("RA_APITOKEN")
    headers["authorizationtoken"] = required_env("RA_AUTHORIZATIONTOKEN")
    headers["deviceid"] = required_env("RA_DEVICEID")
    cookies = parse_cookie_string(required_env("RA_COOKIE"))

    OUT_DIR.mkdir(exist_ok=True)
    session = requests.Session(impersonate="chrome")
    session.headers.update(headers)
    session.cookies.update(cookies)

    for page in range(FIRST_PAGE, LAST_PAGE + 1):
        try:
            data = fetch_page(session, page)
        except RuntimeError as exc:
            print(exc, file=sys.stderr)
            print("Stopping. If this is a 401/403, copy fresh tokens from the browser.",
                  file=sys.stderr)
            return 1
        out = OUT_DIR / f"goa_page_{page:02d}.json"
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"page {page}: saved {out}")
        if page < LAST_PAGE:
            time.sleep(DELAY_SECONDS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
