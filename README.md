# 99acres "New Launch" scraper

Scrapes the **New Launch** panel of 99acres for Tamil Nadu, Karnataka,
Maharashtra, Goa and Uttarakhand every day, stores every project (with its RERA
number) in CockroachDB, and tracks price / possession / RERA changes over time.
It lives next to `E:\Scraper-for-RERA` and writes to the same database, so a
99acres project can be joined to the official RERA record.

```
99acers new updates/
├── scrape_99acres.py      # 99acres CLI entry point (discover + details + DB)
├── scrape_magicbricks.py  # Magicbricks MagicHomes CLI (see the section at the end)
├── mb/                    # Magicbricks package: config, http, parse, db
├── watchdog.py            # daily report + health check, printed by GitHub Actions
├── run_hidden.vbs         # what Task Scheduler runs (no console window)
├── run_daily.cmd          # called by it: git pull, then scrape
├── register_tasks.ps1     # creates the 5 daily tasks (+ optional full run)
├── schema.sql             # reference copy of the tables (created automatically)
├── acres/
│   ├── config.py          # state ids, search URL, limits
│   ├── http.py            # curl_cffi session + captcha warm-up through Chrome
│   ├── discover.py        # walks the results pages
│   ├── parse.py           # JSON payload -> flat record
│   └── db.py              # tables, queue, upsert, change history, run log
├── tests/                 # offline tests on saved payloads
├── certs/root.crt         # CA for CockroachDB sslmode=verify-full
├── data/                  # logs, JSON dumps (git-ignored)
└── state/                 # cookies + Chrome profile (git-ignored)
```

## How it works

1. **Discovery.** For each state it loads the same URL the New Launch panel
   uses in the browser (`/search/property/buy/<state>?city=<id>&availability=3…`)
   page by page. 99acres server-renders the results into a
   `window.__initialData__` JSON blob; the scraper reads that blob instead of
   parsing HTML. Every project tuple goes into the `acres_scrape_queue` table
   (new ones as `pending`, known ones get `last_seen_at` refreshed).
2. **Details.** It then takes up to 60 due projects from the queue (pending
   first, then the ones whose weekly re-check is due), loads each project page,
   and parses the full record: header, RERA block, configurations, price,
   description, specifications, facilities, nearby places, brochure, builder,
   locality insights and reviews.
3. **Storage.** `acres_new_launch` gets one row per project. A content hash
   decides whether anything changed; changed fields are written to
   `acres_change_history`. If the RERA number matches a row in the existing
   `rera_projects` table, `rera_project_id` is filled in.
4. **Run log.** `acres_scrape_runs` records counts per state per run. The
   watchdog reads it.

### Why a Windows PC and not GitHub Actions

99acres blocks datacenter IPs and plain HTTP clients:

| Client | Project page | Results page |
|---|---|---|
| `requests` | 403 | 403 |
| `curl_cffi` (Chrome TLS fingerprint), fresh | loader shell, then full page on 2nd request | reCAPTCHA |
| `curl_cffi` with cookies from a real Chrome | full page | full page |
| headless Chrome | "Access Denied" | "Access Denied" |
| headed Chrome | full page | passes the invisible reCAPTCHA by itself |

So the scraper uses `curl_cffi` for everything, and only when it hits the
captcha does it open a real Chrome window (persistent profile in `state/`),
wait for the challenge to pass, copy the cookies, and carry on.

GitHub Actions is where you **read the logs**: the "daily report" job runs
`watchdog.py` every evening (20:00 IST) and prints, from the database, the
runs per state, new projects, price/RERA changes, failed pages and queue
health. It also appears in the job summary. Run it on demand from the Actions
tab ("Run workflow", choose the window in hours). It goes red, and GitHub
e-mails you, when a state had no successful run in 36 hours.

## Setup (once)

```
cd "E:\99acers new updates"
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env        # optional: it falls back to ..\Scraper-for-RERA\.env
```

Playwright uses the Chrome already installed on the PC (`channel="chrome"`),
so no browser download is needed.

Warm the cookies once and test a single project:

```
.venv\Scripts\python scrape_99acres.py --warm-cookies
.venv\Scripts\python scrape_99acres.py --url https://www.99acres.com/seraano-by-aarza-siolim-north-goa-npxid-r469160
```

First real run, small:

```
.venv\Scripts\python scrape_99acres.py --state GA --max-details 5
```

Then schedule it:

```
powershell -ExecutionPolicy Bypass -File .\register_tasks.ps1
```

That creates two daily tasks, both at **11:00**: "99acres New Launch - All
states" (TN, KA, MH, GA, UK one after another, `run_hidden.vbs all`) and
"Magicbricks MagicHomes - India" (`run_hidden.vbs mb IN`). They run side by side
(separate locks), with no console window. Keep the PC on and your user logged
in (locked is fine); a missed start runs as soon as the PC is on.

A one-time **full run** of every state (all listing pages, every due project,
no 60-per-state cap) is added with:

```
powershell -ExecutionPolicy Bypass -File .\register_tasks.ps1 -FullRunAt 22:20
```

Logs: `data\scheduler_all.log` (99acres), `data\scheduler_mb_IN.log`
(Magicbricks), `data\run.log` and `data\mb_run.log`.

## Running by hand

```
python scrape_99acres.py --state TN                  # discover + 60 details
python scrape_99acres.py --state all --pause 120     # every state, 2 min apart
python scrape_99acres.py --state all --full          # every page, every due project
python scrape_99acres.py --state KA --discover-only  # only refresh the queue
python scrape_99acres.py --state KA --details-only --max-details 150
python scrape_99acres.py --state TN --no-db          # JSON files under data/TN/
python scrape_99acres.py --state TN --broad          # drop the keyword from the URL -> more results
python scrape_99acres.py --url <project url> [--save]
python -m unittest discover -s tests -v              # offline tests
```

