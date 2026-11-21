import argparse
import json
from pathlib import Path

from music_dashboard.app import app


def configure(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = commands.add_parser("contract", help="Regenerate or verify the frozen OpenAPI")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("../contracts/openapi.json"))
    parser.set_defaults(handler=run)


def run(args: argparse.Namespace) -> None:
    content = json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"
    path: Path = args.output
    if args.check:
        if not path.exists() or path.read_text() != content:
            raise SystemExit("OpenAPI contract is stale; run music-dashboard contract")
        print("OpenAPI contract is current")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        print(f"Wrote {path}")
