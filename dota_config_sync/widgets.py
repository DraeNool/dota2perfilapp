"""Widgets reutilizables: barra de estado y tarjeta de cuenta."""

import customtkinter as ctk

from .theme import C, make_avatar_placeholder, medal_for_tier


class StatusBar(ctk.CTkFrame):
    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        self.configure(fg_color=C["header"], corner_radius=0, border_width=0)
        self.dot = ctk.CTkLabel(self, text="●", font=ctk.CTkFont(size=10), text_color=C["green"], width=16)
        self.dot.pack(side="left", padx=(12, 4), pady=6)
        self.msg = ctk.CTkLabel(self, text="Iniciando...", font=ctk.CTkFont(size=11), text_color=C["txt3"], anchor="w")
        self.msg.pack(side="left", fill="x", expand=True)
        self.right = ctk.CTkLabel(self, text="", font=ctk.CTkFont(size=10), text_color=C["txt3"], anchor="e")
        self.right.pack(side="right", padx=12)

    def set(self, text: str, state: str = "ok"):
        colors = {"ok": C["green"], "loading": C["amber"], "error": C["red"], "info": C["accent"]}
        dots = {"ok": "●", "loading": "◌", "error": "●", "info": "●"}
        self.dot.configure(text=dots.get(state, "●"), text_color=colors.get(state, C["green"]))
        self.msg.configure(text=text)

    def set_right(self, text: str):
        self.right.configure(text=text)


class AccountCard(ctk.CTkFrame):
    def __init__(self, master, role: str, on_change=None, **kw):
        super().__init__(master, **kw)
        self.role = role
        self.on_change = on_change
        self.accounts = []
        self.selected = None
        self.configure(
            fg_color=C["bg2"], corner_radius=12, border_width=1,
            border_color=C["src"] if role == "source" else C["dst"],
        )
        self._build()

    def _build(self):
        badge_txt = "● Cuenta principal" if self.role == "source" else "● Cuenta secundaria"
        badge_bg = C["badge_src"] if self.role == "source" else C["badge_dst"]
        badge_fg = C["accent"] if self.role == "source" else C["dst"]
        ctk.CTkLabel(
            self, text=badge_txt, font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=badge_bg, text_color=badge_fg, corner_radius=20, padx=10, pady=3,
        ).pack(anchor="w", padx=14, pady=(12, 6))

        self.combo = ctk.CTkComboBox(
            self, values=["  Seleccionar cuenta..."], command=self._on_select,
            fg_color=C["card"], border_color=C["border"], button_color=C["accent2"],
            button_hover_color=C["accent"], text_color=C["txt"], dropdown_fg_color=C["bg2"],
            dropdown_hover_color=C["card"], dropdown_text_color=C["txt"],
            font=ctk.CTkFont(size=13), width=240,
        )
        self.combo.set("  Seleccionar cuenta...")
        self.combo.pack(fill="x", padx=14, pady=(0, 10))

        preview = ctk.CTkFrame(self, fg_color=C["card"], corner_radius=10, border_width=1, border_color=C["border"])
        preview.pack(fill="x", padx=14, pady=(0, 14))
        preview.columnconfigure(1, weight=1)

        self.av_lbl = ctk.CTkLabel(
            preview, text="?", width=48, height=48, fg_color=C["bg2"], corner_radius=8,
            font=ctk.CTkFont(size=16, weight="bold"), text_color=C["txt2"],
        )
        self.av_lbl.grid(row=0, column=0, rowspan=4, padx=12, pady=12, sticky="n")

        self.name_lbl = ctk.CTkLabel(
            preview, text="Ninguna cuenta seleccionada", font=ctk.CTkFont(size=13, weight="bold"),
            text_color=C["txt2"], anchor="w",
        )
        self.name_lbl.grid(row=0, column=1, sticky="sw", padx=(0, 12), pady=(12, 0))

        self.id_lbl = ctk.CTkLabel(preview, text="", font=ctk.CTkFont(size=11), text_color=C["txt3"], anchor="w")
        self.id_lbl.grid(row=1, column=1, sticky="w", padx=(0, 12))

        self.rank_lbl = ctk.CTkLabel(preview, text="", font=ctk.CTkFont(size=11), text_color=C["txt2"], anchor="w")
        self.rank_lbl.grid(row=2, column=1, sticky="nw", padx=(0, 12), pady=(0, 4))

        self.stats_lbl = ctk.CTkLabel(
            preview, text="", font=ctk.CTkFont(size=11), text_color=C["txt2"],
            anchor="w", justify="left", wraplength=250,
        )
        self.stats_lbl.grid(row=3, column=1, sticky="nw", padx=(0, 12), pady=(0, 10))

    def load_accounts(self, accounts: list[dict]):
        self.accounts = accounts
        names = [f"  {a['name']}" for a in accounts]
        self.combo.configure(values=names if names else ["  (sin cuentas)"])

    def select_by_index(self, idx: int):
        if 0 <= idx < len(self.accounts):
            acc = self.accounts[idx]
            self.combo.set(f"  {acc['name']}")
            self._apply(acc)
            if self.on_change:
                self.on_change(acc)

    def _on_select(self, value: str):
        name = value.strip()
        acc = next((a for a in self.accounts if a["name"] == name), None)
        if acc:
            self._apply(acc)
            if self.on_change:
                self.on_change(acc)

    def _apply(self, acc: dict):
        self.selected = acc
        self.name_lbl.configure(text=acc["name"], text_color=C["txt"])
        id_text = f"SteamID64: {acc['steam_id64']}"
        login_name = acc.get("login_name")
        if login_name:
            id_text += f"  •  Usuario Steam: {login_name}"
        self.id_lbl.configure(text=id_text)

        label, color, stars = medal_for_tier(acc.get("rank_tier"))
        stars_str = " ★" * stars if stars else ""
        dota = "✔ Dota 2" if acc["has_dota"] else "✘ Sin Dota 2"
        self.rank_lbl.configure(text=f"{label}{stars_str}  •  {dota}", text_color=color)

        winrate = acc.get("competitive_winrate")
        hours = acc.get("total_hours")
        last_hero = acc.get("last_hero")
        last_res = acc.get("last_result")
        last_kda = acc.get("last_kda")
        source = acc.get("competitive_source") or "global"

        lines = [
            f"Winrate ({source}): {winrate * 100:.1f}%" if winrate is not None else "Winrate: cargando...",
            f"Horas totales: {hours:.0f} h" if hours is not None else "Horas: cargando...",
            f"Último héroe: {last_hero}" if last_hero else "Último héroe: cargando...",
            f"Último match: {last_res or 'N/D'}  |  KDA {last_kda or 'N/D'}" if last_hero else "",
        ]
        self.stats_lbl.configure(text="\n".join(line for line in lines if line), text_color=C["txt2"])

        if acc.get("avatar_img"):
            self._set_avatar(acc["avatar_img"])
        else:
            self._set_avatar(make_avatar_placeholder(acc["name"][:2], color=color))

    def _set_avatar(self, img: ctk.CTkImage):
        self.av_lbl.configure(image=img, text="")
        self.av_lbl._img_ref = img

    def refresh_selected(self):
        if self.selected:
            updated = next((a for a in self.accounts if a["steam_id3"] == self.selected["steam_id3"]), None)
            if updated:
                self.selected = updated
                self._apply(updated)
