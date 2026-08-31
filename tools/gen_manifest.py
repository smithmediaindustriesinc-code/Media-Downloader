"""
Regenerate versions.json for the PUBLIC distribution repo.

This script reads GitHub Releases from smithmediaindustriesinc-code/Media-Downloader-Releases
and builds a version manifest consumed by:
  - The unified bootstrapper installer
  - The in-app updater

Usage:
    python tools/gen_manifest.py --out ../mdr/versions.json

The PUBLIC repo working copy is normally at <scratchpad>/mdr and the caller
commits + pushes it.

Requires: `gh` CLI authenticated.
"""
import argparse
import json
import re
import subprocess
import sys


REPO = "smithmediaindustriesinc-code/Media-Downloader-Releases"


def _version_key(v):
    """Sort key for version strings. Matches core/app_update.py::_version_key.

    '1.6.10' -> (1, 6, 10, 0); a '-preview'/'-beta'/'-rc' suffix sorts BELOW
    the same numbers without a suffix, so 1.5.4-preview < 1.5.4.
    Unparseable -> sorts lowest.
    """
    if not v:
        return (0,)
    s = str(v).strip().lstrip("vV")
    m = re.match(r"(\d+(?:\.\d+)*)(.*)$", s)
    if not m:
        return (0,)
    nums = tuple(int(x) for x in m.group(1).split("."))
    # Pad to 3 so (1, 6) and (1, 6, 0) compare equal-ish
    nums = nums + (0,) * (3 - len(nums)) if len(nums) < 3 else nums
    pre = 0 if not m.group(2).strip() else -1
    return nums + (pre,)


def fetch_releases():
    """Fetch releases from GitHub. Returns list of release dicts, or None on error."""
    try:
        cmd = ["gh", "api", "--paginate", f"repos/{REPO}/releases"]
        # encoding must be explicit - GitHub returns UTF-8, but text=True
        # otherwise decodes with the Windows locale codepage and mangles any
        # non-ASCII in release notes (em dashes, etc).
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding="utf-8", check=True)
        data = json.loads(result.stdout)
        return data if isinstance(data, list) else [data]
    except subprocess.CalledProcessError as e:
        print(f"gh error: {e.stderr}", file=sys.stderr)
        return None
    except json.JSONDecodeError as e:
        print(f"JSON parse error: {e}", file=sys.stderr)
        return None


def _extract_notes(body):
    """Extract first non-empty line from release body, stripped of markdown."""
    if not body:
        return ""
    for line in body.split("\n"):
        line = line.strip()
        if not line:
            continue
        # Remove leading markdown markers, drop trailing ** bold markers,
        # and normalise common unicode punctuation to ASCII so the manifest
        # stays plain-ASCII regardless of what the release notes used.
        line = re.sub(r"^[#\*\-\s]+", "", line).replace("**", "").strip()
        line = (line.replace("—", "-").replace("–", "-")
                    .replace("’", "'").replace("“", '"')
                    .replace("”", '"').replace("…", "..."))
        if line:
            return line[:200]
    return ""


def _find_exe_asset(assets, version, beta=False):
    """Find the installer .exe asset. Returns (asset_dict, url) or (None, None)."""
    if not assets:
        return None, None

    if beta:
        # The "-beta" side-by-side installer. Match by name only - there is
        # NO fallback: if a release has no *-beta.exe asset it simply has no
        # beta variant (do not fall back to the normal installer).
        for asset in assets:
            name = asset.get("name", "")
            if name.endswith("-beta.exe") and name.startswith("MediaDownloaderSetup"):
                return asset, asset.get("browser_download_url")
        return None, None

    # Try the exact per-version name, and also the name with any
    # -preview / -rc / -dev suffix stripped (the asset is usually
    # MediaDownloaderSetup1.7.3.1.exe even when the TAG is v1.7.3.1-preview).
    base_version = version.split("-", 1)[0]
    for cand in (f"MediaDownloaderSetup{version}.exe",
                 f"MediaDownloaderSetup{base_version}.exe"):
        for asset in assets:
            if asset.get("name") == cand:
                return asset, asset.get("browser_download_url")

    # Fall back to the first per-version .exe - a versioned name like
    # MediaDownloaderSetup1.7.3.1.exe, NOT the bare bootstrapper
    # (MediaDownloaderSetup.exe) or a -beta variant.
    for asset in assets:
        name = asset.get("name", "")
        if (name.startswith("MediaDownloaderSetup")
                and name.endswith(".exe")
                and name != "MediaDownloaderSetup.exe"
                and "-beta" not in name):
            return asset, asset.get("browser_download_url")

    return None, None


