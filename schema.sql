-- Reference copy of the schema. The scraper creates these itself on first run
-- (CREATE TABLE IF NOT EXISTS, see acres/db.py); this file is for reading.
-- Dialect: CockroachDB (PostgreSQL compatible).

CREATE TABLE IF NOT EXISTS acres_new_launch (
  -- identity
  project_id            STRING PRIMARY KEY,        -- r459201 (from the URL ...-npxid-r459201)
  url                   STRING NOT NULL,
  source_state_code     STRING NOT NULL,           -- TN / KA / MH / GA / UK (which New Launch panel listed it)

  -- header
  name                  STRING,                    -- Casagrand Aquagrove
  locality              STRING,                    -- Madhavaram
  city                  STRING,                    -- Chennai North
  state                 STRING,                    -- Tamil Nadu
  pincode               STRING,                    -- 600060
  street_address        STRING,
  latitude              FLOAT8,
  longitude             FLOAT8,
  locality_id           STRING,                    -- 99acres ids, handy for joining their other pages
  city_id               STRING,
  state_id              STRING,
  builder_name          STRING,                    -- brand (Casagrand)
  builder_legal_name    STRING,                    -- Casagrand Builder Private Limited
  builder_url           STRING,                    -- builders.99acres.com/...-bid-149020
  builder_id            STRING,                    -- 149020
  is_rera               BOOL,
  no_brokerage          BOOL,
  has_3d_floor_plans    BOOL,
  top_facilities_cnt    INT,                       -- "+60 Top Facilities" -> 60

  -- RERA, as published by the advertiser on 99acres
  rera_status           STRING,                    -- REGISTERED
  rera_number           STRING,                    -- TNRERA/29/BLG/0183/2026
  rera_number_raw       STRING,                    -- "TN/35/Building/0097/2025dated 02-04-2025"
  rera_number_key       STRING,                    -- TNRERA29BLG01832026 (join key)
  rera_registered_on    DATE,                      -- parsed from "dated dd-mm-yyyy" when present
  rera_url              STRING,                    -- https://rera.tn.gov.in/
  rera_phases           JSONB,                     -- [{title:"Phase 1", number, status, qr_code_url, bank_name, bank_account, ifsc}]
  rera_project_id       UUID,                      -- rera_projects.id when the number matches (same DB)
  rera_match_method     STRING,                    -- exact_number / none

  -- status
  construction_status   STRING,                    -- New Launch
  construction_status_code STRING,                 -- NEW_LAUNCH
  completion_date       STRING,                    -- Jun, 2030
  completion_on         DATE,                      -- 2030-06-01 (from the epoch 99acres sends)
  possession_label      STRING,                    -- Possession will start from Jun, 2030
  is_new_launch         BOOL,

  -- price
  price_text            STRING,                    -- 1.04 - 2.15 Cr
  price_min             INT8,                      -- 10400000 (rupees)
  price_max             INT8,                      -- 21479271
  has_extra_charges     BOOL,                      -- "+ Charges" shown
  price_per_sqft        INT8,                      -- 7700 (from the results tuple)
  property_types        STRING,                    -- 2, 3, 4 BHK Apartment
  property_type         STRING,                    -- Apartment / Villa / Plot
  configurations        JSONB,                     -- [{bhk:"2 BHK", type:"Apartment", area_min, area_max, area_display, area_type, price_min, price_max, price_label}]

  -- text sections
  highlights            STRING[],                  -- "Why you should consider ..."
  description           STRING,                    -- "More about ..."
  specifications        STRING,                    -- interiors / other specifications
  unit_count            INT,                       -- 544
  tower_count           INT,                       -- 3
  floor_count           INT,                       -- 28
  total_area_text       STRING,                    -- 6.7 acres
  open_area_pct         INT,                       -- 85
  brochure_url          STRING,                    -- PDF
  payment_plan_url      STRING,                    -- PDF
  cover_image_url       STRING,
  logo_url              STRING,
  images_count          INT,
  facilities            STRING[],                  -- Swimming Pool, Club House, ...
  nearby_places         JSONB,                     -- [{category:"School", name, distance:"1.1 Km"}]
  offers                JSONB,
  faqs                  JSONB,                     -- [{q, a}]

  -- Explore locality + locality reviews
  yoy_price_change      DECIMAL(6,2),              -- 16.7
  locality_pros         STRING[],                  -- What's great here
  locality_cons         STRING[],                  -- What needs attention
  rating_avg            DECIMAL(3,1),              -- 4.3
  rating_total          INT,                       -- 84
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
  likes                 JSONB,                     -- {"Good Public Transport": 63, ...}
  dislikes              JSONB,                     -- {"Frequent Traffic Jams": 43, ...}
  listing_tags          STRING[],                  -- NO_BROKERAGE, OFFERS, TOP_FACILITIES ...
  meta_title            STRING,

  -- bookkeeping
  content_hash          STRING NOT NULL,           -- sha256 of the comparable fields
  first_seen_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_scraped_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_changed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  scraped_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS acres_new_launch_state_idx    ON acres_new_launch (source_state_code, last_scraped_at);
CREATE INDEX IF NOT EXISTS acres_new_launch_rera_key_idx ON acres_new_launch (rera_number_key);

-- one row per changed field (price, completion date, RERA number, ...)
CREATE TABLE IF NOT EXISTS acres_change_history (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id  STRING NOT NULL REFERENCES acres_new_launch (project_id) ON DELETE CASCADE,
  changed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  field_name  STRING NOT NULL,
  old_value   STRING,
  new_value   STRING
);
CREATE INDEX IF NOT EXISTS acres_change_history_project_idx ON acres_change_history (project_id, changed_at);

-- the work queue: "60 today, the rest tomorrow" survives crashes because of this
CREATE TABLE IF NOT EXISTS acres_scrape_queue (
  project_id        STRING PRIMARY KEY,
  source_state_code STRING NOT NULL,
  url               STRING NOT NULL,
  status            STRING NOT NULL DEFAULT 'pending',   -- pending / done / failed / gone
  attempts          INT NOT NULL DEFAULT 0,
  last_error        STRING,
  listing           JSONB,                               -- the tuple from the results page
  discovered_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at      TIMESTAMPTZ NOT NULL DEFAULT now(),  -- last time the panel still listed it
  next_scrape_at    TIMESTAMPTZ NOT NULL DEFAULT now(),  -- done rows: +7 days (weekly re-check)
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS acres_scrape_queue_due_idx ON acres_scrape_queue (source_state_code, status, next_scrape_at);

-- one row per state per run, what the watchdog reads
CREATE TABLE IF NOT EXISTS acres_scrape_runs (
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
);

-- Handy queries ---------------------------------------------------------------
-- New launches found in the last 7 days, with their RERA number and RERA-portal match:
--   SELECT a.source_state_code, a.name, a.builder_name, a.price_text, a.completion_date,
--          a.rera_number, r.project_status AS rera_portal_status, a.first_seen_at
--   FROM acres_new_launch a LEFT JOIN rera_projects r ON r.id = a.rera_project_id
--   WHERE a.first_seen_at > now() - INTERVAL '7 days' ORDER BY a.first_seen_at DESC;
-- Price changes:
--   SELECT * FROM acres_change_history WHERE field_name IN ('price_text','price_min','price_max') ORDER BY changed_at DESC;
