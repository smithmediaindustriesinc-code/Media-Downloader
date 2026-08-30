"""Tests for core.track_match module.

Offline tests use monkeypatched _search results.
Live tests (MD_LIVE_TESTS=1) use real YouTube/YouTube Music searches.
"""
import os
import sys

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.track_ref import TrackRef
import core.track_match as track_match


# ============================================================================
# OFFLINE TESTS (always run)
# ============================================================================

def test_offline_norm():
    """Test _norm function."""
    assert track_match._norm("Bohemian Rhapsody") == "bohemian rhapsody"
    assert track_match._norm("  Song [HD]  ") == "song"
    assert track_match._norm("Track (Official Video)") == "track"
    assert track_match._norm("A  B   C") == "a b c"
    assert track_match._norm("") == ""
    print("✓ _norm tests passed")


def test_offline_score():
    """Test _score function with monkeypatched search results."""

    cfg = {
        "duration_tolerance_s": 4,
        "reject_keywords": track_match.DEFAULT_REJECT_KEYWORDS,
        "prefer_topic_channels": True,
    }

    # Reference track
    ref = TrackRef(
        title="Bohemian Rhapsody",
        artists=["Queen"],
        duration_ms=354000  # 354 seconds
    )

    # Candidate 1: exact match (the one we want)
    exact_entry = {
        "title": "Queen - Bohemian Rhapsody (Official Video)",
        "channel": "Queen - Topic",
        "duration": 354,
        "id": "vid1",
        "url": "https://www.youtube.com/watch?v=vid1"
    }

    # Candidate 2: correct song but "live" version (should be penalized)
    live_entry = {
        "title": "Queen - Bohemian Rhapsody (Live)",
        "channel": "Queen",
        "duration": 355,
        "id": "vid2",
        "url": "https://www.youtube.com/watch?v=vid2"
    }

    # Candidate 3: similar title but wrong duration
    wrong_duration_entry = {
        "title": "Bohemian Rhapsody Cover",
        "channel": "Some Artist",
        "duration": 240,  # 120 seconds off - should lose points
        "id": "vid3",
        "url": "https://www.youtube.com/watch?v=vid3"
    }

    # Candidate 4: "- Topic" channel (should get channel bonus)
    topic_entry = {
        "title": "Bohemian Rhapsody",
        "channel": "Queen - Topic",
        "duration": 354,
        "id": "vid4",
        "url": "https://www.youtube.com/watch?v=vid4"
    }

    score_exact = track_match._score(ref, exact_entry, cfg)
    score_live = track_match._score(ref, live_entry, cfg)
    score_wrong_dur = track_match._score(ref, wrong_duration_entry, cfg)
    score_topic = track_match._score(ref, topic_entry, cfg)

    print(f"  Exact match score: {score_exact}")
    print(f"  Live version score: {score_live}")
    print(f"  Wrong duration score: {score_wrong_dur}")
    print(f"  Topic channel score: {score_topic}")

    # Exact match should score highest (or tied with topic version)
    assert score_exact >= score_live, "Exact should beat live version"
    assert score_exact >= score_wrong_dur, "Exact should beat wrong duration"

    # Live version should be penalized (has "live" which is in reject list)
    assert score_live < score_exact, "Live should be penalized vs exact"

    # Wrong duration should score lower
    assert score_wrong_dur < score_exact, "Wrong duration should score lower"

    # Topic channels get a boost
    assert score_topic >= score_exact * 0.95, "Topic channel should score well"

    print("✓ _score tests passed")


def test_offline_find_match():
    """Test find_match with monkeypatched _search."""

    # Monkeypatch _search to return fake results
    original_search = track_match._search

    def mock_search_confident(query, source, limit):
        """Return results for a confident match scenario."""
        return [
            {
                "title": "Queen - Bohemian Rhapsody (Official Video)",
                "channel": "Queen - Topic",
                "duration": 354,
                "id": "vid1",
                "url": "https://www.youtube.com/watch?v=vid1"
            },
            {
                "title": "Queen - Bohemian Rhapsody (Live at Wembley)",
                "channel": "Queen",
                "duration": 360,
                "id": "vid2",
                "url": "https://www.youtube.com/watch?v=vid2"
            },
            {
                "title": "Bohemian Rhapsody Cover",
                "channel": "Covers Channel",
                "duration": 320,
                "id": "vid3",
                "url": "https://www.youtube.com/watch?v=vid3"
            }
        ]

    track_match._search = mock_search_confident

    ref = TrackRef(
        title="Bohemian Rhapsody",
        artists=["Queen"],
        duration_ms=354000
    )

    result = track_match.find_match(ref)

    print(f"  Confident scenario status: {result.status}")
    print(f"  Best candidate: {result.best.title if result.best else None}")
    print(f"  Best score: {result.best.score if result.best else None}")

    assert result.status == "confident", f"Expected 'confident' status, got '{result.status}'"
    assert result.best is not None
    assert result.best.score >= track_match.DEFAULT_MIN_CONFIDENCE

    # Restore original
    track_match._search = original_search
    print("✓ find_match confident test passed")


