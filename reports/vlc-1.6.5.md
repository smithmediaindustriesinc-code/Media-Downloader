# 1.6.5 — VLC integration reimplemented (lighter)

Branch `v1.6.5-dev` off `v1.6.4-dev` (94c5cf1). Commit `19f010b`. Pushed to `origin/v1.6.5-dev`.

Reverses most of commit `0b02c4f` ("1.5.4: remove all VLC integration") **but keeps** the
direct-file-open (`open_file` / "Open file" buttons) that replaced it. The new rule: VLC is
**detection-only** — the app never downloads or installs it.

## What was restored (adapted)

### core/dependencies.py
- `WINDOWS_VLC_PATHS`, `find_vlc()`, `check_vlc()`.
- `find_vlc()` now also checks the **VideoLAN registry key**
  (`HKLM/HKCU\SOFTWARE\VideoLAN\VLC` + `WOW6432Node`, `InstallDir` value) in addition to
  PATH and the two standard `Program Files` locations.
- `check_all()` again returns a `"VLC Media Player"` row:
  `{"name": "VLC Media Player", "kind": "vlc", "pip_spec": None, "ok": <bool>, "detail": ...}`
  (same FFmpeg-style dict shape).

### core/utils.py
- Re-added `from core.dependencies import find_vlc`, `open_in_vlc(path)`,
  `VIDEO_EXTENSIONS` / `AUDIO_EXTENSIONS`, `open_media_smart(path)`.
- `open_file()` from 1.5.4 is untouched — **both `open_file` and `open_in_vlc` exist**.
- `open_in_vlc()`'s "not installed" message now points to the Version tab / videolan.org
  instead of "Use the Version tab to install it".

### gui/app.py
- `VLC_BUTTON_COLORS = {"fg_color": "#ff8800", "hover_color": "#cc6d00"}` restored.
- Added `VLC_DOWNLOAD_PAGE` (`https://www.videolan.org/vlc/`) and `VLC_STORE_PAGE`
  (`https://apps.microsoft.com/detail/xpdm1179r5zg4l`) constants.
- New **"Open in VLC" / "VLC"** button placed **next to the existing "Open file" / "Open"**
  on:
  - History rows (`_refresh_history_tab`) — "VLC" button after "Open file".
  - Playlists track list (`_select_playlist`) — "VLC" button after "Open".
  - Media Library results (`_run_library_search`) — "VLC" button after "Open"
    (columns 2–5 shifted).
  - Download tab output row — "Open in VLC" button in new column 5, after "Open file".
- `open_output_in_vlc()` — opens `last_downloaded_path` in VLC (sibling of
  `open_output_file`).
- `_open_in_vlc_or_warn(path)` — launches VLC; if not installed, a yes/no messagebox
  offers to open the download page. Never crashes.
- Version tab (`_refresh_version_tab`): the VLC row shows **green with no button** when VLC
  is detected (like every other satisfied dep), and a **"Get VLC"** button when missing.
  `_get_vlc_clicked()` — yes/no/cancel box: Yes → videolan.org, No → Microsoft Store,
  Cancel → nothing.
- `_run_dependency_action` / `_run_update_all` handle `kind == "vlc"` (report detection
  status, never install).

### updates/update_all.py
- Handles `kind == "vlc"` — reports detection status, never installs.

### README.md
- New `### 1.6.5` changelog section.
- Intro line + `core/` structure notes updated to mention VLC again.

### Version bump
- `core/app_info.py` `APP_VERSION` 1.6.4 → **1.6.5**.
- `installer.iss` `#define MyAppVersion` 1.6.4 → **1.6.5**.

## What was deliberately NOT restored

- `install_vlc()` in core/dependencies.py — the silent-installer download.
- `VLC_DOWNLOAD_URL` (`get.videolan.org/.../vlc-win64.exe`) and the
  `vlc_setup_temp.exe` download/run/retry logic.
- The VLC "Install" action / `deps.install_vlc(...)` branch in the Version tab.
- `updates/update_vlc.py` — not recreated (the optional videolan.org-pointer stub was
  judged unnecessary; `update_all.py` handles the vlc kind directly).
- `_vlc_or_warn` / `_open_playlist_in_vlc` (whole-playlist-in-VLC button) — replaced by
  the cleaner per-row `_open_in_vlc_or_warn`. The playlist header keeps only "Open folder".
- `_open_media_or_warn` was **left calling `open_file`** (OS default), not rewired to
  `open_media_smart` — so "Open file" stays OS-default and "Open in VLC" is the separate,
  explicit VLC path, per the task.

## Verification

```
py -3 -m compileall -q core gui updates main.py   → COMPILEALL OK
py -3 -c "from core.utils import open_in_vlc, find_vlc, open_file, open_media_smart;
          from core.dependencies import check_all, check_vlc; ..."
  check_all VLC entry: [{'name': 'VLC Media Player', 'kind': 'vlc', 'pip_spec': None,
                         'ok': True, 'detail': 'Found: C:\\Program Files\\VideoLAN\\VLC\\vlc.exe'}]
  check_vlc: (True, 'C:\\Program Files\\VideoLAN\\VLC\\vlc.exe')
py -3 main.py           → launched, ran 20s, no traceback (only normal Tk teardown noise on kill)
```

Build:
```
build_exe.bat                                    → dist\MediaDownloader\MediaDownloader.exe (Build complete)
ISCC.exe installer.iss                            → installer_output\MediaDownloaderSetup.exe (Successful compile)
MediaDownloader.exe (frozen)                      → GUI ran >15s, no early exit
MediaDownloader.exe --playwright-install          → exit 0
```

Installer archived to:
`Workspace\Versions\Windows Build\Version Installers\MediaDownloaderSetup1.6.5.exe`
(49,721,840 bytes)

## What needs testing (by the user, before promotion)

1. **VLC detected path** — on a machine with VLC installed: Version tab shows VLC green with
   no button; every "Open in VLC" / "VLC" button (History, Playlists, Library, Download
   output row) actually launches VLC and plays the file.
2. **VLC missing path** — on a machine *without* VLC: Version tab shows a red dot + "Get VLC"
   button; clicking it opens videolan.org (Yes) / MS Store (No). Clicking any "VLC" button
   shows the "VLC not found" prompt and opens the download page on Yes — no crash.
3. **"Open file" still works** — the OS-default "Open file" / "Open" buttons are unchanged
   and still open with the default app.
4. **Update All** on the Version tab — VLC row reports detection status, doesn't error.
5. Frozen-build check of 1–4 (registry lookup + `webbrowser.open` under PyInstaller).

Not released, no GitHub release / email / issue created — per task instructions, left for
the requester to handle after diff review. Stable untouched.
