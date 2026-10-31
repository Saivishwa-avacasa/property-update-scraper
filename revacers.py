import json
import time
import random
import argparse
from curl_cffi import requests
from acres.captcha_solver import solve_captcha_with_browser

# Updated with correct State IDs (city=223 for Maharashtra)
STATES = {
    "GA": "https://www.99acres.com/search/property/buy/goa?city=233&keyword=goa&preference=S&budget_min=0&availability=3&res_com=R&isPreLeased=N",
    "TN": "https://www.99acres.com/search/property/buy/tamilnadu?city=227&keyword=tamilnadu&preference=S&budget_min=0&availability=3&res_com=R&isPreLeased=N",
    "KA": "https://www.99acres.com/search/property/buy/karnataka?city=105&keyword=karnataka&preference=S&budget_min=0&availability=3&res_com=R&isPreLeased=N",
    "MH": "https://www.99acres.com/search/property/buy/maharashtra?city=223&keyword=maharashtra&preference=S&budget_min=0&availability=3&res_com=R&isPreLeased=N",
    "UK": "https://www.99acres.com/search/property/buy/uttarakhand?city=197&keyword=uttarakhand&preference=S&budget_min=0&availability=3&res_com=R&isPreLeased=N"
}

def extract_initial_data(html):
    marker = "window.__initialData__="
    if marker not in html:
        return None
    start = html.find(marker) + len(marker)
    try:
        obj, _ = json.JSONDecoder().raw_decode(html, start)
        return obj
    except json.JSONDecodeError:
        return None

def find_projects_list(obj):
    """Recursively finds the property list in the massive JSON object."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(value, list) and len(value) > 0:
                first_item = value[0]
                if isinstance(first_item, dict) and any(k in first_item for k in ["PROP_ID", "projectUnitId", "PROP_NAME", "SPID"]):
                    return value
            result = find_projects_list(value)
            if result:
                return result
    elif isinstance(obj, list):
        for item in obj:
            result = find_projects_list(item)
            if result:
                return result
    return []

def scrape_all_pages(state_code, start_page=1, max_pages=1000):
    if state_code not in STATES:
        print(f"❌ Unknown state: {state_code}. Choose from {list(STATES.keys())}")
        return

    base_url = STATES[state_code]
    session = requests.Session(impersonate="chrome")
    all_properties = []

    for page in range(start_page, max_pages + 1):
        url = f"{base_url}&page={page}"
        print(f"\n📄 Fetching {state_code} page {page} -> {url}")
        
        try:
            # 1. Try fast curl_cffi first
            response = session.get(url, timeout=30)
            data = extract_initial_data(response.text)
            
            # 2. If blocked, fallback to browser (Laya + SeleniumBase)
            if data is None:
                print(f"🛑 Blocked on {state_code} page {page}! Launching Laya Solver...")
                current_cookies = {c.name: c.value for c in session.cookies.jar}
                
                # Returns BOTH cookies and the HTML from the browser
                new_cookies, html_source = solve_captcha_with_browser(url, current_cookies)
                
                # Update session cookies for the next pages
                for k, v in new_cookies.items():
                    session.cookies.set(k, v, domain=".99acres.com")
                
                # Extract data directly from the browser's HTML
                data = extract_initial_data(html_source)
                
                if data is None:
                    print(f"❌ Still blocked on {state_code} page {page}. Stopping to protect IP.")
                    break
            
            # 3. Extract properties
            properties = find_projects_list(data)
            
            if not properties:
                print(f"🏁 No more properties found for {state_code}. Reached the end!")
                break
                
            all_properties.extend(properties)
            print(f"✅ Got {len(properties)} properties. Total: {len(all_properties)}")
            
            # Polite delay
            delay = random.uniform(10.0, 15.0)
            print(f"⏳ Sleeping for {delay:.1f} seconds...")
            time.sleep(delay)
            
        except Exception as e:
            print(f"💥 Error on page {page}: {e}")
            break

    # Save data with page range in the filename so batch runs don't overwrite each other
    filename = f"{state_code}_new_launches_{start_page}_to_{max_pages}.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(all_properties, f, indent=2)
    print(f"\n🎉 Done! Saved {len(all_properties)} properties to {filename}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape 99acres New Launches.")
    parser.add_argument("--state", default="all", help="State code (GA, TN, KA, MH, UK) or 'all'")
    parser.add_argument("--start-page", type=int, default=1, help="Page to start from (default: 1)")
    parser.add_argument("--max-pages", type=int, default=1000, help="Max pages to scrape (default: 1000)")
    args = parser.parse_args()

    if args.state.lower() == "all":
        state_list = list(STATES.keys())
        for i, state in enumerate(state_list):
            print(f"\n{'='*60}\n🚀 STARTING SCRAPE FOR: {state}\n{'='*60}")
            # This uses the start/max pages you pass in the terminal
            scrape_all_pages(state_code=state, start_page=args.start_page, max_pages=args.max_pages)
            
            if i < len(state_list) - 1:
                print(f"\n⏸️ Pausing for 30 seconds before starting the next state...")
                time.sleep(30)
    else:
        scrape_all_pages(state_code=args.state.upper(), start_page=args.start_page, max_pages=args.max_pages)