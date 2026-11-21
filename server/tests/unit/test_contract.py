import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from music_dashboard.app import app
from music_dashboard.presentation.api.schemas.statistics import Coverage

client = TestClient(app)
PARAMS = {"from": "2024-03-10", "to": "2024-03-10", "tz": "America/New_York"}


def test_contract_is_frozen() -> None:
    contract = Path(__file__).parents[3] / "contracts/openapi.json"
    assert json.loads(contract.read_text()) == app.openapi()


def test_all_chart_families_and_get_only() -> None:
    paths = app.openapi()["paths"]
    assert len(paths) == 11
    for path, operations in paths.items():
        assert set(operations) == {"get"}
        response = client.get(path, params=PARAMS)
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
            assert params == {"from", "to", "tz", "limit"}
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
