"""
Developer access: the built-in credentials plus a way for an existing
developer to grant access to someone else on a different machine.

Granted credentials are stored in a separate, dedicated file (not
config.json) so they can be protected on their own and aren't mixed in
with ordinary user settings. This file is never bundled with the app -
it's created fresh on whichever machine a grant happens on.
"""
import hashlib
import json
import os

from core.paths import app_dir

DEV_USERNAME = "michaeltsmith2007"
DEV_PASSWORD = "tofee232"

DEV_ACCESS_PATH = os.path.join(app_dir(), "options", "dev_access.json")


def _hash(password):
    # Not reversible, and salted with a fixed app-specific string so this
    # file alone isn't directly useful even if someone got a copy of it -
    # this is a lightweight deterrent for a hobby-project credentials
    # file, not a claim of serious cryptographic security.
    return hashlib.sha256(f"media-downloader::{password}".encode("utf-8")).hexdigest()


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
    if username == DEV_USERNAME and password == DEV_PASSWORD:
        return True
    data = _load()
    entry = data["granted"].get(username)
    return bool(entry) and entry["password_hash"] == _hash(password)


def grant_access(granter_password, new_username, new_password):
    """Only works if granter_password matches the BUILT-IN dev password
    (a granted user can't themselves grant further access - keeps this
    simple and avoids an open-ended chain of trust). Returns (ok, message)."""
    if granter_password != DEV_PASSWORD:
        return False, "Granting access requires the primary developer password."
    if not new_username or not new_password:
        return False, "Both a username and password are required."
    if new_username == DEV_USERNAME:
        return False, "That username is reserved."
    data = _load()
    if new_username in data["granted"]:
        return False, "That username already has access on this machine."
    data["granted"][new_username] = {"password_hash": _hash(new_password)}
    _save(data)
    return True, f"Access granted to '{new_username}' on this machine."


def revoke_access(username):
    data = _load()
    if username in data["granted"]:
        del data["granted"][username]
        _save(data)
        return True
    return False


def list_granted_users():
    return sorted(_load()["granted"].keys())
