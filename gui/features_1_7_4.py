"""1.7.4 feature batch - the Settings-tab section for the download-behaviour
features (speed limit, quality rules, skip rules, repeat-URL warning, file
organization, bandwidth budget). Preset bar, history tags, stats, monitor,
scheduling and sync UI live in their own spots (see gui/app.py).

Kept in its own module so gui/app.py's already-huge _build_settings_tab only
gains a single call.
"""
import customtkinter as ctk
from tkinter import messagebox, simpledialog

from core.config import save_config
from core.downloader import set_rate_limit, set_quality_rules
from core import presets as _presets

_VIDEO_QUALITIES = ["Best", "4K / 2160p", "1440p", "1080p", "720p", "480p", "360p", "240p"]


def _save(app):
    save_config(app.cfg)


def _apply_runtime(app):
    """Push the currently-saved speed limit + quality rules into core.downloader
    so a change takes effect without a restart."""
    cfg = app.cfg
    set_rate_limit(cfg.get("speed_limit_kbps", 0) * 1024
                   if cfg.get("speed_limit_enabled") else 0)
    set_quality_rules(cfg.get("quality_rules") if cfg.get("quality_rules_enabled") else [])


# --------------------------------------------------------------------------- #
# F1: preset bar for the Download tab
# --------------------------------------------------------------------------- #
def build_preset_bar(app, parent):
    row = ctk.CTkFrame(parent, fg_color="transparent")
    ctk.CTkLabel(row, text="Preset:", font=app.font_normal).pack(side="left")
    var = ctk.StringVar(value=app.cfg.get("last_used_preset", "") or "(none)")
    menu = ctk.CTkOptionMenu(row, variable=var, width=190, font=app.font_normal,
                             values=["(none)"] + _presets.preset_names(app.cfg))
    menu.pack(side="left", padx=(6, 6))
    app._preset_menu = menu
    app._preset_var = var

    def refresh():
        menu.configure(values=["(none)"] + _presets.preset_names(app.cfg))

    def on_pick(name):
        if name and name != "(none)":
            app._apply_preset(name)

    menu.configure(command=on_pick)

    def save_current():
        name = simpledialog.askstring("Save preset", "Name this preset:", parent=parent)
        if not name or not name.strip():
            return
        _presets.save_preset(app.cfg, _presets.make_preset(name, app._capture_preset()))
        app.cfg["last_used_preset"] = name.strip()
        save_config(app.cfg)
        refresh()
        var.set(name.strip())

    def delete_current():
        name = var.get()
        if name in ("", "(none)"):
            return
        if messagebox.askyesno("Delete preset", f"Delete preset \"{name}\"?"):
            _presets.delete_preset(app.cfg, name)
            if app.cfg.get("last_used_preset") == name:
                app.cfg["last_used_preset"] = ""
            save_config(app.cfg)
            refresh()
            var.set("(none)")

    ctk.CTkButton(row, text="Save current…", width=110, font=app.font_small,
                  command=save_current).pack(side="left")
    ctk.CTkButton(row, text="✕", width=28, font=app.font_small, fg_color="gray40",
                  hover_color="gray30", command=delete_current).pack(side="left", padx=(6, 0))
    app._refresh_preset_menu = refresh
    return row


