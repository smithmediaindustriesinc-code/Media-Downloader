# Media Downloader

A customtkinter GUI for `yt-dlp`, built for someone who downloads media
constantly - single-window, tabbed, with playlists, history, and dependency
auto-install.

## Setup (running from source)

1. Install Python 3.9+
2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Run the app:
   ```
   python main.py
   ```
FFmpeg does NOT need to be pre-installed - the app detects it on
first launch and lets you install it with one click from the **Version**
tab (no admin rights needed; FFmpeg is dropped straight into the app's own
folder).

On first launch you'll be asked to pick a default download folder - the
app creates `Videos/` and `Music/` subfolders inside it automatically.

## Folder structure

```
VideoDownloaderApp/
├── main.py                    # entry point
├── icon.ico                    # app icon (from Media_Center.png)
├── config.json / history.json / playlists.json   # auto-created at runtime
├── DISCLAIMER.txt
├── requirements.txt
├── build_exe.bat                # builds a Windows .exe
├── installer.iss                # optional Inno Setup installer script
├── Update Helper.txt            # how the code works / how to extend it
├── assets/
│   ├── media_center.png          # app icon source
│   └── settings_gear.png          # Settings tab icon
├── core/                          # all logic, no GUI code
│   ├── paths.py                    # dev vs. packaged .exe file locations
│   ├── config.py                   # settings storage
│   ├── history.py                  # download history storage
│   ├── playlists.py                 # playlist storage
│   ├── dependencies.py              # check/install ffmpeg and pip pkgs
│   ├── downloader.py                 # yt-dlp wrapper
│   └── utils.py                       # file moves, folder/file opening, sanitizing
├── gui/
│   ├── app.py                         # the whole window (5 tabs)
│   ├── scrollable_dropdown.py          # custom scrollable dropdown widget
│   └── dialogs.py                       # move-files / new-playlist dialogs
└── updates/                              # one script per dependency
    ├── update_ytdlp.py
    ├── update_ffmpeg.py
    ├── update_customtkinter.py
    ├── update_pillow.py
    └── update_all.py
```

## The five tabs

- **Download** - single download or a batch queue (paste many URLs, each
  auto-named from its title). Aspect ratio, quality, format, playlist and
  subtitle options, thumbnail preview, per-field clear buttons, and a Clear
  Log button.
- **Playlists** - Spotify-style: create a named playlist, add any finished
  download to it, browse/remove tracks, and open a track with your OS's default app.
- **History** - every download attempt (success, failed, or cancelled)
  with the reason it failed if it did, plus one-click folder/file opening.
- **Settings** - change your default download folder (with a prompt to
  move existing files, Select All included), video/audio defaults,
  clipboard auto-detect, theme, color, font family/size, and a bold-text
  accessibility toggle.
- **Version** - live status of every dependency (yt-dlp, customtkinter,
  Pillow, FFmpeg) with per-item Install/Update buttons and one "Update All"
  button.

## Turning this into a real Windows app

**Step 1 - Single .exe:**
1. Copy this whole folder to Windows.
2. Double-click `build_exe.bat`. It builds `dist\MediaDownloader.exe`.
3. Share just that one `.exe` - config/history/playlists/ffmpeg are all
   created next to it automatically, so nothing else on the PC gets
   touched, and there's nothing else to zip up or hunt down.

