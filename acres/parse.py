"""Turn 99acres JSON payloads into flat records.

Two inputs:
* a search results page (`__initialData__.srp.pageData.properties`) -> listing tuples
* a project page (`__initialData__.projectDetailState.pageData`)       -> full record
"""
import hashlib
import json
import re
from datetime import date, datetime, timezone

# ── helpers ──────────────────────────────────────────────────────────────────

def _g(obj, *path, default=None):
    """Safe nested get: _g(d, "a", "b", 0, "c")."""
    cur = obj
    for key in path:
        if cur is None:
            return default
        if isinstance(key, int):
            if not isinstance(cur, list) or key >= len(cur):
                return default
            cur = cur[key]
        elif isinstance(cur, dict):
            cur = cur.get(key)
        else:
            return default
    return default if cur is None else cur


def _int(v):
    try:
        if v in (None, "", "0") and v != 0:
            return None
        return int(float(str(v).replace(",", "")))
    except (TypeError, ValueError):
        return None


def _float(v):
    try:
        return float(str(v).replace(",", "")) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _epoch_to_date(v):
    v = _int(v)
    if not v:
        return None
    return datetime.fromtimestamp(v, tz=timezone.utc).date()


def _id_from_url(url):
    m = re.search(r"npxid-(r\d+)", url or "")
    return m.group(1) if m else None


def _plus_count(label):
    m = re.search(r"\+\s*(\d+)", label or "")
    return int(m.group(1)) if m else None


_DATE_RE = re.compile(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})")


def split_rera_number(raw):
    """'TN/35/Building/0097/2025dated 02-04-2025' -> ('TN/35/Building/0097/2025', date(2025,4,2))"""
    if not raw:
        return None, None
    text = str(raw).strip()
    reg_date = None
    m = _DATE_RE.search(text)
    if m:
        d, mth, y = (int(x) for x in m.groups())
        try:
            reg_date = date(y, mth, d)
        except ValueError:
            reg_date = None
    number = re.split(r"(?i)\s*dated", text)[0]
    number = re.sub(r"\s+", "", number).strip(" ,;")
    return (number or None), reg_date


def normalize_rera_number(number):
    """Key used to match against rera_projects.rera_number."""
    return re.sub(r"[^A-Z0-9]", "", (number or "").upper()) or None


def content_hash(record, fields):
    payload = {f: record.get(f) for f in fields}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


# ── listing page ─────────────────────────────────────────────────────────────

def parse_listing(initial_data):
    """Return (tuples, meta). Only PROJECT tuples are kept (the New Launch
    panel is projects; individual resale properties are ignored)."""
    srp = initial_data.get("srp") or {}
    page_data = srp.get("pageData") or {}
    out = []
    for p in page_data.get("properties") or []:
        if p.get("entityType") != "PROJECT":
            continue
        url = _g(p, "landingPage", "url") or (
            "https://www.99acres.com" + p["landingPageRelativeUrl"] if p.get("landingPageRelativeUrl") else None)
        pid = _id_from_url(url) or (f"r{p['projectUnitId']}" if p.get("projectUnitId") else None)
        if not pid or not url:
            continue
        loc = p.get("location") or {}
        out.append({
            "project_id": pid,
            "url": url.split("?")[0],
            "name": (p.get("heading") or "").strip() or None,
            "builder_name": p.get("builderName"),
            "locality": loc.get("localityName"),
            "city": loc.get("cityName"),
            "state": loc.get("stateName"),
            "possession_label": _g(p, "possessionStatus", "label"),
            "price_text": _g(p, "price", "label"),
            "price_min": _int(_g(p, "price", "min")),
            "price_max": _int(_g(p, "price", "max")),
            "price_per_sqft": _int(p.get("localizedPrice")),
            "property_types": p.get("subHeading"),
            "is_new_launch": bool(p.get("isNewLaunch")),
            "is_rera": p.get("reraDisplayTag") == "RERA",
            "tags": [t.get("id") for t in p.get("tags") or [] if isinstance(t, dict)],
        })
    meta = {
        "count": page_data.get("count"),
        "is_last_page": bool(srp.get("isLastpage")),
        "page": _int(_g(srp, "filters", "page")),
    }
    return out, meta


# ── project page ─────────────────────────────────────────────────────────────

TRACKED_FIELDS = [
    "name", "builder_name", "construction_status", "completion_date", "price_text",
    "price_min", "price_max", "property_types", "configurations", "rera_status",
    "rera_number", "unit_count", "brochure_url", "locality", "city",
]
HASH_FIELDS = TRACKED_FIELDS + [
    "highlights", "description", "specifications", "nearby_places", "facilities",
    "offers", "latitude", "longitude", "pincode", "street_address",
]


def _configurations(summary):
    cards = _g(summary, "configCards", "cards") or []
    out = []
    for c in cards:
        area = c.get("area") or {}
        price = c.get("price") or {}
        out.append({
            "bhk": _g(c, "configInfo", "label"),
            "type": _g(c, "configInfo", "subLabel"),
            "area_min": _int(area.get("min")),
            "area_max": _int(area.get("max")),
            "area_display": area.get("display"),
            "area_type": _g(area, "type", "label"),
            "price_min": _int(price.get("min")),
            "price_max": _int(price.get("max")),
            "price_label": price.get("label"),
        })
    return out


