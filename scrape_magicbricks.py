#!/usr/bin/env python
"""Scrape Magicbricks MagicHomes (new projects, all India) into CockroachDB.

    python scrape_magicbricks.py                            # all-India feed (~1,407 pages) + 1,500 project pages
    python scrape_magicbricks.py --full                     # all-India feed + every due project page (~26 h)
    python scrape_magicbricks.py --discover-only            # only walk the list (prices/status tracked daily)
    python scrape_magicbricks.py --details-only --max-details 50
    python scrape_magicbricks.py --state GA                 # one state's cities instead of the India feed
    python scrape_magicbricks.py --state GA --city 2669     # one city only
    python scrape_magicbricks.py --url https://www.magicbricks.com/...-pdpid-...   # one project, print JSON

Exit codes: 0 ok · 2 blocked (403/429/captcha) · 3 database error
            4 finished but some pages failed · 5 another run held the lock too long
"""
import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from acres.runlock import acquire_run_lock  # noqa: E402
from mb import config, db  # noqa: E402
from mb.http import Site, Blocked, Gone  # noqa: E402
from mb.parse import parse_card, parse_project, date_ymd  # noqa: E402

log = logging.getLogger("mb")
DATE_FIELDS = ("launch_date", "possession_date", "rera_updated_on")


def setup_logging(verbose):
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    handlers = [logging.StreamHandler(sys.stdout),
                logging.FileHandler(config.DATA_DIR / "mb_run.log", encoding="utf-8")]
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format=fmt, handlers=handlers)
    logging.getLogger("curl_cffi").setLevel(logging.WARNING)


def _json_default(o):
    return o.isoformat() if hasattr(o, "isoformat") else str(o)


def discover_city(site, feed_code, city_id, city_name, max_pages, on_page):
    """Walk the cards API (city_id None = all-India feed), handing each page's
    cards to on_page(cards) as it arrives. Returns (found, pages, blocked)."""
    seen, pages = set(), 0
    for page in range(1, max_pages + 1):
        try:
            data = site.cards(city_id, page)
        except Blocked as e:
            log.error("blocked while listing %s/%s page %d: %s", feed_code, city_name, page, e)
            return len(seen), pages, True
        pages += 1
        raw = data.get("projectsCards") or []
        cards = [parse_card(c, city_id) for c in raw if c.get("psmid") and (c.get("pdpUrl") or c.get("oldPdpUrl"))]
        if city_id and page == 1 and cards and all(
                c["city"] and c["city"].lower() != city_name.lower() for c in cards):
            # Magicbricks falls back to Chennai for unknown city ids
            log.error("%s: city id %s returned %s projects, not %s - check config", feed_code,
                      city_id, cards[0]["city"], city_name)
            return 0, pages, False
        fresh = [c for c in cards if c["psmid"] not in seen]
        seen.update(c["psmid"] for c in cards)
        if fresh:
            on_page(fresh)
        total, page_count = data.get("totalCount"), data.get("pageCount") or 0
        if page == 1 or page % 100 == 0:
            log.info("%s %s page %d/%s: total so far %d of %s", feed_code, city_name, page, page_count,
                     len(seen), total)
        if not raw or page >= page_count:
            break
    return len(seen), pages, False


def run_state(site, conn, state_code, args):
    """Runs one state and always records a row in mb_scrape_runs."""
    started = datetime.now(timezone.utc)
    counts = {"cities": 0, "pages": 0, "discovered": 0, "new_projects": 0, "list_changes": 0, "details": 0,
              "inserted": 0, "updated": 0, "unchanged": 0, "failed": 0, "blocked": False}
    outcome = {"status": "ok", "error": None}
    try:
        _run_state(site, conn, state_code, args, counts, outcome)
    except KeyboardInterrupt:
        outcome.update(status="failed", error="interrupted")
        raise
    except Exception as e:
        outcome.update(status="failed", error=repr(e))
        raise
    finally:
        if outcome["status"] == "ok" and counts["failed"]:
            outcome["status"] = "partial"
        if conn is not None:
            try:
                db.record_run(conn, state_code, started, outcome["status"], counts, error=outcome["error"])
            except Exception as e:
                log.error("could not record run: %s", db.redact(e))
        log.info("%s done: %s %s", state_code, outcome["status"], counts)
    return outcome["status"], counts


