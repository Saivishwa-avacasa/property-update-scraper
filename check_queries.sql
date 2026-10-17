-- Queries to check a run in CockroachDB (SQL shell / DBeaver / cockroach sql).
-- Times are shown in IST.

-- 1. Runs in the last 12 hours, one row per state per run
SELECT source_state_code AS state,
       to_char(started_at   AT TIME ZONE 'Asia/Kolkata', 'DD Mon HH24:MI') AS started_ist,
       to_char(completed_at AT TIME ZONE 'Asia/Kolkata', 'DD Mon HH24:MI') AS ended_ist,
       status, pages_fetched, discovered, new_in_queue, details_fetched,
       inserted, updated, unchanged, failed, error_message
FROM acres_scrape_runs
WHERE started_at > now() - INTERVAL '12 hours'
ORDER BY started_at;

-- 2. Live progress of the queue (re-run while the full run is going)
SELECT source_state_code AS state, status, count(*) AS projects
FROM acres_scrape_queue
GROUP BY 1, 2
ORDER BY 1, 2;

-- 3. Stored projects per state
SELECT source_state_code AS state,
       count(*)                                                          AS total,
       count(*) FILTER (WHERE first_seen_at > now() - INTERVAL '12 hours') AS new_last_12h,
       count(*) FILTER (WHERE is_rera)                                    AS with_rera_no,
       count(rera_project_id)                                             AS linked_to_rera_projects,
       to_char(max(last_scraped_at) AT TIME ZONE 'Asia/Kolkata', 'DD Mon HH24:MI') AS last_scraped_ist
FROM acres_new_launch
GROUP BY 1
ORDER BY 1;

-- 4. Latest projects saved, newest first
SELECT source_state_code AS state, project_id, name, city, builder_name,
       price_text, completion_date, rera_number,
       to_char(last_scraped_at AT TIME ZONE 'Asia/Kolkata', 'DD Mon HH24:MI') AS scraped_ist
FROM acres_new_launch
ORDER BY last_scraped_at DESC
LIMIT 50;

-- 5. Price / possession / RERA changes in the last 7 days
SELECT to_char(c.changed_at AT TIME ZONE 'Asia/Kolkata', 'DD Mon HH24:MI') AS when_ist,
       a.source_state_code AS state, a.name, c.field_name, c.old_value, c.new_value
FROM acres_change_history c
JOIN acres_new_launch a ON a.project_id = c.project_id
WHERE c.changed_at > now() - INTERVAL '7 days'
ORDER BY c.changed_at DESC;

-- 6. Pages that failed or disappeared
SELECT source_state_code AS state, project_id, status, attempts, last_error, url
FROM acres_scrape_queue
WHERE status IN ('failed', 'gone') OR last_error IS NOT NULL
ORDER BY updated_at DESC;

-- 7. 99acres projects matched to the official RERA record
SELECT a.source_state_code AS state, a.name, a.rera_number,
       r.project_status AS rera_portal_status, a.first_seen_at
FROM acres_new_launch a
JOIN rera_projects r ON r.id = a.rera_project_id
ORDER BY a.first_seen_at DESC;

-- =============================================================================
-- Magicbricks MagicHomes (mb_* tables)
-- =============================================================================

-- 8. Magicbricks runs in the last 24 hours
SELECT source_state_code AS state,
       to_char(started_at   AT TIME ZONE 'Asia/Kolkata', 'DD Mon HH24:MI') AS started_ist,
       to_char(completed_at AT TIME ZONE 'Asia/Kolkata', 'DD Mon HH24:MI') AS ended_ist,
       status, cities, pages_fetched, discovered, new_projects, details_fetched,
       inserted, updated, unchanged, failed, error_message
FROM mb_scrape_runs
WHERE started_at > now() - INTERVAL '24 hours'
ORDER BY started_at;

-- 9. Magicbricks projects per state and city (state is known once the project page is read)
SELECT coalesce(state_code, '?') AS state, city,
       count(*)                                    AS projects,
       count(*) FILTER (WHERE details_scraped)     AS with_full_details,
       count(*) FILTER (WHERE is_rera)             AS rera_flag,
       count(rera_number)                          AS rera_number_known,
       count(rera_project_id)                      AS linked_to_rera_projects,
       count(*) FILTER (WHERE launch_date > current_date - INTERVAL '180 days') AS launched_last_6_months
FROM mb_new_projects
GROUP BY 1, 2
ORDER BY 1, 3 DESC;

-- 10. Latest Magicbricks launches with RERA number
SELECT state_code AS state, city, locality, name, developer_name, status,
       launch_date, possession_date, price_text, price_per_sqft_min, rera_number, url
FROM mb_new_projects
ORDER BY launch_date DESC NULLS LAST
LIMIT 50;

-- 11. Magicbricks price / status / RERA changes in the last 7 days
SELECT to_char(c.changed_at AT TIME ZONE 'Asia/Kolkata', 'DD Mon HH24:MI') AS when_ist,
       p.state_code AS state, p.city, p.name, c.field_name, c.old_value, c.new_value
FROM mb_change_history c
JOIN mb_new_projects p ON p.psmid = c.psmid
WHERE c.changed_at > now() - INTERVAL '7 days'
ORDER BY c.changed_at DESC;

-- 12. Magicbricks queue progress (pending = project page not read yet)
SELECT status, count(*) AS projects
FROM mb_scrape_queue
GROUP BY 1
ORDER BY 1;

-- 13. Same RERA number on both 99acres and Magicbricks
SELECT a.source_state_code AS state, a.rera_number_key, a.name AS on_99acres, m.name AS on_magicbricks,
       a.price_text AS price_99acres, m.price_text AS price_magicbricks
FROM acres_new_launch a
JOIN mb_new_projects m ON m.rera_number_key = a.rera_number_key
ORDER BY a.source_state_code, a.name;

-- 14. Magicbricks projects that dropped off MagicHomes (not in the list for 3+ days).
--     They are never deleted, their last_seen_at simply stops moving.
SELECT state_code AS state, city, name, developer_name, status,
       to_char(last_seen_at AT TIME ZONE 'Asia/Kolkata', 'DD Mon YYYY') AS last_seen_ist, url
FROM mb_new_projects
WHERE last_seen_at < now() - INTERVAL '3 days'
ORDER BY last_seen_at DESC;

-- 15. Magicbricks sync health: one row per project, how fresh the data is
SELECT count(*)                                                     AS projects,
       count(DISTINCT psmid)                                        AS distinct_ids,
       count(*) FILTER (WHERE last_seen_at > now() - INTERVAL '1 day') AS in_list_last_24h,
       count(*) FILTER (WHERE details_scraped)                      AS project_page_read,
       count(*) FILTER (WHERE last_scraped_at > now() - INTERVAL '30 days' AND details_scraped) AS page_read_last_30d
FROM mb_new_projects;
