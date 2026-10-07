"""Walk the "New Launch" results pages for one state and return the project
tuples found. Stops at the last page, at an empty page, when a page repeats
only known ids, or at the page cap."""
import logging

from . import config
from .http import Site, Blocked
from .parse import parse_listing

log = logging.getLogger("acres.discover")


def discover(site: Site, state_code, max_pages=config.MAX_LISTING_PAGES, keyword=True):
    seen = {}
    pages = 0
    blocked = False
    for page in range(1, max_pages + 1):
        url = config.search_url(state_code, page, keyword=keyword)
        try:
            data = site.fetch_initial_data(url, warmup_url=config.search_url(state_code, 1, keyword=keyword))
        except Blocked as e:
            log.error("blocked while listing %s page %d: %s", state_code, page, e)
            blocked = True
            break
        pages += 1
        tuples, meta = parse_listing(data)
        fresh = [t for t in tuples if t["project_id"] not in seen]
        for t in tuples:
            seen.setdefault(t["project_id"], t)
        log.info("%s page %d: %d projects (%d new), total so far %d, count=%s, last=%s",
                 state_code, page, len(tuples), len(fresh), len(seen), meta.get("count"), meta.get("is_last_page"))
        if not tuples or meta.get("is_last_page") or (page > 1 and not fresh):
            break
    return list(seen.values()), {"pages": pages, "blocked": blocked}
