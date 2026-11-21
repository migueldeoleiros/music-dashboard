from music_dashboard.domain.artwork.matching import (
    AlbumCandidate,
    ArtistCandidate,
    Match,
    TrackCandidate,
    fold,
    match_album,
    match_artist,
    match_track,
)

A = "https://cdn-images.dzcdn.net/images/cover/" + "a" * 32 + "/1000x1000.jpg"
B = "https://cdn-images.dzcdn.net/images/cover/" + "b" * 32 + "/1000x1000.jpg"


def test_fold_is_exact_not_fuzzy():
    assert fold("  Beyoncé \u00a0 KNOWLES ") == "beyoncé knowles"
    assert fold("ＡＢＣ") == "abc"
    assert fold(None) == "" and fold("   ") == ""
    assert fold("Beyonce") != fold("Beyoncé")


def test_artist_exact_name_beats_alias_and_substrings_never_match():
    pool = [ArtistCandidate("1", "Prince And The Revolution", B), ArtistCandidate("2", "PRINCE", A)]
    assert match_artist(pool, "Prince") == Match("artist_name", "2", A)
    assert match_artist([ArtistCandidate("1", "Princess", A)], "Prince") is None


def test_artist_alias_used_only_when_name_finds_nothing():
    pool = [ArtistCandidate("9", "Yeule", A)]
    assert match_artist(pool, "Nat Ćmiel", ["Yeule"]) == Match("artist_alias", "9", A)
    # name present but ambiguous: aliases must not rescue it
    ambiguous = [ArtistCandidate("1", "Nat", A), ArtistCandidate("2", "Nat", B), *pool]
    assert match_artist(ambiguous, "Nat", ["Yeule"]) is None


def test_artist_ambiguity_rejected_but_duplicate_with_same_image_ok():
    two = [ArtistCandidate("7", "Muse", A), ArtistCandidate("3", "muse", B)]
    assert match_artist(two, "Muse") is None
    same = [ArtistCandidate("7", "Muse", A), ArtistCandidate("3", "muse", A)]
    assert match_artist(same, "Muse") == Match("artist_name", "3", A)  # smallest id, stable


def test_placeholder_only_or_next_to_real_is_rejected():
    assert match_artist([ArtistCandidate("1", "Muse", None)], "Muse") is None
    mixed = [ArtistCandidate("1", "Muse", None), ArtistCandidate("2", "Muse", A)]
    assert match_artist(mixed, "Muse") is None


def test_album_title_and_artist_with_conflicting_credit_rejected():
    pool = [AlbumCandidate("1", "Blue", "Joni Mitchell", A), AlbumCandidate("2", "Blue", "Blue", B)]
    assert match_album(pool, "blue", ["Joni Mitchell"]) == Match("album_title_artist", "1", A)
    assert match_album(pool[1:], "Blue", ["Joni Mitchell"]) is None  # clear artist conflict
    assert match_album(pool, "Blue", ["Other Artist"]) is None


def test_album_title_only_last_resort_needs_single_image():
    pool = [AlbumCandidate("1", "Blue", "X", A), AlbumCandidate("2", "Blue", "Y", B)]
    assert match_album(pool, "Blue", []) is None  # ambiguous across artists
    assert match_album(pool[:1], "Blue", [None, " "]) == Match("album_title", "1", A)
    assert match_album(pool, "Red", []) is None
    assert match_album(pool, None, []) is None


def test_track_requires_artist_and_prefers_listened_album():
    pool = [
        TrackCandidate("1", "Song", "Ann", "Single", A),
        TrackCandidate("2", "Song", "Ann", "Album", B),
    ]
    assert match_track(pool, "Song", ["Ann"]) is None  # ambiguous covers
    assert match_track(pool, "Song", ["Ann"], "album") == Match("track_title_artist", "2", B)
    assert match_track(pool, "Song", [], "Album") is None  # never title-only
    assert match_track(pool, "Song", ["Bob"]) is None
    # unknown listened album does not widen to a wrong cover
    assert match_track(pool, "Song", ["Ann"], "Other") is None


def test_track_title_never_matches_album_title():
    pool = [TrackCandidate("1", "Self Titled", "Ann", "Different Album", A)]
    assert match_track(pool, "Different Album", ["Ann"]) is None
    assert match_track(pool, "Self Titled", ["Ann"], "Self Titled") == Match(
        "track_title_artist", "1", A
    )
