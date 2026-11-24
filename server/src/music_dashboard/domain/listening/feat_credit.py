"""Split an unmapped, unsplit artist credit like "A feat. B" into primary + guests.

Only used when the listen carries no mapped credits (e.g. local files tagged "A feat. B").
Guests are separated on commas only: "&" / "and" can be part of a real name, so they stay whole.
"""

import re

_FEAT = r"(?:feat(?:uring)?\.?|ft\.?)"
_BRACKETED = re.compile(rf"\s*[(\[]\s*{_FEAT}\s+(?P<g>[^)\]]+?)\s*[)\]]", re.IGNORECASE)
_INLINE = re.compile(rf"\s+{_FEAT}\s+", re.IGNORECASE)


def split_feat(credit: str) -> list[str]:
    """[primary, *guests]; [credit] when there is no clear feat. marker or a part is empty."""
    guests: list[str] = []
    primary = credit
    match = _BRACKETED.search(credit)
    if match:
        primary = (credit[: match.start()] + credit[match.end() :]).strip()
        guests = [match["g"]]
    else:
        parts = _INLINE.split(credit, maxsplit=1)
        if len(parts) == 2:
            primary, guests = parts[0].strip(), [parts[1]]
    names = [g.strip() for g in ",".join(guests).split(",")]
    if not primary or not names or not all(names):
        return [credit]
    return [primary, *names]
