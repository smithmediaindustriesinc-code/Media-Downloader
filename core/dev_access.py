"""
Developer access: the built-in credentials plus a way for an existing
developer to grant access to someone else on a different machine.

Granted credentials are stored in a separate, dedicated file (not
config.json) so they can be protected on their own and aren't mixed in
with ordinary user settings. This file is never bundled with the app -
it's created fresh on whichever machine a grant happens on.

The built-in password is NOT stored in plaintext. It is kept as a
PBKDF2-HMAC-SHA256 digest with a random per-install-time salt and a high
iteration count (stdlib only). Granted accounts use the same scheme, one
random salt per entry. Old-format granted entries ({"password_hash": ...},
fixed-salt SHA-256) are still accepted for backward compatibility so an
existing dev_access.json keeps working after an app update.
"""
import hashlib
import hmac
import json
import os
import secrets

from core.paths import app_dir

DEV_USERNAME = "dev"

# --- Built-in developer password (hashed, not plaintext) --------------------
# The login is:  username "dev" / password "password".
# To change the built-in password, run this exact one-liner and paste the
# three printed values into _DEV_PW_SALT / _DEV_PW_HASH / _DEV_PW_ITERS below:
#
#   py -3 -c "import secrets,hashlib; s=secrets.token_bytes(16); i=200000; print('salt=',s.hex()); print('hash=',hashlib.pbkdf2_hmac('sha256', input('new password: ').encode(), s, i).hex()); print('iters=',i)"
#
_DEV_PW_SALT = "c7e4ed9e19e73970fe8bab97f341c56e"
_DEV_PW_HASH = "a1c9fb6e8d2d369e38ab3850198a241caf5f0b9f7d123ca4fcb50f246d0288ea"
_DEV_PW_ITERS = 200000
# --------------------------------------------------------------------------

DEV_ACCESS_PATH = os.path.join(app_dir(), "options", "dev_access.json")


def _hash(password):
    # Legacy fixed-salt SHA-256. Kept ONLY to read old dev_access.json
    # granted entries written by earlier app versions - never used for new
    # writes. New entries use _pbkdf2() below.
    return hashlib.sha256(f"media-downloader::{password}".encode("utf-8")).hexdigest()


def _pbkdf2(password, salt_hex, iters):
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), iters
    ).hex()


def _verify(password, salt_hex, hash_hex, iters):
    """Constant-time check of `password` against a stored PBKDF2 digest."""
    try:
        candidate = _pbkdf2(password, salt_hex, iters)
    except Exception:
        return False
    return hmac.compare_digest(candidate, hash_hex)


def _new_entry(password):
    """Build a fresh granted-account record: random salt + PBKDF2 digest."""
    salt_hex = secrets.token_hex(16)
    return {
        "salt": salt_hex,
        "hash": _pbkdf2(password, salt_hex, _DEV_PW_ITERS),
        "iters": _DEV_PW_ITERS,
    }


def _verify_entry(entry, password):
    """Verify a password against one granted-account record, accepting both
    the new PBKDF2 format and the legacy fixed-salt SHA-256 format."""
    if not entry:
        return False
    if "hash" in entry and "salt" in entry:
        return _verify(password, entry["salt"], entry["hash"],
                       int(entry.get("iters", _DEV_PW_ITERS)))
    # Legacy: {"password_hash": "<fixed-salt sha256>"}
    if "password_hash" in entry:
        return hmac.compare_digest(entry["password_hash"], _hash(password))
    return False


def _load():
    if os.path.exists(DEV_ACCESS_PATH):
        try:
            with open(DEV_ACCESS_PATH, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"granted": {}}


def _save(data):
    os.makedirs(os.path.dirname(DEV_ACCESS_PATH), exist_ok=True)
    with open(DEV_ACCESS_PATH, "w") as f:
        json.dump(data, f, indent=2)


def check_credentials(username, password):
    """True if username/password matches either the built-in dev account
    or any granted account on this machine."""
    if username == DEV_USERNAME and _verify(password, _DEV_PW_SALT,
                                            _DEV_PW_HASH, _DEV_PW_ITERS):
        return True
    data = _load()
    entry = data.get("granted", {}).get(username)
    return _verify_entry(entry, password)


def grant_access(granter_password, new_username, new_password):
    """Only works if granter_password matches the BUILT-IN dev password
    (a granted user can't themselves grant further access - keeps this
    simple and avoids an open-ended chain of trust). Returns (ok, message)."""
    if not _verify(granter_password, _DEV_PW_SALT, _DEV_PW_HASH, _DEV_PW_ITERS):
        return False, "Granting access requires the primary developer password."
    if not new_username or not new_password:
        return False, "Both a username and password are required."
    if new_username == DEV_USERNAME:
        return False, "That username is reserved."
    data = _load()
    data.setdefault("granted", {})
    if new_username in data["granted"]:
        return False, "That username already has access on this machine."
    data["granted"][new_username] = _new_entry(new_password)
    _save(data)
    return True, f"Access granted to '{new_username}' on this machine."


def revoke_access(username):
    data = _load()
    if username in data.get("granted", {}):
        del data["granted"][username]
        _save(data)
        return True
    return False


def list_granted_users():
    return sorted(_load().get("granted", {}).keys())
