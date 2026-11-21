import argparse

from sqlalchemy import create_engine

from music_dashboard.config import settings


def configure(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = commands.add_parser("seed-fixtures", help="Seed synthetic data; never needs a token")
    parser.set_defaults(handler=run)


def run(args: argparse.Namespace) -> None:
    from music_dashboard.infrastructure.persistence.seed import seed_fixtures

    engine = create_engine(settings.database_url)
    try:
        seed_fixtures(engine)
    finally:
        engine.dispose()
    print("Synthetic fixtures seeded")
