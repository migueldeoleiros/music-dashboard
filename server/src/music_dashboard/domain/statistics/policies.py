"""Display policies, independent of persistence and presentation.

Names never alias to MBIDs: a name may refer to several artists. Only explicit
credited MBIDs resolve identity. Credited artists are deduplicated and share a
listen equally; an unsplit submitted credit is the fallback when credits are absent.
"""

import unicodedata
from datetime import date


def normalized_name(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold().strip()


FAMILIES = frozenset(
    [f"rankings/{d}" for d in ("artists", "albums", "tracks", "genres")]
    + [f"{f}/{d}" for f in ("pies", "evolution") for d in ("artists", "genres")]
    + ["heatmap", "countries"]
)
# Path-param family: route /api/countries/{code}/artists. The code travels in
# StatisticsFilter.country_code, so FAMILIES (code-free) stays unchanged.
COUNTRY_ARTISTS_FAMILY = "countries/artists"
SUPPORTED_FAMILIES = FAMILIES | {COUNTRY_ARTISTS_FAMILY}
DIMENSIONS = ("artists", "albums", "tracks", "genres", "countries", "listens")
CACHE_TTL_SECONDS = 30.0
CACHE_MAX_ENTRIES = 128
CACHE_MAX_BYTES = 16 * 1024 * 1024
# Month is the coarsest contract bucket. Even the entire supported calendar fits.
MAX_BUCKETS = 120_000
MAX_EVOLUTION_VALUES = 2_000_000
# ISO 3166-1 alpha-2, including territories. Never pseudo-countries XW/XE/ZZ.
COUNTRY_CODES = """
AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO
BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ
DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP
GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG
KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML
MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE
PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL
SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM
US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW
""".split()


class InvalidStatisticsRequest(ValueError):
    pass


class StatisticsUnavailable(RuntimeError):
    pass


def validate_country_code(code: str) -> str:
    """Strict: uppercase ISO alpha-2 from COUNTRY_CODES; never pseudo-countries XW/XE/ZZ."""
    if code not in COUNTRY_CODES:
        raise InvalidStatisticsRequest("Unsupported country code")
    return code


def validate_filters(from_date: date, to_date: date, limit: int) -> None:
    if from_date > to_date:
        raise InvalidStatisticsRequest("from must be on or before to")
    if not 1 <= limit <= 100:
        raise InvalidStatisticsRequest("limit must be between 1 and 100")
