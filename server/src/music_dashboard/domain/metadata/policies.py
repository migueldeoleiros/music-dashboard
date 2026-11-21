"""Latest-metadata policies; no upstream or persistence dependencies."""

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

# ISO 3166-1 alpha-2; intentionally excludes MB pseudo-countries XW and XE.
ISO_COUNTRIES = frozenset(
    "AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO "
    "BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ "
    "DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP "
    "GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG "
    "KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML "
    "MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE "
    "PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL "
    "SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM "
    "US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW".split()
)


@dataclass(frozen=True)
class GenreVote:
    mbid: str
    name: str
    count: int


def genre_weights(votes: Iterable[GenreVote]) -> dict[str, float]:
    """Collapse duplicate identities, prefer positive votes, otherwise equal shares."""
    totals: dict[str, int] = defaultdict(int)
    for vote in votes:
        totals[vote.mbid] += vote.count
    positive = {key: count for key, count in totals.items() if count > 0}
    if positive:
        total = sum(positive.values())
        return {key: count / total for key, count in positive.items()}
    return {key: 1 / len(totals) for key in totals}


def resolve_genres(
    recording: Iterable[GenreVote],
    release_group: Iterable[GenreVote],
    credited_artists: Iterable[str],
    artist_votes: Mapping[str, Iterable[GenreVote]],
) -> dict[str, float]:
    for level in (recording, release_group):
        weights = genre_weights(level)
        if weights:
            return weights
    resolved = [
        weights
        for artist in set(credited_artists)
        if (weights := genre_weights(artist_votes.get(artist, ())))
    ]
    result: dict[str, float] = defaultdict(float)
    for weights in resolved:
        for genre, weight in weights.items():
            result[genre] += weight / len(resolved)
    return dict(result)


def valid_country(country: object) -> str | None:
    return country if isinstance(country, str) and country in ISO_COUNTRIES else None


def resolve_countries(
    credited_artists: Iterable[str], artist_countries: Mapping[str, str | None]
) -> dict[str, float]:
    countries = [
        country
        for artist in set(credited_artists)
        if (country := valid_country(artist_countries.get(artist)))
    ]
    result: dict[str, float] = defaultdict(float)
    for country in countries:
        result[country] += 1 / len(countries)
    return dict(result)
