"""Discovered explicit enrichment command; no token is required or read."""

import argparse


def configure(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = commands.add_parser("enrich-metadata", help="Process a durable metadata queue batch")
    parser.add_argument("--limit", type=int, default=100, choices=range(1, 1001), metavar="1..1000")
    parser.add_argument("--allow-network", action="store_true", help="Permit public metadata HTTP")
    parser.set_defaults(handler=run)


def run(args: argparse.Namespace) -> None:
    if not args.allow_network:
        raise SystemExit("Pass --allow-network and set MUSIC_FIXTURE_MODE=false for metadata HTTP")
    from music_dashboard.application.jobs.enrichment import run_once

    try:
        completed = run_once(args.limit)
    except Exception:
        # Adapter/DB exceptions can contain private URLs or credentials. Never echo them.
        raise SystemExit(
            "Enrichment failed safely. Set MUSIC_FIXTURE_MODE=false and a descriptive "
            "MUSIC_METADATA_USER_AGENT with contact information; check database and "
            "upstream availability. No exception details are printed."
        ) from None
    print(f"Metadata entities completed: {completed}")
