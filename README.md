# Media Downloader

A customtkinter GUI for `yt-dlp`, built for someone who downloads media
constantly - single-window, tabbed, with playlists, history, dependency
auto-install, and "Open in VLC" shortcuts.

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
│   ├── dependencies.py              # check/install ffmpeg + pip pkgs, detect VLC
│   ├── downloader.py                 # yt-dlp wrapper
│   └── utils.py                       # file moves, folder/file/VLC opening, sanitizing
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

### 1.6.10

Closes out the 1.6.x line and rolls the whole thing (1.6.1–1.6.10) up into one
stable release. Highlights across the line:

- **Drag a video thumbnail from your browser onto the window** to queue it (1.6.1).
- **VLC is back** — "Open in VLC" alongside "Open file"; the Version tab links you to
  get VLC instead of auto-downloading it (1.6.5).
- **Update Media Downloader from inside the Version tab** (1.6.10).
- **"View a request" is an in-app page now**, not a pop-up window (1.6.9).
- **Optional "pre-fetch file sizes" for batches** → size-based, not count-based, ETAs
  for the queue and for single downloads (1.6.8).
- **Redownload** a successful item; per-request **"Retry failed"**; the retry counter
  resumes where the request left off (1.6.7).
- Per-row **Delete** on History; a **"Save to Requests / History"** toggle; a live
  **queue count** in the dynamic Batch Queue; the top mini-log mirrors the real log
  exactly (1.6.2–1.6.6).
- Developer-unlock credentials are now a salted PBKDF2 hash, not plaintext (1.6.2).

1.6.10 itself:

- **Update Media Downloader from inside the app.** The Version tab lists
  "Media Downloader" itself at the top, with its own dedicated
  **Download / Update** button (always visible) and a **Beta** checkbox
  (off by default) right beside it. It checks the release list; when a newer
  version is out the button reads **Update** and an in-place update downloads
  the installer, launches it, and closes the app so it can be replaced.
  **Update All** includes the app update (run last, since it closes the app).
- **Beta: separate copy or update in place.** With **Beta** ticked, pressing
  the button asks whether to install the latest preview build as a **separate
  copy** (downloads the installer and shows it in Explorer — your current
  install is left alone, you pick a different folder) or to **update this copy**
  in place like a normal update.

### 1.6.9

- **"View a request" is now an in-app page, not a pop-up.** Clicking **View**
  on a request in **History → Request History** no longer opens a separate
  modal window — it swaps the request list out for the request's detail page
  in place, with a **← Back** button at the top (Escape also goes back). All
  the same controls are there: the editable title, the "Select multiple"
  toolbar with Copy/Retry Selected, and the per-URL Copy Link / Retry /
  Redownload buttons.
- **Renaming a request no longer flashes the screen.** The Rename/Save
  toggle updates the title in place instead of tearing down and rebuilding
  the whole view.
- **Faster request detail view.** Re-renders (a retry finishing, toggling a
  checkbox) now only rebuild the rows that actually changed instead of every
  row, the refresh is debounced, file sizes are read with a single stat call,
  and the multi-select toolbar is reused across renders.

### 1.6.8

- **Optional "pre-fetch file sizes" stage for batches.** New toggle in
  **Settings → Advanced → Batch Queue** ("Pre-fetch file sizes before a
  batch"), off by default. When on, a batch or full-playlist download first
  does a quick pass that fetches only each item's download size — nothing is
  downloaded yet — showing "Pre-fetching file sizes… 3/20" in the log.
- **Size-based ETAs.** When the pre-fetch data is available, the whole-queue
  "time remaining" is now *(total remaining bytes ÷ current average download
  speed)* rather than *(items left × average time per item)*, and the same
  bytes-remaining ÷ speed math drives the single-item ETA. If some items had
  no known size, the queue ETA is shown as a lower bound. With the toggle off,
  ETAs behave exactly as before.

### 1.6.7

- **Redownload a successful item.** In a request's detail view, a successful
  item now has a **Redownload** button (was a disabled "Retry") — handy when
  you've deleted the file. It fetches again into the request's original folder.
- **Retry only one request's failures.** Each request row with failed items now
  has its own **"Retry failed (N)"** button, separate from the global Retry All.
- When retrying, the queue counter now **resumes from where the request left
  off** — a 20-item request with 6 already done shows "Retry 7/20" onward, not
  "Retry 1/14".

### 1.6.6

- Each row in the **History** tab now has its own **Delete** button, so you can
  remove a single entry without selecting it first or clearing the whole list.
  It only removes the history record — the downloaded file is left alone — and
  asks for confirmation first.

### 1.6.5

- **VLC integration is back.** "Open in VLC" buttons return alongside "Open
  file" — on History rows, the Playlists track list, the Media Library results,
  and the Download tab's output row. "Open file" still opens with your OS's
  default app; the VLC button launches VLC directly.
- The **Version** tab now shows whether VLC is installed (detected via PATH,
  the standard install folders, or the VideoLAN registry key). When it's
  missing it offers a **"Get VLC"** button that opens the videolan.org download
  page (or the Microsoft Store listing) — the app no longer downloads or
  installs VLC automatically.
- If VLC isn't installed, the "Open in VLC" buttons warn gracefully with a
  link to get it rather than doing nothing.

### 1.6.4

- New **"Save to Requests / History"** toggle on the Download tab. When it's
  off, a download runs normally but is not recorded to Request History or the
  History tab. On by default.

### 1.6.3

- The **Batch Queue** dynamic URL list now shows a live count of what's queued
  ("3 URLs") as you add URLs — one at a time, pasted in bulk, or dropped in —
  and as you remove, undo, or clear them, before you press Start Queue.

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
