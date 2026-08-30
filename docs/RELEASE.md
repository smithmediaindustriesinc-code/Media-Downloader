# Release process

Two GitHub repos:

- **`smithmediaindustriesinc-code/Media-Downloader`** — PRIVATE, the source. Must
  stay private (`core/dev_access.py` ships the dev-unlock salt+hash).
- **`smithmediaindustriesinc-code/Media-Downloader-Releases`** — PUBLIC, source-free.
  Holds the built installers as release assets + `versions.json`. The unified
  bootstrapper and the in-app updater read from here (no auth needed).

## Cutting version `X.Y.Z`

1. On `vX.Y.Z-dev`, set `core/app_info.py` `APP_VERSION` and `installer.iss`
   `#define MyAppVersion` to `X.Y.Z`. Add a `### X.Y.Z` changelog block to
   `README.md`.
2. **Build the app + both installer variants:**
   ```
   build_exe.bat
   "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" installer.iss
   "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" /DBETA installer.iss
   ```
   -> `installer_output\MediaDownloaderSetup.exe`   (rename to `MediaDownloaderSetupX.Y.Z.exe`)
   -> `installer_output\MediaDownloaderSetup-beta.exe` (rename to `MediaDownloaderSetupX.Y.Z-beta.exe`)
   The `-beta` build is "Media Downloader Beta": own AppId / install dir / Start-menu
   entry / `%APPDATA%\Media Downloader Beta` / uninstaller; it drops `instance.flag`
   so the app uses the Beta data folder. It's the target of the in-app updater's
   "install the beta as a separate copy" option.
3. Smoke-test the frozen `dist\MediaDownloader\MediaDownloader.exe` (launches,
   `--playwright-install` exits 0).
4. `git archive` a source zip: `Media_DownloaderX.Y.Z.zip`.
5. **Publish** (from the interactive session — `gh release create` is blocked for
   sub-agents, and pushes to `stable`/`main` need the user per the test-gate):
   - PRIVATE repo: `gh release create vX.Y.Z <installer> <installer-beta> <src-zip>
     --repo .../Media-Downloader [--prerelease | --latest]`
   - PUBLIC repo: same assets, same flag.
   - Prereleases (`--prerelease`) = beta channel. A non-prerelease release
     (`--latest`) requires the user to have tested that exact build first
     (the hard test-gate).
6. **Regenerate the manifest:**
   ```
   python tools/gen_manifest.py --out <path-to-public-repo-checkout>/versions.json
   ```
   then commit + push it in the public repo. `gen_manifest.py` reads the public
   repo's releases via `gh api` and writes version / channel / date / asset / url /
   `sha256` (from the asset digest) / notes / `beta_variant_url` (the `-beta` asset).
7. `Latest Working Version/` snapshot = `git archive` of `stable` (only after a
   stable merge).

## The bootstrapper (`bootstrapper.iss`)

`MediaDownloaderSetup.exe` on the PUBLIC repo's **latest** release is the
bootstrapper, NOT a per-version installer. Rebuild + re-upload it **only when
`bootstrapper.iss` itself changes** (its own `BootVersion`):
```
"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" bootstrapper.iss
```
It reads `versions.json`, shows the version picker (+ "show beta" checkbox),
downloads the chosen per-version installer, verifies its SHA-256, runs it silently.

## Test-gate (HARD RULE)

Nothing reaches `stable` / `main` — no merge, push, fast-forward, tag, or
non-prerelease release — until the user says in chat they tested THAT build.
"go" / "sounds good" / approving a plan do not count.