def _nearby(components):
    out = []
    for t in _g(components, "locationHighlights", "tuples") or []:
        out.append({"category": t.get("category"), "name": t.get("heading"), "distance": t.get("subHeading")})
    return out


def _strings(obj):
    """Collect every string inside a small nested structure (used for the
    locality 'what's great / what needs attention' lists whose exact shape
    varies)."""
    found = []
    if isinstance(obj, str):
        found.append(obj)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("imageUrl", "spriteIdentifier", "spriteUrl", "id", "iconUrl"):
                continue
            found.extend(_strings(v))
    elif isinstance(obj, list):
        for v in obj:
            found.extend(_strings(v))
    return found


def _locality_pros_cons(components):
    pros, cons = [], []
    for t in _g(components, "reiOverview", "pointsToConsider", "tuples") or []:
        tid = (t.get("id") or "").upper()
        texts = [s for s in _strings({k: v for k, v in t.items() if k not in ("title", "id")}) if len(s) > 3]
        if "GREAT" in tid:
            pros.extend(texts)
        elif "ATTENTION" in tid or "CONSIDER" in tid or "BAD" in tid:
            cons.extend(texts)
    return pros, cons


def _yoy(components):
    for ins in _g(components, "reiOverview", "localityInsights") or []:
        for p in ins.get("pointers") or []:
            m = re.search(r"(-?[\d.]+)\s*%\s*YoY", p.get("title") or "")
            if m:
                return _float(m.group(1))
    return None


def _ratings(components):
    rv = components.get("localityReviewDetails") or {}
    tab = rv.get("ratingTab") or {}
    counts = {c.get("rating"): c.get("count") for c in tab.get("counts") or []}
    feats = {t.get("identifier"): t.get("rating") for t in _g(rv, "ratingsByFeature", "tuples") or []}
    likes, dislikes = {}, {}
    for tg in rv.get("tags") or []:
        title = (tg.get("title") or "").lower()
        if "positive" in title:
            likes = tg.get("tagsCountMap") or {}
        elif "negative" in title:
            dislikes = tg.get("tagsCountMap") or {}
    return {
        "rating_avg": _float(tab.get("overall")),
        "rating_total": _int(tab.get("totalCount")),
        "rating_5_star": _int(counts.get(5)), "rating_4_star": _int(counts.get(4)),
        "rating_3_star": _int(counts.get(3)), "rating_2_star": _int(counts.get(2)),
        "rating_1_star": _int(counts.get(1)),
        "rating_connectivity": _float(feats.get("CONNECTIVITY_AND_COMMUTE")),
        "rating_lifestyle": _float(feats.get("LIFESTYLE_AND_FACILITIES")),
        "rating_safety": _float(feats.get("SAFETY_AND_SECURITY") or feats.get("SAFETY")),
        "rating_green_area": _float(feats.get("GREEN_AREA_AND_PARKS") or feats.get("GREEN_AREA") or feats.get("ENVIRONMENT")),
        "positive_mentions_pct": _int(rv.get("posTagsPercent")),
        "likes": likes or None,
        "dislikes": dislikes or None,
    }


def _rera(summary):
    r = summary.get("rera")
    if not r:
        return {"is_rera": False, "rera_status": None, "rera_number_raw": None, "rera_number": None,
                "rera_registered_on": None, "rera_url": None, "rera_phases": None}
    number, reg_date = split_rera_number(r.get("registrationNumber"))
    phases = []
    for t in r.get("tuples") or []:
        n, d = split_rera_number(t.get("registrationNumber"))
        phases.append({"title": t.get("title"), "status": _g(t, "registrationStatus", "value"),
                       "number": n, "registered_on": d.isoformat() if d else None,
                       "qr_code_url": t.get("qrCodeUrl") or None,
                       "bank_name": t.get("bankName") or None, "bank_account": t.get("bankAccountNumber") or None,
                       "ifsc": t.get("ifscCode") or None})
    return {
        "is_rera": True,
        "rera_status": _g(r, "registrationStatus", "value") or _g(r, "registrationStatus", "label"),
        "rera_number_raw": r.get("registrationNumber"),
        "rera_number": number,
        "rera_registered_on": reg_date,
        "rera_url": r.get("url"),
        "rera_phases": phases or None,
    }


