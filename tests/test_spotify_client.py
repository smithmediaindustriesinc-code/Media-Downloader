"""Offline tests for core.spotify_client - URL/URI parsing + response mapping.

Live OAuth needs a real Client ID + Premium account; run manually.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.spotify_client import (parse_spotify_ref, track_obj_to_ref, SpotifyClient,
                                 SpotifyError, _pkce_pair, _MemoryStore,
                                 is_spotify_search_query)

fails = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


# --- parse_spotify_ref --------------------------------------------------- #
cases = {
    "https://open.spotify.com/track/6rqhFgbbKwnb9MLmUQDhG6?si=abc": ("track", "6rqhFgbbKwnb9MLmUQDhG6"),
    "https://open.spotify.com/album/1DFixLWuPkv3KT3TnV35m3": ("album", "1DFixLWuPkv3KT3TnV35m3"),
    "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M": ("playlist", "37i9dQZF1DXcBWIGoYBM5M"),
    "https://open.spotify.com/artist/0OdUWJ0sBjDrqHygGUXeCF": ("artist", "0OdUWJ0sBjDrqHygGUXeCF"),
    "spotify:track:6rqhFgbbKwnb9MLmUQDhG6": ("track", "6rqhFgbbKwnb9MLmUQDhG6"),
    "spotify:user:foo:playlist:37i9dQZF1DXcBWIGoYBM5M": ("playlist", "37i9dQZF1DXcBWIGoYBM5M"),
    "https://open.spotify.com/user/spotify/playlist/37i9dQZF1DXcBWIGoYBM5M": ("playlist", "37i9dQZF1DXcBWIGoYBM5M"),
    "https://open.spotify.com/intl-de/track/6rqhFgbbKwnb9MLmUQDhG6": ("track", "6rqhFgbbKwnb9MLmUQDhG6"),
    "liked": ("saved", None),
    "Saved Songs": ("saved", None),
}
for text, expected in cases.items():
    check(f"parse {text[:45]}", parse_spotify_ref(text) == expected)

for bad in ("", "https://youtube.com/watch?v=x", "just some text", "spotify:weird:x"):
    try:
        parse_spotify_ref(bad)
        check(f"reject {bad!r}", False)
    except SpotifyError:
        check(f"reject {bad!r}", True)


# --- track_obj_to_ref -------------------------------------------------- #
album_obj = {
    "name": "Greatest Hits", "album_type": "compilation",
    "total_tracks": 17, "release_date": "1981-11-02",
    "images": [{"url": "https://i/large.jpg", "width": 640},
               {"url": "https://i/small.jpg", "width": 64}],
    "artists": [{"name": "Queen"}],
    "external_ids": {"upc": "999"},
}
track_in_album = {
    "name": "Bohemian Rhapsody", "track_number": 2, "disc_number": 1,
    "duration_ms": 354000, "explicit": False, "id": "abc123",
    "artists": [{"name": "Queen"}], "external_ids": {"isrc": "GBUM71029604"},
}
ref = track_obj_to_ref(track_in_album, album=album_obj)
check("ref title", ref.title == "Bohemian Rhapsody")
check("ref artists", ref.artists == ["Queen"])
check("ref album", ref.album == "Greatest Hits")
check("ref album_artist", ref.album_artist == "Queen")
check("ref track_no", ref.track_no == 2)
check("ref total_tracks", ref.total_tracks == 17)
check("ref isrc", ref.isrc == "GBUM71029604")
check("ref duration", ref.duration_ms == 354000)
check("ref cover <=800 picked", ref.cover_url == "https://i/large.jpg")
check("ref album_type", ref.album_type == "compilation")
check("ref year prop", ref.year == "1981")

# playlist-style: track carries its own album
track_with_album = dict(track_in_album, album=album_obj)
ref2 = track_obj_to_ref(track_with_album)
check("playlist-style ref album", ref2.album == "Greatest Hits")

check("None track -> None", track_obj_to_ref(None) is None)


# --- pkce + client wiring -------------------------------------------- #
v, c = _pkce_pair()
check("pkce verifier length 43-128", 43 <= len(v) <= 128)
check("pkce challenge urlsafe no pad", "=" not in c and "+" not in c and "/" not in c)

cl = SpotifyClient("my_client_id", redirect_port=9999, token_store=_MemoryStore())
check("redirect uri", cl.redirect_uri == "http://127.0.0.1:9999/callback")
check("not connected initially", cl.is_connected is False)
cl._token_store.set("fake_refresh")
check("connected after token set", cl.is_connected is True)


# --- search + is_spotify_search_query ---------------------------------- #
# Test search with a canned response including None entries in playlists
def fake_get_search(path, params):
    """Return a canned /search response with a None entry in playlists."""
    return {
        "tracks": {
            "items": [
                {
                    "name": "One More Time",
                    "id": "track1",
                    "artists": [{"name": "Daft Punk"}],
                    "album": {"name": "Discovery", "total_tracks": 14,
                              "artists": [{"name": "Daft Punk"}], "images": []},
                    "track_number": 1,
                    "disc_number": 1,
                    "duration_ms": 320000,
                    "explicit": False,
                    "external_ids": {"isrc": ""}
                }
            ]
        },
        "playlists": {
            "items": [
                {
                    "id": "pl1",
                    "name": "Daft Punk Best",
                    "owner": {"display_name": "spotify"},
                    "tracks": {"total": 25}
                },
                None,  # Spotify can return null entries in search results
                {
                    "id": "pl2",
                    "name": "Electronic Vibes",
                    "owner": {"display_name": "user123"},
                    "tracks": {"total": 50}
                }
            ]
        },
        "albums": {
            "items": [
                {
                    "id": "alb1",
                    "name": "Discovery",
                    "artists": [{"name": "Daft Punk"}],
                    "total_tracks": 14
                }
            ]
        },
        "artists": {
            "items": [
                {
                    "id": "art1",
                    "name": "Daft Punk"
                }
            ]
        }
    }

# Monkeypatch the search
cl_search = SpotifyClient("test_id", token_store=_MemoryStore())
cl_search._token_store.set("fake_token")
cl_search._access_token = "fake_token"
cl_search._access_expires = float('inf')
original_get = cl_search._get
cl_search._get = fake_get_search

result = cl_search.search("daft punk", kinds=("track", "playlist", "album", "artist"), limit=20)

check("search tracks returned", len(result["tracks"]) == 1)
check("search track title", result["tracks"][0].title == "One More Time")
check("search playlists skips None", len(result["playlists"]) == 2)
check("search playlist 1 name", result["playlists"][0]["name"] == "Daft Punk Best")
check("search playlist 2 name", result["playlists"][1]["name"] == "Electronic Vibes")
check("search albums returned", len(result["albums"]) == 1)
check("search album artist", result["albums"][0]["artist"] == "Daft Punk")
check("search artists returned", len(result["artists"]) == 1)

# Test search with empty query
result_empty = cl_search.search("", kinds=("track", "playlist"))
check("search empty query returns empty dict keys", set(result_empty.keys()) == {"track", "playlist"})
check("search empty query returns empty lists", all(len(v) == 0 for v in result_empty.values()))

# Test search with only requested kinds
result_subset = cl_search.search("daft", kinds=("track", "artist"), limit=20)
check("search subset has only requested keys", set(result_subset.keys()) == {"tracks", "artists"})

# Test is_spotify_search_query
check("is_spotify_search_query plain text True", is_spotify_search_query("daft punk one more time") is True)
check("is_spotify_search_query Spotify URL False", is_spotify_search_query("https://open.spotify.com/track/abc123") is False)
check("is_spotify_search_query spotify URI False", is_spotify_search_query("spotify:track:abc123") is False)
check("is_spotify_search_query other URL False", is_spotify_search_query("https://youtube.com/watch?v=x") is False)
check("is_spotify_search_query empty string False", is_spotify_search_query("") is False)
check("is_spotify_search_query whitespace False", is_spotify_search_query("   ") is False)
check("is_spotify_search_query liked False", is_spotify_search_query("liked") is False)

print()
if fails:
    print(f"{len(fails)} FAILED: {fails}")
    sys.exit(1)
print("ALL PASS")
