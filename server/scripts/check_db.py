"""Run destructive integration tests against an isolated, disposable PostgreSQL container.

Requires Docker and the server dev environment. Does not use .env or any music-service token.
The only published DB port is ephemeral loopback for this test container, never Compose DB.
"""

import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
ROOT = SERVER.parent
NAME = "music-dashboard-tests-" + secrets.token_hex(4)
PASSWORD = secrets.token_urlsafe(24)


def docker(*args: str) -> str:
    return subprocess.check_output(["docker", *args], text=True).strip()


def main() -> None:
    try:
        docker(
            "run",
            "--detach",
            "--rm",
            "--name",
            NAME,
            "--publish",
            "127.0.0.1::5432",
            "--env",
            "POSTGRES_USER=postgres",
            "--env",
            "POSTGRES_DB=music_dashboard_test",
            "--env",
            "POSTGRES_PASSWORD",
            "--env",
            "MUSIC_WRITER_PASSWORD",
            "--env",
            "MUSIC_API_PASSWORD",
            "--mount",
            f"type=bind,src={ROOT / 'deploy/init-db.sh'},"
            "dst=/docker-entrypoint-initdb.d/10-roles.sh,readonly",
            "--health-cmd",
            "pg_isready -h 127.0.0.1 -U postgres -d music_dashboard_test",
            "--health-interval",
            "1s",
            "--health-timeout",
            "2s",
            "--health-retries",
            "30",
            "postgres:17.7-alpine",
        )
        # Readiness wait is for PostgreSQL, not agent/task completion.
        for _ in range(150):
            info = json.loads(docker("inspect", NAME))[0]
            if info["State"]["Health"]["Status"] == "healthy":
                break
            if not info["State"]["Running"]:
                raise RuntimeError("Disposable PostgreSQL exited during initialization")
            time.sleep(0.2)
        else:
            raise RuntimeError("Disposable PostgreSQL did not become healthy within 30 seconds")
        port = info["NetworkSettings"]["Ports"]["5432/tcp"][0]["HostPort"]
        env = dict(os.environ)
        for key, role in (
            ("TEST_DATABASE_URL", "postgres"),
            ("TEST_API_DATABASE_URL", "music_api"),
            ("TEST_WRITER_DATABASE_URL", "music_writer"),
        ):
            env[key] = (
                f"postgresql+psycopg://{role}:{PASSWORD}@127.0.0.1:{port}/music_dashboard_test"
            )
        env.pop("LISTENBRAINZ_TOKEN", None)
        subprocess.run(
            [sys.executable, "-m", "pytest", "tests/integration", "-q"],
            cwd=SERVER,
            env=env,
            check=True,
        )
    finally:
        subprocess.run(["docker", "rm", "--force", NAME], capture_output=True, check=False)


if __name__ == "__main__":
    # Inherit values through env, not docker/psql argv. Never read the local .env.
    for key in ("POSTGRES_PASSWORD", "MUSIC_WRITER_PASSWORD", "MUSIC_API_PASSWORD"):
        os.environ[key] = PASSWORD
    main()
