"""Tests for core/music_import.py - offline only using monkeypatching."""
import json
import os
import sys
import tempfile
import threading
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.music_import import (
    MatchedTrack, ImportSession, resolve_source, match_tracks, build_session,
    load_imports, get_import, save_import, record_from_session, diff_for_resync,
    _store_path
)
from core.track_match import MatchCandidate, MatchResult
from core.track_ref import TrackRef


def fake_match_result():
    """A fake MatchResult for testing."""
    best = MatchCandidate(
        url="https://www.youtube.com/watch?v=fake123",
        title="Test Song",
        channel="Test Channel",
        duration_s=180,
        source="youtube",
        score=0.85
    )
    return MatchResult(
        status="confident",
        best=best,
        candidates=[best],
        query="Test Query"
    )


def stub_spotify_client():
    """A stub SpotifyClient for testing."""
    return MagicMock()


# ============================================================================
# Test resolve_source
# ============================================================================

def test_resolve_source_pasted_text():
    """Parse pasted 'A - B\nC - D' as text import."""
    text = "Artist One - Title One\nArtist Two - Title Two"
    kind, name, sid, snap_id, tracks = resolve_source(text)

    assert kind == "text", f"Expected kind='text', got {kind}"
    assert name == "Pasted list", f"Expected name='Pasted list', got {name}"
    assert sid == "", f"Expected empty sid, got {sid}"
    assert snap_id == "", f"Expected empty snap_id, got {snap_id}"
    assert len(tracks) == 2, f"Expected 2 tracks, got {len(tracks)}"
    assert tracks[0].title == "Title One"
    assert tracks[0].artist_str == "Artist One"
    assert tracks[1].title == "Title Two"
    assert tracks[1].artist_str == "Artist Two"
    print("✓ resolve_source_pasted_text passed")


def test_resolve_source_empty_text():
    """Empty text raises ValueError."""
    try:
        resolve_source("")
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "No tracks found" in str(e)
        print("✓ resolve_source_empty_text passed")


def test_resolve_source_spotify_uri_no_client():
    """Spotify link without client raises ValueError."""
    text = "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M"
    try:
        resolve_source(text)
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "Connect Spotify first" in str(e)
        print("✓ resolve_source_spotify_uri_no_client passed")


def test_resolve_source_spotify_url_with_client():
    """Spotify URL with client returns resolved data."""
    text = "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"

    client = stub_spotify_client()
    resolved = MagicMock()
    resolved.kind = "playlist"
    resolved.name = "Test Playlist"
    resolved.spotify_id = "37i9dQZF1DXcBWIGoYBM5M"
    resolved.snapshot_id = "snap123"
    resolved.tracks = [
        TrackRef(title="Song 1", artists=["Artist 1"]),
        TrackRef(title="Song 2", artists=["Artist 2"])
    ]
    client.resolve.return_value = resolved

    kind, name, sid, snap_id, tracks = resolve_source(text, client)

    assert kind == "playlist"
    assert name == "Test Playlist"
    assert sid == "37i9dQZF1DXcBWIGoYBM5M"
    assert snap_id == "snap123"
    assert len(tracks) == 2
    print("✓ resolve_source_spotify_url_with_client passed")


# ============================================================================
# Test match_tracks
# ============================================================================

def test_match_tracks_basic():
    """Match tracks with a stubbed find_match."""
    refs = [
        TrackRef(title="Song 1", artists=["Artist 1"]),
        TrackRef(title="Song 2", artists=["Artist 2"])
    ]

    result_to_return = fake_match_result()

    with patch("core.music_import.find_match", return_value=result_to_return):
        result = match_tracks(refs)

    assert len(result) == 2, f"Expected 2 matches, got {len(result)}"
    assert all(isinstance(mt, MatchedTrack) for mt in result)
    assert result[0].ref == refs[0]
    assert result[0].status == "confident"
    print("✓ match_tracks_basic passed")