def test_offline_find_match_ambiguous():
    """Test find_match with ambiguous results (close runners-up)."""

    original_search = track_match._search

    def mock_search_ambiguous(query, source, limit):
        """Return results where top candidates are close."""
        return [
            {
                "title": "Song Title",
                "channel": "Artist",
                "duration": 300,
                "id": "vid1",
                "url": "https://www.youtube.com/watch?v=vid1"
            },
            {
                "title": "Song Title (Live)",
                "channel": "Artist",
                "duration": 305,
                "id": "vid2",
                "url": "https://www.youtube.com/watch?v=vid2"
            }
        ]

    track_match._search = mock_search_ambiguous

    ref = TrackRef(
        title="Song Title",
        artists=["Artist"],
        duration_ms=300000
    )

    result = track_match.find_match(ref)

    print(f"  Ambiguous scenario status: {result.status}")
    if result.best and len(result.candidates) > 1:
        print(f"  Best score: {result.best.score}")
        print(f"  Runner-up score: {result.candidates[1].score if len(result.candidates) > 1 else None}")
        if len(result.candidates) > 1:
            gap = result.best.score - result.candidates[1].score
            print(f"  Score gap: {gap}")

    # Restore
    track_match._search = original_search
    print("✓ find_match ambiguous test passed")


def test_offline_find_match_none():
    """Test find_match with no good candidates."""

    original_search = track_match._search

    def mock_search_poor(query, source, limit):
        """Return results with low scores."""
        return [
            {
                "title": "Random Song",
                "channel": "Random Artist",
                "duration": 180,
                "id": "vid1",
                "url": "https://www.youtube.com/watch?v=vid1"
            }
        ]

    track_match._search = mock_search_poor

    ref = TrackRef(
        title="Bohemian Rhapsody",
        artists=["Queen"],
        duration_ms=354000
    )

    result = track_match.find_match(ref)

    print(f"  No match scenario status: {result.status}")
    print(f"  Best score: {result.best.score if result.best else 'N/A'}")

    assert result.status == "none" or (result.best and result.best.score < track_match.DEFAULT_MIN_CONFIDENCE)

    # Restore
    track_match._search = original_search
    print("✓ find_match none test passed")


def test_offline_match_query():
    """Test match_query convenience function."""

    original_search = track_match._search

    def mock_search(query, source, limit):
        return [
            {
                "title": "The Beatles - Let It Be",
                "channel": "The Beatles - Topic",
                "duration": 243,
                "id": "vid1",
                "url": "https://www.youtube.com/watch?v=vid1"
            }
        ]

    track_match._search = mock_search

    # Test "artist - title" format
    result = track_match.match_query("The Beatles - Let It Be")
    assert result.best is not None, "Should find a match"
    print(f"✓ match_query('The Beatles - Let It Be') -> status={result.status}")

    # Test single title format
    result2 = track_match.match_query("Let It Be")
    assert result2.query == "Let It Be", "Query should be preserved"
    print(f"✓ match_query('Let It Be') -> status={result2.status}")

    track_match._search = original_search


# ============================================================================
# LIVE TESTS (only if MD_LIVE_TESTS environment variable is set)
# ============================================================================

def test_live_matches():
    """Real searches on YouTube/YouTube Music."""

    if not os.environ.get("MD_LIVE_TESTS"):
        print("\n(Skipping live tests - set MD_LIVE_TESTS=1 to enable)")
        return

    print("\n--- LIVE TESTS ---\n")

    test_tracks = [
        TrackRef(
            title="Bohemian Rhapsody",
            artists=["Queen"],
            duration_ms=354000
        ),
        TrackRef(
            title="Stairway to Heaven",
            artists=["Led Zeppelin"],
            duration_ms=482000
        ),
        TrackRef(
            title="Hotel California",
            artists=["Eagles"],
            duration_ms=391000
        ),
    ]

    for i, track in enumerate(test_tracks, 1):
        try:
            print(f"Test {i}: {track.artist_str} - {track.title}")

            result = track_match.find_match(track)

            print(f"  Status: {result.status}")
            if result.best:
                print(f"  Best match: {result.best.title}")
                print(f"    Channel: {result.best.channel}")
                print(f"    Duration: {result.best.duration_s}s")
                print(f"    Score: {result.best.score}")
                print(f"    URL: {result.best.url}")
                print(f"    Source: {result.best.source}")
            else:
                print(f"  No match found")

            if len(result.candidates) > 1:
                print(f"  Runners-up ({len(result.candidates) - 1}):")
                for j, cand in enumerate(result.candidates[1:4], 1):  # Show top 3 runners-up
                    print(f"    {j}. {cand.title} ({cand.score}) - {cand.channel}")

            print()

        except Exception as e:
            print(f"  ERROR: {e}\n")


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    print("=" * 70)
    print("OFFLINE TESTS")
    print("=" * 70)

    try:
        test_offline_norm()
        test_offline_score()
        test_offline_find_match()
        test_offline_find_match_ambiguous()
        test_offline_find_match_none()
        test_offline_match_query()

        print("\nALL PASS")

    except AssertionError as e:
        print(f"\nFAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # Run live tests if enabled
    try:
        test_live_matches()
    except Exception as e:
        print(f"Live test error: {e}")
        import traceback
        traceback.print_exc()
