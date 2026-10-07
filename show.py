#!/usr/bin/env python
"""Print what is stored, every column included.

    python show.py                      # all projects, all columns, one block each
    python show.py --state TN           # one state
    python show.py --id r459201         # one project
    python show.py --table              # compact table (key columns only)
    python show.py --changes            # change history
    python show.py --queue              # scrape queue
    python show.py --runs               # run log
    python show.py --json > all.json    # everything as JSON (for Excel/Sheets import, etc.)
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from acres import db  # noqa: E402


def rows_as_dicts(conn, sql, params=()):
    cur = conn.execute(sql, params)
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def fmt(v):
    if v is None:
        return "-"
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, indent=2, default=str)
    return str(v)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--state")
    ap.add_argument("--id")
    ap.add_argument("--table", action="store_true")
    ap.add_argument("--changes", action="store_true")
    ap.add_argument("--queue", action="store_true")
    ap.add_argument("--runs", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    conn = db.connect()

    where, params = [], []
    if a.state:
        where.append("source_state_code = %s"); params.append(a.state.upper())
    if a.id:
        where.append("project_id = %s"); params.append(a.id if a.id.startswith("r") else f"r{a.id}")
    w = (" WHERE " + " AND ".join(where)) if where else ""

    if a.changes:
        rows = rows_as_dicts(conn, f"""SELECT c.changed_at, a.source_state_code, a.name, c.field_name, c.old_value, c.new_value
                                      FROM acres_change_history c JOIN acres_new_launch a USING (project_id)
                                      {w.replace('source_state_code', 'a.source_state_code').replace('project_id =', 'a.project_id =')}
                                      ORDER BY c.changed_at DESC""", params)
    elif a.queue:
        rows = rows_as_dicts(conn, f"SELECT * FROM acres_scrape_queue{w} ORDER BY source_state_code, status, discovered_at DESC", params)
    elif a.runs:
        rows = rows_as_dicts(conn, "SELECT * FROM acres_scrape_runs ORDER BY started_at DESC")
    else:
        rows = rows_as_dicts(conn, f"SELECT * FROM acres_new_launch{w} ORDER BY source_state_code, first_seen_at DESC", params)

    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1, default=str))
        return 0

    if a.table and not (a.changes or a.queue or a.runs):
        keys = ["source_state_code", "project_id", "name", "city", "builder_name", "price_text",
                "completion_date", "rera_number", "rera_match_method", "first_seen_at"]
        for r in rows:
            print(" | ".join(fmt(r.get(k))[:36] for k in keys))
        print(f"\n{len(rows)} rows")
        return 0

    for r in rows:
        print("=" * 100)
        for k, v in r.items():
            text = fmt(v)
            if "\n" in text:
                print(f"{k}:")
                for line in text.splitlines():
                    print("    " + line)
            else:
                print(f"{k:24} {text}")
    print(f"\n{len(rows)} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
