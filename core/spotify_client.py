"""Read track lists from Spotify for the "import from Spotify" feature.

IMPORTANT: Spotify never hands a third-party app the actual audio. This
module only reads *metadata* - the track list + tags + cover art of a
playlist / album / track / artist / the user's saved songs - via the
official Web API. The audio Media Downloader produces is a YouTube match
of each track (see core/track_match.py), NOT the Spotify recording, and
the UI must say so.

Auth is Authorization-Code + PKCE with a **user-supplied Client ID**
(no client secret is ever shipped or stored). The user creates a free
app at https://developer.spotify.com/dashboard, adds the redirect URI
http://127.0.0.1:<port>/callback, and pastes the Client ID into
Settings. As of 2026 Spotify also requires that account to have Spotify
Premium for API access - check_auth() surfaces that clearly.

Nothing here blocks the GUI thread except connect() (which waits on the
browser round-trip and should be run on a worker). Network / parsing
failures raise SpotifyError subclasses with a human message; callers
wrap in try/except.
"""
import base64
import hashlib
import http.server
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass, field

from core.track_ref import TrackRef
from core.secure_store import FileSecretStore

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"
SCOPES = "playlist-read-private playlist-read-collaborative user-library-read"
_UA = "MediaDownloader-spotify-import"


def _token_file():
    """Where the Spotify refresh token is kept: under the app's own data
    folder so it survives updates (installer never touches %APPDATA%) and
    is removed on uninstall (the uninstaller wipes that folder). A beta
    instance has its own folder, hence its own token."""
    from core.paths import app_dir
    return os.path.join(app_dir(), "options", "spotify_token.dat")


class SpotifyError(Exception):
    """Base - a readable message safe to show the user."""


class SpotifyAuthError(SpotifyError):
    """Not connected / token refresh failed / user needs to reconnect."""


class SpotifyPremiumRequired(SpotifyAuthError):
    """Spotify rejected the account for lacking Premium (2026 rule)."""


class SpotifyRateLimited(SpotifyError):
    def __init__(self, retry_after):
        self.retry_after = retry_after
        super().__init__(
            f"Spotify is rate-limiting this app. Try again in "
            f"{retry_after}s (a shared/over-used Client ID can be throttled "
            f"for up to 24h - using your own Client ID avoids this).")


# --------------------------------------------------------------------------- #
# URL / URI parsing
# --------------------------------------------------------------------------- #
_KINDS = ("track", "album", "playlist", "artist")


def parse_spotify_ref(text):
    """'https://open.spotify.com/playlist/37i9..?si=x' / 'spotify:track:...'
    / 'https://open.spotify.com/user/foo/playlist/ID' / the literal
    'liked' or 'saved'  ->  (kind, spotify_id).

    kind is one of: track, album, playlist, artist, saved.
    Raises SpotifyError if it isn't a recognisable Spotify reference."""
    if not text:
        raise SpotifyError("No Spotify link given.")
    s = text.strip()
    low = s.lower()
    if low in ("liked", "liked songs", "saved", "saved songs", "my songs"):
        return ("saved", None)

    # spotify:playlist:ID  (also spotify:user:foo:playlist:ID)
    if low.startswith("spotify:"):
        parts = s.split(":")
        for i, p in enumerate(parts):
            if p in _KINDS and i + 1 < len(parts):
                return (p, parts[i + 1])
        raise SpotifyError("Unrecognised Spotify URI.")

    # URL forms
    if "open.spotify.com" in low or "spotify.com" in low:
        path = urllib.parse.urlparse(s).path.strip("/").split("/")
        # path like ['playlist','ID'] or ['user','name','playlist','ID'] or
        # ['intl-de','track','ID']
        for i, seg in enumerate(path):
            if seg in _KINDS and i + 1 < len(path):
                pid = path[i + 1].split("?")[0]
                return (seg, pid)
        raise SpotifyError("That Spotify link doesn't point at a track, "
                           "album, playlist or artist.")

    raise SpotifyError("That doesn't look like a Spotify link.")


