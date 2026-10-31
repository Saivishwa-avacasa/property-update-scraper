# acres/captcha_solver.py
import time
import os

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

from functools import lru_cache
from html.parser import HTMLParser
from laya import Router
from seleniumbase import sb_cdp

CAPTCHA_KEYWORDS = (
    "captcha", "turnstile", "recaptcha", "hcaptcha",
    "datadome", "friendlycaptcha", "challenge",
    "cf-turnstile", "g-recaptcha", "h-captcha",
    "arkoselabs", "funcaptcha", "geetest",
)

LAYA_MODEL = "english"
MAX_VALUE_CHARS = 100
MAX_TEXT_CHARS = 150
SKIPPED_ATTRS = ("style", "nonce", "integrity")
MIN_TYPE_PROBABILITY = 0.35

class CaptchaHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.evidence = []
        self.title = []
        self.in_title = False
        self.in_style = False

    def add(self, item):
        if item not in self.evidence:
            self.evidence.append(item)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "title":
            self.in_title = True
        elif tag == "style":
            self.in_style = True

        if any(
            keyword in str(value).lower()
            for key, value in attrs.items()
            for keyword in CAPTCHA_KEYWORDS
        ):
            self.add(
                f"<{tag} "
                + " ".join(
                    f'{key}="{value[:MAX_VALUE_CHARS]}"'
                    for key, value in attrs.items()
                    if value is not None and key not in SKIPPED_ATTRS
                )
                + ">"
            )

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)
            return
        if self.in_style:
            return
        text = " ".join(data.split())
        lower = text.lower()
        hits = [lower.find(k) for k in CAPTCHA_KEYWORDS if k in lower]
        if hits:
            start = max(0, min(hits) - 50)
            self.add(text[start:start + MAX_TEXT_CHARS])

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        elif tag == "style":
            self.in_style = False

def reduce_html(html: str, max_chars: int = 3000) -> str:
    parser = CaptchaHTMLParser()
    parser.feed(html)
    title = " ".join(parser.title).strip()
    evidence = "\n".join(parser.evidence)
    reduced = (
        f"Page title: {title or '(not found)'}\n"
        f"CAPTCHA-related HTML evidence:\n"
        f"{evidence or '(no matching elements found)'}"
    )
    return reduced[:max_chars]

QUESTIONS = {
    "captcha_detected": {
        "type": "noul",
        "instructions": "Does this webpage HTML contain credible evidence of a CAPTCHA integration or challenge?",
    },
    "captcha_type": {
        "type": "choice",
        "instructions": "Which CAPTCHA provider or category is best supported by the HTML?",
        "criteria": {
            "Cloudflare Turnstile": "Cloudflare Turnstile widget",
            "Google reCAPTCHA": "Google reCAPTCHA widget",
            "hCaptcha": "hCaptcha widget",
            "DataDome Slider": "DataDome slider CAPTCHA",
            "Friendly CAPTCHA": "Friendly CAPTCHA widget",
            "Other CAPTCHA": "Another CAPTCHA provider, or no CAPTCHA detected",
        },
    },
}

@lru_cache(maxsize=1)
def get_router() -> Router:
    return Router()

def detect_captcha(html: str) -> dict:
    if not html or not html.strip():
        raise ValueError("html must be a non-empty string")

    response = get_router().predict_long(html, QUESTIONS, model=LAYA_MODEL)
    answers = response["answers"]

    detected_value = answers["captcha_detected"]["noul"]
    detected = detected_value >= 0.5
    confidence = detected_value if detected else 1 - detected_value

    type_answer = answers["captcha_type"]
    selected_type = type_answer["choice"]
    if type_answer["probabilities"][selected_type] < MIN_TYPE_PROBABILITY:
        selected_type = "Other CAPTCHA"

    return {
        "captcha_detected": "Yes" if detected else "No",
        "score": max(0, min(100, round(confidence * 100))),
        "captcha_type": (selected_type if detected else None),
    }

def solve_captcha_with_browser(url, cookies=None):
    print(f"🚀 CAPTCHA Triggered! Launching SeleniumBase to bypass: {url}")
    sb = sb_cdp.Chrome(browser_args=["--disable-features=ExpectCT"])
    try:
        sb.goto("https://www.99acres.com")
        
        if cookies:
            cookie_list = [
                {"name": name, "value": value, "domain": ".99acres.com", "path": "/"}
                for name, value in cookies.items()
            ]
            sb.set_all_cookies(cookie_list)
        
        sb.goto(url)
        sb.sleep(3.0)
        
        html = sb.get_html()
        reduced = reduce_html(html)
        info = detect_captcha(reduced)
        print(f"🧠 Laya Detection: {info}")
        
        if info["captcha_detected"] == "Yes" or "verifycaptcha" in sb.get_current_url().lower():
            print("🛠️ Attempting automated bypass via SeleniumBase...")
            
            try:
                sb.solve_captcha()
            except Exception as e:
                print(f"⚠️ solve_captcha note: {e}")
            
            sb.sleep(3.0)
            
            if "verifycaptcha" in sb.get_current_url().lower():
                print("\n🚨 IMAGE GRID DETECTED! 🚨")
                print("👉 MANUAL ACTION REQUIRED: Please click all the images (e.g., buses) and hit VERIFY.")
                print("⏳ Pausing for 60 seconds to let you solve it...")
                sb.sleep(60) 
            
            # CRITICAL FIX: 99acres strips the URL after solving. 
            # We must go BACK to the exact URL we need!
            print("🔄 Navigating back to the exact target URL to get the data...")
            try:
                sb.goto(url)
                sb.sleep(4.0) # Wait for the page to load
            except Exception:
                pass
        
        new_cookies = sb.get_all_cookies()
        cookie_dict = {}
        for c in new_cookies:
            domain = getattr(c, 'domain', '')
            if '99acres.com' in domain:
                cookie_dict[c.name] = c.value
                
        print(f"✅ Browser bypass finished. Extracted {len(cookie_dict)} cookies.")
        
        html_source = sb.get_page_source()
        return cookie_dict, html_source
        
    finally:
        sb.quit()