"""Worker tick for the opt-in public refresh request; a no-op when nothing is pending.

Registered before `sync` (alphabetical), so a user-requested incremental runs ahead of the
scheduled one within the same serial tick. Never starts an export.
"""

from music_dashboard.application.jobs.sync import run_public_refresh


def tick() -> None:
    run_public_refresh()
