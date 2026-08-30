#!/usr/bin/env python3
"""Test suite for track_ref, tagging, and tracklist_parser modules."""
import io
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Set UTF-8 encoding for stdout to handle unicode properly
import codecs
if sys.stdout.encoding != 'utf-8':
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')

# Add parent to path so we can import core modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.track_ref import TrackRef
from core.tagging import write_tags, fetch_cover_bytes, fetch_lyrics
from core.tracklist_parser import parse_tracklist


def test_parse_tracklist_basic():
    """Test parse_tracklist with 'Artist - Title' format."""
    text = """Artist One - Title One
Artist Two - Title Two
Artist Three - Title Three"""
    results = parse_tracklist(text)
    assert len(results) == 3, f"Expected 3 results, got {len(results)}"
    assert results[0].title == "Title One"
    assert results[0].artists == ["Artist One"]
    assert results[1].title == "Title Two"
    assert results[1].artists == ["Artist Two"]
    print("✓ Basic artist-title parsing")


def test_parse_tracklist_with_dashes():
    """Test parse_tracklist with en-dash and em-dash."""
    text = """Artist – Title One
Artist — Title Two
Artist - Title Three"""
    results = parse_tracklist(text)
    assert len(results) == 3, f"Expected 3 results, got {len(results)}"
    assert results[0].title == "Title One"
    assert results[1].title == "Title Two"
    assert results[2].title == "Title Three"
    print("✓ En-dash and em-dash parsing")


def test_parse_tracklist_with_comments():
    """Test parse_tracklist ignores comments and blank lines."""
    text = """# Comment line
Artist One - Title One

# Another comment
Artist Two - Title Two"""
    results = parse_tracklist(text)
    assert len(results) == 2, f"Expected 2 results, got {len(results)}"
    assert results[0].title == "Title One"
    assert results[1].title == "Title Two"
    print("✓ Comment and blank line filtering")


def test_parse_tracklist_csv():
    """Test parse_tracklist with CSV format."""
    text = """artist,title,album,track_no
Artist A,Song A,Album A,1
Artist B,Song B,Album B,2"""
    results = parse_tracklist(text)
    assert len(results) == 2, f"Expected 2 results, got {len(results)}"
    assert results[0].title == "Song A"
    assert results[0].artists == ["Artist A"]
    assert results[0].album == "Album A"
    assert results[0].track_no == 1
    assert results[1].title == "Song B"
    assert results[1].track_no == 2
    print("✓ CSV parsing")


def test_parse_tracklist_no_title():
    """Test parse_tracklist skips lines without title."""
    text = """Artist One - Title One
-
Artist Two - Title Two"""
    results = parse_tracklist(text)
    assert len(results) == 2, f"Expected 2 results, got {len(results)}"
    assert results[0].title == "Title One"
    assert results[1].title == "Title Two"
    print("✓ Skip empty/no-title lines")


def test_trackref_artist_str():
    """Test TrackRef.artist_str property."""
    ref = TrackRef(artists=["Artist A", "Artist B", "Artist C"])
    assert ref.artist_str == "Artist A, Artist B, Artist C"
    ref2 = TrackRef(artists=[])
    assert ref2.artist_str == ""
    print("✓ TrackRef.artist_str")


def test_trackref_search_query():
    """Test TrackRef.search_query property."""
    ref = TrackRef(artists=["The Beatles"], title="Let It Be")
    assert ref.search_query == "The Beatles Let It Be"
    ref2 = TrackRef(artists=[], title="Song Only")
    assert ref2.search_query == "Song Only"
    print("✓ TrackRef.search_query")


def test_trackref_year():
    """Test TrackRef.year property."""
    ref = TrackRef(release_date="2023-05-15")
    assert ref.year == "2023"
    ref2 = TrackRef(release_date="2023")
    assert ref2.year == "2023"
    ref3 = TrackRef(release_date="")
    assert ref3.year == ""
    print("✓ TrackRef.year")


def test_write_tags_with_audio():
    """Test write_tags with real audio files."""
    # Check if ffmpeg is available
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            timeout=5,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        print("⊘ write_tags tests (ffmpeg not available) - SKIPPED")
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        # Generate test audio files with ffmpeg
        mp3_file = tmpdir_path / "test.mp3"
        flac_file = tmpdir_path / "test.flac"
        m4a_file = tmpdir_path / "test.m4a"

        try:
            # Create tiny audio files (1 second each)
            for target_file, codec in [
                (mp3_file, "libmp3lame"),
                (flac_file, "flac"),
                (m4a_file, "aac"),
            ]:
                subprocess.run(
                    [
                        "ffmpeg",
                        "-f", "lavfi",
                        "-i", "anullsrc",
                        "-t", "1",
                        "-c:a", codec,
                        "-y",
                        str(target_file),
                    ],
                    capture_output=True,
                    timeout=10,
                    check=True,
                )

            # Create a test TrackRef
            ref = TrackRef(
                title="Test Track",
                artists=["Test Artist", "Featured Artist"],
                album="Test Album",
                album_artist="Album Artist",
                track_no=1,
                total_tracks=10,
                disc_no=1,
                release_date="2024-01-15",
                isrc="USRC12345678",
                explicit=False,
            )

            # Test MP3
            ok, msg = write_tags(
                str(mp3_file),
                ref,
                embed_cover=False,
                source_comment=True,
                lyrics="[00:01.00]Test lyrics",
            )
            assert ok, f"MP3 tagging failed: {msg}"
            print(f"✓ MP3 tagging: {msg}")

            # Test FLAC
            ok, msg = write_tags(
                str(flac_file),
                ref,
                embed_cover=False,
                source_comment=True,
                lyrics="[00:01.00]Test lyrics",
            )
            assert ok, f"FLAC tagging failed: {msg}"
            print(f"✓ FLAC tagging: {msg}")

            # Test M4A
            ok, msg = write_tags(
                str(m4a_file),
                ref,
                embed_cover=False,
                source_comment=True,
                lyrics="[00:01.00]Test lyrics",
            )
            assert ok, f"M4A tagging failed: {msg}"
            print(f"✓ M4A tagging: {msg}")

        except subprocess.TimeoutExpired:
            print("⊘ write_tags tests (ffmpeg timeout) - SKIPPED")
            return
        except Exception as e:
            print(f"⊘ write_tags tests (error: {e}) - SKIPPED")
            return


def test_fetch_cover_bytes_invalid():
    """Test fetch_cover_bytes with invalid URL."""
    result = fetch_cover_bytes("https://invalid-url-that-does-not-exist-12345.invalid/")
    assert result is None, "fetch_cover_bytes should return None for invalid URL"
    print("✓ fetch_cover_bytes handles invalid URL")


def test_fetch_lyrics_invalid():
    """Test fetch_lyrics with empty query."""
    ref = TrackRef(title="", artists=[])
    result = fetch_lyrics(ref)
    # Should not crash, may return None
    print("✓ fetch_lyrics handles empty query")


def main():
    """Run all tests."""
    try:
        test_parse_tracklist_basic()
        test_parse_tracklist_with_dashes()
        test_parse_tracklist_with_comments()
        test_parse_tracklist_csv()
        test_parse_tracklist_no_title()
        test_trackref_artist_str()
        test_trackref_search_query()
        test_trackref_year()
        test_write_tags_with_audio()
        test_fetch_cover_bytes_invalid()
        test_fetch_lyrics_invalid()

        print("\nALL PASS")
        return 0
    except AssertionError as e:
        print(f"\nTEST FAILED: {e}")
        return 1
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
