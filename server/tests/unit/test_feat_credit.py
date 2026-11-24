import pytest

from music_dashboard.domain.listening.feat_credit import split_feat
from music_dashboard.infrastructure.persistence.repositories.listening_sync import _parse


@pytest.mark.parametrize(
    ("credit", "expected"),
    [
        ("A feat. B", ["A", "B"]),
        ("A Feat B, C", ["A", "B", "C"]),
        ("A ft. B", ["A", "B"]),
        ("A featuring B", ["A", "B"]),
        ("A (feat. B)", ["A", "B"]),
        ("A [ft. B, C]", ["A", "B", "C"]),
        ("A feat. B & C", ["A", "B & C"]),
        ("Simon & Garfunkel", ["Simon & Garfunkel"]),
        ("Left Fielder", ["Left Fielder"]),
        ("Soft Machine", ["Soft Machine"]),
        ("feat. B", ["feat. B"]),
        ("A feat. ", ["A feat. "]),
    ],
)
def test_split_feat(credit, expected):
    assert split_feat(credit) == expected


def _payload(artist, mapping=None):
    tm = {"artist_name": artist, "track_name": "T"}
    if mapping:
        tm["mbid_mapping"] = mapping
    return {
        "listened_at": 1700000000,
        "recording_msid": "11111111-1111-4111-8111-111111111111",
        "track_metadata": tm,
    }


def test_parse_splits_only_unmapped_credits():
    p = _parse(_payload("A feat. B"), "u")
    assert [a["n"] for a in p.artists] == ["A", "B"]
    assert p.fields["artist_name"] == "A feat. B"
    mbid = "22222222-2222-4222-8222-222222222222"
    one = _parse(_payload("A feat. B", {"artist_mbids": [mbid]}), "u")
    assert [(a["n"], a["m"]) for a in one.artists] == [("A feat. B", mbid)]
    mapped = _parse(_payload("A feat. B", {"artists": [{"artist_credit_name": "A feat. B"}]}), "u")
    assert [a["n"] for a in mapped.artists] == ["A feat. B"]
