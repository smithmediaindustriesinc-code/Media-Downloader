; Optional: build a proper Windows installer (Start Menu shortcut, uninstaller,
; desktop icon) from the PyInstaller output.
;
; 1. Run build_exe.bat first so dist\MediaDownloader\MediaDownloader.exe exists
;    (this is now a --onedir build - a folder, not a single file - to avoid
;    antivirus false-positives against onefile self-extracting exes).
; 2. Install Inno Setup (free): https://jrsoftware.org/isdl.php
; 3. Open this file in Inno Setup and click "Compile" (or right-click > Compile).
; 4. The installer .exe is written to the "installer_output" folder.
;
; Everything the app needs (assets, DISCLAIMER, update scripts) is packaged
; into this single installer. Everything it creates at runtime (config.json,
; history.json, playlists.json, ffmpeg\) is written to the current user's
; %APPDATA%\Media Downloader folder, NOT next to the installed .exe - the
; default install location (Program Files) is read-only to normal users,
; so writing there would fail with a permissions error.

#define MyAppName "Media Downloader"
#define MyAppVersion "1.6.10"
#define MyAppPublisher "Smith Media Industries inc."
#define MyAppExeName "MediaDownloader.exe"
#define MyDistFolder "MediaDownloader"

[Setup]
AppId={{B6E1B5C4-8B1B-4A3E-9C2A-MEDIADOWNLOADER}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
; If Smith Media Industries has a real website, uncomment and fill these
; in - Inno Setup shows them as clickable links in Add/Remove Programs.
; A placeholder/fake URL here would be worse than no URL at all.
;AppPublisherURL=
;AppSupportURL=
;AppUpdatesURL=
AppCopyright=Copyright (C) 2026 {#MyAppPublisher}
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} Setup
VersionInfoCopyright=Copyright (C) 2026 {#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=installer_output
OutputBaseFilename=MediaDownloaderSetup
Compression=lzma2/max
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile=installer_icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
WizardStyle=modern
InfoBeforeFile=DISCLAIMER.txt
DisableWelcomePage=no
SetupLogging=yes
MinVersion=10.0.17763
ShowLanguageDialog=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
; The whole onedir build output - already includes DISCLAIMER.txt, assets,
; and the updates/ scripts inside it (PyInstaller bundled them via
; --add-data in build_exe.bat), so nothing else needs listing separately.
Source: "dist\{#MyDistFolder}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs
Source: "Update Helper.txt"; DestDir: "{app}"; Flags: ignoreversion
; Bundled FFmpeg - lets the app work immediately after install without
; every user needing to download FFmpeg separately on first run (see
; core/dependencies.py's check_ffmpeg(), which checks here as one of
; its three lookup locations). This entry expects real ffmpeg.exe/
; ffprobe.exe binaries to already be present in vendor\ffmpeg\bin\
; alongside this .iss file BEFORE compiling the installer - Inno Setup
; packages files at compile time, it can't fetch them itself.
; skipifsourcedoesntexist lets the build succeed even if that folder is
; empty (falls back to the app's existing runtime-download behavior,
; same as before this was added) rather than failing the whole compile
; over a missing optional file - NOTE: I can't compile-test Inno Setup
; scripts in this environment, so this specific line is worth a real
; test compile on your end before relying on it.
Source: "vendor\ffmpeg\bin\*"; DestDir: "{app}\ffmpeg\bin"; Flags: ignoreversion recursesubdirs skipifsourcedoesntexist

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Downloads the actual Chromium browser Playwright needs for URL
; Scraping to work at all - the playwright PACKAGE being bundled in
; the .exe is not the same as the browser itself, which is a separate
; download Playwright's own installer fetches. Without this step ever
; running, URL Scraping fails immediately the first time anyone tries
; it, with no obvious reason why - this is exactly that fix, run
; automatically once, right after installation. Needs internet access
; at install time; runs before the "launch the app now" step below so
; scraping is ready to go from the very first launch. Shown (not
; hidden) with its own status text since a several-hundred-MB browser
; download can take a little while - a silent freeze here would look
; like the installer hung.
; runasoriginaluser: on an elevated (admin) install this step must run as the
; actual signed-in user, so Playwright's browser lands in THAT user's
; %APPDATA%\Media Downloader\playwright-browsers (where the app looks for it),
; not the admin account's. The app itself hides the child console window and
; verifies the browser is usable afterwards (see core/url_scraper.py).
Filename: "{app}\{#MyAppExeName}"; Parameters: "--playwright-install"; \
    StatusMsg: "Downloading the browser component needed for URL Scraping..."; \
    Flags: waituntilterminated runasoriginaluser
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
// The app writes its own data (config.json, history.json, playlists.json,
// download_requests.json, the downloaded ffmpeg\ folder, etc) to
// %APPDATA%\Media Downloader - NOT under {app} (see the note at the top
// of this file for why). Inno Setup's generated uninstaller only ever
// removes what it itself installed under {app}, so without this [Code]
// section, that whole AppData folder would be silently left behind on
// every uninstall.
//
// BrowseForFolder is one of Inno Setup's own built-in Pascal Scripting
// functions - no external plugin/DLL needed, just call it directly.

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  AppDataPath: string;
  BackupRoot: string;
  BackupTarget: string;
  ResultCode: Integer;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    AppDataPath := ExpandConstant('{userappdata}\Media Downloader');
    if DirExists(AppDataPath) then
    begin
      if MsgBox('Media Downloader also stored settings, history, and playlists in your '
        + 'AppData folder, separately from the program files.' + #13#10 + #13#10
        + 'Would you like to save a copy of that data somewhere before it''s permanently '
        + 'deleted?', mbConfirmation, MB_YESNO) = IDYES then
      begin
        BackupRoot := '';
        if BrowseForFolder('Choose a folder to save your Media Downloader data to:', BackupRoot, False) then
        begin
          BackupTarget := BackupRoot + '\Media Downloader Backup';
          if not DirExists(BackupTarget) then
            CreateDir(BackupTarget);
          Exec('cmd.exe', '/c xcopy "' + AppDataPath + '" "' + BackupTarget + '\" /E /I /Y /Q',
            '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
        end;
      end;
      DelTree(AppDataPath, True, True, True);
    end;
  end;
end;

// On a FRESH install (AppData folder doesn't already exist - an upgrade
// over an existing install skips this, since there's nothing meaningful
// to "import" when settings already exist right there), offers to
// import a previously-exported settings file. The chosen file is copied
// to a marker location the Python app itself checks for on its very
// first launch (see core/config.py's check-and-import-pending-export
// logic) - Inno Setup can't run the app's own Python import logic
// directly, so this just stages the file for the app to pick up itself.
procedure CurStepChanged(CurStep: TSetupStep);
var
  AppDataPath: string;
  ImportSource: string;
  OptionsDir: string;
begin
  if CurStep = ssPostInstall then
  begin
    AppDataPath := ExpandConstant('{userappdata}\Media Downloader');
    if not DirExists(AppDataPath) then  // genuinely fresh - no existing settings to protect
    begin
      if MsgBox('Would you like to import settings from a previous export (a .json file saved '
        + 'from Media Downloader''s Settings tab)?', mbConfirmation, MB_YESNO) = IDYES then
      begin
        ImportSource := '';
        if GetOpenFileName('Choose a settings export file to import:', ImportSource,
          '', 'JSON files (*.json)|*.json|All files (*.*)|*.*', 'json') then
        begin
          if not DirExists(AppDataPath) then
            CreateDir(AppDataPath);
          OptionsDir := AppDataPath + '\options';
          if not DirExists(OptionsDir) then
            CreateDir(OptionsDir);
          CopyFile(ImportSource, OptionsDir + '\pending_import.json', False);
        end;
      end;
    end;
  end;
end;