**Step 2 - Optional real installer (Start Menu, uninstaller):**
1. Install the free [Inno Setup](https://jrsoftware.org/isdl.php).
2. Open `installer.iss`, click Compile.
3. `installer_output\MediaDownloaderSetup.exe` is a normal Windows
   installer, published as "Smith Media Industries inc."

## Notes / limitations to be aware of

- Aspect-ratio filtering picks the closest matching format yt-dlp reports
  for that video rather than a guaranteed exact crop - most sources don't
  offer every ratio for every video.
- Playlists here are lightweight (named lists of file paths you build up
  yourself) rather than a full drag-and-drop reorder UI.
- DRM-protected sources (Spotify, Disney+, Hulu, Peacock, etc.) are not
  supported - see DISCLAIMER.txt.

See **Update Helper.txt** for a full walkthrough of how the code is
organized and how to change or add features yourself.

## Changelog

### 1.6.2

- Developer-unlock credentials rotated and stored as a salted PBKDF2 hash instead of plaintext.
- The one-line log echo shown at the top on non-Download tabs now mirrors the
  **bottom line of the visible log exactly** — same text and colour, and it
  respects the current log mode and the log-enabled toggle (it no longer shows
  lines that were filtered out of the log itself).

### 1.6.1

- **Drag a video thumbnail straight onto the window.** Drag a thumbnail (or a
  link) from your browser — e.g. a video on the YouTube home page — and drop it
  anywhere on Media Downloader. It pulls the real video URL out of the drop,
  puts it in the URL box, and then:
  - on the **Single Download** tab, starts the download immediately;
  - on the **Batch Queue** tab, adds the URL to the list (plain textbox or the
    dynamic URL list, whichever mode is on) without starting;
  - dropped on any other tab, fills the Single Download URL box and waits.
  Needs the `tkinterdnd2` package; if it isn't installed the app runs exactly
  as before, just without this feature.

### 1.6.0 (in development)

- **Huge playlists no longer time out.** The playlist-info lookup now streams
  entries in and only gives up if nothing arrives for a while, instead of a
  fixed 60-second cap - a playlist with tens of thousands of entries can
  finish. The Cancel button also aborts a slow lookup now.
- The FFmpeg location is looked up once and cached (was re-scanned on every
  metadata fetch and every download).
- Settings files now carry a schema version with explicit, numbered
  migrations for future changes.

### 1.5.4

Bug fixes:
- **URL Scraping "browser component" install loop.** The Playwright/Chromium
  install could fail silently and leave the app re-prompting the same popup
  forever (and a manual `playwright install` was invisible to it). Chromium
  now installs to, and loads from, a per-user app-owned folder
  (`%APPDATA%\Media Downloader\playwright-browsers`), the install runs with a
  hidden console and its result is verified before the app reports success,
  and a still-missing browser shows a plain error instead of looping.
- **"Remember maximized" now works.** Closing while maximized is remembered
  and a "Remembered" launch restores a true maximized window (it used to
  reopen as a plain floating window at maximized size).
- **"Select multiple" toolbar spacing.** The multi-select toolbar (on
  Playlists, History, Library, URL Scraping) no longer inflates its row to
  ~200px and leaves the toggle floating in a big gap while selection is off.
- URL Scraping shows its "No results yet" placeholder from first render.

Appearance / behavior:
- Removed the VLC integration - the app no longer bundles/installs VLC or has
  "Open in VLC" buttons. Files now open with your OS's default app for that
  file type (video, audio, or anything else), directly from History, Playlists
  and the Library.
- Launches maximized by default; any non-maximized launch is centered on
  screen.
- The startup logo animation now loops (grow → hold → shrink → hold → repeat).
- "URL Scraping" is the first and default sub-tab of the More tab.
- The dynamic batch-queue URL input is on by default.
- Enter now triggers the adjacent action for text+button fields that aren't
  downloads (Fetch info, Scrape, dev login, the search boxes).
- The default download folder is automatically a Media Library folder.
- The Media Library search box no longer squishes the controls beside it.

Build:
- `build_exe.bat` is now double-click-safe (runs from its own folder,
  auto-detects Python, bundles the customtkinter themes + the Playwright
  package). `installer.iss` warnings cleared; the post-install browser
  download runs as the real user.

### 1.5.3

Bug fixes:
- **Interrupted downloads no longer get stranded.** An item that was
  downloading when the app was closed used to stay frozen as "downloading"
  forever. It's now marked as a retryable failure on next launch, and the
  background daemon resumes such items (into the folder the request actually
  used, not just the default).
- **Retry button parity with the main download path.** Retrying a URL now
  passes the "cookies from browser" setting, shows the proper bot-check /
  cookie-locked popups, and wires up the Cancel button and progress bar.
  Retry-All spaces its requests out (like the batch queue) and refuses to
  run while another download is active.
- **Clipboard watcher** no longer throws once a second when the clipboard
  holds an image or files instead of text.
- **Settings and history files** are now written atomically (temp file +
  rename), so a crash mid-write can't corrupt them.
- yt-dlp minimum version raised from 2024.1.0 to 2025.1.1.

Performance:
- The History and Requests lists no longer rebuild from scratch on every
  download-progress event - refreshes are coalesced, removing the stutter
  during long batches.
- Video metadata is fetched once per download instead of two or three times.
- The startup heartbeat no longer forces a disk sync every few seconds.
- Autosave only writes `config.json` when something actually changed.