def _run_state(site, conn, state_code, args, counts, outcome):
    out_dir = config.DATA_DIR / "magicbricks" / state_code
    if state_code == config.INDIA:
        cities = {None: "India"}
    else:
        cities = config.STATES[state_code]["cities"]
        if args.city:
            cities = {k: v for k, v in cities.items() if k in args.city.split(",")}
    all_cards = []

    def on_page(cards):
        if conn is not None:
            new, changed = db.save_listing(conn, state_code, cards)
            counts["new_projects"] += new
            counts["list_changes"] += changed
        else:
            all_cards.extend(cards)

    # 1) listing: every page of the feed (or of each city)
    if not args.details_only:
        for city_id, city_name in cities.items():
            found, pages, blocked = discover_city(site, state_code, city_id, city_name, args.max_pages, on_page)
            counts["cities"] += 1
            counts["pages"] += pages
            counts["discovered"] += found
            log.info("%s %s: %d projects on %d pages", state_code, city_name, found, pages)
            if blocked:
                counts["blocked"] = True
                outcome.update(status="blocked", error=f"blocked while listing {city_name}")
                return
        if conn is None and all_cards:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "listing.json").write_text(
                json.dumps(all_cards, indent=1, ensure_ascii=False, default=_json_default), encoding="utf-8")
        log.info("%s listing: %d projects, %d new, %d with price/status/date changes", state_code,
                 counts["discovered"], counts["new_projects"], counts["list_changes"])

    # 2) details
    if args.discover_only:
        return
    if conn is not None:
        work = db.claim_due(conn, state_code, args.max_details)
    else:
        work = [{"psmid": c["psmid"], "url": c["url"], "listing": c} for c in all_cards[: args.max_details]]
    log.info("%s details: %d project pages to fetch", state_code, len(work))
    for n, item in enumerate(work, 1):
        card = dict(item.get("listing") or {})
        for k in DATE_FIELDS:  # dates come back from JSONB as strings
            if isinstance(card.get(k), str):
                card[k] = date_ymd(card[k])
        try:
            state = site.project_state(item["url"])
            rec = parse_project(state, item["url"], card=card)
            counts["details"] += 1
            if conn is not None:
                result = db.upsert_project(conn, state_code, rec)
                db.mark_done(conn, item["psmid"])
                counts[result] += 1
            else:
                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / f"{rec['psmid']}.json").write_text(
                    json.dumps(rec, indent=1, ensure_ascii=False, default=_json_default), encoding="utf-8")
                counts["inserted"] += 1
            if n <= 5 or n % 25 == 0:
                log.info("  [%d/%d] %s %s | %s | %s | RERA %s", n, len(work), rec["psmid"], rec.get("name"),
                         rec.get("price_text"), rec.get("status"), rec.get("rera_number") or "-")
        except Gone as e:
            counts["failed"] += 1
            log.warning("  gone: %s", e)
            if conn is not None:
                db.mark_failed(conn, item["psmid"], str(e), gone=True)
        except Blocked as e:
            counts["blocked"] = True
            outcome.update(status="blocked", error=str(e))
            log.error("  blocked, stopping %s for today: %s", state_code, e)
            break
        except Exception as e:
            counts["failed"] += 1
            log.exception("  failed %s: %s", item["psmid"], db.redact(e))
            if conn is not None:
                try:
                    db.mark_failed(conn, item["psmid"], repr(e))
                except Exception as e2:
                    log.error("  could not mark failed: %s", db.redact(e2))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", default=config.INDIA,
                    help="IN = all-India MagicHomes feed (default); or TN, KA, MH, GA, UK, comma list")
    ap.add_argument("--city", help="only these Magicbricks city ids (comma list), e.g. 2669")
    ap.add_argument("--discover-only", action="store_true")
    ap.add_argument("--details-only", action="store_true")
    ap.add_argument("--max-pages", type=int, default=None, help=f"per feed/city, default {config.MAX_LISTING_PAGES}")
    ap.add_argument("--max-details", type=int, default=None, help=f"per run, default {config.MAX_DETAILS_PER_RUN}")
    ap.add_argument("--full", action="store_true", help="every page and every due project page")
    ap.add_argument("--pause", type=int, default=30, help="seconds between states")
    ap.add_argument("--lock-wait", type=int, default=240)
    ap.add_argument("--no-db", action="store_true", help="write JSON to data/magicbricks instead of the database")
    ap.add_argument("--url", help="scrape one project page and print the record")
    ap.add_argument("--save", action="store_true", help="with --url: also write it to the database")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    if args.max_pages is None:
        args.max_pages = config.FULL_MAX_LISTING_PAGES if args.full else config.MAX_LISTING_PAGES
    if args.max_details is None:
        args.max_details = config.FULL_MAX_DETAILS if args.full else config.MAX_DETAILS_PER_RUN
    setup_logging(args.verbose)

    site = Site()
    conn = None
    if not args.no_db and (not args.url or args.save):
        try:
            conn = db.connect()
            db.ensure_schema(conn)
        except Exception as e:
            log.error("database: %s", db.redact(e))
            return 3

    if args.url:
        try:
            state = site.project_state(args.url)
            card = None
            if conn is not None:  # reuse the listing card so card-only fields are kept
                psmid = (state["projectDetailData"]["prjMobileBean"] or {}).get("psmId")
                row = conn.execute("SELECT listing FROM mb_scrape_queue WHERE psmid = %s", (psmid,)).fetchone()
                if row and row[0]:
                    card = {k: (date_ymd(v) if k in DATE_FIELDS and isinstance(v, str) else v)
                            for k, v in row[0].items()}
            rec = parse_project(state, args.url, card=card)
        except Blocked as e:
            log.error("%s", e)
            return 2
        print(json.dumps(rec, indent=1, ensure_ascii=False, default=_json_default))
        if conn is not None:
            print("db:", db.upsert_project(conn, config.INDIA, rec))
        return 0

    states = list(config.STATES) if args.state == "all" else [s.strip().upper() for s in args.state.split(",")]
    unknown = [s for s in states if s != config.INDIA and s not in config.STATES]
    if unknown:
        ap.error(f"unknown state(s) {unknown}; choose from {[config.INDIA] + list(config.STATES)}")

    lock = acquire_run_lock(args.lock_wait, name="mb_run.lock")  # noqa: F841  (held until exit)
    if lock is None:
        log.error("another Magicbricks run is still going after %d min - giving up", args.lock_wait)
        return 5
    if args.full:
        log.info("FULL run: up to %d pages per city and %d project pages per state", args.max_pages, args.max_details)

    worst = 0
    try:
        for i, state_code in enumerate(states):
            if i and args.pause:
                time.sleep(args.pause)
            try:
                status, _ = run_state(site, conn, state_code, args)
            except Exception as e:
                log.exception("unexpected error in %s: %s", state_code, db.redact(e))
                worst = max(worst, 3 if "psycopg" in type(e).__module__ else 4)
                continue
            worst = max(worst, {"ok": 0, "partial": 4, "blocked": 2}.get(status, 4))
    except KeyboardInterrupt:
        log.error("interrupted - stopping")
        worst = 4
    return worst


if __name__ == "__main__":
    sys.exit(main())
