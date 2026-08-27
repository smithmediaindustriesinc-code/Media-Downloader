@echo off
REM Run this from inside the VideoDownloaderApp folder on Windows.

python -m pip install -r requirements.txt
python -m pip install pyinstaller

python -m PyInstaller --noconfirm --onedir --windowed ^
    --name "MediaDownloader" --icon "icon.ico" ^
    --add-data "DISCLAIMER.txt;." ^
    --add-data "assets;assets" ^
    --add-data "icon.ico;." ^
    --add-data "updates;updates" ^
    main.py

if exist "dist\MediaDownloader\MediaDownloader.exe" (
    echo.
    echo Build complete. Find MediaDownloader.exe inside dist\MediaDownloader\
    echo (this is a folder build, not a single file - see the note below).
    echo config.json, history.json, playlists.json and the ffmpeg\ folder
    echo will be created under %%APPDATA%%\Media Downloader on first run -
    echo that works even if the app is later installed to Program Files.
    echo.
    echo NOTE: this is now built as --onedir instead of --onefile. A onefile
    echo .exe self-extracts to a temp folder every time it runs, which is
    echo exactly the behavior antivirus/Windows Defender heuristics often
    echo flag and silently kill - with no error message, no crash log,
    echo nothing - because the kill happens at the OS level, outside the
    echo app entirely. --onedir avoids that self-extraction step, so it
    echo starts faster AND is far less likely to get silently terminated.
    echo Share the whole dist\MediaDownloader\ folder, not just the .exe.
) else (
    echo.
    echo Build FAILED - scroll up for the PyInstaller error.
)
pause
