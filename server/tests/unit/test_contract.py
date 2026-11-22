import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from music_dashboard.app import app
from music_dashboard.presentation.api.schemas.statistics import Coverage, DimensionCoverage

client = TestClient(app)
PARAMS = {"from": "2024-03-10", "to": "2024-03-10", "tz": "America/New_York"}


def test_contract_is_frozen() -> None:
    contract = Path(__file__).parents[3] / "contracts/openapi.json"
    assert json.loads(contract.read_text()) == app.openapi()


def test_all_chart_families_and_get_only() -> None:
    all_paths = app.openapi()["paths"]
    assert set(all_paths["/api/refresh"]) == {"get", "post"}
    paths = {p: ops for p, ops in all_paths.items() if p != "/api/refresh"}
    assert len(paths) == 12
    assert len(all_paths) == 13
    assert "/api/countries/{code}/artists" in paths
    for path, operations in paths.items():
        assert set(operations) == {"get"}
        response = client.get(path.replace("{code}", "US"), params=PARAMS)
        assert response.status_code == 200, response.text
        assert set(response.json()["coverage"]) == {
            "artists",
            "albums",
            "tracks",
            "genres",
            "countries",
            "listens",
        }
        if path != "/api/health":
            params = {p["name"] for p in operations["get"]["parameters"]}
            expected = {"from", "to", "tz", "limit"} | ({"code"} if "{code}" in path else set())
            assert params == expected
            assert "422" not in operations["get"]["responses"]
            assert "400" in operations["get"]["responses"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"from": "not-a-date"},
        {"to": "2024-03-09"},
        {"tz": "Invalid/Timezone"},
        {"limit": 0},
        {"limit": 101},
        {"limit": "no"},
    ],
)
def test_bad_params_are_400(overrides: dict[str, object]) -> None:
    response = client.get("/api/heatmap", params={**PARAMS, **overrides})
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert "details" in response.json()


def test_image_url_is_nullable_with_null_default() -> None:
    schemas = app.openapi()["components"]["schemas"]
    entity = schemas["RankedEntity"]
    assert entity["properties"]["image_url"]["default"] is None
    assert {"type": "null"} in entity["properties"]["image_url"]["anyOf"]
    assert "image_url" not in entity["required"]
    assert entity["additionalProperties"] is False
    response = client.get("/api/rankings/artists", params={**PARAMS, "limit": 1})
    assert all(item["image_url"] is None for item in response.json()["items"])


def test_country_artists_route_contract() -> None:
    operation = app.openapi()["paths"]["/api/countries/{code}/artists"]["get"]
    schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema == {"$ref": "#/components/schemas/RankingResponse"}
    response = client.get("/api/countries/US/artists", params={**PARAMS, "limit": 1})
    assert response.status_code == 200
    assert response.json()["dimension"] == "artists"
    assert response.json()["items"] == []  # fixture-mode empty for non-example filters


@pytest.mark.parametrize("code", ["XX", "ZZ", "XW", "XE", "us", "USA", "U", "00", "%20", "A1"])
def test_country_artists_rejects_unknown_codes_with_400(code: str) -> None:
    response = client.get(f"/api/countries/{code}/artists", params=PARAMS)
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"


def test_country_artists_validates_filters_too() -> None:
    response = client.get("/api/countries/US/artists", params={**PARAMS, "limit": 101})
    assert response.status_code == 400


def test_supported_alias_and_full_range() -> None:
    response = client.get(
        "/api/heatmap",
        params={
            "from": "0001-01-01",
            "to": "9999-12-31",
            "tz": "US/Eastern",
        },
    )
    assert response.status_code == 200
    assert response.json()["range"]["tz"] == "America/New_York"


def test_coverage_invariant() -> None:
    with pytest.raises(ValidationError):
        Coverage(total_listens=3, included_listens=2, excluded_listens=2)
    with pytest.raises(ValidationError):
        DimensionCoverage.model_validate({})


@pytest.mark.parametrize("name", ["from", "to"])
@pytest.mark.parametrize("value", ["0", "1704067200", "2024-01-01T00:00:00", "2024-1-1"])
def test_dates_are_strict_local_calendar_dates(name: str, value: str) -> None:
    response = client.get("/api/heatmap", params={**PARAMS, name: value})
    assert response.status_code == 400


@pytest.mark.parametrize("dimension", ["albums", "tracks"])
def test_missing_metadata_is_available_in_http_fixtures(dimension: str) -> None:
    response = client.get(
        f"/api/rankings/{dimension}",
        params={
            "from": "2026-01-05",
            "to": "2026-01-05",
            "tz": "UTC",
            "limit": 2,
        },
    )
    assert response.status_code == 200
    assert any(item["metadata_state"] == "missing" for item in response.json()["items"])


def test_populated_contract_fixtures() -> None:
    root = Path(__file__).parents[2]
    examples_path = root / "tests/fixtures/aggregate_responses.json"
    package_path = root / "src/music_dashboard/infrastructure/aggregate_responses.json"
    assert examples_path.read_bytes() == package_path.read_bytes()
    examples = json.loads(examples_path.read_text())
    assert len(examples) == 11
    for family in examples:
        response = client.get(
            "/api/countries/US/artists" if family == "countries/artists" else f"/api/{family}",
            params={
                "from": "2026-01-05",
                "to": "2026-01-05",
                "tz": "UTC",
                "limit": 1,
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()  # FastAPI has validated the full nested response model.
        field = (
            "items"
            if family.startswith("rankings/")
            else "series"
            if family.startswith("evolution/")
            else "slices"
            if family.startswith("pies/")
            else "cells"
            if family == "heatmap"
            else "items"
            if family == "countries/artists"
            else "countries"
        )
        assert data[field], family
        assert data["coverage"]["genres"]["excluded_listens"] == 1
        assert data["coverage"]["countries"]["included_listens"] == 2
        if field in ("series", "slices"):
            assert any(item["is_other"] for item in data[field])
