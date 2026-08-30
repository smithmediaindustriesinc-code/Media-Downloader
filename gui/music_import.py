"""The "Import from Spotify" tab + its Settings section.

Reads a Spotify playlist/album/track/artist/Liked-Songs (metadata only -
Spotify never gives a third-party app the audio), finds each track on
YouTube, and hands the confirmed YouTube URLs to the app's normal batch
queue. A post-download hook (see gui/app.py _maybe_tag_music_track) writes
the Spotify tags + cover + lyrics onto each downloaded file.

The audio is a YouTube match, NOT the Spotify master - the UI says so
unmissably and every file gets a comment tag to the same effect.
"""
import threading

import customtkinter as ctk
from tkinter import messagebox

from core import music_import
from core.config import save_config
from core.spotify_client import (SpotifyClient, SpotifyError, SpotifyAuthError,
                                 SpotifyPremiumRequired)

_BANNER = ("Media Downloader can't download from Spotify. It reads the track "
           "list, finds each song on YouTube, and downloads that - the audio "
           "and exact version may differ from the original.")

_STATUS_COLOR = {"confident": "#2fa84f", "ambiguous": "#d68910", "none": "#c0392b"}
_STATUS_TEXT = {"confident": "match", "ambiguous": "check", "none": "no match"}


def _match_cfg(app):
    return {
        "min_confidence": app.cfg.get("music_match_min_confidence", 0.55),
        "duration_tolerance_s": app.cfg.get("music_match_duration_tolerance_s", 4),
    }


def get_spotify_client(app):
    """One SpotifyClient per app, rebuilt when the Client ID / port changes."""
    cid = (app.cfg.get("spotify_client_id") or "").strip()
    port = int(app.cfg.get("spotify_redirect_port", 8888) or 8888)
    cur = getattr(app, "_spotify_client", None)
    if cur is None or cur.client_id != cid or cur.redirect_port != port:
        app._spotify_client = SpotifyClient(cid, redirect_port=port)
    return app._spotify_client


# --------------------------------------------------------------------------- #
# the tab
# --------------------------------------------------------------------------- #
def build_import_tab(app, tab):
    ui = _ImportUI(app, tab)
    app._music_import_ui = ui
    return ui