def test_match_tracks_with_progress_callback():
    """match_tracks calls progress_cb for each track."""
    refs = [
        TrackRef(title="Song 1", artists=["Artist 1"]),
        TrackRef(title="Song 2", artists=["Artist 2"]),
        TrackRef(title="Song 3", artists=["Artist 3"])
    ]

    progress_calls = []
    def progress_cb(index, total, ref):
        progress_calls.append((index, total, ref))

    result_to_return = fake_match_result()

    with patch("core.music_import.find_match", return_value=result_to_return):
        result = match_tracks(refs, progress_cb=progress_cb)

    assert len(result) == 3
    assert len(progress_calls) == 3, f"Expected 3 progress callbacks, got {len(progress_calls)}"
    assert progress_calls[0] == (1, 3, refs[0])
    assert progress_calls[1] == (2, 3, refs[1])
    assert progress_calls[2] == (3, 3, refs[2])
    print("✓ match_tracks_with_progress_callback passed")


def test_match_tracks_with_cancel_event():
    """match_tracks respects cancel_event."""
    refs = [
        TrackRef(title="Song 1", artists=["Artist 1"]),
        TrackRef(title="Song 2", artists=["Artist 2"]),
        TrackRef(title="Song 3", artists=["Artist 3"])
    ]

    cancel_event = threading.Event()
    call_count = [0]
    result_to_return = fake_match_result()

    def counting_find_match(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 2:
            cancel_event.set()
        return result_to_return

    with patch("core.music_import.find_match", side_effect=counting_find_match):
        result = match_tracks(refs, cancel_event=cancel_event)

    assert len(result) <= 2, f"Should have stopped after cancel, got {len(result)}"
    print("✓ match_tracks_with_cancel_event passed")


# ============================================================================
# Test MatchedTrack properties
# ============================================================================

def test_matched_track_download_url_override():
    """download_url returns override_url if set."""
    result = fake_match_result()
    mt = MatchedTrack(
        ref=TrackRef(title="Song"),
        result=result,
        override_url="https://youtube.com/watch?v=override"
    )
    assert mt.download_url == "https://youtube.com/watch?v=override"
    print("✓ matched_track_download_url_override passed")


def test_matched_track_download_url_from_best():
    """download_url returns result.best.url if no override."""
    result = fake_match_result()
    mt = MatchedTrack(
        ref=TrackRef(title="Song"),
        result=result
    )
    assert mt.download_url == "https://www.youtube.com/watch?v=fake123"
    print("✓ matched_track_download_url_from_best passed")


def test_matched_track_download_url_empty():
    """download_url returns empty string if no best and no override."""
    no_match = MatchResult(status="none", best=None, candidates=[], query="Q")
    mt = MatchedTrack(
        ref=TrackRef(title="Song"),
        result=no_match
    )
    assert mt.download_url == ""
    print("✓ matched_track_download_url_empty passed")


def test_matched_track_status():
    """status property returns result.status."""
    result = fake_match_result()
    mt = MatchedTrack(
        ref=TrackRef(title="Song"),
        result=result
    )
    assert mt.status == "confident"
    print("✓ matched_track_status passed")


# ============================================================================
# Test persistence: load/save/get
# ============================================================================

def test_save_and_load_imports():
    """save_import / load_imports round-trip."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_file = os.path.join(tmpdir, "imports.json")
        with patch("core.music_import._store_path", return_value=tmp_file):
            record = {
                "kind": "playlist",
                "name": "Test",
                "spotify_id": "abc123",
                "snapshot_id": "snap1",
                "tracks": []
            }
            save_import(record)

            imports = load_imports()
            assert len(imports) == 1, f"Expected 1 import, got {len(imports)}"
            assert imports[0]["name"] == "Test"
            assert imports[0]["kind"] == "playlist"
            print("✓ save_and_load_imports passed")


def test_get_import():
    """get_import retrieves a specific record."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_file = os.path.join(tmpdir, "imports.json")
        with patch("core.music_import._store_path", return_value=tmp_file):
            record = {
                "id": "test-123",
                "kind": "playlist",
                "name": "Test"
            }
            save_import(record)

            found = get_import("test-123")
            assert found is not None, "Should find the record"
            assert found["name"] == "Test"

            not_found = get_import("nonexistent")
            assert not_found is None, "Should not find nonexistent record"
            print("✓ get_import passed")


def test_save_import_replaces_existing():
    """save_import replaces record with same id, doesn't duplicate."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_file = os.path.join(tmpdir, "imports.json")
        with patch("core.music_import._store_path", return_value=tmp_file):
            record = {
                "id": "test-123",
                "kind": "playlist",
                "name": "Original"
            }
            save_import(record)

            # Update it
            record["name"] = "Updated"
            save_import(record)

            imports = load_imports()
            assert len(imports) == 1, f"Expected 1 import after update, got {len(imports)}"
            assert imports[0]["name"] == "Updated"
            print("✓ save_import_replaces_existing passed")


def test_load_imports_missing_file():
    """load_imports returns [] if file doesn't exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_file = os.path.join(tmpdir, "nonexistent.json")
        with patch("core.music_import._store_path", return_value=tmp_file):
            imports = load_imports()
            assert imports == [], f"Expected empty list, got {imports}"
            print("✓ load_imports_missing_file passed")


def test_load_imports_broken_json():
    """load_imports returns [] on broken JSON."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_file = os.path.join(tmpdir, "imports.json")
        with open(tmp_file, "w") as f:
            f.write("{ invalid json")

        with patch("core.music_import._store_path", return_value=tmp_file):
            imports = load_imports()
            assert imports == [], f"Expected empty list on broken JSON, got {imports}"
            print("✓ load_imports_broken_json passed")


def test_save_import_fills_defaults():
    """save_import fills in id and date if missing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_file = os.path.join(tmpdir, "imports.json")
        with patch("core.music_import._store_path", return_value=tmp_file):
            record = {
                "kind": "text",
                "name": "Manual list"
            }
            save_import(record)

            imports = load_imports()
            assert len(imports) == 1
            assert "id" in imports[0], "Should have id"
            assert "date" in imports[0], "Should have date"
            assert imports[0]["id"].startswith("text-"), f"ID should start with 'text-', got {imports[0]['id']}"
            print("✓ save_import_fills_defaults passed")


# ============================================================================
# Test record_from_session
# ============================================================================

def test_record_from_session():
    """record_from_session builds a proper record dict."""
    result = fake_match_result()
    session = ImportSession(
        source_text="Artist - Title",
        kind="text",
        name="Pasted list",
        tracks=[
            MatchedTrack(
                ref=TrackRef(
                    title="Song One",
                    artists=["Artist One"],
                    spotify_id="spot123",
                    isrc="ISRC001"
                ),
                result=result
            )
        ]
    )

    downloaded = [
        (session.tracks[0], "/path/to/song_one.mp3")
    ]

    record = record_from_session(session, "/downloads", downloaded)

    assert record["kind"] == "text"
    assert record["name"] == "Pasted list"
    assert record["folder"] == "/downloads"
    assert len(record["tracks"]) == 1
    assert record["tracks"][0]["title"] == "Song One"
    assert record["tracks"][0]["artist"] == "Artist One"
    assert record["tracks"][0]["yt_url"] == "https://www.youtube.com/watch?v=fake123"
    assert record["tracks"][0]["file"] == "/path/to/song_one.mp3"
    print("✓ record_from_session passed")


# ============================================================================
# Test build_session
# ============================================================================

def test_build_session_text():
    """build_session orchestrates resolve + match for text input."""
    text = "Artist - Title"
    result = fake_match_result()

    with patch("core.music_import.find_match", return_value=result):
        session = build_session(text)

    assert session.kind == "text"
    assert session.name == "Pasted list"
    assert len(session.tracks) == 1
    assert session.tracks[0].status == "confident"
    print("✓ build_session_text passed")


def test_build_session_spotify():
    """build_session handles Spotify import."""
    text = "spotify:playlist:abc123"
    result = fake_match_result()

    client = stub_spotify_client()
    resolved = MagicMock()
    resolved.kind = "playlist"
    resolved.name = "My Playlist"
    resolved.spotify_id = "abc123"
    resolved.snapshot_id = "snap1"
    resolved.tracks = [
        TrackRef(title="Song 1", artists=["Artist 1"]),
        TrackRef(title="Song 2", artists=["Artist 2"])
    ]
    client.resolve.return_value = resolved

    with patch("core.music_import.find_match", return_value=result):
        session = build_session(text, spotify_client=client)

    assert session.kind == "playlist"
    assert session.name == "My Playlist"
    assert session.spotify_id == "abc123"
    assert len(session.tracks) == 2
    print("✓ build_session_spotify passed")


# ============================================================================
# Test diff_for_resync
# ============================================================================

def test_diff_for_resync_with_new_and_removed():
    """diff_for_resync detects new and removed tracks."""
    result = fake_match_result()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_file = os.path.join(tmpdir, "imports.json")
        with patch("core.music_import._store_path", return_value=tmp_file):
            record = {
                "id": "resync-test",
                "kind": "playlist",
                "spotify_id": "pl123",
                "snapshot_id": "snap1",
                "tracks": [
                    {"isrc": "X", "spotify_id": "spot-x", "title": "Track X"},
                    {"isrc": "Y", "spotify_id": "spot-y", "title": "Track Y"}
                ]
            }
            save_import(record)

            client = stub_spotify_client()
            resolved = MagicMock()
            resolved.kind = "playlist"
            resolved.spotify_id = "pl123"
            resolved.snapshot_id = "snap2"
            resolved.tracks = [
                TrackRef(title="Track X", isrc="X"),
                TrackRef(title="Track Z", isrc="Z")
            ]
            client.resolve.return_value = resolved

            with patch("core.music_import.find_match", return_value=result):
                diff = diff_for_resync("resync-test", client)

    assert len(diff["new"]) == 1, f"Expected 1 new track, got {len(diff['new'])}"
    assert diff["new"][0].ref.isrc == "Z"

    assert len(diff["removed"]) == 1, f"Expected 1 removed track, got {len(diff['removed'])}"
    assert diff["removed"][0]["isrc"] == "Y"

    assert diff["unchanged"] == 1, f"Expected 1 unchanged track, got {diff['unchanged']}"
    assert diff["snapshot_changed"] is True
    print("✓ diff_for_resync_with_new_and_removed passed")


def test_diff_for_resync_not_spotify_import():
    """diff_for_resync raises ValueError for non-Spotify imports."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_file = os.path.join(tmpdir, "imports.json")
        with patch("core.music_import._store_path", return_value=tmp_file):
            record = {
                "id": "text-import",
                "kind": "text",
                "spotify_id": ""
            }
            save_import(record)

            client = stub_spotify_client()
            try:
                diff_for_resync("text-import", client)
                assert False, "Should have raised ValueError"
            except ValueError as e:
                assert "can't be re-synced" in str(e)
                print("✓ diff_for_resync_not_spotify_import passed")


def test_diff_for_resync_missing_import():
    """diff_for_resync raises ValueError if import not found."""
    client = stub_spotify_client()
    try:
        diff_for_resync("nonexistent-id", client)
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "can't be re-synced" in str(e)
        print("✓ diff_for_resync_missing_import passed")


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    print("=" * 70)
    print("OFFLINE TESTS: core/music_import.py")
    print("=" * 70)

    try:
        test_resolve_source_pasted_text()
        test_resolve_source_empty_text()
        test_resolve_source_spotify_uri_no_client()
        test_resolve_source_spotify_url_with_client()

        test_match_tracks_basic()
        test_match_tracks_with_progress_callback()
        test_match_tracks_with_cancel_event()

        test_matched_track_download_url_override()
        test_matched_track_download_url_from_best()
        test_matched_track_download_url_empty()
        test_matched_track_status()

        test_save_and_load_imports()
        test_get_import()
        test_save_import_replaces_existing()
        test_load_imports_missing_file()
        test_load_imports_broken_json()
        test_save_import_fills_defaults()

        test_record_from_session()

        test_build_session_text()
        test_build_session_spotify()

        test_diff_for_resync_with_new_and_removed()
        test_diff_for_resync_not_spotify_import()
        test_diff_for_resync_missing_import()

        print("\nALL PASS")

    except AssertionError as e:
        print(f"\nFAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
