"""
Filesystem-based playlists: a playlist IS a folder, full stop. No JSON
tracking of membership anymore - a folder's contents ARE the playlist,
so there's nothing that can ever get out of sync between "what the app
thinks is in a playlist" and what's actually on disk. Creating a
playlist is just making a folder; browsing one is just listing its
files; removing a track just takes the file back out of the folder.

If the Playlists root folder doesn't exist yet (fresh install, or it got
deleted), nothing here raises an error for that - list_playlists()
simply reports no playlists, and create_playlist() creates the root
folder itself on first use, silently, as part of creating the first
playlist.
"""
import os
import shutil

from core.utils import sanitize_filename


def ensure_playlists_root(root):
    os.makedirs(root, exist_ok=True)
    return root


def list_playlists(root):
    """Every subfolder under root is a playlist. Missing root = no
    playlists, not an error - nothing to create until the user actually
    wants one."""
    if not root or not os.path.isdir(root):
        return []
    return sorted(
        d for d in os.listdir(root)
        if os.path.isdir(os.path.join(root, d)) and not d.startswith(".")
    )


def create_playlist(root, name):
    """Returns (created_name, message). created_name is None on failure."""
    name = sanitize_filename(name)
    if not name:
        return None, "Enter a playlist name."
    ensure_playlists_root(root)
    path = os.path.join(root, name)
    if os.path.isdir(path):
        return None, "A playlist with that name already exists."
    os.makedirs(path)
    return name, "Playlist created."


def delete_playlist(root, name, delete_files=False, dest_dir=None):
    """delete_files=False (the default) moves the playlist's files to
    dest_dir instead of deleting them - the playlist grouping goes away,
    the actual media doesn't. dest_dir defaults to the parent (Playlists)
    folder if not given, matching the original behavior - pass a
    different folder (e.g. the Archived Content folder, or Videos/Music,
    or a folder the user picked) to redirect where the files land
    instead. Pass delete_files=True for a real, permanent delete of the
    folder and everything in it (dest_dir is ignored in that case)."""
    path = os.path.join(root, name)
    if not os.path.isdir(path):
        return False
    if delete_files:
        shutil.rmtree(path)
        return True

    target = dest_dir or os.path.dirname(path)
    os.makedirs(target, exist_ok=True)
    for f in os.listdir(path):
        src = os.path.join(path, f)
        if not os.path.isfile(src):
            continue
        dst = _unique_dest(os.path.join(target, f))
        shutil.move(src, dst)
    try:
        os.rmdir(path)
    except OSError:
        pass  # non-empty (e.g. a stray subfolder) - leave it rather than error
    return True


def playlist_path(root, name):
    return os.path.join(root, name)


def playlist_contents(root, name):
    """Filenames (not full paths) of every file directly inside the
    playlist folder."""
    path = playlist_path(root, name)
    if not os.path.isdir(path):
        return []
    return sorted(f for f in os.listdir(path) if os.path.isfile(os.path.join(path, f)))


def _unique_dest(dest):
    if not os.path.exists(dest):
        return dest
    directory, filename = os.path.split(dest)
    base, ext = os.path.splitext(filename)
    n = 1
    candidate = dest
    while os.path.exists(candidate):
        candidate = os.path.join(directory, f"{base} ({n}){ext}")
        n += 1
    return candidate


def add_file_to_playlist(root, name, filepath):
    """Copies (not moves) filepath into the playlist folder - a file
    being 'in a playlist' shouldn't make it disappear from wherever it
    normally lives (Videos/Music). Returns the new path, or None if the
    source file doesn't exist."""
    if not filepath or not os.path.isfile(filepath):
        return None
    ensure_playlists_root(root)
    dest_dir = playlist_path(root, name)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, os.path.basename(filepath))
    if os.path.abspath(dest) == os.path.abspath(filepath):
        return dest
    dest = _unique_dest(dest)
    shutil.copy2(filepath, dest)
    return dest


def remove_file_from_playlist(root, name, filename):
    """Takes a file back OUT of a playlist folder by moving it up into
    the parent (Playlists) folder - 'remove from playlist' never deletes
    the actual file, only ungroups it."""
    src = os.path.join(playlist_path(root, name), filename)
    if not os.path.isfile(src):
        return False
    parent = root
    dest = _unique_dest(os.path.join(parent, filename))
    shutil.move(src, dest)
    return True


def import_folder_as_playlist(root, source_folder):
    """Turns an existing folder somewhere on disk into a new playlist -
    named after the folder itself (deduplicated if that name's already
    taken, same as create_playlist), with every file in it COPIED (not
    moved - the original folder is left untouched) into the new playlist
    folder. Subfolders inside source_folder aren't recursed into, same as
    everywhere else in this module treats a playlist as a flat folder of
    files.

    Returns (playlist_name, copied_count, message). playlist_name is None
    on failure (message explains why)."""
    if not source_folder or not os.path.isdir(source_folder):
        return None, 0, "That folder doesn't exist."

    base_name = sanitize_filename(os.path.basename(os.path.normpath(source_folder))) or "Imported Playlist"
    ensure_playlists_root(root)
    name = base_name
    n = 1
    while os.path.isdir(os.path.join(root, name)):
        n += 1
        name = f"{base_name} ({n})"

    dest_dir = os.path.join(root, name)
    os.makedirs(dest_dir)

    copied = 0
    skipped = 0
    for filename in sorted(os.listdir(source_folder)):
        src = os.path.join(source_folder, filename)
        if not os.path.isfile(src):
            continue  # flat, like every other playlist - subfolders aren't imported
        dest = _unique_dest(os.path.join(dest_dir, filename))
        try:
            shutil.copy2(src, dest)
            copied += 1
        except (OSError, shutil.Error):
            skipped += 1

    if copied == 0 and skipped == 0:
        message = f"Playlist '{name}' created, but that folder had no files directly in it to import."
    elif skipped:
        message = f"Playlist '{name}' created - {copied} file(s) imported, {skipped} couldn't be copied."
    else:
        message = f"Playlist '{name}' created with {copied} file(s) imported."
    return name, copied, message
