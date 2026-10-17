"""Magicbricks JSON -> flat record for mb_new_projects.

Two sources fill the same columns:
  * parse_card(): one card from the cards API (listing; ~35 fields)
  * parse_project(): the project page's SERVER_PRELOADED_STATE_ (full record)
The detail record is built on top of the card, so nothing the card knew is lost.
"""
import hashlib
import html as htmllib
import json
import re
from datetime import date, datetime

from . import config

IMG_BASE = "https://img.staticmb.com/mbimages/project/"

# Fields whose changes are written to mb_change_history.
# LIST_TRACKED come from the cards and are compared every day for every project
# (the list is walked daily); the card value is also what gets stored, so the
# project page never overwrites them with a differently formatted number.
LIST_TRACKED = [
    "name", "status", "launch_date", "possession_date", "price_min", "price_max",
    "price_per_sqft_min", "price_per_sqft_max", "total_units",
]
# PAGE_TRACKED only exist on the project page; compared when a page is re-read.
PAGE_TRACKED = ["price_text", "is_rera", "rera_number", "available_units", "developer_name"]
TRACKED_FIELDS = LIST_TRACKED + PAGE_TRACKED

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


# ── small helpers ──────────────────────────────────────────────────────────────

def _s(v):
    if v is None:
        return None
    v = str(v).strip()
    return v or None


def _int(v):
    try:
        if v in (None, "", "0", 0):
            return None if v in (None, "") else 0
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _pos_int(v):
    v = _int(v)
    return v if v else None


