"""Discovered sync commands; no worker settings or credentials read on import."""

import argparse
import json
from typing import Any

from music_dashboard.application.jobs.sync import SyncConfigurationError, run_sync


def _handle(args: argparse.Namespace) -> None:
    try:
        result = run_sync(args.sync_kind, allow_large_delete=args.allow_large_delete)
    except Exception as exc:
        # Config and domain errors are controlled actionable text. Unknown adapter/DB
        # exceptions may contain credentials; never echo their string or traceback.
        if isinstance(exc, SyncConfigurationError):
            message = str(exc)
        else:
            message = (
                "Sync failed safely; check worker configuration and upstream availability. "
                "If a timestamp boundary is ambiguous, run music-dashboard reconcile. "
                "For a verified deletion above 10%, reconcile --allow-large-delete is required."
            )
        raise SystemExit(message) from None
    print(json.dumps(result, sort_keys=True))


def configure(subparsers: Any) -> None:
    for name, kind, help_text in (
        ("bootstrap-import", "bootstrap", "Import initial history from a complete export"),
        ("sync", "incremental", "Sync recent listens using the configured overlap"),
        ("reconcile", "reconcile", "Reconcile history against a complete export snapshot"),
    ):
        parser = subparsers.add_parser(name, help=help_text)
        if kind in {"bootstrap", "reconcile"}:
            parser.add_argument(
                "--allow-large-delete",
                action="store_true",
                help="Explicitly allow tombstoning more than 10%% of in-scope history",
            )
        else:
            parser.set_defaults(allow_large_delete=False)
        parser.set_defaults(handler=_handle, sync_kind=kind)
