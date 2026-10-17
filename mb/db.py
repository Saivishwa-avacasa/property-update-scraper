"""CockroachDB storage for Magicbricks: project table, change history, scrape
queue, run log. Uses the same connection as the 99acres scraper (acres.db).
Nothing here ever drops data."""
import logging

from acres.db import connect, redact, _text  # noqa: F401  (re-exported)

from . import config
from .parse import LIST_TRACKED, PAGE_TRACKED

log = logging.getLogger("mb.db")

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS mb_new_projects (
        psmid                        STRING PRIMARY KEY,   -- 5436337 (Magicbricks project id)
        url                          STRING NOT NULL,
        source_state_code            STRING NOT NULL,      -- feed it came from: IN (all India) or a state code
        -- location
        name                         STRING,
        city                         STRING,
        city_id                      STRING,
        locality                     STRING,
        locality_id                  STRING,
        state                        STRING,
        pincode                      STRING,
        full_address                 STRING,
        latitude                     FLOAT8,
        longitude                    FLOAT8,
        -- developer
        developer_id                 STRING,
        developer_name               STRING,
        developer_url                STRING,
        developer_logo_url           STRING,
        developer_experience_years   INT,
        developer_total_projects     INT,
        developer_completed_projects INT,
        developer_ongoing_projects   INT,
        developer_description        STRING,
        -- RERA
        is_rera                      BOOL NOT NULL DEFAULT false,
        rera_number                  STRING,
        rera_numbers                 STRING[],
        rera_number_key              STRING,
        rera_phases                  JSONB,
        rera_portal_url              STRING,
        rera_updated_on              DATE,
        rera_project_id              UUID,
        rera_match_method            STRING,
        -- status and dates
        status_code                  INT,
        status                       STRING,
        posting_type                 STRING,
        top_tag                      STRING,
        is_prelaunch                 BOOL,
        launch_date                  DATE,
        possession_text              STRING,
        possession_date              DATE,
        -- price and size
        price_text                   STRING,
        price_min                    INT8,
        price_max                    INT8,
        price_per_sqft_min           INT8,
        price_per_sqft_max           INT8,
        property_type                STRING,
        bhk_text                     STRING,
        configurations               JSONB,
        project_area                 FLOAT8,
        project_area_unit            STRING,
        project_area_text            STRING,
        total_units                  INT,
        available_units              INT,
        tower_count                  INT,
        -- content
        usp                          STRING[],
        summary                      STRING,
        highlights                   STRING,
        description                  STRING,
        specifications               STRING,
        amenities                    STRING[],
        brochure_url                 STRING,
        master_plan_url              STRING,
        cover_image_url              STRING,
        images_count                 INT,
        video_url                    STRING,
        faqs                         JSONB,
        -- ratings and locality
        rating_avg                   DECIMAL(3,1),
        rating_count                 INT,
        reviews_count                INT,
        demand_score                 FLOAT8,
        locality_rating_avg          DECIMAL(3,1),
        locality_rating_count        INT,
        locality_ratings             JSONB,
        locality_rank                STRING,
        locality_price_sqft          INT8,
        locality_price_sqft_prev_qtr INT8,
        locality_price_growth_pct    DECIMAL(6,2),
        -- bookkeeping
        source_last_modified         DATE,
        details_scraped              BOOL NOT NULL DEFAULT false,
        content_hash                 STRING,
        first_seen_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
        last_seen_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
        last_scraped_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
        last_changed_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
        INDEX mb_new_projects_state_idx (source_state_code, last_scraped_at),
        INDEX mb_new_projects_rera_key_idx (rera_number_key)
    )""",
    """CREATE TABLE IF NOT EXISTS mb_change_history (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        psmid       STRING NOT NULL REFERENCES mb_new_projects (psmid) ON DELETE CASCADE,
        changed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        field_name  STRING NOT NULL,
        old_value   STRING,
        new_value   STRING,
        INDEX mb_change_history_project_idx (psmid, changed_at)
    )""",
    """CREATE TABLE IF NOT EXISTS mb_scrape_queue (
        psmid             STRING PRIMARY KEY,
        source_state_code STRING NOT NULL,
        city_id           STRING,
        url               STRING NOT NULL,
        status            STRING NOT NULL DEFAULT 'pending',   -- pending / done / failed / gone
        attempts          INT NOT NULL DEFAULT 0,
        last_error        STRING,
        listing           JSONB,                               -- parsed card from the cards API
        launch_date       DATE,                                -- newest launches get details first
        discovered_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
        last_seen_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        next_scrape_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
        INDEX mb_scrape_queue_due_idx (source_state_code, status, next_scrape_at)
    )""",
    """CREATE TABLE IF NOT EXISTS mb_scrape_runs (
        id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        source_state_code STRING NOT NULL,
        started_at        TIMESTAMPTZ NOT NULL,
        completed_at      TIMESTAMPTZ,
        status            STRING NOT NULL,                     -- ok / partial / blocked / failed
        cities            INT NOT NULL DEFAULT 0,
        pages_fetched     INT NOT NULL DEFAULT 0,
        discovered        INT NOT NULL DEFAULT 0,
        new_projects      INT NOT NULL DEFAULT 0,
        details_fetched   INT NOT NULL DEFAULT 0,
        inserted          INT NOT NULL DEFAULT 0,
        updated           INT NOT NULL DEFAULT 0,
        unchanged         INT NOT NULL DEFAULT 0,
        failed            INT NOT NULL DEFAULT 0,
        blocked           BOOL NOT NULL DEFAULT false,
        error_message     STRING
    )""",
    "ALTER TABLE mb_new_projects ADD COLUMN IF NOT EXISTS state_code STRING",   # TN / KA / MH ... from the project page
    "CREATE INDEX IF NOT EXISTS mb_new_projects_state_code_idx ON mb_new_projects (state_code)",
    # projects no longer on MagicHomes = last_seen_at stops moving (see check_queries.sql #14)
    "CREATE INDEX IF NOT EXISTS mb_new_projects_last_seen_idx ON mb_new_projects (last_seen_at)",
    "ALTER TABLE mb_scrape_runs ADD COLUMN IF NOT EXISTS list_changes INT NOT NULL DEFAULT 0",
]

ARRAY_COLUMNS = {"rera_numbers", "usp", "amenities"}
JSON_COLUMNS = {"rera_phases", "configurations", "faqs", "locality_ratings"}
SKIP_COLUMNS = {"psmid", "url", "content_hash", "rera_project_id", "rera_match_method"}


def ensure_schema(conn):
    for stmt in SCHEMA:
        conn.execute(stmt)


def _adapt(rec):
    from psycopg.types.json import Jsonb
    cols, vals = [], []
    for k, v in rec.items():
        if k in SKIP_COLUMNS:
            continue
        cols.append(k)
        if k in JSON_COLUMNS:
            vals.append(Jsonb(v) if v is not None else None)
        elif k in ARRAY_COLUMNS:
            vals.append([str(x) for x in v] if v else None)
        else:
            vals.append(v)
    return cols, vals


# ── listing stage ──────────────────────────────────────────────────────────────

def save_listing(conn, feed_code, cards):
    """Store one page of cards. New projects go into mb_new_projects (list
    fields) and the queue. Known projects: the card is compared with
    yesterday's card for LIST_TRACKED fields, changes go to mb_change_history
    and onto the project row. Returns (new, changed_projects).

    Batched: a page costs ~5 round trips to the database instead of ~60
    (each round trip to CockroachDB Cloud is ~70 ms)."""
    from psycopg.types.json import Jsonb
    if not cards:
        return 0, 0
    cards = list({c["psmid"]: c for c in cards}.values())   # one row per project in a statement
    ids = [c["psmid"] for c in cards]
    old_cards = {r[0]: r[1] for r in conn.execute(
        "SELECT psmid, listing FROM mb_scrape_queue WHERE psmid = ANY(%s)", (ids,)).fetchall()}
    known = {r[0] for r in conn.execute(
        "SELECT psmid FROM mb_new_projects WHERE psmid = ANY(%s)", (ids,)).fetchall()}

    fresh = [c for c in cards if c["psmid"] not in known]
    seen, changes = [], []          # known projects; (psmid, field, old, new)
    for c in cards:
        if c["psmid"] not in known:
            continue
        seen.append(c["psmid"])
        old = old_cards.get(c["psmid"]) or {}
        for f in LIST_TRACKED:
            if f in old and c.get(f) is not None and _text(old.get(f)) != _text(c.get(f)):
                changes.append((c["psmid"], f, old.get(f), c.get(f)))

    with conn.transaction():
        # queue: one multi-row upsert
        conn.execute(
            "INSERT INTO mb_scrape_queue (psmid, source_state_code, city_id, url, listing, launch_date) VALUES "
            + ", ".join(["(%s, %s, %s, %s, %s, %s)"] * len(cards))
            + """ ON CONFLICT (psmid) DO UPDATE
                 SET last_seen_at = now(), updated_at = now(), listing = EXCLUDED.listing,
                     url = EXCLUDED.url, launch_date = EXCLUDED.launch_date,
                     source_state_code = EXCLUDED.source_state_code""",
            [v for c in cards for v in (c["psmid"], feed_code, c.get("city_id"), c["url"],
                                        Jsonb(c, dumps=_dumps), c.get("launch_date"))])
        # new projects: one multi-row insert (every card has the same keys)
        if fresh:
            cols, _ = _adapt(fresh[0])
            all_cols = ["psmid", "url", "source_state_code"] + cols
            rows = []
            for c in fresh:
                _, vals = _adapt(c)
                rows += [c["psmid"], c["url"], feed_code] + vals
            conn.execute(
                f"INSERT INTO mb_new_projects ({', '.join(all_cols)}) VALUES "
                + ", ".join(["(" + ", ".join(["%s"] * len(all_cols)) + ")"] * len(fresh))
                + " ON CONFLICT (psmid) DO NOTHING", rows)
        # known projects: still listed today
        if seen:
            conn.execute("UPDATE mb_new_projects SET last_seen_at = now(), source_state_code = %s "
                         "WHERE psmid = ANY(%s)", (feed_code, seen))
        # field changes (rare): history rows + new values on the project
        if changes:
            conn.execute(
                "INSERT INTO mb_change_history (psmid, field_name, old_value, new_value) VALUES "
                + ", ".join(["(%s, %s, %s, %s)"] * len(changes)),
                [v for p, f, o, n in changes for v in (p, f, _text(o), _text(n))])
            per_project = {}
            for p, f, _, n in changes:
                per_project.setdefault(p, []).append((f, n))
            for p, fields in per_project.items():
                conn.execute(
                    f"UPDATE mb_new_projects SET {', '.join(f'{f} = %s' for f, _ in fields)}, "
                    "last_changed_at = now() WHERE psmid = %s", [n for _, n in fields] + [p])
    return len(fresh), len({p for p, *_ in changes})


def _dumps(obj):
    import json
    return json.dumps(obj, ensure_ascii=False, default=str)


# ── queue ──────────────────────────────────────────────────────────────────────

def claim_due(conn, state_code, limit):
    """Projects whose page should be fetched now: never-detailed first (newest
    launch first), then failed retries, then the weekly re-checks."""
    rows = conn.execute(
        """SELECT psmid, url, listing, status, attempts
           FROM mb_scrape_queue
           WHERE source_state_code = %s
             AND status IN ('pending', 'done', 'failed')
             AND next_scrape_at <= now()
             AND attempts < %s
           ORDER BY CASE status WHEN 'pending' THEN 0 WHEN 'failed' THEN 1 ELSE 2 END,
                    launch_date DESC NULLS LAST
           LIMIT %s""",
        (state_code, config.MAX_ATTEMPTS * 3, limit),
    ).fetchall()
    return [{"psmid": r[0], "url": r[1], "listing": r[2], "status": r[3], "attempts": r[4]} for r in rows]


def mark_done(conn, psmid):
    conn.execute(
        """UPDATE mb_scrape_queue
           SET status = 'done', attempts = 0, last_error = NULL, updated_at = now(),
               next_scrape_at = now() + %s * INTERVAL '1 day'
           WHERE psmid = %s""",
        (config.RESCRAPE_AFTER_DAYS, psmid))


def mark_failed(conn, psmid, error, gone=False):
    if gone:
        conn.execute(
            "UPDATE mb_scrape_queue SET status = 'gone', last_error = %s, updated_at = now(), "
            "next_scrape_at = now() + INTERVAL '30 days' WHERE psmid = %s",
            (str(error)[:500], psmid))
        return
    conn.execute(
        """UPDATE mb_scrape_queue
           SET attempts = attempts + 1, last_error = %s, updated_at = now(),
               status = CASE WHEN attempts + 1 >= %s THEN 'failed' ELSE status END,
               next_scrape_at = now() + INTERVAL '1 day'
           WHERE psmid = %s""",
        (redact(error)[:500], config.MAX_ATTEMPTS, psmid))


# ── projects ───────────────────────────────────────────────────────────────────

def match_rera(conn, state_code, key):
    """Best-effort join with the existing rera_projects table (same DB),
    using the project's own state (state_code from its page)."""
    rera_state = state_code if state_code in config.RERA_STATE_CODES else None
    if not key or not rera_state:
        return None, None
    try:
        row = conn.execute(
            """SELECT id FROM rera_projects
               WHERE state_code = %s
                 AND upper(regexp_replace(rera_number, '[^A-Za-z0-9]', '', 'g')) = %s
               LIMIT 1""", (rera_state, key)).fetchone()
    except Exception as e:
        log.debug("rera match skipped: %s", redact(e))
        return None, None
    return (row[0], "exact_number") if row else (None, "none")