def parse_project(initial_data, url, listing=None):
    """Full record for `acres_new_launch`. `listing` is the tuple captured at
    discovery time (adds price/sqft and the panel's possession label)."""
    pds = initial_data.get("projectDetailState") or {}
    pd = pds.get("pageData") or {}
    basic = pd.get("basicDetails") or {}
    comps = pd.get("components") or {}
    summary = comps.get("summaryLayer") or {}
    marketing = pd.get("marketing") or {}
    loc = basic.get("location") or {}
    more = comps.get("moreAboutProject") or {}
    layer = more.get("layerContent") or {}
    docs = comps.get("documents") or {}
    builder = comps.get("builder") or {}
    listing = listing or {}

    project_id = _id_from_url(url) or (f"r{basic['projectId']}" if basic.get("projectId") else None)
    if not project_id:
        raise ValueError(f"no project id in {url}")

    tags = {t.get("id"): t.get("label") for t in summary.get("tags") or [] if isinstance(t, dict)}
    price_min = _int(_g(basic, "price", "min")) or _int(marketing.get("minPrice")) or _int(marketing.get("budgetMin"))
    price_max = _int(_g(basic, "price", "max")) or _int(marketing.get("maxPrice")) or _int(marketing.get("budgetMax"))
    completion_epoch = _int(_g(summary, "completionDate", "value"))
    pros, cons = _locality_pros_cons(comps)
    builder_url = builder.get("url")
    bid = re.search(r"bid-(\d+)", builder_url or "")

    rec = {
        "project_id": project_id,
        "url": url.split("?")[0],
        "name": (basic.get("name") or summary.get("name") or listing.get("name") or "").strip() or None,
        "locality": loc.get("localityName") or marketing.get("localityName"),
        "city": loc.get("cityName") or marketing.get("cityName"),
        "state": loc.get("stateName") or listing.get("state"),
        "pincode": (basic.get("postalCode") or None) if str(basic.get("postalCode") or "").strip("0 ") else None,
        "street_address": basic.get("streetAddress") or None,
        "latitude": _float(loc.get("latitude")),
        "longitude": _float(loc.get("longitude")),
        "locality_id": (loc.get("localityId") or "").split("_")[0] or None,
        "city_id": (loc.get("cityId") or "").split("_")[0] or None,
        "state_id": (loc.get("stateId") or "").split("_")[0] or None,
        "builder_name": builder.get("name") or marketing.get("builderName") or listing.get("builder_name"),
        "builder_legal_name": marketing.get("builderName"),
        "builder_url": builder_url,
        "builder_id": bid.group(1) if bid else None,
        "no_brokerage": "NO_BROKERAGE" in tags,
        "has_3d_floor_plans": "FLOORPLANS_3D" in tags,
        "top_facilities_cnt": _plus_count(tags.get("TOP_FACILITIES")),
        "construction_status": _g(summary, "constructionStatus", "label") or marketing.get("availability"),
        "construction_status_code": _g(summary, "constructionStatus", "value") or marketing.get("constructionStatus"),
        "completion_date": (_g(summary, "constructionStageInfo", "subLabel") or "").replace("Completion in", "").strip() or None,
        "completion_on": _epoch_to_date(completion_epoch),
        "possession_label": _g(summary, "completionDate", "label") or listing.get("possession_label"),
        "price_text": _g(summary, "price", "label") or _g(basic, "price", "label") or listing.get("price_text"),
        "price_min": price_min or listing.get("price_min"),
        "price_max": price_max or listing.get("price_max"),
        "has_extra_charges": (_g(summary, "price", "footerText") or "").lower().find("charges") >= 0,
        "price_per_sqft": listing.get("price_per_sqft"),
        "property_types": _g(summary, "configCards", "configLabel") or listing.get("property_types"),
        "property_type": marketing.get("propertyType"),
        "configurations": _configurations(summary) or None,
        "highlights": [t for t in _g(comps, "usp", "tuples") or [] if isinstance(t, str)] or None,
        "description": more.get("description") or None,
        "specifications": layer.get("specifications") or None,
        "unit_count": _int(layer.get("unitCount")),
        "tower_count": _int(layer.get("towerCount")),
        "floor_count": _int(layer.get("floorCount")),
        "total_area_text": _g(layer, "totalArea", "display"),
        "open_area_pct": _int(layer.get("openAreaPercentage")),
        "brochure_url": docs.get("primaryDownloadUrl") or _g(docs, "brochureDocument", "variants", "ORIGINAL"),
        "payment_plan_url": _g(docs, "priceListDocument", "variants", "ORIGINAL"),
        "cover_image_url": _g(summary, "coverImage", "photoGrids", 0, "variants", "MEDIUM") or marketing.get("photo"),
        "logo_url": _g(summary, "logo", "variants", "ORIGINAL"),
        "images_count": _int(summary.get("mediaCount")),
        "facilities": [t.get("label") for t in _g(comps, "facilities", "tuples") or [] if t.get("label")] or None,
        "nearby_places": _nearby(comps) or None,
        "offers": [t for t in (_g(comps, "offers", "tuples") or []) ] or None,
        "faqs": [{"q": t.get("question"), "a": t.get("answer")} for t in _g(comps, "questionnaire", "tuples") or []] or None,
        "yoy_price_change": _yoy(comps),
        "locality_pros": pros or None,
        "locality_cons": cons or None,
        "is_new_launch": bool(marketing.get("isNewLaunch")) or listing.get("is_new_launch", False),
        "listing_tags": listing.get("tags") or None,
        "meta_title": _g(pd, "seoContent", "metaTitle"),
    }
    rec.update(_rera(summary))
    rec.update(_ratings(comps))
    rec["rera_number_key"] = normalize_rera_number(rec["rera_number"])
    rec["content_hash"] = content_hash(rec, HASH_FIELDS)
    return rec