class _ImportUI:
    def __init__(self, app, tab):
        self.app = app
        self.session = None            # music_import.ImportSession
        self.row_widgets = []          # per-track row dicts
        self._cancel = None            # threading.Event during a resolve

        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(4, weight=1)

        ctk.CTkLabel(tab, text=_BANNER, font=app.font_small, wraplength=760,
                     justify="left", text_color="gray60").grid(
            row=0, column=0, sticky="ew", padx=12, pady=(12, 6))

        ctk.CTkLabel(tab, text="Paste a Spotify link, or a list of \"Artist - Title\" "
                     "lines / CSV:", font=app.font_label).grid(
            row=1, column=0, sticky="w", padx=12, pady=(6, 2))

        self.input_box = ctk.CTkTextbox(tab, height=90, font=app.font_normal)
        self.input_box.grid(row=2, column=0, sticky="ew", padx=12)

        bar = ctk.CTkFrame(tab, fg_color="transparent")
        bar.grid(row=3, column=0, sticky="ew", padx=12, pady=(8, 4))
        bar.grid_columnconfigure(3, weight=1)
        self.resolve_btn = ctk.CTkButton(bar, text="Resolve", width=110,
                                         font=app.font_normal, command=self._resolve_clicked)
        self.resolve_btn.grid(row=0, column=0)
        self.liked_btn = ctk.CTkButton(bar, text="My Liked Songs", width=130,
                                       font=app.font_normal, fg_color="transparent",
                                       border_width=1, command=self._liked_clicked)
        self.liked_btn.grid(row=0, column=1, padx=(8, 0))
        self.status_label = ctk.CTkLabel(bar, text="", font=app.font_small, anchor="w")
        self.status_label.grid(row=0, column=3, sticky="ew", padx=(12, 0))

        self.results = ctk.CTkScrollableFrame(tab, label_text="")
        self.results.grid(row=4, column=0, sticky="nsew", padx=12, pady=(4, 4))
        self.results.grid_columnconfigure(0, weight=1)

        foot = ctk.CTkFrame(tab, fg_color="transparent")
        foot.grid(row=5, column=0, sticky="ew", padx=12, pady=(2, 10))
        foot.grid_columnconfigure(1, weight=1)
        self.download_btn = ctk.CTkButton(foot, text="Download selected", height=38,
                                          font=app.font_label, state="disabled",
                                          command=self._download_clicked)
        self.download_btn.grid(row=0, column=0)
        self.summary_label = ctk.CTkLabel(foot, text="", font=app.font_small, anchor="w")
        self.summary_label.grid(row=0, column=1, sticky="ew", padx=(12, 0))

        self._build_imports_list(tab)

    # -- resolve ------------------------------------------------------- #
    def _liked_clicked(self):
        self.input_box.delete("1.0", "end")
        self.input_box.insert("1.0", "liked")
        self._resolve_clicked()

    def _resolve_clicked(self):
        text = self.input_box.get("1.0", "end").strip()
        if not text:
            messagebox.showinfo("Import", "Paste a Spotify link or a track list first.")
            return
        self.resolve_btn.configure(state="disabled", text="Resolving...")
        self.download_btn.configure(state="disabled")
        self._set_status("Resolving...", "gray60")
        for w in self.results.winfo_children():
            w.destroy()
        self.row_widgets = []
        self._cancel = threading.Event()
        threading.Thread(target=self._resolve_worker, args=(text,), daemon=True).start()

    def _resolve_worker(self, text):
        app = self.app
        client = get_spotify_client(app)

        def prog(done, total, ref):
            self.app.after(0, lambda: self._set_status(
                f"Matching {done}/{total}: {ref.title[:40]}", "gray60"))

        try:
            session = music_import.build_session(
                text, spotify_client=client, cfg=_match_cfg(app),
                progress_cb=prog, cancel_event=self._cancel)
        except SpotifyPremiumRequired as e:
            return self._resolve_failed(str(e))
        except SpotifyAuthError as e:
            return self._resolve_failed(f"{e}\n\nOpen Settings to connect Spotify.")
        except (SpotifyError, ValueError) as e:
            return self._resolve_failed(str(e))
        except Exception as e:  # never let the worker die silently
            return self._resolve_failed(f"Couldn't resolve that: {e}")
        self.app.after(0, lambda: self._show_session(session))

    def _resolve_failed(self, msg):
        self.app.after(0, lambda: (
            self.resolve_btn.configure(state="normal", text="Resolve"),
            self._set_status("", "gray60"),
            messagebox.showerror("Import", msg)))

    # -- render the resolved list ------------------------------------ #
    def _show_session(self, session):
        self.session = session
        self.resolve_btn.configure(state="normal", text="Resolve")
        n = len(session.tracks)
        conf = sum(1 for t in session.tracks if t.status == "confident")
        amb = sum(1 for t in session.tracks if t.status == "ambiguous")
        none = n - conf - amb
        self._set_status(f'"{session.name}" - {n} tracks '
                         f"({conf} matched, {amb} to check, {none} no match)", "gray60")

        auto = self.app.cfg.get("music_auto_download_confident", True)
        for i, mt in enumerate(session.tracks):
            mt.selected = bool(mt.download_url) and (mt.status == "confident" or not auto)
            self._build_row(i, mt)
        self._refresh_summary()
        self.download_btn.configure(state="normal" if any(
            m.selected for m in session.tracks) else "disabled")

    def _build_row(self, i, mt):
        app = self.app
        row = ctk.CTkFrame(self.results)
        row.grid(row=i, column=0, sticky="ew", pady=2)
        row.grid_columnconfigure(1, weight=1)

        var = ctk.BooleanVar(value=mt.selected)

        def on_toggle(m=mt, v=var):
            m.selected = v.get()
            self._refresh_summary()

        chk = ctk.CTkCheckBox(row, text="", width=24, variable=var, command=on_toggle)
        chk.grid(row=0, column=0, rowspan=2, padx=(6, 4))

        ctk.CTkLabel(row, text=f"{mt.ref.title}  -  {mt.ref.artist_str}",
                     font=app.font_normal, anchor="w").grid(
            row=0, column=1, sticky="ew", padx=4, pady=(4, 0))

        best = mt.result.best
        if best:
            sub = f"YouTube: {best.title[:60]}  ({best.channel}, {_fmt_dur(best.duration_s)})"
        elif mt.override_url:
            sub = f"YouTube: {mt.override_url}"
        else:
            sub = "no YouTube match - paste a link"
        self._row_sub = ctk.CTkLabel(row, text=sub, font=app.font_small, anchor="w",
                                     text_color="gray55")
        self._row_sub.grid(row=1, column=1, sticky="ew", padx=4, pady=(0, 4))

        badge = ctk.CTkLabel(row, text=_STATUS_TEXT.get(mt.status, mt.status),
                             font=app.font_small, width=64,
                             text_color=_STATUS_COLOR.get(mt.status, "gray50"))
        badge.grid(row=0, column=2, rowspan=2, padx=4)

        ctk.CTkButton(row, text="Pick / paste URL", width=120, font=app.font_small,
                      fg_color="transparent", border_width=1,
                      command=lambda m=mt, r=row, idx=i: self._pick_clicked(m, idx)).grid(
            row=0, column=3, rowspan=2, padx=(2, 8))

        self.row_widgets.append({"row": row, "var": var, "sub": self._row_sub,
                                 "badge": badge, "mt": mt})

    def _pick_clicked(self, mt, idx):
        opts = []
        for c in (mt.result.candidates or [])[:6]:
            opts.append(f"{c.score:.2f}  {c.title[:70]}  ({c.channel}, {_fmt_dur(c.duration_s)})")
        dlg = _PickDialog(self.app, mt.ref, opts, mt.result.candidates or [])
        self.app.wait_window(dlg)
        if dlg.chosen_url is not None:
            mt.override_url = dlg.chosen_url
            mt.selected = bool(dlg.chosen_url)
            self._rerender_row(idx)
            self._refresh_summary()

    def _rerender_row(self, idx):
        w = self.row_widgets[idx]
        mt = w["mt"]
        w["var"].set(mt.selected)
        if mt.override_url:
            w["sub"].configure(text=f"YouTube: {mt.override_url}")
        w["badge"].configure(
            text="manual" if mt.override_url else _STATUS_TEXT.get(mt.status, mt.status),
            text_color="#2fa84f" if mt.override_url else _STATUS_COLOR.get(mt.status, "gray50"))

    def _refresh_summary(self):
        if not self.session:
            return
        sel = [m for m in self.session.tracks if m.selected and m.download_url]
        self.summary_label.configure(
            text=f"{len(sel)} of {len(self.session.tracks)} tracks will download")
        self.download_btn.configure(state="normal" if sel else "disabled")

    # -- download --------------------------------------------------- #
    def _download_clicked(self):
        if not self.session:
            return
        picked = [m for m in self.session.tracks if m.selected and m.download_url]
        if not picked:
            return
        urls, tag_map = [], {}
        for m in picked:
            u = m.download_url
            urls.append(u)
            tag_map[u] = m.ref
        self.app._start_music_import_download(self.session, picked, urls, tag_map)

    # -- saved imports list ---------------------------------------- #
    def _build_imports_list(self, tab):
        wrap = ctk.CTkFrame(tab, fg_color="transparent")
        wrap.grid(row=6, column=0, sticky="ew", padx=12, pady=(0, 10))
        wrap.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(wrap, text="Previous imports", font=self.app.font_label).grid(
            row=0, column=0, sticky="w")
        self.imports_frame = ctk.CTkFrame(wrap, fg_color="transparent")
        self.imports_frame.grid(row=1, column=0, sticky="ew")
        self.imports_frame.grid_columnconfigure(0, weight=1)
        self.refresh_imports()

    def refresh_imports(self):
        for w in self.imports_frame.winfo_children():
            w.destroy()
        recs = music_import.load_imports()
        if not recs:
            ctk.CTkLabel(self.imports_frame, text="None yet.", font=self.app.font_small,
                         text_color="gray55").grid(row=0, column=0, sticky="w")
            return
        for i, rec in enumerate(reversed(recs[-15:])):
            r = ctk.CTkFrame(self.imports_frame)
            r.grid(row=i, column=0, sticky="ew", pady=2)
            r.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(r, text=f"{rec.get('name', '?')}  -  "
                         f"{len(rec.get('tracks', []))} tracks  ({rec.get('date', '')})",
                         font=self.app.font_small, anchor="w").grid(row=0, column=0, sticky="ew", padx=6)
            resyncable = bool(rec.get("spotify_id"))
            ctk.CTkButton(r, text="Re-sync", width=90, font=self.app.font_small,
                          state="normal" if resyncable else "disabled",
                          command=lambda rid=rec.get("id"): self._resync_clicked(rid)).grid(
                row=0, column=1, padx=6, pady=4)

    def _resync_clicked(self, import_id):
        self._set_status("Re-syncing...", "gray60")
        threading.Thread(target=self._resync_worker, args=(import_id,), daemon=True).start()

    def _resync_worker(self, import_id):
        app = self.app
        try:
            diff = music_import.diff_for_resync(
                import_id, get_spotify_client(app), cfg=_match_cfg(app))
        except (SpotifyError, ValueError) as e:
            return app.after(0, lambda: messagebox.showerror("Re-sync", str(e)))
        except Exception as e:
            return app.after(0, lambda: messagebox.showerror("Re-sync", f"Failed: {e}"))
        app.after(0, lambda: self._show_resync(import_id, diff))

    def _show_resync(self, import_id, diff):
        new = [m for m in diff["new"] if m.download_url]
        removed = diff["removed"]
        msg = (f"{len(new)} new track(s), {diff['unchanged']} unchanged, "
               f"{len(removed)} no longer in the playlist.\n\n")
        if not new:
            messagebox.showinfo("Re-sync", msg + "Nothing new to download.")
            self._set_status("", "gray60")
            return
        if not messagebox.askyesno("Re-sync", msg + f"Download the {len(new)} new track(s)?"):
            self._set_status("", "gray60")
            return
        rec = music_import.get_import(import_id) or {}
        sess = music_import.ImportSession(
            source_text="", kind=rec.get("kind", ""), name=rec.get("name", "re-sync"),
            spotify_id=rec.get("spotify_id", ""), snapshot_id="", tracks=new)
        urls = [m.download_url for m in new]
        tag_map = {m.download_url: m.ref for m in new}
        self.app._start_music_import_download(sess, new, urls, tag_map,
                                              existing_import_id=import_id)

    # -- helpers -------------------------------------------------- #
    def _set_status(self, text, color):
        self.status_label.configure(text=text, text_color=color)


