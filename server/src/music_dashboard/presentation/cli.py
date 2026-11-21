"""Commands self-register by exporting configure(subparsers) from cli_commands/*.py."""

import argparse
import importlib
import pkgutil
from collections.abc import Callable
from typing import cast

from music_dashboard.presentation import cli_commands


def main() -> None:
    parser = argparse.ArgumentParser(prog="music-dashboard")
    commands = parser.add_subparsers(dest="command", required=True)
    for module in sorted(pkgutil.iter_modules(cli_commands.__path__), key=lambda item: item.name):
        importlib.import_module(f"{cli_commands.__name__}.{module.name}").configure(commands)
    args = parser.parse_args()
    cast(Callable[[argparse.Namespace], None], args.handler)(args)


if __name__ == "__main__":
    main()
