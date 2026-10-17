"""One scraper run at a time per source: they share a Chrome profile (99acres)
and log files. Waits for a running one to finish (e.g. a missed task catching
up while another state is still going)."""
import logging
import os
import time

from . import config

log = logging.getLogger("acres")


def acquire_run_lock(wait_minutes, name="run.lock"):
    """Returns the open lock file (keep a reference), or None on timeout.
    The lock is released when the process exits."""
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    f = open(config.STATE_DIR / name, "a+")
    deadline = time.time() + wait_minutes * 60
    told = False
    while True:
        try:
            if os.name == "nt":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return f
        except OSError:
            if time.time() > deadline:
                f.close()
                return None
            if not told:
                log.info("another scraper run is in progress - waiting up to %d min", wait_minutes)
                told = True
            time.sleep(30)
