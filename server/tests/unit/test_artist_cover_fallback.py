"""Pure artist-cover fallback policy: precedence, eligibility, ordering, URL validation."""

from music_dashboard.domain.artwork.fallback import (
    AlbumRef,
    AlbumStats,
    order_eligible,
    pick_cover,
    resolve_image,
)
from music_dashboard.infrastructure.artwork.urls import validate_image_url

H = "d" * 32
COVER_A = f"https://cdn-images.dzcdn.net/images/cover/{H}/a.jpg"
COVER_B = f"https://cdn-images.dzcdn.net/images/cover/{H}/b.jpg"
PHOTO = f"https://cdn-images.dzcdn.net/images/artist/{H}/p.jpg"
A, B, C = (AlbumRef(kid=f"00000000-0000-4000-8000-00000000000{n}") for n in (1, 2, 3))


def test_photo_wins_then_cover_then_none():
    stats = [AlbumStats(A, 4, 4)]
    covers = {A: COVER_A}
    assert resolve_image(PHOTO, stats, covers, validate_image_url) == PHOTO
    assert resolve_image(None, stats, covers, validate_image_url) == COVER_A
    assert resolve_image("http://evil.example/x.jpg", stats, covers, validate_image_url) == COVER_A
    assert resolve_image(None, stats, {}, validate_image_url) is None  # initials
    assert resolve_image(None, [], covers, validate_image_url) is None


def test_most_played_wins_with_stable_tie_break():
    covers = {A: COVER_A, B: COVER_B}
    assert pick_cover([AlbumStats(A, 5, 2), AlbumStats(B, 5, 3)], covers, validate_image_url) == (
        COVER_B
    )
    tie = [AlbumStats(B, 3, 3), AlbumStats(A, 3, 3)]
    assert [s.ref for s in order_eligible(tie)] == [A, B]
    assert [s.ref for s in order_eligible(reversed(tie))] == [A, B]  # input order irrelevant
    name = AlbumRef(artist="x", album="y")
    assert [s.ref for s in order_eligible([AlbumStats(name, 3, 3), AlbumStats(A, 3, 3)])] == [
        A,
        name,
    ]


def test_missing_or_invalid_cover_falls_through_to_next_album():
    stats = [AlbumStats(A, 9, 9), AlbumStats(B, 4, 4), AlbumStats(C, 2, 2)]
    assert pick_cover(stats, {B: COVER_B}, validate_image_url) == COVER_B
    assert pick_cover(stats, {A: "https://evil.example/c.jpg", C: COVER_A}, validate_image_url) == (
        COVER_A
    )
    assert pick_cover(stats, {}, validate_image_url) is None


def test_compilations_and_minor_credits_are_ineligible():
    covers = {A: COVER_A, B: COVER_B}
    assert not AlbumStats(A, 10, 5).eligible  # exactly half: not a clear main credit
    assert not AlbumStats(A, 10, 3).eligible  # featured / compilation track
    assert not AlbumStats(A, 2, 2, various=True).eligible
    assert not AlbumStats(A, 2, 0).eligible
    assert AlbumStats(A, 10, 6).eligible
    stats = [AlbumStats(A, 10, 4), AlbumStats(B, 2, 2, various=True)]
    assert pick_cover(stats, covers, validate_image_url) is None
