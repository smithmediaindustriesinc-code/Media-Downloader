# Import from Spotify

Media Downloader **cannot** download audio from Spotify — no third-party app can;
Spotify never hands out the files. What the **Import** tab does instead:

1. reads the **track list + metadata** (title, artist, album, track number, ISRC,
   album art) of a Spotify playlist / album / track / artist / your Liked Songs,
   using Spotify's official Web API;
2. finds each track on **YouTube / YouTube Music**;
3. downloads that with the normal queue;
4. writes the Spotify metadata + album art + lyrics onto the file, plus a comment
   tag noting the audio is a YouTube match, not the original master.

Confident matches download automatically. Anything uncertain is flagged for you to
pick the right result (or paste a YouTube URL). You can also paste a plain
`Artist - Title` list or a CSV instead of a Spotify link — that path needs no
Spotify account at all.

## One-time setup

You use **your own** free Spotify app. Media Downloader never sees or stores a
password or client secret — sign-in is the PKCE flow.

1. Go to <https://developer.spotify.com/dashboard> and log in.
2. **Create app.** Name/description can be anything.
3. In the app's **Settings**, add this **Redirect URI** exactly:
   `http://127.0.0.1:8888/callback`
   (if you change the port in Media Downloader's settings, match it here).
4. Copy the **Client ID**.
5. In Media Downloader: **Settings → Import from Spotify**, paste the Client ID,
   click **Save**, then **Connect**. A browser tab opens; approve; done.

**Spotify Premium is required** on that account — since February 2026 Spotify
blocks API access for free accounts. If you see a "Premium required" message,
that's why.

## Re-syncing a playlist

Each Spotify import is remembered under **Previous imports**. Click **Re-sync** to
re-read the playlist: Media Downloader downloads any tracks added since, and tells
you which ones were removed (it never deletes your files).

## Settings

| Setting | Default | Notes |
|---|---|---|
| Auto-download confident matches | on | queue confident matches without ticking each one |
| Embed album art | on | from Spotify's cover image |
| Embed lyrics | on | via syncedlyrics (multiple providers) |
| "matched from YouTube" comment tag | on | honest provenance in the file |
| Redirect port | 8888 | must match the Redirect URI in your Spotify app |

## Limitations

- The match is only as good as what's on YouTube — remasters, radio edits, live
  versions, or "sped up" uploads can slip through. Check the flagged rows.
- YouTube audio is lossy (~128–160 kbps); this is never CD quality.
- Podcasts and Spotify-exclusive tracks can't be matched.
- Classical / compilation tagging on the YouTube side is often poor.