Exit codes: `0` ok · `2` blocked by captcha (run `--warm-cookies`, or just wait
for tomorrow) · `3` database error · `4` finished but some pages failed (they
are retried next run, up to 3 attempts) · `5` another run held the lock too
long.

## What gets stored

See [schema.sql](schema.sql) for every column with an example value. The main
groups in `acres_new_launch`:

- **Identity / location:** project id (`r459201`), URL, name, locality, city,
  state, pincode, street address, lat/long, 99acres locality/city/state ids.
- **Builder:** brand name, legal name, builder page URL and id.
- **RERA:** status, number (clean and raw), registration date when 99acres
  prints "dated …", the state RERA portal URL, per-phase numbers with QR code
  URL and escrow bank details, and `rera_project_id` linking to `rera_projects`.
- **Status / price:** construction status, completion month, possession label,
  price range text and rupee min/max, price per sqft, "+ Charges" flag,
  property types and the per-configuration table (BHK, area, price).
- **Content:** highlights, description, specifications, unit/tower/floor
  counts, project area, open-area %, brochure and payment-plan PDFs, cover
  image, logo, facilities, nearby places with distances, offers, FAQs.
- **Locality:** YoY price change, pros/cons, average rating, star histogram,
  ratings by connectivity/lifestyle/safety/green area, positive-mention %,
  like/dislike tag counts.
- **Bookkeeping:** content hash, first seen, last scraped, last changed.

Useful queries are at the bottom of `schema.sql`.

## Limits and known behaviour

- The results list is ML-ranked and its count moves around (the same TN panel
  showed 188, 260 and 699 results on different URL variants). Discovery keeps
  the browser's exact URL by default; `--broad` drops the `keyword` parameter
  for the wider list. Both are de-duplicated by project id.
- A project with no RERA block on 99acres (common for pre-launch Goa villas)
  is stored with `is_rera = false`; the RERA panel on such pages shows only a
  disclaimer.
- **Captcha.** 99acres sometimes shows a "Security checkup, tick the box"
  reCAPTCHA. The scraper then opens a Chrome window in front of you, beeps
  three times, and waits up to 10 minutes for you to tick the box and click
  Proceed. After that it continues inside that Chrome, and the profile keeps
  the cookie so the next runs usually go straight through. If nobody ticks the
  box in time, the state is marked `blocked` for that run and the next day's
  task tries again. Nothing is lost; the queue waits. Never close the Chrome
  window or the hidden console yourself: that killed three runs in October.
- To clear the captcha ahead of the schedule, run
  `python scrape_99acres.py --warm-cookies`, tick the box, done.
- Requests are spaced 5–10 s apart. A normal state day (≈10 listing pages + 60
  details) takes about 10–15 minutes; a full run of all states takes hours.

## Magicbricks MagicHomes (all India)

A second scraper, `scrape_magicbricks.py` (package `mb/`), covers
[Magicbricks MagicHomes](https://www.magicbricks.com/magichomes), "All 28,122
Projects in India", and writes to the same database (`mb_*` tables).

**How it works.** The MagicHomes page loads its project cards from a JSON
endpoint (`/mbproject/newProjectCards?pageNo=N&magichome=Y`, 20 per page,
~1,407 pages). Every day the scraper:

1. **Walks the whole list** (~1 h). New projects are stored at once in
   `mb_new_projects` with the list fields (name, city, locality, pincode,
   builder, status, launch and possession dates, price range, price per sqft,
   units, RERA flag). For known projects, yesterday's card is compared with
   today's: changes to price, price per sqft, status, launch/possession date and
   units go to `mb_change_history`. So all 28k projects are change-tracked daily.
2. **Reads up to 1,500 project pages** (~1.5 h), never-read ones first, newest
   launches first. The page's `window.SERVER_PRELOADED_STATE_` JSON gives the
   full record: state, RERA number(s) with validity per phase, full address,
   lat/long, unit plans, amenities, specifications, developer track record,
   locality ratings and price trend, FAQs. Each page is re-read every 30 days.
   The first pass over all 28k pages takes about 19 days (or one full run).

No captcha and no Chrome: Magicbricks answers plain `curl_cffi` requests.
Projects in TN, KA and MH are linked to `rera_projects` by RERA number
(`rera_project_id`), using the state from the project page (`state_code`).

Tables: `mb_new_projects` (one row per project, key `psmid`), `mb_change_history`,
`mb_scrape_queue`, `mb_scrape_runs`. Queries 8-13 in
[check_queries.sql](check_queries.sql); query 13 matches projects that appear on
both sites by RERA number.

```
python scrape_magicbricks.py                         # daily: whole India list + 1,500 project pages
python scrape_magicbricks.py --full                  # whole list + every due project page (~26 h)
python scrape_magicbricks.py --discover-only         # only the list (daily change tracking)
python scrape_magicbricks.py --max-pages 5 --max-details 10   # quick test
python scrape_magicbricks.py --state GA              # one state's cities instead (mb/config.py)
python scrape_magicbricks.py --url <project url> [--save]
```

Scheduled by `register_tasks.ps1`: task "Magicbricks MagicHomes - India" daily at
11:00 (`run_daily.cmd mb IN`, log `data\scheduler_mb_IN.log`, about 2.5 h).
A one-time full run: `-MbFullRunAt HH:mm`. The GitHub "daily report" shows
Magicbricks in sections 6-9.

## Where the data lives

All scraped data (99acres New Launch, Magicbricks MagicHomes and the RERA
registries) is stored in the Avacasa **CockroachDB** cluster, not in this
repository. Docker images: https://hub.docker.com/repositories/techavacasa ·
Other repositories: https://github.com/Saivishwa-avacasa
