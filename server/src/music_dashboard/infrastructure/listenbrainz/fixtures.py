"""Offline ListenBrainz port for CLI demos; never makes upstream requests."""

from __future__ import annotations

from typing import Any

from music_dashboard.domain.listening.entities import ExportSnapshot
from music_dashboard.infrastructure.persistence import seed_data as d


class FixtureListenBrainzClient:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        for index, row in enumerate(d.LISTENS):
            self.rows.append(
                {
                    "listened_at": int(d.ts(row["at"]).timestamp()),
                    "recording_msid": str(d.uid(f"msid:{index}:{row['key']}")),
                    "track_metadata": {
                        "track_name": row["track"],
                        "artist_name": " & ".join(name for _, name in row["artists"]),
                        "release_name": row["release"],
                    },
                }
            )

    def page(
        self,
        user: str,
        *,
        max_ts: int | None = None,
        min_ts: int | None = None,
        count: int = 1000,
    ) -> list[dict[str, Any]]:
        return sorted(
            [
                row
                for row in self.rows
                if (max_ts is None or row["listened_at"] < max_ts)
                and (min_ts is None or row["listened_at"] > min_ts)
            ],
            key=lambda row: row["listened_at"],
            reverse=True,
        )[:count]

    def export(self, user: str) -> ExportSnapshot:
        return ExportSnapshot(self.rows, user, len(self.rows), complete=True)