def is_spotify_search_query(text):
    """A plain search phrase is anything that ISN'T a Spotify ref and isn't a
    URL. Used so the search box only sends deliberate queries."""
    t = (text or "").strip()
    if not t:
        return False
    try:
        parse_spotify_ref(t)
        return False   # it's a real Spotify ref, not a search
    except SpotifyError:
        pass
    return "://" not in t   # not some other URL


# --------------------------------------------------------------------------- #
# response -> TrackRef
# --------------------------------------------------------------------------- #
def _img_url(images):
    if not images:
        return ""
    # images are largest-first; take the largest <= 800 or just the largest
    for im in images:
        if im.get("width") and im["width"] <= 800:
            return im.get("url", "")
    return images[0].get("url", "")


def track_obj_to_ref(t, album=None):
    """A Spotify track object -> TrackRef. `album` is the parent album
    object when the track object's own `album` key is absent (album and
    playlist track listings differ)."""
    if not t:
        return None
    alb = t.get("album") or album or {}
    artists = [a.get("name", "") for a in (t.get("artists") or []) if a.get("name")]
    alb_artists = [a.get("name", "") for a in (alb.get("artists") or []) if a.get("name")]
    return TrackRef(
        title=t.get("name", ""),
        artists=artists,
        album=alb.get("name", ""),
        album_artist=(alb_artists[0] if alb_artists else (artists[0] if artists else "")),
        track_no=int(t.get("track_number") or 0),
        disc_no=int(t.get("disc_number") or 0),
        total_tracks=int(alb.get("total_tracks") or 0),
        release_date=alb.get("release_date", "") or "",
        duration_ms=int(t.get("duration_ms") or 0),
        isrc=((t.get("external_ids") or {}).get("isrc", "") or ""),
        upc=((alb.get("external_ids") or {}).get("upc", "") or ""),
        cover_url=_img_url(alb.get("images")),
        explicit=bool(t.get("explicit")),
        spotify_id=t.get("id", "") or "",
        album_type=alb.get("album_type", "") or "",
    )


@dataclass
class ResolvedImport:
    kind: str                       # track | album | playlist | artist | saved
    name: str                       # display name ("Deep Focus", "Abbey Road", ...)
    spotify_id: str = ""
    snapshot_id: str = ""           # playlists only - changes when the playlist edits
    tracks: list = field(default_factory=list)   # list[TrackRef]


