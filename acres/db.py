"""CockroachDB storage: project table, change history, scrape queue, run log.

Connection handling mirrors E:\\Scraper-for-RERA\\common\\db.py so the same
DATABASE_URL / certs/root.crt setup works. Nothing here ever drops data.
"""
import json
import logging
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import config
from .parse import TRACKED_FIELDS

log = logging.getLogger("acres.db")
ROOT = config.ROOT
RERA_REPO = ROOT.parent / "Scraper-for-RERA"   # sibling checkout, for .env / cert fallback

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS acres_new_launch (
        project_id            STRING PRIMARY KEY,          -- r459201 (from URL)
        url                   STRING NOT NULL,
        source_state_code     STRING NOT NULL,             -- TN / KA / MH / GA / UK (which panel found it)
        -- header
        name                  STRING,
        locality              STRING,
        city                  STRING,
        state                 STRING,
        pincode               STRING,
        street_address        STRING,
        latitude              FLOAT8,
        longitude             FLOAT8,
        locality_id           STRING,
        city_id               STRING,
        state_id              STRING,
        builder_name          STRING,                      -- brand shown on page (Casagrand)
        builder_legal_name    STRING,                      -- Casagrand Builder Private Limited
        builder_url           STRING,
        builder_id            STRING,
        is_rera               BOOL,
        no_brokerage          BOOL,
        has_3d_floor_plans    BOOL,
        top_facilities_cnt    INT,
        -- RERA (as published by the advertiser on 99acres)
        rera_status           STRING,                      -- REGISTERED / APPLIED / ...
        rera_number           STRING,                      -- TNRERA/29/BLG/0183/2026
        rera_number_raw       STRING,                      -- as shown, e.g. '... dated 02-04-2025'
        rera_number_key       STRING,                      -- normalized, for joining with rera_projects
        rera_registered_on    DATE,
        rera_url              STRING,                      -- state RERA portal
        rera_phases           JSONB,                       -- [{title, number, status, qr_code_url, bank...}]
        rera_project_id       UUID,                        -- match into rera_projects.id (nullable)
        rera_match_method     STRING,                      -- exact_number / none
        -- status
        construction_status   STRING,                      -- New Launch
        construction_status_code STRING,                   -- NEW_LAUNCH
        completion_date       STRING,                      -- Mar, 2030
        completion_on         DATE,
        possession_label      STRING,
        is_new_launch         BOOL,
        -- price
        price_text            STRING,                      -- 93.03 L - 1.32 Cr
        price_min             INT8,
        price_max             INT8,
        has_extra_charges     BOOL,
        price_per_sqft        INT8,
        property_types        STRING,                      -- 2, 3 BHK Apartment
        property_type         STRING,
        configurations        JSONB,                       -- [{bhk, type, area_min, area_max, price_min, price_max}, ...]
        -- text sections
        highlights            STRING[],
        description           STRING,
        specifications        STRING,
        unit_count            INT,
        tower_count           INT,
        floor_count           INT,
        total_area_text       STRING,
        open_area_pct         INT,
        brochure_url          STRING,
        payment_plan_url      STRING,
        cover_image_url       STRING,
        logo_url              STRING,
        images_count          INT,
        facilities            STRING[],
        nearby_places         JSONB,                       -- [{category, name, distance}, ...]
        offers                JSONB,
        faqs                  JSONB,
        -- locality
        yoy_price_change      DECIMAL(6,2),
        locality_pros         STRING[],
        locality_cons         STRING[],
        rating_avg            DECIMAL(3,1),
        rating_total          INT,
        rating_5_star         INT,
        rating_4_star         INT,
        rating_3_star         INT,
        rating_2_star         INT,
        rating_1_star         INT,
        rating_connectivity   DECIMAL(3,1),
        rating_lifestyle      DECIMAL(3,1),
        rating_safety         DECIMAL(3,1),
        rating_green_area     DECIMAL(3,1),
        positive_mentions_pct INT,
        likes                 JSONB,
        dislikes              JSONB,
        listing_tags          STRING[],
        meta_title            STRING,
        -- bookkeeping
        content_hash          STRING NOT NULL,
        first_seen_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
        last_scraped_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        last_changed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        scraped_at            TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    "CREATE INDEX IF NOT EXISTS acres_new_launch_state_idx ON acres_new_launch (source_state_code, last_scraped_at)",
    "CREATE INDEX IF NOT EXISTS acres_new_launch_rera_key_idx ON acres_new_launch (rera_number_key)",
    """CREATE TABLE IF NOT EXISTS acres_change_history (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id  STRING NOT NULL REFERENCES acres_new_launch (project_id) ON DELETE CASCADE,
        changed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        field_name  STRING NOT NULL,
        old_value   STRING,
        new_value   STRING
    )""",
    "CREATE INDEX IF NOT EXISTS acres_change_history_project_idx ON acres_change_history (project_id, changed_at)",
    """CREATE TABLE IF NOT EXISTS acres_scrape_queue (
        project_id        STRING PRIMARY KEY,
        source_state_code STRING NOT NULL,
        url               STRING NOT NULL,
        status            STRING NOT NULL DEFAULT 'pending',   -- pending / done / failed / gone
        attempts          INT NOT NULL DEFAULT 0,
        last_error        STRING,
        listing           JSONB,                               -- tuple from the results page
        discovered_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
        last_seen_at      TIMESTAMPTZ NOT NULL DEFAULT now(),  -- last time the panel listed it
        next_scrape_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    "CREATE INDEX IF NOT EXISTS acres_scrape_queue_due_idx ON acres_scrape_queue (source_state_code, status, next_scrape_at)",
    """CREATE TABLE IF NOT EXISTS acres_scrape_runs (
        id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        source_state_code STRING NOT NULL,
        started_at        TIMESTAMPTZ NOT NULL,
        completed_at      TIMESTAMPTZ,
        status            STRING NOT NULL,                     -- ok / partial / blocked / failed
        pages_fetched     INT NOT NULL DEFAULT 0,
        discovered        INT NOT NULL DEFAULT 0,
        new_in_queue      INT NOT NULL DEFAULT 0,
        details_fetched   INT NOT NULL DEFAULT 0,
        inserted          INT NOT NULL DEFAULT 0,
        updated           INT NOT NULL DEFAULT 0,
        unchanged         INT NOT NULL DEFAULT 0,
        failed            INT NOT NULL DEFAULT 0,
        blocked           BOOL NOT NULL DEFAULT false,
        error_message     STRING,
        summary           JSONB
    )""",
]

ARRAY_COLUMNS = {"highlights", "facilities", "locality_pros", "locality_cons", "listing_tags"}
JSON_COLUMNS = {"rera_phases", "configurations", "nearby_places", "offers", "faqs", "likes", "dislikes"}
SKIP_COLUMNS = {"content_hash", "first_seen_at", "last_scraped_at", "last_changed_at", "scraped_at",
                "project_id", "url", "source_state_code", "rera_project_id", "rera_match_method"}


# ── configuration ────────────────────────────────────────────────────────────

def _read_dotenv(path):
    values = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        values[k.strip()] = v.strip().strip('"').strip("'")
    return values


def database_url():
    """DATABASE_URL from the environment, else ./.env, else the RERA repo's .env."""
    url = (os.environ.get("DATABASE_URL")
           or _read_dotenv(ROOT / ".env").get("DATABASE_URL")
           or _read_dotenv(RERA_REPO / ".env").get("DATABASE_URL"))
    return (url or "").strip().strip('"').strip("'") or None


def redact(text):
    text = re.sub(r"(?i)\b(postgres(?:ql)?(?:\+\w+)?://)[^@\s\"']*@", r"\1***:***@", str(text))
    return re.sub(r"(?i)(password\s*=\s*)\S+", r"\1***", text)


def _ssl_root_cert():
    if os.environ.get("PGSSLROOTCERT"):
        return None
    defaults = [Path.home() / ".postgresql" / "root.crt"]
    if os.environ.get("APPDATA"):
        defaults.append(Path(os.environ["APPDATA"]) / "postgresql" / "root.crt")
    if any(p.exists() for p in defaults):
        return None
    for candidate in (ROOT / "certs" / "root.crt", RERA_REPO / "certs" / "root.crt"):
        if candidate.exists():
            return str(candidate)
    return None


def connect(url=None):
    import psycopg
    url = url or database_url()
    if not url:
        raise RuntimeError("DATABASE_URL is not set (environment, .env, or ../Scraper-for-RERA/.env)")
    kwargs = {"connect_timeout": 20, "application_name": "acres-new-launch"}
    cert = _ssl_root_cert()
    if cert and "sslrootcert=" not in url:
        kwargs["sslrootcert"] = cert
    return psycopg.connect(url, autocommit=True, **kwargs)


def ensure_schema(conn):
    for stmt in SCHEMA:
        conn.execute(stmt)


# ── queue ────────────────────────────────────────────────────────────────────

def enqueue(conn, state_code, tuples):
    """Insert newly discovered projects; refresh last_seen/listing for known
    ones. Returns the number of brand-new rows."""
    from psycopg.types.json import Jsonb
    if not tuples:
        return 0
    ids = [t["project_id"] for t in tuples]
    known = {r[0] for r in conn.execute(
        "SELECT project_id FROM acres_scrape_queue WHERE project_id = ANY(%s)", (ids,)).fetchall()}
    with conn.transaction():
        for t in tuples:
            # CockroachDB has no xmax trick, so "new" is decided by the SELECT above.
            conn.execute(
                """INSERT INTO acres_scrape_queue (project_id, source_state_code, url, listing)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (project_id) DO UPDATE
                     SET last_seen_at = now(), listing = EXCLUDED.listing, updated_at = now(),
                         url = EXCLUDED.url""",
                (t["project_id"], state_code, t["url"], Jsonb(t)),
            )
    return len(set(ids) - known)


def claim_due(conn, state_code, limit):
    """Projects to fetch now: pending first (newest discovery first), then
    rows whose weekly re-check is due."""
    rows = conn.execute(
        """SELECT project_id, url, listing, status, attempts
           FROM acres_scrape_queue
           WHERE source_state_code = %s
             AND status IN ('pending', 'done', 'failed')
             AND next_scrape_at <= now()
             AND attempts < %s
           ORDER BY CASE status WHEN 'pending' THEN 0 WHEN 'failed' THEN 1 ELSE 2 END,
                    discovered_at DESC
           LIMIT %s""",
        (state_code, config.MAX_ATTEMPTS * 3, limit),
    ).fetchall()
    return [{"project_id": r[0], "url": r[1], "listing": r[2], "status": r[3], "attempts": r[4]} for r in rows]


def mark_done(conn, project_id):
    conn.execute(
        """UPDATE acres_scrape_queue
           SET status = 'done', attempts = 0, last_error = NULL, updated_at = now(),
               next_scrape_at = now() + %s * INTERVAL '1 day'
           WHERE project_id = %s""",
        (config.RESCRAPE_AFTER_DAYS, project_id),
    )


def mark_failed(conn, project_id, error, gone=False):
    if gone:
        conn.execute(
            "UPDATE acres_scrape_queue SET status = 'gone', last_error = %s, updated_at = now(), "
            "next_scrape_at = now() + INTERVAL '30 days' WHERE project_id = %s",
            (str(error)[:500], project_id))
        return
    conn.execute(
        """UPDATE acres_scrape_queue
           SET attempts = attempts + 1, last_error = %s, updated_at = now(),
               status = CASE WHEN attempts + 1 >= %s THEN 'failed' ELSE status END,
               next_scrape_at = now() + INTERVAL '1 day'
           WHERE project_id = %s""",
        (redact(error)[:500], config.MAX_ATTEMPTS, project_id),
    )


# ── projects ─────────────────────────────────────────────────────────────────

def _text(v):
    if v is None:
        return None
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, sort_keys=True, default=str)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return str(v)


