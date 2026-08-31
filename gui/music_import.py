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
# The Spotify dialog (replaces the old Import tab). One per app, hidden when
# closed so it re-opens instantly with its state intact.
# --------------------------------------------------------------------------- #
def open_spotify_dialog(app):
    dlg = getattr(app, "_spotify_dialog", None)
    if dlg is None or not dlg.winfo_exists():
        dlg = SpotifyDialog(app)
        app._spotify_dialog = dlg
    dlg.show()
    return dlg


def _get_import_ui(app):
    """The _ImportUI, creating the (hidden) dialog if needed. Used by the
    Download-tab Spotify handoff, which only shows the dialog if some tracks
    need a manual pick."""
    dlg = getattr(app, "_spotify_dialog", None)
    if dlg is None or not dlg.winfo_exists():
        dlg = SpotifyDialog(app)
        app._spotify_dialog = dlg
        dlg.withdraw()
    return dlg.ui


class SpotifyDialog(ctk.CTkToplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Import from Spotify")
        self.geometry("840x720")
        self.minsize(640, 520)
        self.protocol("WM_DELETE_WINDOW", self.hide)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 4))
        top.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(top, text=_BANNER, font=app.font_small, wraplength=780,
                     justify="left", text_color="gray60").grid(row=0, column=0, sticky="w")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=0, pady=0)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)
        self.ui = _ImportUI(app, body, host=self)
        app._music_import_ui = self.ui

    def show(self):
        self.deiconify()
        self.lift()
        self.focus_force()
        try:
            self.ui.refresh_my_playlists()
        except Exception:
            pass

    def hide(self):
        self.withdraw()


