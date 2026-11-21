"""Discovered explicit artwork command; no ListenBrainz token is required or read."""

import argparse


def configure(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = commands.add_parser(
        "resolve-artwork", help="Resolve a bounded batch of album/track/artist image URLs"
    )
    parser.add_argument("--limit", type=int, default=20, choices=range(1, 1001), metavar="1..1000")
    parser.add_argument("--allow-network", action="store_true", help="Permit public artwork HTTP")
    parser.add_argument(
        "--reset-misses",
        action="store_true",
        help="First make every miss/transient row due now (resolved rows keep their TTL)",
    )
    parser.set_defaults(handler=run)


def run(args: argparse.Namespace) -> None:
    if not args.allow_network:
        raise SystemExit("Pass --allow-network and set MUSIC_FIXTURE_MODE=false for artwork HTTP")
    from music_dashboard.application.jobs.artwork import run_once

    try:
        stats = run_once(args.limit, reset_retries=args.reset_misses)
    except Exception:
        # Adapter/DB exceptions can contain private URLs or credentials. Never echo them.
        raise SystemExit(
            "Artwork resolution failed safely. Set MUSIC_FIXTURE_MODE=false and a descriptive "
            "MUSIC_METADATA_USER_AGENT with contact information; check database and upstream "
            "availability. No exception details are printed."
        ) from None
    print(
        "Artwork resolved: {resolved}, misses: {miss}, transient: {transient}, "
        "quota stops: {quota}".format(**stats)
    )