# --------------------------------------------------------------------------- #
# PKCE helpers
# --------------------------------------------------------------------------- #
def _pkce_pair():
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    return verifier, challenge


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    code = None
    error = None

    def do_GET(self):  # noqa: N802
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _CallbackHandler.code = (q.get("code") or [None])[0]
        _CallbackHandler.error = (q.get("error") or [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        msg = ("Media Downloader is connected to Spotify. You can close this tab."
               if _CallbackHandler.code else
               f"Spotify authorisation failed: {_CallbackHandler.error}")
        self.wfile.write(f"<html><body style='font-family:sans-serif'>{msg}</body></html>"
                         .encode("utf-8"))

    def log_message(self, *a):  # silence
        pass


# --------------------------------------------------------------------------- #
# the client
# --------------------------------------------------------------------------- #
class SpotifyClient:
    def __init__(self, client_id, redirect_port=8888, token_store=None):
        self.client_id = (client_id or "").strip()
        self.redirect_port = int(redirect_port or 8888)
        self.redirect_uri = f"http://127.0.0.1:{self.redirect_port}/callback"
        self._access_token = None
        self._access_expires = 0.0
        # token_store: object with get()->str|None / set(str) / clear().
        # Default = a DPAPI-protected file under the app data folder.
        self._token_store = token_store or FileSecretStore(_token_file())

    # -- auth ------------------------------------------------------------- #
    @property
    def is_connected(self):
        return bool(self._token_store.get())

    def connect(self, open_browser=True, timeout=180):
        """Run the PKCE flow. Blocks until the browser round-trip finishes
        or `timeout` seconds pass. Run me on a worker thread."""
        if not self.client_id:
            raise SpotifyAuthError("No Spotify Client ID set. Add one in Settings "
                                   "(create a free app at developer.spotify.com).")
        verifier, challenge = _pkce_pair()
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
            "scope": SCOPES,
            "state": secrets.token_urlsafe(16),
        }
        _CallbackHandler.code = None
        _CallbackHandler.error = None
        try:
            httpd = http.server.HTTPServer(("127.0.0.1", self.redirect_port),
                                           _CallbackHandler)
        except OSError as e:
            raise SpotifyAuthError(
                f"Couldn't open the local sign-in port {self.redirect_port}. "
                f"Close whatever is using it or change the port in Settings. ({e})")
        httpd.timeout = timeout
        t = threading.Thread(target=httpd.handle_request, daemon=True)
        t.start()
        if open_browser:
            webbrowser.open(f"{AUTH_URL}?{urllib.parse.urlencode(params)}")
        t.join(timeout + 5)
        try:
            httpd.server_close()
        except Exception:
            pass

        if _CallbackHandler.error:
            raise SpotifyAuthError(f"Spotify sign-in was denied: {_CallbackHandler.error}")
        if not _CallbackHandler.code:
            raise SpotifyAuthError("Spotify sign-in timed out - nothing came back "
                                   "from the browser.")
        self._exchange_code(_CallbackHandler.code, verifier)

    def disconnect(self):
        self._access_token = None
        self._access_expires = 0.0
        try:
            self._token_store.clear()
        except Exception:
            pass

    def _exchange_code(self, code, verifier):
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "client_id": self.client_id,
            "code_verifier": verifier,
        }
        tok = self._token_request(data)
        rt = tok.get("refresh_token")
        if rt:
            self._token_store.set(rt)
        self._apply_access(tok)

    def _refresh(self):
        rt = self._token_store.get()
        if not rt:
            raise SpotifyAuthError("Not connected to Spotify. Connect in Settings.")
        data = {
            "grant_type": "refresh_token",
            "refresh_token": rt,
            "client_id": self.client_id,
        }
        tok = self._token_request(data)
        # Spotify may hand back a rotated refresh token
        if tok.get("refresh_token"):
            self._token_store.set(tok["refresh_token"])
        self._apply_access(tok)

    def _apply_access(self, tok):
        self._access_token = tok.get("access_token")
        self._access_expires = time.time() + int(tok.get("expires_in") or 3600) - 60
        if not self._access_token:
            raise SpotifyAuthError("Spotify didn't return an access token.")

    def _token_request(self, data):
        body = urllib.parse.urlencode(data).encode("ascii")
        req = urllib.request.Request(
            TOKEN_URL, data=body, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "User-Agent": _UA})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = json.loads(e.read().decode("utf-8")).get("error_description", "")
            except Exception:
                pass
            if e.code in (401, 400) and "revoked" in detail.lower():
                self.disconnect()
                raise SpotifyAuthError("Spotify sign-in expired. Reconnect in Settings.")
            raise SpotifyAuthError(f"Spotify auth failed ({e.code}). {detail}".strip())
        except urllib.error.URLError as e:
            raise SpotifyError(f"Couldn't reach Spotify: {e.reason}")

    def _token(self):
        if self._access_token and time.time() < self._access_expires:
            return self._access_token
        self._refresh()
        return self._access_token

    # -- API ------------------------------------------------------------- #
    def _get(self, path, params=None, _retry=True):
        url = path if path.startswith("http") else f"{API_BASE}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {self._token()}",
                          "User-Agent": _UA})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 401 and _retry:
                self._refresh()
                return self._get(path, params, _retry=False)
            if e.code == 429:
                raise SpotifyRateLimited(int(e.headers.get("Retry-After") or 5))
            if e.code == 403:
                body = ""
                try:
                    body = e.read().decode("utf-8")
                except Exception:
                    pass
                if "premium" in body.lower():
                    raise SpotifyPremiumRequired(
                        "Spotify now requires a Premium account to use the API. "
                        "The account that owns this Client ID isn't Premium.")
                if "/playlists/" in path:
                    raise SpotifyError(
                        "Spotify blocked access to that playlist (403).\n\n"
                        "Spotify-made playlists - Discover Weekly, Release Radar, Daily Mix, "
                        "and anything under \"Made For You\" - can't be read by third-party "
                        "apps since Spotify's November 2024 API change.\n\n"
                        "Your own playlists, playlists you follow, and public playlists made "
                        "by other users all still work: use \"Your Spotify playlists\" or the "
                        "search box in this window.")
                raise SpotifyError(f"Spotify refused the request (403). {body[:200]}")
            if e.code == 404:
                if "/playlists/" in path:
                    raise SpotifyError(
                        "That playlist wasn't found. If it's a Spotify-made playlist "
                        "(Discover Weekly, Daily Mix, etc.), third-party apps can't read "
                        "those - use \"Your Spotify playlists\" or search instead.")
                raise SpotifyError("That Spotify item wasn't found (private, "
                                   "region-locked, or deleted).")
            raise SpotifyError(f"Spotify API error {e.code}.")
        except urllib.error.URLError as e:
            raise SpotifyError(f"Couldn't reach Spotify: {e.reason}")

    def _paged(self, first):
        """Yield every item across a paged endpoint. `first` is the initial
        response dict containing 'items' and 'next'."""
        page = first
        while page:
            for it in page.get("items") or []:
                yield it
            nxt = page.get("next")
            page = self._get(nxt) if nxt else None

    # -- high level ---------------------------------------------------- #
    def resolve(self, ref_text):
        """Spotify link/URI/'liked'  ->  ResolvedImport (with .tracks)."""
        kind, sid = parse_spotify_ref(ref_text)
        if kind == "track":
            t = self._get(f"/tracks/{sid}")
            return ResolvedImport("track", t.get("name", "track"), sid,
                                  tracks=[track_obj_to_ref(t)])
        if kind == "album":
            alb = self._get(f"/albums/{sid}")
            refs = []
            first = alb.get("tracks") or self._get(f"/albums/{sid}/tracks", {"limit": 50})
            for t in self._paged(first):
                refs.append(track_obj_to_ref(t, album=alb))
            return ResolvedImport("album", alb.get("name", "album"), sid, tracks=refs)
        if kind == "playlist":
            pl = self._get(f"/playlists/{sid}", {"fields":
                "name,snapshot_id,owner(display_name,id),tracks.total"})
            refs = []
            # additional_types=track so a playlist that also holds podcast
            # episodes doesn't 400/return null items.
            first = self._get(f"/playlists/{sid}/tracks",
                              {"limit": 100, "additional_types": "track"})
            for row in self._paged(first):
                tr = (row or {}).get("track") or {}
                if tr.get("type") == "track" or tr.get("id"):
                    ref = track_obj_to_ref(tr)
                    if ref and ref.title:
                        refs.append(ref)
            return ResolvedImport("playlist", pl.get("name", "playlist"), sid,
                                  snapshot_id=pl.get("snapshot_id", ""), tracks=refs)
        if kind == "artist":
            art = self._get(f"/artists/{sid}")
            top = self._get(f"/artists/{sid}/top-tracks", {"market": "from_token"})
            refs = [track_obj_to_ref(t) for t in (top.get("tracks") or [])]
            return ResolvedImport("artist", art.get("name", "artist") + " - top tracks",
                                  sid, tracks=[r for r in refs if r])
        if kind == "saved":
            refs = []
            first = self._get("/me/tracks", {"limit": 50})
            for row in self._paged(first):
                ref = track_obj_to_ref((row or {}).get("track") or {})
                if ref and ref.title:
                    refs.append(ref)
            return ResolvedImport("saved", "Liked Songs", tracks=refs)
        raise SpotifyError(f"Can't import a Spotify {kind}.")

    def list_my_playlists(self):
        """The signed-in user's own + followed playlists, plus a synthetic
        'Liked Songs' row first. Each: {id, name, tracks_total, owner, kind}.
        'Liked Songs' has id='' and kind='saved'."""
        out = [{"id": "", "name": "Liked Songs", "tracks_total": None,
                "owner": "you", "kind": "saved"}]
        first = self._get("/me/playlists", {"limit": 50})
        for pl in self._paged(first):
            if not pl:
                continue
            out.append({
                "id": pl.get("id", ""),
                "name": pl.get("name", "(untitled)"),
                "tracks_total": (pl.get("tracks") or {}).get("total"),
                "owner": (pl.get("owner") or {}).get("display_name", ""),
                "kind": "playlist",
            })
        return out

    def playlist_snapshot(self, playlist_id):
        """Just the current snapshot_id - cheap check for re-sync."""
        return self._get(f"/playlists/{playlist_id}",
                         {"fields": "snapshot_id"}).get("snapshot_id", "")

    def search(self, query, kinds=("track", "playlist"), limit=20):
        """Search Spotify. `kinds` is any of "track","playlist","album","artist".
        Returns a dict: {"tracks": [TrackRef, ...],
                         "playlists": [{"id","name","owner","tracks_total"}, ...],
                         "albums": [{"id","name","artist","tracks_total"}, ...],
                         "artists": [{"id","name"}, ...]}
        Only the requested kinds are populated. Raises the usual SpotifyError
        subclasses (SpotifyAuthError / SpotifyPremiumRequired / SpotifyRateLimited)."""
        q = query.strip()
        if not q:
            return {k: [] for k in kinds if k in ("track", "playlist", "album", "artist")}

        type_param = ",".join(k for k in kinds if k in ("track", "playlist", "album", "artist"))
        resp = self._get("/search", {"q": q, "type": type_param, "limit": max(1, min(50, limit))})

        result = {}

        if "track" in kinds:
            result["tracks"] = [
                track_obj_to_ref(t) for t in (resp.get("tracks") or {}).get("items", []) if t
            ]
            result["tracks"] = [t for t in result["tracks"] if t and t.title]

        if "playlist" in kinds:
            playlists = []
            for p in (resp.get("playlists") or {}).get("items", []):
                if p is None:
                    continue
                playlists.append({
                    "id": p.get("id", ""),
                    "name": p.get("name", "(untitled)"),
                    "owner": (p.get("owner") or {}).get("display_name", ""),
                    "tracks_total": (p.get("tracks") or {}).get("total"),
                })
            result["playlists"] = playlists

        if "album" in kinds:
            albums = []
            for a in (resp.get("albums") or {}).get("items", []):
                if a is None:
                    continue
                artists = a.get("artists") or []
                artist_name = artists[0].get("name", "") if artists else ""
                albums.append({
                    "id": a.get("id", ""),
                    "name": a.get("name", ""),
                    "artist": artist_name,
                    "tracks_total": a.get("total_tracks"),
                })
            result["albums"] = albums

        if "artist" in kinds:
            artists = []
            for art in (resp.get("artists") or {}).get("items", []):
                if art is None:
                    continue
                artists.append({
                    "id": art.get("id", ""),
                    "name": art.get("name", ""),
                })
            result["artists"] = artists

        return result


# --------------------------------------------------------------------------- #
# token storage
# --------------------------------------------------------------------------- #
class _MemoryStore:
    """In-memory token store - only used when a caller passes it explicitly
    (e.g. tests). The real default is a FileSecretStore, see __init__."""

    def __init__(self):
        self._v = None

    def get(self):
        return self._v

    def set(self, v):
        self._v = v

    def clear(self):
        self._v = None