# --------------------------------------------------------------------------- #
def build_download_behaviour_section(app, parent):
    box = ctk.CTkFrame(parent)
    box.grid_columnconfigure(0, weight=1)
    row = [0]

    def sub(text):
        ctk.CTkLabel(box, text=text, font=app.font_label).grid(
            row=row[0], column=0, sticky="w", padx=12, pady=(12, 2)); row[0] += 1

    def note(text):
        ctk.CTkLabel(box, text=text, font=app.font_small, text_color="gray60",
                     wraplength=680, justify="left").grid(
            row=row[0], column=0, sticky="w", padx=12, pady=(0, 4)); row[0] += 1

    def line(widget):
        widget.grid(row=row[0], column=0, sticky="w", padx=12, pady=3); row[0] += 1

    # ---- F2: speed limiter ---------------------------------------------- #
    sub("Download speed limit")
    note("Cap how fast downloads run so a big batch doesn't saturate your "
         "connection. 0 or off = unlimited.")
    sl_enabled = ctk.BooleanVar(value=app.cfg.get("speed_limit_enabled", False))
    sl_kbps = ctk.StringVar(value=str(app.cfg.get("speed_limit_kbps", 0) or ""))

    def on_speed_change(*_):
        app.cfg["speed_limit_enabled"] = bool(sl_enabled.get())
        try:
            app.cfg["speed_limit_kbps"] = max(0, int(sl_kbps.get() or 0))
        except ValueError:
            app.cfg["speed_limit_kbps"] = 0
        _apply_runtime(app)
        _save(app)

    r = ctk.CTkFrame(box, fg_color="transparent")
    ctk.CTkSwitch(r, text="Limit to", font=app.font_normal, variable=sl_enabled,
                  command=on_speed_change).pack(side="left")
    e = ctk.CTkEntry(r, width=90, font=app.font_normal, textvariable=sl_kbps)
    e.pack(side="left", padx=(8, 4))
    e.bind("<FocusOut>", on_speed_change)
    e.bind("<Return>", on_speed_change)
    ctk.CTkLabel(r, text="KB/s", font=app.font_normal).pack(side="left")
    line(r)

    # ---- F15: content-aware quality rules ------------------------------- #
    sub("Quality by video length")
    note('Pick video quality automatically from the video\'s duration instead '
         'of one fixed setting. Rules are checked shortest-cap first; a video '
         'longer than every cap uses your normal default quality.')
    qr_enabled = ctk.BooleanVar(value=app.cfg.get("quality_rules_enabled", False))
    qr_list_frame = ctk.CTkFrame(box, fg_color="transparent")

    def render_quality_rules():
        for w in qr_list_frame.winfo_children():
            w.destroy()
        rules = app.cfg.get("quality_rules") or []
        for i, rule in enumerate(rules):
            rr = ctk.CTkFrame(qr_list_frame, fg_color=("gray92", "gray16"))
            rr.pack(fill="x", pady=2)
            cap = rule.get("max_minutes")
            ctk.CTkLabel(rr, text=("any length" if cap in (None, 0, "")
                                   else f"≤ {cap} min"),
                         font=app.font_small, width=90, anchor="w").pack(side="left", padx=(8, 4), pady=4)
            ctk.CTkLabel(rr, text="→  " + str(rule.get("quality", "?")),
                         font=app.font_small, anchor="w").pack(side="left")
            ctk.CTkButton(rr, text="✕", width=26, font=app.font_small,
                          fg_color="gray40", hover_color="gray30",
                          command=lambda idx=i: (rules.pop(idx), app.cfg.__setitem__("quality_rules", rules),
                                                 _apply_runtime(app), _save(app), render_quality_rules())
                          ).pack(side="right", padx=6)
        add = ctk.CTkFrame(qr_list_frame, fg_color="transparent")
        add.pack(fill="x", pady=(4, 0))
        cap_v = ctk.StringVar()
        q_v = ctk.StringVar(value="1080p")
        ctk.CTkEntry(add, width=70, font=app.font_small, textvariable=cap_v,
                     placeholder_text="min").pack(side="left")
        ctk.CTkLabel(add, text="min  →", font=app.font_small).pack(side="left", padx=4)
        ctk.CTkOptionMenu(add, values=_VIDEO_QUALITIES, variable=q_v,
                          width=120, font=app.font_small).pack(side="left")

        def add_rule():
            rules = app.cfg.get("quality_rules") or []
            try:
                mm = int(cap_v.get()) if cap_v.get().strip() else None
            except ValueError:
                mm = None
            rules.append({"max_minutes": mm, "quality": q_v.get()})
            app.cfg["quality_rules"] = rules
            _apply_runtime(app)
            _save(app)
            render_quality_rules()
        ctk.CTkButton(add, text="Add", width=60, font=app.font_small,
                      command=add_rule).pack(side="left", padx=6)

    def on_qr_toggle():
        app.cfg["quality_rules_enabled"] = bool(qr_enabled.get())
        _apply_runtime(app)
        _save(app)
    line(ctk.CTkSwitch(box, text="Use quality rules", font=app.font_normal,
                       variable=qr_enabled, command=on_qr_toggle))
    line(qr_list_frame)
    render_quality_rules()

    # ---- F6: pre-download skip rules ----------------------------------- #
    sub("Skip rules (batch & playlist)")
    note("Skip items before they download. Size needs \"Pre-fetch file sizes\" "
         "on (Batch Queue settings above).")
    sk_enabled = ctk.BooleanVar(value=app.cfg.get("skip_rules_enabled", False))

    def num_row(label, key, unit):
        rr = ctk.CTkFrame(box, fg_color="transparent")
        ctk.CTkLabel(rr, text=label, font=app.font_normal, width=220, anchor="w").pack(side="left")
        v = ctk.StringVar(value=str(app.cfg.get(key, 0) or ""))
        ent = ctk.CTkEntry(rr, width=80, font=app.font_normal, textvariable=v)
        ent.pack(side="left")
        ctk.CTkLabel(rr, text=unit, font=app.font_small, text_color="gray60").pack(side="left", padx=4)

        def commit(*_):
            try:
                app.cfg[key] = max(0, int(v.get() or 0))
            except ValueError:
                app.cfg[key] = 0
            _save(app)
        ent.bind("<FocusOut>", commit)
        ent.bind("<Return>", commit)
        line(rr)

    line(ctk.CTkSwitch(box, text="Apply skip rules", font=app.font_normal,
                       variable=sk_enabled,
                       command=lambda: (app.cfg.__setitem__("skip_rules_enabled", bool(sk_enabled.get())),
                                        _save(app))))
    num_row("Skip if shorter than", "skip_shorter_than_s", "seconds (0 = off)")
    num_row("Skip if longer than", "skip_longer_than_s", "seconds (0 = off)")
    num_row("Skip if larger than", "skip_larger_than_mb", "MB (0 = off)")
    num_row("Skip if below", "skip_min_height", "px tall (0 = off)")
    sk_lib = ctk.BooleanVar(value=app.cfg.get("skip_if_in_library", False))
    line(ctk.CTkSwitch(box, text="Skip if a matching file is already in the Media Library",
                       font=app.font_normal, variable=sk_lib,
                       command=lambda: (app.cfg.__setitem__("skip_if_in_library", bool(sk_lib.get())),
                                        _save(app))))

    # ---- F7: repeat-URL warning --------------------------------------- #
    sub("Repeat-download warning")
    warn_v = ctk.BooleanVar(value=app.cfg.get("warn_on_repeat_url", True))
    line(ctk.CTkSwitch(box, text="Warn before downloading a URL that's already in History",
                       font=app.font_normal, variable=warn_v,
                       command=lambda: (app.cfg.__setitem__("warn_on_repeat_url", bool(warn_v.get())),
                                        _save(app))))

    # ---- F4: post-download file organization -------------------------- #
    sub("Organize files after download")
    note("Move each finished file into sub-folders automatically. "
         "Pattern tokens: {source} {date} {title} {height} {ext} {type}")
    org_mode = ctk.StringVar(value=app.cfg.get("organize_mode", "off"))
    org_pat = ctk.StringVar(value=app.cfg.get("organize_pattern", "{source}/{date}"))
    org_auto = ctk.BooleanVar(value=app.cfg.get("organize_apply_automatically", False))

    def on_org_change(*_):
        app.cfg["organize_mode"] = org_mode.get()
        app.cfg["organize_pattern"] = org_pat.get().strip() or "{source}/{date}"
        app.cfg["organize_apply_automatically"] = bool(org_auto.get())
        _save(app)
        pat_entry.configure(state="normal" if org_mode.get() == "pattern" else "disabled")

    line(ctk.CTkOptionMenu(box, values=["off", "by_source", "by_date", "by_resolution", "pattern"],
                           variable=org_mode, width=180, font=app.font_normal,
                           command=on_org_change))
    pr = ctk.CTkFrame(box, fg_color="transparent")
    ctk.CTkLabel(pr, text="Pattern:", font=app.font_normal).pack(side="left")
    pat_entry = ctk.CTkEntry(pr, width=320, font=app.font_normal, textvariable=org_pat)
    pat_entry.pack(side="left", padx=6)
    pat_entry.bind("<FocusOut>", on_org_change)
    pat_entry.bind("<Return>", on_org_change)
    pat_entry.configure(state="normal" if org_mode.get() == "pattern" else "disabled")
    line(pr)
    line(ctk.CTkSwitch(box, text="Apply automatically after every download",
                       font=app.font_normal, variable=org_auto, command=on_org_change))

    # ---- F16: monthly bandwidth budget ------------------------------- #
    sub("Monthly data budget")
    note("Adds up bytes downloaded this month (from History) and warns - or "
         "pauses new downloads - when you reach the cap. 0 = off.")
    bb_gb = ctk.StringVar(value=str(app.cfg.get("bandwidth_budget_gb", 0) or ""))
    bb_action = ctk.StringVar(value=app.cfg.get("bandwidth_budget_action", "warn"))

    def on_bb_change(*_):
        try:
            app.cfg["bandwidth_budget_gb"] = max(0, float(bb_gb.get() or 0))
        except ValueError:
            app.cfg["bandwidth_budget_gb"] = 0
        app.cfg["bandwidth_budget_action"] = bb_action.get()
        _save(app)

    br = ctk.CTkFrame(box, fg_color="transparent")
    ctk.CTkLabel(br, text="Cap at", font=app.font_normal).pack(side="left")
    bbe = ctk.CTkEntry(br, width=80, font=app.font_normal, textvariable=bb_gb)
    bbe.pack(side="left", padx=(6, 4))
    bbe.bind("<FocusOut>", on_bb_change)
    bbe.bind("<Return>", on_bb_change)
    ctk.CTkLabel(br, text="GB/month, then", font=app.font_normal).pack(side="left", padx=(0, 6))
    ctk.CTkOptionMenu(br, values=["warn", "pause"], variable=bb_action, width=90,
                      font=app.font_normal, command=on_bb_change).pack(side="left")
    line(br)
    ctk.CTkLabel(box, text="", height=6).grid(row=row[0], column=0); row[0] += 1

    return box
