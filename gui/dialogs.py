"""Small modal dialogs used by the Settings and Playlists tabs."""
import customtkinter as ctk


class MoveFilesDialog(ctk.CTkToplevel):
    """Shown when the user changes the default download folder. Lets them
    pick which existing files to move to the new location, with a
    Select All convenience button."""

    def __init__(self, master, files, font_normal, font_label, on_confirm):
        super().__init__(master)
        self.title("Move existing files?")
        self.geometry("420x480")
        self.minsize(360, 320)
        self.grab_set()
        self.on_confirm = on_confirm

        ctk.CTkLabel(self, text="Move files to the new folder?", font=font_label).pack(
            anchor="w", padx=15, pady=(15, 4))
        ctk.CTkLabel(self, text="Choose which files should move with you.",
                     font=font_normal, text_color="gray60").pack(anchor="w", padx=15, pady=(0, 10))

        top_row = ctk.CTkFrame(self, fg_color="transparent")
        top_row.pack(fill="x", padx=15)
        self.select_all_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(top_row, text="Select all", font=font_normal, variable=self.select_all_var,
                        command=self._toggle_all).pack(anchor="w")

        self.scroll = ctk.CTkScrollableFrame(self)
        self.scroll.pack(fill="both", expand=True, padx=15, pady=10)

        self.file_vars = {}
        for name in files:
            var = ctk.BooleanVar(value=True)
            ctk.CTkCheckBox(self.scroll, text=name, font=font_normal, variable=var).pack(anchor="w", pady=2)
            self.file_vars[name] = var

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=15, pady=(0, 15))
        ctk.CTkButton(btn_row, text="Move selected", font=font_normal,
                      command=self._confirm).pack(side="right", padx=(10, 0))
        ctk.CTkButton(btn_row, text="Skip (leave files where they are)", font=font_normal,
                      fg_color="gray40", hover_color="gray30",
                      command=self.destroy).pack(side="right")

    def _toggle_all(self):
        state = self.select_all_var.get()
        for var in self.file_vars.values():
            var.set(state)

    def _confirm(self):
        selected = [name for name, var in self.file_vars.items() if var.get()]
        self.on_confirm(selected)
        self.destroy()


class NewPlaylistDialog(ctk.CTkToplevel):
    def __init__(self, master, font_normal, font_label, on_create):
        super().__init__(master)
        self.title("New Playlist")
        self.geometry("340x160")
        self.grab_set()
        self.on_create = on_create

        ctk.CTkLabel(self, text="Playlist name", font=font_label).pack(anchor="w", padx=15, pady=(20, 4))
        self.name_entry = ctk.CTkEntry(self, font=font_normal, placeholder_text="Road trip mix")
        self.name_entry.pack(fill="x", padx=15)
        self.name_entry.focus_set()
        self.name_entry.bind("<Return>", lambda e: self._confirm())

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=15, pady=15)
        ctk.CTkButton(btn_row, text="Create", font=font_normal, command=self._confirm).pack(
            side="right", padx=(10, 0))
        ctk.CTkButton(btn_row, text="Cancel", font=font_normal, fg_color="gray40",
                      hover_color="gray30", command=self.destroy).pack(side="right")

    def _confirm(self):
        name = self.name_entry.get().strip()
        if name:
            self.on_create(name)
            self.destroy()
