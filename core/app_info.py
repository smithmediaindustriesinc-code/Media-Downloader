"""Single source of truth for the app's own version/release/publisher
info, shown at the top of the Version tab. Keep this in sync with
installer.iss's MyAppVersion when bumping versions - they're two
separate files (Inno Setup scripts can't import Python), but this is the
one place in the actual application to update.

APP_IS_PRERELEASE: True on a -preview / -dev build so the Version tab can
show a "pre-release" badge (#V2). Flip to False in the commit that merges
to stable for a proper release.
"""

APP_VERSION = "1.7.3.1"
APP_RELEASE_DATE = "2026-08-30"
APP_PUBLISHER = "Smith Media Industries inc."
APP_IS_PRERELEASE = True