def upsert_project(conn, feed_code, rec):
    """Write the full record from a project page. Returns 'inserted' |
    'updated' | 'unchanged'. Only PAGE_TRACKED fields are compared here (and
    only between two page reads); LIST_TRACKED ones are tracked daily by
    save_listing, so a change is never logged twice."""
    existing = conn.execute(
        f"SELECT content_hash, details_scraped, {', '.join(PAGE_TRACKED)} FROM mb_new_projects WHERE psmid = %s",
        (rec["psmid"],)).fetchone()
    rera_project_id, method = match_rera(conn, rec.get("state_code"), rec.get("rera_number_key"))
    state_code = feed_code
    cols, vals = _adapt(rec)
    cols += ["rera_project_id", "rera_match_method", "details_scraped"]
    vals += [rera_project_id, method, True]

    with conn.transaction():
        if existing is None:
            all_cols = ["psmid", "url", "source_state_code", "content_hash"] + cols
            all_vals = [rec["psmid"], rec["url"], state_code, rec["content_hash"]] + vals
            conn.execute(
                f"INSERT INTO mb_new_projects ({', '.join(all_cols)}) VALUES ({', '.join(['%s'] * len(all_vals))})",
                all_vals)
            return "inserted"

        old_hash, was_detailed, *old_tracked = existing
        if was_detailed and old_hash == rec["content_hash"]:
            conn.execute("UPDATE mb_new_projects SET last_scraped_at = now(), last_seen_at = now(), "
                         "rera_project_id = COALESCE(%s, rera_project_id) WHERE psmid = %s",
                         (rera_project_id, rec["psmid"]))
            return "unchanged"

        changed = False
        if was_detailed:
            for field, old in zip(PAGE_TRACKED, old_tracked):
                new = rec.get(field)
                if _text(old) != _text(new):
                    changed = True
                    conn.execute(
                        "INSERT INTO mb_change_history (psmid, field_name, old_value, new_value) VALUES (%s, %s, %s, %s)",
                        (rec["psmid"], field, _text(old), _text(new)))
        sets = ", ".join(f"{c} = %s" for c in cols)
        conn.execute(
            f"UPDATE mb_new_projects SET {sets}, url = %s, content_hash = %s, last_seen_at = now(), "
            f"last_scraped_at = now(){', last_changed_at = now()' if changed or not was_detailed else ''} "
            f"WHERE psmid = %s",
            vals + [rec["url"], rec["content_hash"], rec["psmid"]])
        # first detail scrape of a project the listing already stored counts as "inserted"
        return "updated" if was_detailed else "inserted"


# ── runs ───────────────────────────────────────────────────────────────────────

def record_run(conn, state_code, started, status, counts, error=None):
    conn.execute(
        """INSERT INTO mb_scrape_runs (source_state_code, started_at, completed_at, status, cities, pages_fetched,
             discovered, new_projects, list_changes, details_fetched, inserted, updated, unchanged, failed,
             blocked, error_message)
           VALUES (%s, %s, now(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (state_code, started, status, counts.get("cities", 0), counts.get("pages", 0),
         counts.get("discovered", 0), counts.get("new_projects", 0), counts.get("list_changes", 0),
         counts.get("details", 0),
         counts.get("inserted", 0), counts.get("updated", 0), counts.get("unchanged", 0),
         counts.get("failed", 0), bool(counts.get("blocked")), redact(error) if error else None))
