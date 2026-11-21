"""Single-process extensible job host. T-003/T-004 add modules, not registry edits."""

import importlib
import logging
import pkgutil
import signal
from threading import Event

from music_dashboard.application import jobs


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    stopped = Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    logging.info("Worker started; discovery-only foundation, no sync/enrichment jobs yet")
    handlers = [
        importlib.import_module(f"{jobs.__name__}.{item.name}").tick
        for item in sorted(pkgutil.iter_modules(jobs.__path__), key=lambda item: item.name)
        if not item.name.startswith("_")
    ]
    while not stopped.is_set():
        for handler in handlers:
            try:
                handler()
            except Exception:
                # Deliberately do not log payloads, credentials or exception strings.
                logging.error("Worker job failed; retry on next tick")
        stopped.wait(30)


if __name__ == "__main__":
    main()
