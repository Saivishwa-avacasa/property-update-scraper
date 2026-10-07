#!/usr/bin/env python
"""Scrape the 99acres "New Launch" panel into CockroachDB.

    python scrape_99acres.py --state TN                 # discover + up to 60 detail pages
    python scrape_99acres.py --state all                # all five states, one after another
    python scrape_99acres.py --state KA --discover-only # just refresh the queue
    python scrape_99acres.py --state KA --details-only --max-details 100
    python scrape_99acres.py --url https://www.99acres.com/...-npxid-r459201   # one project, print JSON
    python scrape_99acres.py --warm-cookies             # open Chrome once to get past the captcha
    python scrape_99acres.py --state TN --no-db         # write JSON files to data/ instead of the DB

Exit codes: 0 ok · 2 blocked by captcha (try again later / run --warm-cookies)
            3 database error · 4 finished but some detail pages failed
"""
import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from acres import config, db  # noqa: E402
from acres.discover import discover  # noqa: E402
from acres.http import Site, Blocked, Gone  # noqa: E402
from acres.parse import parse_project  # noqa: E402

log = logging.getLogger("acres")


def setup_logging(verbose):
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    handlers = [logging.StreamHandler(sys.stdout),
                logging.FileHandler(config.DATA_DIR / "run.log", encoding="utf-8")]
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format=fmt, handlers=handlers)
    logging.getLogger("curl_cffi").setLevel(logging.WARNING)


def _json_default(o):
    if hasattr(o, "isoformat"):
        return o.isoformat()
    return str(o)


def run_state(site, conn, state_code, args):
    started = datetime.now(timezone.utc)
    counts = {"pages": 0, "discovered": 0, "new_in_queue": 0, "details": 0,
              "inserted": 0, "updated": 0, "unchanged": 0, "failed": 0, "blocked": False}
    status = "ok"
    error = None
    out_dir = config.DATA_DIR / state_code
    tuples = []

    # 1) discovery
    if not args.details_only:
        tuples, meta = discover(site, state_code, max_pages=args.max_pages, keyword=not args.broad)
        counts["pages"] = meta["pages"]
        counts["discovered"] = len(tuples)
        counts["blocked"] = meta["blocked"]
        if conn is not None and tuples:
            counts["new_in_queue"] = db.enqueue(conn, state_code, tuples)
        elif tuples:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "listing.json").write_text(json.dumps(tuples, indent=1, ensure_ascii=False), encoding="utf-8")
        log.info("%s discovery: %d projects on %d pages, %d new in queue",
                 state_code, len(tuples), meta["pages"], counts["new_in_queue"])
        if meta["blocked"] and not tuples:
            status = "blocked"

    # 2) details
    if not args.discover_only and status != "blocked":
        if conn is not None:
            work = db.claim_due(conn, state_code, args.max_details)
        else:
            work = [{"project_id": t["project_id"], "url": t["url"], "listing": t} for t in tuples[: args.max_details]]
        log.info("%s details: %d pages to fetch", state_code, len(work))
        for item in work:
            try:
                data = site.fetch_initial_data(item["url"], warmup_url=config.search_url(state_code, 1))
                rec = parse_project(data, item["url"], listing=item.get("listing"))
                counts["details"] += 1
                if conn is not None:
                    result = db.upsert_project(conn, state_code, rec)
                    db.mark_done(conn, item["project_id"])
                    counts[result] += 1
                else:
                    out_dir.mkdir(parents=True, exist_ok=True)
                    (out_dir / f"{rec['project_id']}.json").write_text(
                        json.dumps(rec, indent=1, ensure_ascii=False, default=_json_default), encoding="utf-8")
                    counts["inserted"] += 1
                log.info("  %s %s | %s | %s | RERA %s", item["project_id"], rec.get("name"),
                         rec.get("price_text"), rec.get("completion_date"), rec.get("rera_number") or "-")
            except Gone as e:
                counts["failed"] += 1
                log.warning("  gone: %s", e)
                if conn is not None:
                    db.mark_failed(conn, item["project_id"], str(e), gone=True)
            except Blocked as e:
                counts["blocked"] = True
                status = "blocked"
                error = str(e)
                log.error("  blocked, stopping %s for today: %s", state_code, e)
                break
            except Exception as e:  # parse or DB error on one project
                counts["failed"] += 1
                log.exception("  failed %s: %s", item["project_id"], db.redact(e))
                if conn is not None:
                    try:
                        db.mark_failed(conn, item["project_id"], repr(e))
                    except Exception as e2:
                        log.error("  could not mark failed: %s", db.redact(e2))

    if status == "ok" and counts["failed"]:
        status = "partial"
    if conn is not None:
        try:
            db.record_run(conn, state_code, started, status, counts, error=error)
        except Exception as e:
            log.error("could not record run: %s", db.redact(e))
    log.info("%s done: %s %s", state_code, status, counts)
    return status, counts


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", default="all", help="TN, KA, MH, GA, UK, 'all', or comma list")
    ap.add_argument("--discover-only", action="store_true")
    ap.add_argument("--details-only", action="store_true")
    ap.add_argument("--max-pages", type=int, default=config.MAX_LISTING_PAGES)
    ap.add_argument("--max-details", type=int, default=config.MAX_DETAILS_PER_RUN)
    ap.add_argument("--broad", action="store_true", help="drop the keyword from the search URL (more results)")
    ap.add_argument("--pause", type=int, default=120, help="seconds between states when running several")
    ap.add_argument("--no-db", action="store_true", help="write JSON to data/ instead of the database")
    ap.add_argument("--no-browser", action="store_true", help="never open Chrome for the captcha; fail instead")
    ap.add_argument("--warm-cookies", action="store_true", help="only open Chrome and refresh cookies")
    ap.add_argument("--url", help="scrape one project page and print the record")
    ap.add_argument("--save", action="store_true", help="with --url: also write it to the database")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    setup_logging(args.verbose)

    site = Site(allow_browser=not args.no_browser)

    if args.warm_cookies:
        try:
            site.warm_up(config.search_url("TN", 1))
        except Blocked as e:
            log.error("%s", e)
            return 2
        return 0

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
            data = site.fetch_initial_data(args.url)
        except Blocked as e:
            log.error("%s", e)
            return 2
        rec = parse_project(data, args.url)
        print(json.dumps(rec, indent=1, ensure_ascii=False, default=_json_default))
        if conn is not None:
            state = next((k for k, v in config.STATES.items() if v["name"] == rec.get("state")), "XX")
            print("db:", db.upsert_project(conn, state, rec))
        return 0

    states = list(config.STATES) if args.state == "all" else [s.strip().upper() for s in args.state.split(",")]
    unknown = [s for s in states if s not in config.STATES]
    if unknown:
        ap.error(f"unknown state(s) {unknown}; choose from {list(config.STATES)}")

    worst = 0
    for i, state_code in enumerate(states):
        if i and args.pause:
            log.info("pausing %ds before %s", args.pause, state_code)
            time.sleep(args.pause)
        try:
            status, counts = run_state(site, conn, state_code, args)
        except Exception as e:
            log.exception("unexpected error in %s: %s", state_code, db.redact(e))
            worst = max(worst, 3 if "psycopg" in type(e).__module__ else 4)
            continue
        worst = max(worst, {"ok": 0, "partial": 4, "blocked": 2}.get(status, 4))
    return worst


if __name__ == "__main__":
    sys.exit(main())
