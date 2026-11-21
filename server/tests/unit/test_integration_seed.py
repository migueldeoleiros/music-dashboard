import importlib.util
import json
import sys
import uuid
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "seed_integration", Path(__file__).resolve().parents[2] / "scripts" / "seed_integration.py"
)
seed = importlib.util.module_from_spec(SPEC)
sys.modules["seed_integration"] = seed
SPEC.loader.exec_module(seed)


def test_dataset_is_deterministic_and_sized():
    a, b = seed.build_dataset(), seed.build_dataset()
    assert seed.dataset_digest(a, seed.build_plan(a)) == seed.dataset_digest(b, seed.build_plan(b))
    assert len(a.payloads) == 8000 and len(a.artists) == 40 and len(seed.GENRES) == 20
    ids = {(p["listened_at"], p["recording_msid"]) for p in a.payloads}
    assert len(ids) == 8000
    assert any(p["track_metadata"].get("mbid_mapping") is None for p in a.payloads)
    json.dumps(a.payloads)  # plain JSON


def test_partitions_isolate_enrichment():
    ds = seed.build_dataset()
    info = {e["name"]: e for e in seed.describe(ds, seed.build_plan(ds))["partitions"]}
    assert all(v["enriched"] == 0 for v in info["zero"]["entities"].values())
    assert (
        0
        < info["partial"]["entities"]["recording"]["enriched"]
        < info["partial"]["entities"]["recording"]["entities"]
    )
    assert all(v["enriched"] == v["entities"] for v in info["full"]["entities"].values())
    assert sum(e["listens"] for e in info.values()) == 8000


def test_cache_entries_valid():
    ds = seed.build_dataset()
    for lk in seed.build_plan(ds).lookups:
        uuid.UUID(lk.mbid)
        assert lk.status in {"positive", "empty"}  # replayable by default
        assert lk.next_retry_at > lk.fetched_at
        for genre in lk.payload.get("genres", []):
            assert genre["name"] in seed.GENRES


def test_refuses_without_confirmation(monkeypatch, capsys):
    monkeypatch.setenv("MUSIC_DATABASE_URL", "postgresql+psycopg://x@127.0.0.1:1/x")
    assert seed.main([]) == 2
    monkeypatch.delenv("MUSIC_DATABASE_URL")
    assert seed.main(["--confirm-synthetic"]) == 2