def build_entries(releases):
    """Build version entries from releases. Returns list, sorted newest-first."""
    entries = []

    for release in releases:
        tag = release.get("tag_name", "").strip()
        if not tag:
            continue

        version = tag.lstrip("vV")
        is_prerelease = release.get("prerelease", False)
        channel = "beta" if is_prerelease else "stable"

        published = release.get("published_at", "")
        date = published[:10] if published else ""

        body = release.get("body", "")
        notes = _extract_notes(body)

        assets = release.get("assets", [])

        # Find main .exe asset
        asset, url = _find_exe_asset(assets, version, beta=False)
        if not asset or not url:
            # Skip releases with no matching .exe
            continue

        # Extract sha256 from digest
        digest = asset.get("digest", "") or ""
        sha256 = ""
        if digest.startswith("sha256:"):
            sha256 = digest.split("sha256:")[-1]

        entry = {
            "version": version,
            "channel": channel,
            "date": date,
            "asset": asset.get("name", ""),
            "url": url,
            "sha256": sha256,
            "notes": notes,
        }

        # Look for -beta variant
        beta_asset, beta_url = _find_exe_asset(assets, version, beta=True)
        if beta_asset and beta_url:
            entry["beta_variant_url"] = beta_url

        entries.append(entry)

    # Sort newest-first by version key
    entries.sort(key=lambda e: _version_key(e["version"]), reverse=True)

    return entries


def main():
    parser = argparse.ArgumentParser(description="Regenerate versions.json manifest")
    parser.add_argument("--out", help="Output file (default: stdout)")
    parser.add_argument("--selftest", action="store_true", help="Run self-test with fixtures")

    args = parser.parse_args()

    if args.selftest:
        # Self-test with 3 fake releases
        fake_releases = [
            {
                "tag_name": "v1.6.10",
                "prerelease": False,
                "published_at": "2026-08-29T00:00:00Z",
                "body": "# Stable release",
                "assets": [
                    {
                        "name": "MediaDownloaderSetup1.6.10.exe",
                        "browser_download_url": "https://example.com/v1.6.10.exe",
                        "digest": "sha256:aabbccdd",
                    }
                ],
            },
            {
                "tag_name": "v1.6.10-beta",
                "prerelease": True,
                "published_at": "2026-08-28T00:00:00Z",
                "body": "Beta version",
                "assets": [
                    {
                        "name": "MediaDownloaderSetup1.6.10-beta.exe",
                        "browser_download_url": "https://example.com/v1.6.10-beta.exe",
                        "digest": None,
                    }
                ],
            },
            {
                "tag_name": "v1.6.9",
                "prerelease": False,
                "published_at": "2026-08-27T00:00:00Z",
                "body": "Previous stable",
                "assets": [
                    {
                        "name": "MediaDownloaderSetup1.6.9.exe",
                        "browser_download_url": "https://example.com/v1.6.9.exe",
                        "digest": "sha256:11223344",
                    }
                ],
            },
        ]

        entries = build_entries(fake_releases)

        # Verify order and fields
        assert len(entries) == 3, f"Expected 3 entries, got {len(entries)}"
        assert entries[0]["version"] == "1.6.10", f"First should be 1.6.10, got {entries[0]['version']}"
        assert entries[1]["version"] == "1.6.10-beta", f"Second should be 1.6.10-beta, got {entries[1]['version']}"
        assert entries[2]["version"] == "1.6.9", f"Third should be 1.6.9, got {entries[2]['version']}"

        # Verify channel
        assert entries[0]["channel"] == "stable", "1.6.10 should be stable"
        assert entries[1]["channel"] == "beta", "1.6.10-beta should be beta"

        # Verify sha256
        assert entries[0]["sha256"] == "aabbccdd", "sha256 should be extracted"
        assert entries[1]["sha256"] == "", "beta sha256 should be empty"

        print("SELFTEST PASS")
        return

    # Normal run: fetch from GitHub
    releases = fetch_releases()
    if releases is None:
        sys.exit(1)

    entries = build_entries(releases)

    output = json.dumps(entries, indent=2) + "\n"

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"Wrote {len(entries)} entries to {args.out}", file=sys.stderr)
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
