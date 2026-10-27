"""Background extraction worker.

Run standalone with ``python -m app.worker`` (separate process), or let the API start it as a daemon
thread (WORKER_MODE=inline), which keeps a single free-tier web service sufficient. Jobs live in Postgres,
so a restart loses nothing: queued rows wait, and rows left in 'processing' are reclaimed after the lease.
"""

from __future__ import annotations

import logging
import threading

from app.config import get_settings
from app.services.pipeline import run_once

log = logging.getLogger(__name__)


class Worker:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def loop(self) -> None:
        poll = get_settings().worker_poll_seconds
        log.info("worker started")
        while not self._stop.is_set():
            try:
                if not run_once():
                    self._stop.wait(poll)
            except Exception:
                log.exception("worker iteration failed")
                self._stop.wait(poll * 2)
        log.info("worker stopped")

    def start_background(self) -> None:
        self._thread = threading.Thread(target=self.loop, name="extraction-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)


def main() -> None:
    from app.logging_config import configure_logging

    configure_logging()
    worker = Worker()
    try:
        worker.loop()
    except KeyboardInterrupt:
        worker.stop()


if __name__ == "__main__":
    main()