def _fmt_dur(s):
    s = int(s or 0)
    return f"{s // 60}:{s % 60:02d}"


# --------------------------------------------------------------------------- #
# the "pick a different YouTube result" dialog
# --------------------------------------------------------------------------- #
class _PickDialog(ctk.CTkToplevel):
    def __init__(self, app, ref, option_labels, candidates):
        super().__init__(app)
        self.chosen_url = None
        self.title("Pick the right track")
        self.geometry("640x420")
        self.transient(app)
        self.grab_set()
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(self, text=f"{ref.title}  -  {ref.artist_str}",
                     font=app.font_label).grid(row=0, column=0, sticky="w", padx=12, pady=10)

        frame = ctk.CTkScrollableFrame(self)
        frame.grid(row=1, column=0, sticky="nsew", padx=12)
        frame.grid_columnconfigure(0, weight=1)
        for i, (lab, cand) in enumerate(zip(option_labels, candidates)):
            ctk.CTkButton(frame, text=lab, anchor="w", font=app.font_small,
                          fg_color="transparent", border_width=1,
                          command=lambda u=cand.url: self._choose(u)).grid(
                row=i, column=0, sticky="ew", pady=2)

        bot = ctk.CTkFrame(self, fg_color="transparent")
        bot.grid(row=2, column=0, sticky="ew", padx=12, pady=10)
        bot.grid_columnconfigure(0, weight=1)
        self.url_entry = ctk.CTkEntry(bot, placeholder_text="...or paste a YouTube URL")
        self.url_entry.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(bot, text="Use URL", width=90,
                      command=lambda: self._choose(self.url_entry.get().strip())).grid(
            row=0, column=1, padx=(8, 0))
        ctk.CTkButton(bot, text="Skip this track", width=110, fg_color="transparent",
                      border_width=1, command=lambda: self._choose("")).grid(
            row=0, column=2, padx=(8, 0))

    def _choose(self, url):
        self.chosen_url = url
        self.destroy()


