#!/usr/bin/env python
"""Daily report + health check, meant for GitHub Actions (which has
DATABASE_URL but cannot reach 99acres). Prints, from the database:

  1. the last runs per state (what the PC did today)
  2. projects first seen in the last 24 h
  3. field changes (price, possession, RERA ...) in the last 24 h
  4. pages that failed, with the error text
  5. queue status per state

Exits 1 (so Actions marks the run red and e-mails you) when a state had no
successful run in the last 36 hours or its pending queue passed 500.

    python watchdog.py            # last 24 h
    python watchdog.py --hours 72
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from acres import config, db  # noqa: E402


def table(rows, headers):
    if not rows:
        print("  (none)")
        return
    rows = [[("" if v is None else str(v)) for v in r] for r in rows]
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    fmt = "  " + "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*("-" * w for w in widths)))
    for r in rows:
        print(fmt.format(*r))


def section(title):
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24)
    args = ap.parse_args(argv)
    h = args.hours

    conn = db.connect()
    problems = []

    section("1. Runs per state (latest 3 each)")
    rows = []
    for code in config.STATES:
        rows += conn.execute(
            """SELECT source_state_code, to_char(started_at AT TIME ZONE 'Asia/Kolkata', 'DD Mon HH24:MI') AS ist,
                      status, pages_fetched, discovered, new_in_queue, details_fetched,
                      inserted, updated, unchanged, failed, left(coalesce(error_message, ''), 60)
               FROM acres_scrape_runs WHERE source_state_code = %s
               ORDER BY started_at DESC LIMIT 3""", (code,)).fetchall()
    table(rows, ["state", "started (IST)", "status", "pages", "found", "new", "details", "ins", "upd", "same", "fail", "error"])

    section(f"2. New projects first seen in the last {h} h")
    rows = conn.execute(
        """SELECT source_state_code, project_id, left(name, 32), left(city, 16), price_text, completion_date,
                  coalesce(rera_number, '-'), CASE WHEN rera_project_id IS NULL THEN '' ELSE 'linked' END
           FROM acres_new_launch WHERE first_seen_at > now() - %s * INTERVAL '1 hour'
           ORDER BY source_state_code, first_seen_at DESC""", (h,)).fetchall()
    table(rows, ["state", "id", "name", "city", "price", "completion", "RERA no.", "RERA db"])

    section(f"3. Changes in the last {h} h")
    rows = conn.execute(
        """SELECT to_char(c.changed_at AT TIME ZONE 'Asia/Kolkata', 'DD Mon HH24:MI'), a.source_state_code,
                  left(a.name, 30), c.field_name, left(coalesce(c.old_value, ''), 28), left(coalesce(c.new_value, ''), 28)
           FROM acres_change_history c JOIN acres_new_launch a ON a.project_id = c.project_id
           WHERE c.changed_at > now() - %s * INTERVAL '1 hour'
           ORDER BY c.changed_at DESC LIMIT 200""", (h,)).fetchall()
    table(rows, ["when (IST)", "state", "project", "field", "old", "new"])

    section("4. Failed / gone pages")
    rows = conn.execute(
        """SELECT source_state_code, project_id, status, attempts, left(coalesce(last_error, ''), 70),
                  to_char(updated_at AT TIME ZONE 'Asia/Kolkata', 'DD Mon HH24:MI')
           FROM acres_scrape_queue WHERE status IN ('failed', 'gone') OR last_error IS NOT NULL
           ORDER BY updated_at DESC LIMIT 50""").fetchall()
    table(rows, ["state", "id", "status", "tries", "error", "updated (IST)"])

    section("5. Queue and health per state")
    rows = []
    for code in config.STATES:
        q = dict(conn.execute(
            "SELECT status, count(*) FROM acres_scrape_queue WHERE source_state_code = %s GROUP BY status",
            (code,)).fetchall())
        total = conn.execute("SELECT count(*) FROM acres_new_launch WHERE source_state_code = %s", (code,)).fetchone()[0]
        recent_ok = conn.execute(
            """SELECT count(*) FROM acres_scrape_runs
               WHERE source_state_code = %s AND started_at > now() - INTERVAL '36 hours'
                 AND status IN ('ok', 'partial')""", (code,)).fetchone()[0]
        pending = q.get("pending", 0)
        health = "OK"
        if not recent_ok:
            health = "NO RUN"
            problems.append(f"{code}: no successful run in the last 36 h (PC off? captcha?)")
        if pending > 500:
            health = "BACKLOG"
            problems.append(f"{code}: {pending} projects pending - the PC is not keeping up")
        rows.append([code, config.STATES[code]["name"], total, pending, q.get("done", 0), q.get("failed", 0), q.get("gone", 0), health])
    table(rows, ["state", "name", "stored", "pending", "done", "failed", "gone", "health"])

    if problems:
        print("\nPROBLEMS:")
        for p in problems:
            print("  -", p)
        return 1
    print("\nall good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