def _float(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def strip_html(v):
    """'AI_DATA <p>x</p><ul><li>y</li></ul>' -> 'x\n• y'"""
    v = _s(v)
    if not v:
        return None
    v = re.sub(r"^AI_DATA\s*", "", v)
    v = re.sub(r"(?i)<li[^>]*>", "\n• ", v)
    v = re.sub(r"(?i)</p>|<br\s*/?>|</h\d>", "\n", v)
    v = re.sub(r"<[^>]+>", "", v)
    v = htmllib.unescape(v)
    v = re.sub(r"[ \t]+", " ", v)
    v = re.sub(r"\n\s*\n+", "\n", v)
    return v.strip() or None


def date_ymd(v):
    """'20250401' or '2028-12-01 00:00:00' or '2025-03-31T18:30:00...' -> date"""
    v = _s(v)
    if not v:
        return None
    m = re.match(r"(\d{4})-?(\d{2})-?(\d{2})", v)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def date_month_year(v):
    """'Dec 2028' / 'Dec, 2028' / "Dec'28" -> 2028-12-01"""
    v = _s(v)
    if not v:
        return None
    m = re.search(r"([A-Za-z]{3})[a-z]*[\s,']*(\d{2,4})", v)
    if not m or m.group(1).lower() not in MONTHS:
        return None
    y = int(m.group(2))
    y = y + 2000 if y < 100 else y
    return date(y, MONTHS[m.group(1).lower()], 1)


def date_text(v):
    """'10 September 2026' -> date"""
    v = _s(v)
    if not v:
        return None
    for fmt in ("%d %B %Y", "%d %b %Y", "%d %b %y"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            pass
    return None


def rera_key(number):
    return re.sub(r"[^A-Za-z0-9]", "", number).upper() if number else None


def parse_rera_validity(v):
    """'[PRGO05252471|12/2030|Phase1],[...]' -> [{number, valid_till, phase}]"""
    out = []
    for num, till, phase in re.findall(r"\[([^|\]]*)\|([^|\]]*)\|([^\]]*)\]", v or ""):
        out.append({"number": num.strip() or None, "valid_till": till.strip() or None,
                    "phase": phase.strip() or None})
    return out or None


def project_url(path):
    path = _s(path)
    if not path:
        return None
    return path if path.startswith("http") else f"{config.BASE}/{path.lstrip('/')}"


def content_hash(rec):
    keep = {k: v for k, v in rec.items()
            if k not in ("content_hash", "source_last_modified", "images_count", "demand_score")}
    blob = json.dumps(keep, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ── listing card ───────────────────────────────────────────────────────────────

def parse_card(c, city_id=None):
    psmid = _s(c.get("psmid"))
    status_code = _int(c.get("psmStatus"))
    price_min = _pos_int(c.get("minPrice") or c.get("minPriceF"))
    price_max = _pos_int(c.get("maxPrice") or c.get("maxPriceF"))
    price_text = None
    if price_min or price_max:
        lo, hi = _s(c.get("minPriceDesc")), _s(c.get("maxPriceDesc"))
        price_text = lo if lo == hi or not hi or hi == "0" else f"{lo} - {hi}"
    elif _s(c.get("showPriceRange")):
        price_text = _s(c.get("showPriceRange"))
    area = _float(c.get("projArea"))
    return {
        "psmid": psmid,
        "url": project_url(c.get("pdpUrl") or c.get("oldPdpUrl")),
        "name": _s(c.get("psmName")),
        "city": _s(c.get("ctname")),
        "city_id": _s(city_id),
        "locality": _s(c.get("lmtDName")),
        "locality_id": _s(c.get("lt")),
        "pincode": _s(c.get("pincode")),
        "developer_id": _s(c.get("devId")),
        "developer_name": _s(c.get("devName")),
        "state_code": None,   # cards carry the city only; the project page fills it
        "is_rera": (c.get("isRera") == "Y") or (c.get("isReraAvl") == "Y"),
        "rera_updated_on": date_ymd(c.get("reraUpDt")),
        "status_code": status_code,
        "status": _s(c.get("showReadyToMove")) or config.STATUS_CODES.get(status_code),
        "posting_type": _s(c.get("postingType")),
        "top_tag": _s(c.get("topTag")),
        "launch_date": date_ymd(c.get("launchDateStr") or c.get("launchDate")),
        "possession_date": date_ymd(c.get("prjPossDate")),
        "price_text": price_text,
        "price_min": price_min,
        "price_max": price_max,
        "price_per_sqft_min": _pos_int(c.get("sqFtPrice")),
        "price_per_sqft_max": _pos_int(c.get("sqFtPrMx")),
        "property_type": _s(c.get("propertyTypeDesc")),
        "project_area": area if area else None,
        "project_area_unit": _s(c.get("projAreaUnitDesc")) if area else None,
        "total_units": _pos_int(c.get("totalUnits")),
        "tower_count": _pos_int(c.get("towersConstructed")) or _pos_int(c.get("noTower")),
        "usp": [str(x) for x in c["projectUsp"]] if isinstance(c.get("projectUsp"), list) and c["projectUsp"] else None,
        "summary": strip_html(c.get("mhDesc")),
        "cover_image_url": _s(c.get("imageUrl")),
        "images_count": _pos_int(c.get("totalImgCt")),
        "brochure_url": _s(c.get("sblink")),
        "video_url": _s(c.get("psmRevlink")),
        "rating_avg": round(_float(c.get("psmavgRt")), 1) if _float(c.get("psmavgRt")) else None,
        "demand_score": _float(c.get("psmMthDmd")),
    }


# ── project page ───────────────────────────────────────────────────────────────

def parse_project(state, url=None, card=None):
    """Full record from a project page's preloaded state. `card` (the listing
    record) supplies anything the page leaves out."""
    pdd = state.get("projectDetailData") or {}
    pb = pdd.get("prjMobileBean") or {}
    db_ = pb.get("detailBean") or {}
    ib = pb.get("infoBean") or {}
    dev = pb.get("projectDeveloperList") or {}
    rt = pb.get("ratingBean") or {}
    st = state.get("projectPageSeoStaticData") or {}
    loc = ((st.get("localityDetailsBean") or {}).get("localityDetailDTO")) or {}
    psm = st.get("psmDataBean") or {}

    rec = dict(card or {})

    rera_numbers = [x for x in (db_.get("reraIdList") or []) if _s(x)] or ([db_["reraId"]] if _s(db_.get("reraId")) else [])
    rera_number = rera_numbers[0] if rera_numbers else None

    configs = []
    for u in ((st.get("pdpUnitPricing") or {}).get("All") or []):
        item = {
            "bhk": _s(u.get("bhkType")),
            "area_sqft": _pos_int(u.get("minArea")),
            "max_area_sqft": _pos_int(u.get("maxArea")),
            "bathrooms": _pos_int(u.get("bathroom")),
            "balconies": _pos_int(u.get("balcony")),
            "floor_plan_url": _s(u.get("planImgUrl")),
        }
        for k, v in u.items():  # price keys vary by project
            if "price" in k.lower() and v not in (None, "", 0, "0"):
                item[k] = v
        configs.append({k: v for k, v in item.items() if v is not None})

    faqs = []
    for qa in ((st.get("pdpFAQs") or {}).get("qaList") or []):
        if isinstance(qa, dict):
            for q, a in qa.items():
                faqs.append({"q": _s(q), "a": strip_html(a)})

    loc_ratings = {k: loc.get(k) for k in (
        "safetyRating", "connectivityRating", "neighbourRating", "roadRating", "cleanRating",
        "transportRating", "parkingRating", "trafficRating", "schoolRating", "restuarantRating",
        "hospitalRating", "marketRating", "environment", "commuting", "placeOfInterest")
        if loc.get(k) not in (None, 0, "")}
    qp = loc.get("quarterPrice") or {}

    brochure = _s(db_.get("brochurelink"))
    detail = {
        "psmid": _s(pb.get("psmId")) or rec.get("psmid"),
        "url": url or project_url(pb.get("projectUrl")) or rec.get("url"),
        "name": _s(ib.get("projectName")) or _s(pb.get("opsmname")),
        "city": _s(ib.get("cityName")),
        "city_id": _s(ib.get("cityId")),
        "locality": _s(ib.get("localityName")),
        "locality_id": _s(ib.get("localityId")),
        "state": _s(db_.get("stateDesc")),
        "state_code": config.STATE_CODES.get((_s(db_.get("stateDesc")) or "").lower()),
        "pincode": _s(db_.get("pincode")),
        "full_address": _s(db_.get("fullAddress")) or _s(db_.get("ad")),
        "latitude": _float(ib.get("psmLatitude")),
        "longitude": _float(ib.get("psmLongitude")),
        "developer_id": _s(pb.get("devid")),
        "developer_name": _s(dev.get("developerName")) or _s(ib.get("devName")),
        "developer_url": project_url(dev.get("builderUrl")),
        "developer_logo_url": _s(dev.get("logo")),
        "developer_experience_years": _pos_int(dev.get("developerExp")),
        "developer_total_projects": _pos_int(dev.get("devTotalProj")),
        "developer_completed_projects": _int(dev.get("devCompProj")),
        "developer_ongoing_projects": _int(dev.get("devOngoingProj")),
        "developer_description": strip_html(dev.get("fullDesc")),
        "is_rera": bool(rera_number) or pdd.get("isReraAvl") == "Y" or rec.get("is_rera", False),
        "rera_number": rera_number,
        "rera_numbers": rera_numbers or None,
        "rera_number_key": rera_key(rera_number),
        "rera_phases": parse_rera_validity(ib.get("reraValidity")),
        "rera_portal_url": _s(pdd.get("pdpReraUrl")),
        "rera_updated_on": date_ymd(psm.get("reraUpDt")),
        "status": _s(db_.get("newHomesPossStatus")) or _s(db_.get("status")),
        "is_prelaunch": pdd.get("isPrelaunch") == "Y",
        "launch_date": date_ymd(db_.get("launchDateStr")),
        "possession_text": _s(db_.get("possessionByDate")),
        "possession_date": date_month_year(db_.get("possdateStr") or db_.get("possessionByDate")),
        "price_text": _s(ib.get("price")),
        "price_min": _pos_int(ib.get("minPrice")),
        "price_max": _pos_int(ib.get("maxPrice")),
        "bhk_text": _s(st.get("unitInfo")),
        "configurations": configs or None,
        "project_area_text": _s(db_.get("projArea")) or _s(db_.get("area")),
        "total_units": _pos_int(db_.get("units")),
        "available_units": _pos_int(pdd.get("availableUnits")),
        "tower_count": _pos_int(db_.get("towers")),
        "highlights": strip_html(db_.get("projectAdvantages")),
        "description": strip_html((pb.get("projectDescription") or {}).get("fullDesc")),
        "specifications": strip_html(db_.get("specifications")),
        "amenities": [a["amenityName"] for a in (pb.get("amenitiesList") or [])
                      if a.get("amenityName") and a.get("isExists") != "false"] or None,
        "brochure_url": (brochure if brochure and brochure.startswith("http") else IMG_BASE + brochure) if brochure else None,
        "master_plan_url": _s(db_.get("masterPlanLayout")),
        "cover_image_url": _s(ib.get("image")),
        "images_count": _pos_int(pb.get("allImgCount")),
        "video_url": _s(ib.get("revVideoLink")),
        "rating_avg": round(float(rt["psmAvgRt"]), 1) if _float(rt.get("psmAvgRt")) else None,
        "rating_count": _pos_int(rt.get("totalRating")),
        "reviews_count": _pos_int((st.get("projectReviews") or {}).get("count")),
        "locality_rating_avg": _float(loc.get("ratingAvgCount")) or None,
        "locality_rating_count": _pos_int(loc.get("ratingCount")),
        "locality_ratings": loc_ratings or None,
        "locality_rank": f"{loc['locRank']}/{loc['totalLoc']}" if loc.get("locRank") and loc.get("totalLoc") else None,
        "locality_price_sqft": _pos_int(qp.get("currentQuarterPrice")),
        "locality_price_sqft_prev_qtr": _pos_int(qp.get("prevQuarterPrice")),
        "locality_price_growth_pct": _float(loc.get("percentageQuarterGrowth")),
        "faqs": faqs or None,
        "source_last_modified": date_text(ib.get("psmLastModiDate")),
    }
    for k, v in detail.items():
        if k in LIST_TRACKED and rec.get(k) is not None:
            continue  # the card value is the one tracked daily
        if v is not None or k not in rec:
            rec[k] = v
    # ids from the card are authoritative for which city the project was found in
    if card and card.get("city_id"):
        rec["city_id"] = card["city_id"]
    rec["content_hash"] = content_hash(rec)
    return rec