def match_rera(conn, state_code, rera_key):
    """Best-effort join with the existing rera_projects table (same DB)."""
    rera_state = config.STATES.get(state_code, {}).get("rera_state_code")
    if not rera_key or not rera_state:
        return None, None
    try:
        row = conn.execute(
            """SELECT id FROM rera_projects
               WHERE state_code = %s
                 AND upper(regexp_replace(rera_number, '[^A-Za-z0-9]', '', 'g')) = %s
               LIMIT 1""",
            (rera_state, rera_key),
        ).fetchone()
    except Exception as e:  # table missing, permissions, etc. - never fatal
        log.debug("rera match skipped: %s", redact(e))
        return None, None
    return (row[0], "exact_number") if row else (None, "none")


def upsert_project(conn, state_code, rec):
    """Insert or update one project. Returns 'inserted' | 'updated' | 'unchanged'."""
    from psycopg.types.json import Jsonb

    existing = conn.execute(
        f"SELECT content_hash, {', '.join(TRACKED_FIELDS)} FROM acres_new_launch WHERE project_id = %s",
        (rec["project_id"],),
    ).fetchone()

    rera_project_id, method = match_rera(conn, state_code, rec.get("rera_number_key"))
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
    cols += ["rera_project_id", "rera_match_method"]
    vals += [rera_project_id, method]

    with conn.transaction():
        if existing is None:
            all_cols = ["project_id", "url", "source_state_code", "content_hash"] + cols
            all_vals = [rec["project_id"], rec["url"], state_code, rec["content_hash"]] + vals
            conn.execute(
                f"INSERT INTO acres_new_launch ({', '.join(all_cols)}) VALUES ({', '.join(['%s'] * len(all_vals))})",
                all_vals,
            )
            return "inserted"

        old_hash, *old_tracked = existing
        if old_hash == rec["content_hash"]:
            conn.execute("UPDATE acres_new_launch SET last_scraped_at = now(), scraped_at = now(), "
                         "rera_project_id = COALESCE(%s, rera_project_id) WHERE project_id = %s",
                         (rera_project_id, rec["project_id"]))
            return "unchanged"

        # record field-level changes
        for field, old in zip(TRACKED_FIELDS, old_tracked):
            new = rec.get(field)
            if _text(old) != _text(new):
                conn.execute(
                    "INSERT INTO acres_change_history (project_id, field_name, old_value, new_value) VALUES (%s, %s, %s, %s)",
                    (rec["project_id"], field, _text(old), _text(new)),
                )
        sets = ", ".join(f"{c} = %s" for c in cols)
        conn.execute(
            f"UPDATE acres_new_launch SET {sets}, url = %s, content_hash = %s, "
            f"last_scraped_at = now(), scraped_at = now(), last_changed_at = now() WHERE project_id = %s",
            vals + [rec["url"], rec["content_hash"], rec["project_id"]],
        )
        return "updated"


# ── runs ─────────────────────────────────────────────────────────────────────

def record_run(conn, state_code, started, status, counts, error=None, summary=None):
    from psycopg.types.json import Jsonb
    conn.execute(
        """INSERT INTO acres_scrape_runs (source_state_code, started_at, completed_at, status, pages_fetched,
             discovered, new_in_queue, details_fetched, inserted, updated, unchanged, failed, blocked,
             error_message, summary)
           VALUES (%s, %s, now(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (state_code, started, status,
         counts.get("pages", 0), counts.get("discovered", 0), counts.get("new_in_queue", 0),
         counts.get("details", 0), counts.get("inserted", 0), counts.get("updated", 0),
         counts.get("unchanged", 0), counts.get("failed", 0), bool(counts.get("blocked")),
         redact(error) if error else None, Jsonb(summary) if summary else None),
    )
