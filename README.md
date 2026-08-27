# Media Downloader

A customtkinter GUI for `yt-dlp`, built for someone who downloads media
constantly - single-window, tabbed, with playlists, history, dependency
auto-install, and VLC integration.

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
FFmpeg and VLC do NOT need to be pre-installed - the app detects them on
first launch and lets you install both with one click from the **Version**
tab (no admin rights needed; FFmpeg is dropped straight into the app's own
folder, VLC uses its official silent installer).

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
│   ├── dependencies.py              # check/install ffmpeg, vlc, pip pkgs
│   ├── downloader.py                 # yt-dlp wrapper
│   └── utils.py                       # file moves, VLC launch, sanitizing
├── gui/
│   ├── app.py                         # the whole window (5 tabs)
│   ├── scrollable_dropdown.py          # custom scrollable dropdown widget
│   └── dialogs.py                       # move-files / new-playlist dialogs
└── updates/                              # one script per dependency
    ├── update_ytdlp.py
    ├── update_ffmpeg.py
    ├── update_vlc.py
    ├── update_customtkinter.py
    ├── update_pillow.py
    └── update_all.py
```

## The five tabs

- **Download** - single download or a batch queue (paste many URLs, each
  auto-named from its title). Aspect ratio, quality, format, playlist and
  subtitle options, thumbnail preview, "Open in VLC" shortcut, per-field
  clear buttons, and a Clear Log button.
- **Playlists** - Spotify-style: create a named playlist, add any finished
  download to it, browse/remove tracks, open a track in VLC.
- **History** - every download attempt (success, failed, or cancelled)
  with the reason it failed if it did, plus one-click "open folder" / VLC.
- **Settings** - change your default download folder (with a prompt to
  move existing files, Select All included), video/audio defaults,
  clipboard auto-detect, theme, color, font family/size, and a bold-text
  accessibility toggle.
- **Version** - live status of every dependency (yt-dlp, customtkinter,
  Pillow, FFmpeg, VLC) with per-item Install/Update buttons and one
  "Update All" button.

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
- Silent VLC installs can be blocked by some locked-down/managed Windows
  machines; if that happens the installer window opens for you to click
  through manually instead of failing silently.
- Playlists here are lightweight (named lists of file paths you build up
  yourself) rather than a full drag-and-drop reorder UI.
- DRM-protected sources (Spotify, Disney+, Hulu, Peacock, etc.) are not
  supported - see DISCLAIMER.txt.

See **Update Helper.txt** for a full walkthrough of how the code is
organized and how to change or add features yourself.

## Changelog

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
