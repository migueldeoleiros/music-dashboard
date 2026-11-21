"""Offline rebuild of normalized listens and metadata projections. Never uses the network."""

from __future__ import annotations

import argparse


def configure(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = commands.add_parser(
        "rebuild",
        help="Rebuild normalized listens and genre/country projections from stored raw and cache",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Compute and report changes, then roll back"
    )
    parser.set_defaults(handler=run)


def run(args: argparse.Namespace) -> None:
    from music_dashboard.application.rebuild_projections import run_once
    from music_dashboard.infrastructure.persistence.repositories.rebuild import (
        RebuildBusy,
        RebuildUnsupported,
    )

    try:
        stats = run_once(dry_run=args.dry_run)
    except (RebuildBusy, RebuildUnsupported) as busy:
        raise SystemExit(f"Rebuild not started: {busy}") from None
    except Exception:
        # DB exceptions may contain connection details; never echo them.
        raise SystemExit(
            "Rebuild failed and was rolled back; no data was changed. Check database "
            "availability and credentials (the role needs write access). No details printed."
        ) from None
    for key in sorted(stats):
        print(f"{key}: {stats[key]}")