# --------------------------------------------------------------------------- #
# Settings tab section
# --------------------------------------------------------------------------- #
def build_spotify_settings_section(app, parent):
    """A CTkFrame with the Spotify-import settings. Caller grids it."""
    box = ctk.CTkFrame(parent)
    box.grid_columnconfigure(1, weight=1)

    ctk.CTkLabel(box, text="Import from Spotify", font=app.font_label).grid(
        row=0, column=0, columnspan=3, sticky="w", padx=12, pady=(10, 2))
    ctk.CTkLabel(box, text="Uses your own free Spotify app (Client ID) - no password is "
                 "stored. The account needs Spotify Premium (Spotify's 2026 rule).",
                 font=app.font_small, text_color="gray60", wraplength=720, justify="left").grid(
        row=1, column=0, columnspan=3, sticky="w", padx=12, pady=(0, 8))

    ctk.CTkLabel(box, text="Client ID", font=app.font_normal).grid(
        row=2, column=0, sticky="w", padx=12, pady=4)
    cid_entry = ctk.CTkEntry(box, font=app.font_normal)
    cid_entry.insert(0, app.cfg.get("spotify_client_id", "") or "")
    cid_entry.grid(row=2, column=1, sticky="ew", padx=6, pady=4)

    ctk.CTkLabel(box, text="Redirect port", font=app.font_normal).grid(
        row=3, column=0, sticky="w", padx=12, pady=4)
    port_entry = ctk.CTkEntry(box, width=90, font=app.font_normal)
    port_entry.insert(0, str(app.cfg.get("spotify_redirect_port", 8888)))
    port_entry.grid(row=3, column=1, sticky="w", padx=6, pady=4)

    status = ctk.CTkLabel(box, text="", font=app.font_small, anchor="w")
    status.grid(row=5, column=0, columnspan=3, sticky="w", padx=12, pady=(2, 4))

    def _save_creds():
        app.cfg["spotify_client_id"] = cid_entry.get().strip()
        try:
            app.cfg["spotify_redirect_port"] = max(1024, min(65535, int(port_entry.get().strip())))
        except ValueError:
            app.cfg["spotify_redirect_port"] = 8888
        port_entry.delete(0, "end")
        port_entry.insert(0, str(app.cfg["spotify_redirect_port"]))
        save_config(app.cfg)

    def _refresh_status():
        c = get_spotify_client(app)
        if not c.client_id:
            status.configure(text="Not set up - paste a Client ID.", text_color="gray55")
        elif c.is_connected:
            status.configure(text="Connected to Spotify.", text_color="#2fa84f")
        else:
            uri = f"http://127.0.0.1:{c.redirect_port}/callback"
            status.configure(text=f"Client ID set. Add redirect URI {uri} in your Spotify "
                             f"app, then click Connect.", text_color="#d68910")

    def _connect():
        _save_creds()
        c = get_spotify_client(app)
        if not c.client_id:
            messagebox.showinfo("Spotify", "Enter your Client ID first.")
            return
        status.configure(text="Opening your browser to sign in...", text_color="gray60")

        def worker():
            try:
                c.connect()
            except SpotifyError as e:
                return app.after(0, lambda: (messagebox.showerror("Spotify", str(e)),
                                             _refresh_status()))
            except Exception as e:
                return app.after(0, lambda: (messagebox.showerror("Spotify", f"Failed: {e}"),
                                             _refresh_status()))
            app.after(0, lambda: (_refresh_status(),
                                  messagebox.showinfo("Spotify", "Connected.")))
        threading.Thread(target=worker, daemon=True).start()

    def _disconnect():
        get_spotify_client(app).disconnect()
        _refresh_status()

    btns = ctk.CTkFrame(box, fg_color="transparent")
    btns.grid(row=4, column=0, columnspan=3, sticky="w", padx=8, pady=(4, 2))
    ctk.CTkButton(btns, text="Save", width=80, command=lambda: (_save_creds(), _refresh_status())).grid(
        row=0, column=0, padx=4)
    ctk.CTkButton(btns, text="Connect", width=100, command=_connect).grid(row=0, column=1, padx=4)
    ctk.CTkButton(btns, text="Disconnect", width=100, fg_color="transparent", border_width=1,
                  command=_disconnect).grid(row=0, column=2, padx=4)
    ctk.CTkButton(btns, text="How to get a Client ID", width=170, fg_color="transparent",
                  border_width=1,
                  command=lambda: _open_help()).grid(row=0, column=3, padx=4)

    # match / tagging toggles
    tog = ctk.CTkFrame(box, fg_color="transparent")
    tog.grid(row=6, column=0, columnspan=3, sticky="w", padx=8, pady=(4, 10))
    _toggle(app, tog, 0, "Auto-download confident matches", "music_auto_download_confident")
    _toggle(app, tog, 1, "Embed album art", "music_embed_cover")
    _toggle(app, tog, 2, "Embed lyrics", "music_embed_lyrics")
    _toggle(app, tog, 3, "Add \"matched from YouTube\" comment tag", "music_tag_source_comment")

    _refresh_status()
    return box


def _toggle(app, parent, row, label, key):
    var = ctk.BooleanVar(value=bool(app.cfg.get(key, True)))

    def on_change():
        app.cfg[key] = var.get()
        save_config(app.cfg)

    ctk.CTkCheckBox(parent, text=label, font=app.font_small, variable=var,
                    command=on_change).grid(row=row, column=0, sticky="w", pady=2)


def _open_help():
    import webbrowser
    webbrowser.open("https://developer.spotify.com/dashboard")