class _ImportUI:
    def __init__(self, app, parent, host=None):
        self.app = app
        self.host = host
        self.session = None            # music_import.ImportSession
        self.row_widgets = []          # per-track row dicts
        self._cancel = None            # threading.Event during a resolve

        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(5, weight=1)

        # -- top: search (left) | your playlists (right), equal height ----- #
        top = ctk.CTkFrame(parent, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
        top.grid_columnconfigure(0, weight=1, uniform="mi")
        top.grid_columnconfigure(1, weight=1, uniform="mi")
        _COL_H = 190

        # left: search
        left = ctk.CTkFrame(top, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        left.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(left, text="Search Spotify", font=app.font_label).grid(
            row=0, column=0, columnspan=3, sticky="w")
        self.search_entry = ctk.CTkEntry(left, font=app.font_normal,
                                         placeholder_text="song or playlist name")
        self.search_entry.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.search_entry.bind("<Return>", lambda e: self._search_clicked())
        ctk.CTkButton(left, text="✕", width=28, font=app.font_small, fg_color="gray40",
                      hover_color="gray30",
                      command=lambda: self.search_entry.delete(0, "end")).grid(
            row=1, column=1, padx=(4, 0), pady=(4, 0))
        ctk.CTkButton(left, text="Search", width=70, font=app.font_small,
                      command=self._search_clicked).grid(row=1, column=2, padx=(4, 0), pady=(4, 0))
        self.search_frame = ctk.CTkScrollableFrame(left, height=_COL_H, label_text="")
        self.search_frame.grid(row=2, column=0, columnspan=3, sticky="nsew", pady=(4, 0))
        self.search_frame.grid_columnconfigure(0, weight=1)

        # right: your playlists
        right = ctk.CTkFrame(top, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        right.grid_columnconfigure(0, weight=1)
        hdr = ctk.CTkFrame(right, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(hdr, text="Your Spotify playlists", font=app.font_label).grid(
            row=0, column=0, sticky="w")
        ctk.CTkButton(hdr, text="Refresh", width=80, font=app.font_small,
                      command=self.refresh_my_playlists).grid(row=0, column=1)
        self.playlists_frame = ctk.CTkScrollableFrame(right, height=_COL_H, label_text="")
        self.playlists_frame.grid(row=1, column=0, sticky="nsew", pady=(4 + 6, 0))
        self.playlists_frame.grid_columnconfigure(0, weight=1)

        # -- paste box ---------------------------------------------------- #
        ctk.CTkLabel(parent, text="...or paste a Spotify link / an \"Artist - Title\" "
                     "list / CSV:", font=app.font_label).grid(
            row=1, column=0, sticky="w", padx=12, pady=(10, 2))

        pbox = ctk.CTkFrame(parent, fg_color="transparent")
        pbox.grid(row=2, column=0, sticky="ew", padx=12)
        pbox.grid_columnconfigure(0, weight=1)
        self.input_box = ctk.CTkTextbox(pbox, height=64, font=app.font_normal)
        self.input_box.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(pbox, text="✕", width=28, font=app.font_small, fg_color="gray40",
                      hover_color="gray30",
                      command=lambda: self.input_box.delete("1.0", "end")).grid(
            row=0, column=1, padx=(4, 0), sticky="n")

        bar = ctk.CTkFrame(parent, fg_color="transparent")
        bar.grid(row=3, column=0, sticky="ew", padx=12, pady=(8, 4))
        bar.grid_columnconfigure(2, weight=1)
        self.resolve_btn = ctk.CTkButton(bar, text="Resolve", width=110,
                                         font=app.font_normal, command=self._resolve_clicked)
        self.resolve_btn.grid(row=0, column=0)
        self.status_label = ctk.CTkLabel(bar, text="", font=app.font_small, anchor="w")
        self.status_label.grid(row=0, column=2, sticky="ew", padx=(12, 0))

        self.results = ctk.CTkScrollableFrame(parent, label_text="")
        self.results.grid(row=5, column=0, sticky="nsew", padx=12, pady=(4, 4))
        self.results.grid_columnconfigure(0, weight=1)

        foot = ctk.CTkFrame(parent, fg_color="transparent")
        foot.grid(row=6, column=0, sticky="ew", padx=12, pady=(2, 10))
        foot.grid_columnconfigure(1, weight=1)
        self.download_btn = ctk.CTkButton(foot, text="Download selected", height=38,
                                          font=app.font_label, state="disabled",
                                          command=self._download_clicked)
        self.download_btn.grid(row=0, column=0)
        self.summary_label = ctk.CTkLabel(foot, text="", font=app.font_small, anchor="w")
        self.summary_label.grid(row=0, column=1, sticky="ew", padx=(12, 0))

        self._build_imports_list(parent)
        self.refresh_my_playlists()

    # -- your playlists --------------------------------------------------- #
    def refresh_my_playlists(self):
        for w in self.playlists_frame.winfo_children():
            w.destroy()
        client = get_spotify_client(self.app)
        if not client.is_connected:
            ctk.CTkLabel(self.playlists_frame,
                         text="Connect Spotify in Settings -> Advanced -> Import from Spotify.",
                         font=self.app.font_small, text_color="gray55").grid(
                row=0, column=0, sticky="w")
            return
        ctk.CTkLabel(self.playlists_frame, text="Loading...", font=self.app.font_small,
                     text_color="gray55").grid(row=0, column=0, sticky="w")
        threading.Thread(target=self._load_my_playlists_worker, daemon=True).start()

    def _load_my_playlists_worker(self):
        app = self.app
        try:
            items = get_spotify_client(app).list_my_playlists()
        except (SpotifyError, ValueError) as e:
            msg = str(e)
            return app.after(0, lambda: self._render_my_playlists([], msg))
        except Exception as e:
            msg = f"Couldn't load playlists: {e}"
            return app.after(0, lambda: self._render_my_playlists([], msg))
        app.after(0, lambda: self._render_my_playlists(items, None))

    def _render_my_playlists(self, items, err):
        for w in self.playlists_frame.winfo_children():
            w.destroy()
        if err:
            ctk.CTkLabel(self.playlists_frame, text=err, font=self.app.font_small,
                         text_color="#c0392b", wraplength=760, justify="left").grid(
                row=0, column=0, sticky="w")
            return
        for i, pl in enumerate(items):
            row = ctk.CTkFrame(self.playlists_frame)
            row.grid(row=i, column=0, sticky="ew", pady=1)
            row.grid_columnconfigure(0, weight=1)
            n = pl.get("tracks_total")
            sub = f"  ({n} tracks)" if isinstance(n, int) else ""
            ctk.CTkLabel(row, text=pl["name"] + sub, font=self.app.font_small,
                         anchor="w").grid(row=0, column=0, sticky="ew", padx=6)
            ref = "liked" if pl["kind"] == "saved" else f"spotify:playlist:{pl['id']}"
            ctk.CTkButton(row, text="Download", width=90, font=self.app.font_small,
                          command=lambda r=ref: self.resolve_and_autoqueue(r)).grid(
                row=0, column=1, padx=6, pady=3)

    # -- search -------------------------------------------------------- #
    def _search_clicked(self):
        q = self.search_entry.get().strip()
        for w in self.search_frame.winfo_children():
            w.destroy()
        if not q:
            return
        client = get_spotify_client(self.app)
        if not client.is_connected:
            ctk.CTkLabel(self.search_frame, text="Connect Spotify first (Settings -> Advanced).",
                         font=self.app.font_small, text_color="gray55").grid(row=0, column=0, sticky="w")
            return
        ctk.CTkLabel(self.search_frame, text="Searching...", font=self.app.font_small,
                     text_color="gray55").grid(row=0, column=0, sticky="w")
        threading.Thread(target=self._search_worker, args=(q,), daemon=True).start()

    def _search_worker(self, q):
        app = self.app
        try:
            res = get_spotify_client(app).search(q, kinds=("track", "playlist"), limit=15)
        except (SpotifyError, ValueError) as e:
            msg = str(e)
            return app.after(0, lambda: self._render_search(None, msg))
        except Exception as e:
            msg = f"Search failed: {e}"
            return app.after(0, lambda: self._render_search(None, msg))
        app.after(0, lambda: self._render_search(res, None))

    def _render_search(self, res, err):
        for w in self.search_frame.winfo_children():
            w.destroy()
        if err:
            ctk.CTkLabel(self.search_frame, text=err, font=self.app.font_small,
                         text_color="#c0392b", wraplength=360, justify="left").grid(
                row=0, column=0, sticky="w")
            return
        r = 0
        pls = res.get("playlists") or []
        if pls:
            ctk.CTkLabel(self.search_frame, text="Playlists", font=self.app.font_small,
                         text_color="gray50").grid(row=r, column=0, sticky="w", pady=(2, 0)); r += 1
        for pl in pls:
            row = ctk.CTkFrame(self.search_frame)
            row.grid(row=r, column=0, sticky="ew", pady=1); r += 1
            row.grid_columnconfigure(0, weight=1)
            n = pl.get("tracks_total")
            sub = f"  ({n})" if isinstance(n, int) else ""
            ctk.CTkLabel(row, text=(pl["name"] + sub)[:44], font=self.app.font_small,
                         anchor="w").grid(row=0, column=0, sticky="ew", padx=6)
            ctk.CTkButton(row, text="Download", width=84, font=self.app.font_small,
                          command=lambda pid=pl["id"]: self.resolve_and_autoqueue(
                              f"spotify:playlist:{pid}")).grid(row=0, column=1, padx=6, pady=2)
        tracks = [t for t in (res.get("tracks") or []) if getattr(t, "spotify_id", "")]
        if tracks:
            ctk.CTkLabel(self.search_frame, text="Songs", font=self.app.font_small,
                         text_color="gray50").grid(row=r, column=0, sticky="w", pady=(4, 0)); r += 1
        for tr in tracks:
            row = ctk.CTkFrame(self.search_frame)
            row.grid(row=r, column=0, sticky="ew", pady=1); r += 1
            row.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(row, text=f"{tr.title} - {tr.artist_str}"[:44],
                         font=self.app.font_small, anchor="w").grid(row=0, column=0, sticky="ew", padx=6)
            ctk.CTkButton(row, text="Download", width=84, font=self.app.font_small,
                          command=lambda sid=tr.spotify_id: self.resolve_and_autoqueue(
                              f"spotify:track:{sid}")).grid(row=0, column=1, padx=6, pady=2)
        if not pls and not tracks:
            ctk.CTkLabel(self.search_frame, text="No results.", font=self.app.font_small,
                         text_color="gray55").grid(row=0, column=0, sticky="w")

    # -- resolve ------------------------------------------------------- #
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

    # -- called from the Download tab (paste a Spotify link there) ---- #
    def resolve_and_autoqueue(self, text):
        """Resolve `text` (one Spotify link, or several - a list, or newline-
        separated - #S4), queue every confident match into the batch queue
        right away, and - only if some tracks still need a manual pick - switch
        to this tab with those loaded so the user can sort them out. Runs on a
        worker; safe to call from the Download tab."""
        if isinstance(text, (list, tuple)):
            sources = [str(t).strip() for t in text if str(t).strip()]
        else:
            sources = [ln.strip() for ln in str(text).splitlines() if ln.strip()]
        display = "\n".join(sources)
        self.input_box.delete("1.0", "end")
        self.input_box.insert("1.0", display)
        for w in self.results.winfo_children():
            w.destroy()
        self.row_widgets = []
        self._cancel = threading.Event()
        self._set_status(
            "Reading the Spotify track list..." if len(sources) == 1
            else f"Reading {len(sources)} Spotify links...", "gray60")
        threading.Thread(target=self._autoqueue_worker, args=(sources,), daemon=True).start()

    def _autoqueue_worker(self, sources):
        app = self.app
        client = get_spotify_client(app)
        if isinstance(sources, str):
            sources = [sources]

        def prog(done, total, ref):
            app.after(0, lambda: app._threadsafe_log(
                f"Spotify import: matching {done}/{total} - {ref.title[:40]}"))

        def fail(msg):
            app.after(0, lambda: (self._set_status("", "gray60"),
                                  messagebox.showerror("Spotify import", msg)))

        try:
            session = music_import.build_combined_session(
                sources, spotify_client=client, cfg=_match_cfg(app),
                progress_cb=prog, cancel_event=self._cancel)
        except (SpotifyError, ValueError) as e:
            return fail(str(e))
        except Exception as e:
            return fail(f"Couldn't read that Spotify link: {e}")
        app.after(0, lambda: self._autoqueue_finish(session))

    def _autoqueue_finish(self, session):
        confident, review = [], []
        for mt in session.tracks:
            if mt.download_url and mt.status == "confident":
                confident.append(mt)
            else:
                review.append(mt)

        if confident:
            urls = [m.download_url for m in confident]
            tag_map = {m.download_url: m.ref for m in confident}
            self.app._start_music_import_download(session, confident, urls, tag_map)

        if not review:
            if not confident:
                messagebox.showinfo("Spotify import",
                                    "Couldn't match any of those tracks on YouTube.")
            return

        # Load just the unmatched/uncertain ones for a manual pass.
        self.session = music_import.ImportSession(
            source_text=session.source_text, kind=session.kind, name=session.name,
            spotify_id=session.spotify_id, snapshot_id=session.snapshot_id, tracks=review)
        self.resolve_btn.configure(state="normal", text="Resolve")
        self._set_status(f'"{session.name}": {len(confident)} queued, '
                         f"{len(review)} need you to pick a match below", "#d68910")
        for i, mt in enumerate(review):
            mt.selected = bool(mt.download_url)
            self._build_row(i, mt)
        self._refresh_summary()
        if self.host is not None:
            self.host.show()
        messagebox.showinfo(
            "Spotify import",
            f"{len(confident)} track(s) are downloading now.\n\n"
            f"{len(review)} couldn't be matched confidently - pick a result (or paste a "
            f"YouTube URL) for each in this window, then hit \"Download selected\".")

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
    def _build_imports_list(self, parent):
        wrap = ctk.CTkFrame(parent, fg_color="transparent")
        wrap.grid(row=7, column=0, sticky="ew", padx=12, pady=(0, 10))
        wrap.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(wrap, text="Previous imports (re-sync a playlist to get new tracks)",
                     font=self.app.font_label).grid(row=0, column=0, sticky="w")
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
            msg = str(e)
            return app.after(0, lambda: messagebox.showerror("Re-sync", msg))
        except Exception as e:
            msg = f"Failed: {e}"
            return app.after(0, lambda: messagebox.showerror("Re-sync", msg))
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


def looks_like_spotify(text):
    """True if `text` is a Spotify link / URI / the word 'liked'."""
    try:
        from core.spotify_client import parse_spotify_ref
        parse_spotify_ref((text or "").strip())
        return True
    except Exception:
        return False


def try_handle_download_spotify(app, lines):
    """Called from the Download tab's start_single_download / start_batch_download
    BEFORE they hand anything to yt-dlp.

    Returns:
      "not_spotify" - no Spotify links; caller proceeds normally.
      "handled"     - every line was a Spotify link; this took over
                      (resolve -> queue confident matches; if some need a
                      manual pick the Spotify window opens). Caller must stop.
      "mixed"       - Spotify links AND other URLs together; caller should show
                      the returned message and stop.
    """
    items = [ln.strip() for ln in lines if ln and ln.strip()]
    if not items:
        return "not_spotify"
    spotify = [x for x in items if looks_like_spotify(x)]
    if not spotify:
        return "not_spotify"
    if len(spotify) != len(items):
        messagebox.showinfo(
            "Spotify link",
            "Mixing a Spotify link with other URLs in one go isn't supported.\n\n"
            'Put the Spotify link(s) in on their own, or use "Import from Spotify".')
        return "mixed"
    # #S4: one or several Spotify links pipeline the same way - resolve each,
    # pool the tracks, match on YouTube, queue confident matches, and open the
    # Spotify window only for the ones that still need a manual pick.
    _get_import_ui(app).resolve_and_autoqueue(spotify)
    return "handled"


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

    ctk.CTkLabel(box, text="Reads a Spotify track list and downloads each song from YouTube. "
                 "Uses your own free Spotify app (Client ID) - no password is stored. The "
                 "account needs Spotify Premium (Spotify's 2026 rule).",
                 font=app.font_small, text_color="gray60", wraplength=720, justify="left").grid(
        row=1, column=0, columnspan=3, sticky="w", padx=12, pady=(10, 8))

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
                msg = str(e)
                return app.after(0, lambda: (messagebox.showerror("Spotify", msg),
                                             _refresh_status()))
            except Exception as e:
                msg = f"Failed: {e}"
                return app.after(0, lambda: (messagebox.showerror("Spotify", msg),
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
    ctk.CTkButton(btns, text="Set up / walkthrough", width=170,
                  command=lambda: SpotifySetupDialog(app, cid_entry, port_entry,
                                                     _save_creds, _refresh_status)).grid(
        row=0, column=3, padx=4)

    # match / tagging toggles
    tog = ctk.CTkFrame(box, fg_color="transparent")
    tog.grid(row=6, column=0, columnspan=3, sticky="w", padx=8, pady=(4, 10))
    _toggle(app, tog, 0, "Auto-download confident matches", "music_auto_download_confident")
    _toggle(app, tog, 1, "Embed album art", "music_embed_cover")
    _toggle(app, tog, 2, "Embed lyrics", "music_embed_lyrics")
    _toggle(app, tog, 3, "Add \"matched from YouTube\" comment tag", "music_tag_source_comment")
    _toggle(app, tog, 4, "Download the music video instead of audio-only "
            "(uses the normal video quality / aspect / subtitle settings)",
            "music_import_as_video", default=False)

    _refresh_status()
    return box


def _toggle(app, parent, row, label, key, default=True):
    var = ctk.BooleanVar(value=bool(app.cfg.get(key, default)))

    def on_change():
        app.cfg[key] = var.get()
        save_config(app.cfg)

    ctk.CTkCheckBox(parent, text=label, font=app.font_small, variable=var,
                    command=on_change).grid(row=row, column=0, sticky="w", pady=2)


class SpotifySetupDialog(ctk.CTkToplevel):
    """A step-by-step walkthrough for connecting Spotify: it spells out
    exactly what to click on Spotify's site, gives a one-click copy of the
    redirect URI (the step people always get wrong), and has the Client ID
    field + Connect button right here so it can be done without hunting
    around the Settings page."""

    STEPS = [
        ("1.  Open the Spotify Developer Dashboard",
         "Click the button below. Sign in with your normal Spotify account - it "
         "must be a Spotify Premium account (Spotify blocked free accounts from "
         "the API in 2026)."),
        ("2.  Create an app",
         'On the dashboard click "Create app". The App name and description can '
         'be anything (e.g. "My Media Downloader"). Tick the Developer Terms box '
         'and click Save.'),
        ("3.  Add the redirect URI  (the important bit)",
         'Open your new app, go to Settings, find "Redirect URIs", and paste the '
         'address below EXACTLY - it must match character-for-character. Click '
         '"Add", then Save at the bottom of the page.'),
        ("4.  Copy your Client ID",
         'Still on the app page (Settings or the top of the app\'s dashboard '
         'page), copy the "Client ID" value. It is a long string of letters and '
         'numbers. (You do NOT need the Client secret.)'),
        ("5.  Paste it here and connect",
         "Paste the Client ID into the box below, click Connect, and approve the "
         "prompt in your browser. That's it - you only do this once."),
    ]

    def __init__(self, app, cid_entry, port_entry, save_creds, refresh_status):
        super().__init__(app)
        self.app = app
        self._save_creds = save_creds
        self._refresh_status = refresh_status
        self._outer_cid = cid_entry
        self._outer_port = port_entry
        self.title("Connect Spotify - step by step")
        self.geometry("620x620")
        self.transient(app)
        self.grab_set()
        self.grid_columnconfigure(0, weight=1)

        port = int(app.cfg.get("spotify_redirect_port", 8888) or 8888)
        self.redirect_uri = f"http://127.0.0.1:{port}/callback"

        wrap = ctk.CTkScrollableFrame(self)
        wrap.grid(row=0, column=0, sticky="nsew", padx=14, pady=12)
        wrap.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        r = 0
        ctk.CTkLabel(wrap, text="Media Downloader can't download from Spotify - no app can. "
                     "This connection just lets it read your playlist track lists; each song "
                     "is then downloaded from YouTube.", font=app.font_small,
                     text_color="gray60", wraplength=540, justify="left").grid(
            row=r, column=0, sticky="w", pady=(0, 10)); r += 1

        for title, body in self.STEPS:
            ctk.CTkLabel(wrap, text=title, font=app.font_label, anchor="w").grid(
                row=r, column=0, sticky="w", pady=(8, 2)); r += 1
            ctk.CTkLabel(wrap, text=body, font=app.font_small, wraplength=540,
                         justify="left", anchor="w").grid(row=r, column=0, sticky="w"); r += 1

            if title.startswith("1."):
                ctk.CTkButton(wrap, text="Open the Spotify Developer Dashboard", width=280,
                              command=lambda: _open_url("https://developer.spotify.com/dashboard")).grid(
                    row=r, column=0, sticky="w", pady=(4, 2)); r += 1
            if title.startswith("3."):
                uri_row = ctk.CTkFrame(wrap, fg_color="transparent")
                uri_row.grid(row=r, column=0, sticky="ew", pady=(4, 2)); r += 1
                uri_row.grid_columnconfigure(0, weight=1)
                box = ctk.CTkEntry(uri_row, font=app.font_small)
                box.insert(0, self.redirect_uri)
                box.configure(state="readonly")
                box.grid(row=0, column=0, sticky="ew")
                ctk.CTkButton(uri_row, text="Copy", width=70,
                              command=self._copy_uri).grid(row=0, column=1, padx=(6, 0))

        ctk.CTkLabel(wrap, text="Client ID", font=app.font_normal).grid(
            row=r, column=0, sticky="w", pady=(12, 2)); r += 1
        self.cid = ctk.CTkEntry(wrap, font=app.font_normal)
        self.cid.insert(0, app.cfg.get("spotify_client_id", "") or "")
        self.cid.grid(row=r, column=0, sticky="ew"); r += 1

        self.status = ctk.CTkLabel(wrap, text="", font=app.font_small, anchor="w",
                                   wraplength=540, justify="left")
        self.status.grid(row=r, column=0, sticky="w", pady=(6, 2)); r += 1

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 12))
        ctk.CTkButton(bar, text="Save & Connect", command=self._connect).pack(side="left")
        ctk.CTkButton(bar, text="Close", fg_color="transparent", border_width=1,
                      command=self.destroy).pack(side="left", padx=(8, 0))

    def _copy_uri(self):
        try:
            self.clipboard_clear()
            self.clipboard_append(self.redirect_uri)
            self.status.configure(text="Redirect URI copied - paste it into Spotify.",
                                  text_color="#2fa84f")
        except Exception:
            pass

    def _connect(self):
        cid = self.cid.get().strip()
        self.app.cfg["spotify_client_id"] = cid
        self._outer_cid.delete(0, "end")
        self._outer_cid.insert(0, cid)
        self._save_creds()
        client = get_spotify_client(self.app)
        if not client.client_id:
            self.status.configure(text="Enter your Client ID first.", text_color="#c0392b")
            return
        self.status.configure(text="Opening your browser to sign in...", text_color="gray60")

        def worker():
            try:
                client.connect()
            except SpotifyError as e:
                msg = str(e)
                return self.app.after(0, lambda: self.status.configure(
                    text=msg, text_color="#c0392b"))
            except Exception as e:
                msg = f"Failed: {e}"
                return self.app.after(0, lambda: self.status.configure(
                    text=msg, text_color="#c0392b"))
            self.app.after(0, self._connected)

        threading.Thread(target=worker, daemon=True).start()

    def _connected(self):
        self.status.configure(text="Connected. You can close this window.",
                              text_color="#2fa84f")
        try:
            self._refresh_status()
        except Exception:
            pass


def _open_url(url):
    import webbrowser
    webbrowser.open(url)
