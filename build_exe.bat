@echo off
setlocal EnableExtensions
REM Builds dist\MediaDownloader\MediaDownloader.exe with PyInstaller (--onedir).
REM Double-click this file, or run it from any terminal - it cd's to its own
REM folder first, so it doesn't matter where you launch it from.
cd /d "%~dp0"
echo Working folder: %CD%
echo.

REM --- find a Python that actually works -------------------------------------
set "PY="
py -3 -V >nul 2>&1 && set "PY=py -3"
if not defined PY ( python -V >nul 2>&1 && set "PY=python" )
if not defined PY (
  for %%P in (
    "%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%ProgramFiles%\Python313\python.exe"
    "%ProgramFiles%\Python312\python.exe"
  ) do if exist "%%~P" set PY="%%~P"
)
REM PY is now either  py -3  /  python  (a command, no quotes) or a
REM "quoted full path" - both expand correctly used unquoted as %PY% below,
REM which matters when the path contains a space (e.g. a user folder like
REM "Elder Michael Smith").
if not defined PY (
  echo.
  echo ERROR: could not find Python. Install it from https://www.python.org/downloads/
  echo         ^(tick "Add python.exe to PATH"^), then run this again.
  echo.
  pause
  exit /b 1
)
echo Using Python: %PY%
%PY% -V
echo.

REM --- dependencies -------------------------------------------------------------
echo Installing / checking dependencies...
%PY% -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 echo [warn] "pip install -r requirements.txt" reported an error - continuing anyway ^(the packages may already be installed^).
%PY% -m pip show pyinstaller >nul 2>&1 || %PY% -m pip install --disable-pip-version-check pyinstaller
%PY% -c "import PyInstaller" 2>nul
if errorlevel 1 (
  echo.
  echo ERROR: PyInstaller is not available and could not be installed. Cannot build.
  echo.
  pause
  exit /b 1
)
echo.

REM --- build -----------------------------------------------------------------
REM --collect-data customtkinter : bundle customtkinter's theme JSON files
REM                                ^(app crashes on launch without them^).
REM --collect-all tkinterdnd2    : bundle the tkdnd Tcl binaries used by the
REM                                drag-a-thumbnail-onto-the-window feature.
REM --exclude-module pygame*     : never needed by this app; keeps the build lean
REM                                if pygame happens to be in the environment.
echo Building - this takes a minute or two...
echo.
%PY% -m PyInstaller --noconfirm --onedir --windowed ^
    --name "MediaDownloader" --icon "icon.ico" ^
    --add-data "DISCLAIMER.txt;." ^
    --add-data "assets;assets" ^
    --add-data "icon.ico;." ^
    --add-data "updates;updates" ^
    --collect-data customtkinter ^
    --collect-all tkinterdnd2 ^
    --collect-all syncedlyrics ^
    --collect-submodules mutagen ^
    --exclude-module pygame ^
    --exclude-module pygame_ce ^
    main.py

echo.
if exist "dist\MediaDownloader\MediaDownloader.exe" (
    echo ============================================================
    echo Build complete:  dist\MediaDownloader\MediaDownloader.exe
    echo ============================================================
    echo Share the whole  dist\MediaDownloader\  folder - it is a --onedir
    echo build, not a single file. Runtime data ^(config, history, ffmpeg^)
    echo is created under %%APPDATA%%\Media Downloader on first run, so nothing
    echo else on the PC is touched.
    echo.
    echo To make a real installer next, compile installer.iss with Inno Setup.
) else (
    echo ############################################################
    echo Build FAILED - scroll up for the PyInstaller error.
    echo ############################################################
)
echo.
pause
endlocal
