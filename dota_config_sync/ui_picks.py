"""Pestaña Picks: draft en vivo con recomendaciones por meta, counters, sinergias y héroes propios."""

import logging
import threading

import customtkinter as ctk

from . import dota2protracker, opendota, picks
from .theme import C
from .widgets import TabContext, card, section_label

log = logging.getLogger(__name__)

POS_LABELS = ["Pos 1", "Pos 2", "Pos 3", "Pos 4", "Pos 5"]
NO_POS = "—"
EMPTY = ""
CHIP_COLORS = {
    "": (C["card"], C["txt2"]), "g": ("#10302a", C["green"]),
    "a": ("#3a2a10", C["amber"]), "r": ("#3a1a1f", C["red"]),
}
COMBO_STYLE = {
    "fg_color": C["card"], "border_color": C["border"], "button_color": C["accent2"],
    "button_hover_color": C["accent"], "text_color": C["txt"], "dropdown_fg_color": C["bg2"],
    "dropdown_hover_color": C["card"], "dropdown_text_color": C["txt"],
}


def _text(parent, text: str, size: int = 12, color: str = C["txt2"], bold: bool = False, **kw) -> ctk.CTkLabel:
    font = ctk.CTkFont(size=size, weight="bold" if bold else "normal")
    kw.setdefault("anchor", "w")
    return ctk.CTkLabel(parent, text=text, font=font, text_color=color, **kw)


def _combo(parent, values: list[str], width: int, command) -> ctk.CTkComboBox:
    return ctk.CTkComboBox(parent, values=values, width=width, command=command,
                           font=ctk.CTkFont(size=12), **COMBO_STYLE)


class HeroPicker(ctk.CTkComboBox):
    """Combo con todos los héroes; acepta texto tipeado y lo resuelve a hero_id."""

    def __init__(self, master, on_change, **kw):
        super().__init__(master, values=[EMPTY], command=lambda _v: self._commit(), font=ctk.CTkFont(size=12),
                         **COMBO_STYLE, **kw)
        self._on_change = on_change
        self.hero_id: int | None = None
        self.set(EMPTY)
        self._entry.bind("<Return>", lambda _e: self._commit())
        self._entry.bind("<FocusOut>", lambda _e: self._commit())

    def set_heroes(self, names: list[str]):
        self.configure(values=[EMPTY, *names])

    def _commit(self):
        text = self.get().strip()
        new_id: int | None = None
        if text:
            ids, _unknown = opendota.resolve_hero_names(text)
            if not ids:
                self.configure(border_color=C["red"])
                return
            new_id = ids[0]
            self.set(opendota.get_hero_map().get(new_id, text))
        self.configure(border_color=C["border"])
        if new_id != self.hero_id:
            self.hero_id = new_id
            self._on_change()

    def clear(self):
        self.hero_id = None
        self.set(EMPTY)
        self.configure(border_color=C["border"])


class PicksTab(ctk.CTkFrame):
    def __init__(self, master, ctx: TabContext, **kw):
        super().__init__(master, fg_color="transparent", **kw)
        self.ctx = ctx
        self.data = picks.PickData()
        self._patch: str | None = None
        self._loaded_static = False
        self._personal_for: str | None = None
        self._matchups_in_flight: set[int] = set()
        self._pending_recompute: str | None = None
        self._build()
        threading.Thread(target=self._load_static, daemon=True).start()

    # ── UI ───────────────────────────────────────────────────────────────────
    def _build(self):
        self.scroll = ctk.CTkScrollableFrame(
            self, fg_color="transparent", scrollbar_button_color=C["border"],
            scrollbar_button_hover_color=C["accent2"],
        )
        self.scroll.pack(fill="both", expand=True, padx=20, pady=(12, 0))

        section_label(self.scroll, "DRAFT", pady=(4, 6))
        box, top = card(self.scroll, C["accent2"])
        box.pack(fill="x")
        top.columnconfigure(3, weight=1)

        _text(top, "Mi cuenta").grid(row=0, column=0, padx=(0, 8))
        self.acc_combo = _combo(top, ["  (cargando...)"], 220, lambda _v: self._on_account_change())
        self.acc_combo.grid(row=0, column=1, padx=(0, 14))
        _text(top, "Mi posición").grid(row=0, column=2, padx=(0, 8))
        self.pos_combo = _combo(top, POS_LABELS, 100, lambda _v: self._schedule_recompute())
        self.pos_combo.set("Pos 3")
        self.pos_combo.grid(row=0, column=3, sticky="w")
        ctk.CTkButton(
            top, text="Limpiar draft", width=110, height=28, fg_color="transparent", border_width=1,
            border_color=C["border"], text_color=C["txt2"], hover_color=C["bg2"], font=ctk.CTkFont(size=12),
            command=self._clear_draft,
        ).grid(row=0, column=4, sticky="e")

        _text(top, "Bans").grid(row=1, column=0, padx=(0, 8), pady=(8, 0))
        self.bans_entry = ctk.CTkEntry(
            top, placeholder_text="nombres separados por coma", fg_color=C["card"],
            border_color=C["border"], text_color=C["txt"], font=ctk.CTkFont(size=12),
        )
        self.bans_entry.grid(row=1, column=1, columnspan=3, sticky="ew", pady=(8, 0), padx=(0, 14))
        self.bans_entry.bind("<Return>", lambda _e: self._schedule_recompute())
        self.bans_entry.bind("<FocusOut>", lambda _e: self._schedule_recompute())
        self.data_status = _text(top, "Cargando meta...", 11, C["txt3"], anchor="e")
        self.data_status.grid(row=1, column=4, sticky="e", pady=(8, 0))

        teams = ctk.CTkFrame(self.scroll, fg_color="transparent")
        teams.pack(fill="x", pady=(12, 0))
        teams.columnconfigure((0, 1), weight=1, uniform="team")
        self.ally_pickers = self._team_column(teams, 0, "● MI EQUIPO", C["accent"], C["accent2"], enemy=False)
        self.enemy_pickers = self._team_column(teams, 1, "● ENEMIGOS", C["red"], "#6b2a3b", enemy=True)

        results = ctk.CTkFrame(self.scroll, fg_color="transparent")
        results.pack(fill="x", pady=(12, 16))
        results.columnconfigure(0, weight=3, uniform="res")
        results.columnconfigure(1, weight=2, uniform="res")

        rbox, rin = card(results)
        rbox.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.rec_title = _text(rin, "RECOMENDADOS", 10, C["txt3"], bold=True)
        self.rec_title.pack(fill="x")
        self.rec_list = ctk.CTkFrame(rin, fg_color="transparent")
        self.rec_list.pack(fill="x", pady=(6, 0))

        abox, ain = card(results)
        abox.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        _text(ain, "AVISOS DEL DRAFT", 10, C["txt3"], bold=True).pack(fill="x")
        self.alert_list = ctk.CTkFrame(ain, fg_color="transparent")
        self.alert_list.pack(fill="x", pady=(6, 0))
        w = picks.DEFAULT_WEIGHTS
        weights = (f"Pesos: meta {w['meta']:.0%} · counters {w['counters']:.0%} · "
                   f"tus héroes {w['personal']:.0%} · posición {w['position']:.0%}")
        _text(ain, weights, 10, C["txt3"], wraplength=300, justify="left").pack(fill="x", pady=(10, 0))

    def _team_column(self, parent, col: int, title: str, color: str, border: str, enemy: bool) -> list[tuple]:
        box, inner = card(parent, border)
        box.grid(row=0, column=col, sticky="nsew", padx=(0, 6) if col == 0 else (6, 0))
        inner.columnconfigure(1, weight=1)
        _text(inner, title, 10, color, bold=True).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        pickers = []
        for i in range(5):
            if enemy:
                pos = _combo(inner, [NO_POS, *POS_LABELS], 78, lambda _v: self._schedule_recompute())
                pos.configure(text_color=C["txt3"], button_color=C["accent3"], font=ctk.CTkFont(size=11))
                pos.set(NO_POS)
            else:
                pos = _text(inner, POS_LABELS[i], 11, C["txt3"], bold=True, width=60)
            pos.grid(row=i + 1, column=0, padx=(0, 8), pady=2, sticky="w")
            picker = HeroPicker(inner, on_change=self._schedule_recompute)
            picker.grid(row=i + 1, column=1, sticky="ew", pady=2)
            marker = _text(inner, "", 10, C["dst"], bold=True, width=34)
            marker.grid(row=i + 1, column=2, padx=(6, 0))
            pickers.append((pos, picker, marker))
        return pickers

    # ── Cuentas y datos ──────────────────────────────────────────────────────
    def set_accounts(self):
        accounts = self.ctx.accounts()
        names = [f"  {a['name']}" for a in accounts] or ["  (sin cuentas)"]
        self.acc_combo.configure(values=names)
        main = self.ctx.main_account()
        self.acc_combo.set(f"  {main['name']}" if main else names[0])
        self._on_account_change()

    def _selected_account(self) -> dict | None:
        name = self.acc_combo.get().strip()
        return next((a for a in self.ctx.accounts() if a["name"] == name), None)

    def _on_account_change(self):
        acc = self._selected_account()
        if not acc or acc["steam_id3"] == self._personal_for:
            return
        self._personal_for = acc["steam_id3"]
        self.data.bracket = picks.bracket_from_rank_tier(acc.get("rank_tier"))
        threading.Thread(target=self._load_personal, args=(acc,), daemon=True).start()

    def _load_personal(self, acc: dict):
        ttl = self.ctx.cfg.cache_ttl_seconds
        rows = picks.fetch_player_heroes(acc["steam_id3"], ttl)
        if self.data.bracket is None:
            profile = opendota.fetch_profile(acc["steam_id3"], ttl)
            self.data.bracket = picks.bracket_from_rank_tier(profile.get("rank_tier"))
        self.data.player_heroes = rows
        self.ctx.log(f"Picks: {len(rows)} héroes con historial para {acc['name']} "
                     f"(bracket {self.data.bracket or '?'})")
        self.after(0, self._schedule_recompute)

    def _load_static(self):
        cfg = self.ctx.cfg
        hero_map = self.ctx.hero_map()
        self.data.hero_names = dict(hero_map)
        names = sorted(hero_map.values())
        self.after(0, lambda: [p.set_heroes(names) for _, p, _ in self.ally_pickers + self.enemy_pickers])

        self.data.hero_stats = picks.fetch_hero_stats(cfg.opendota_stats_ttl_seconds)
        roles, patch, _ = dota2protracker.fetch_meta_roles(cfg.meta_grids_ttl_seconds)
        self.data.roles = roles
        grids, _ = dota2protracker.fetch_meta_hero_grids(cfg.meta_grids_ttl_seconds)
        self.data.relations = picks.parse_d2pt_relations(grids)
        self._patch = patch
        self._loaded_static = True
        self.ctx.log(f"Picks: meta {patch or '?'} listo · {len(self.data.hero_stats)} héroes con stats por bracket")
        self.after(0, self._schedule_recompute)

    def _ensure_enemy_matchups(self, enemies: list[int]):
        """Baja (o lee de caché) la tabla de cada enemigo nuevo; el recompute vuelve a correr al llegar."""
        missing = [e for e in enemies if e not in self.data.matchups and e not in self._matchups_in_flight]
        if not missing:
            return
        self._matchups_in_flight.update(missing)

        def worker():
            for e in missing:
                self.data.matchups[e] = picks.fetch_matchups(e, self.ctx.cfg.opendota_stats_ttl_seconds)
                self._matchups_in_flight.discard(e)
                self.after(0, self._schedule_recompute)

        threading.Thread(target=worker, daemon=True).start()

    # ── Estado del draft ─────────────────────────────────────────────────────
    def _state(self) -> picks.DraftState:
        pos_txt = self.pos_combo.get()
        my_pos = POS_LABELS.index(pos_txt) + 1 if pos_txt in POS_LABELS else None
        allies = [p.hero_id for _, p, _ in self.ally_pickers if p.hero_id is not None]
        enemies: list[int] = []
        enemy_pos: dict[int, int] = {}
        for pos, p, _ in self.enemy_pickers:
            if p.hero_id is None:
                continue
            enemies.append(p.hero_id)
            if pos.get() in POS_LABELS:
                enemy_pos[p.hero_id] = POS_LABELS.index(pos.get()) + 1
        bans_txt = self.bans_entry.get().strip()
        bans = opendota.resolve_hero_names(bans_txt)[0] if bans_txt else []
        return picks.DraftState(my_pos=my_pos, allies=allies, enemies=enemies, enemy_pos=enemy_pos, bans=bans)

    def _clear_draft(self):
        for _, p, _ in self.ally_pickers + self.enemy_pickers:
            p.clear()
        for pos, _, _ in self.enemy_pickers:
            pos.set(NO_POS)
        self.bans_entry.delete(0, "end")
        self._schedule_recompute()

    def _schedule_recompute(self):
        if self._pending_recompute:
            self.after_cancel(self._pending_recompute)
        self._pending_recompute = self.after(120, self._recompute)

    def _recompute(self):
        self._pending_recompute = None
        if not self.data.hero_names:
            return
        state = self._state()
        for i, (_, _, marker) in enumerate(self.ally_pickers):
            marker.configure(text="VOS" if state.my_pos == i + 1 else "")

        self._ensure_enemy_matchups(state.enemies)
        known = sum(1 for e in state.enemies if e in self.data.matchups)
        counters = f"counters {known}/{len(state.enemies)} enemigos" if state.enemies else "counters al pickear"
        pending = not self._loaded_static or known < len(state.enemies)
        bracket = picks.BRACKET_NAMES.get(self.data.bracket or 0, "?")
        meta_txt = f"Parche {self._patch or '?'}" if self._loaded_static else "Cargando meta..."
        self.data_status.configure(
            text=f"{meta_txt} · {counters} · bracket {bracket}",
            text_color=C["amber"] if pending else C["txt3"],
        )

        recs = picks.recommend(state, self.data, limit=10)
        title = f"RECOMENDADOS PARA {POS_LABELS[state.my_pos - 1].upper()}" if state.my_pos else "RECOMENDADOS"
        self.rec_title.configure(text=f"{title} · {len(state.enemies)} enemigo(s)")
        for w in self.rec_list.winfo_children():
            w.destroy()
        for i, r in enumerate(recs, 1):
            self._rec_row(i, r)

        for w in self.alert_list.winfo_children():
            w.destroy()
        alerts = picks.draft_alerts(state, self.data)
        if not alerts:
            _text(self.alert_list, "Cargá picks enemigos para ver avisos.", 11, C["txt3"]).pack(fill="x")
        for tone, text in alerts:
            row = ctk.CTkFrame(self.alert_list, fg_color=C["card"], corner_radius=8)
            row.pack(fill="x", pady=2)
            color = C["amber"] if tone == "warn" else C["green"]
            prefix = "⚠ " if tone == "warn" else "✔ "
            _text(row, prefix + text, 11, color, wraplength=300, justify="left").pack(fill="x", padx=8, pady=5)

    def _rec_row(self, rank: int, r: picks.Recommendation):
        row = ctk.CTkFrame(self.rec_list, fg_color="transparent")
        row.pack(fill="x", pady=(0, 6))
        row.columnconfigure(1, weight=1)
        _text(row, str(rank), 12, C["txt3"], bold=True, width=22).grid(row=0, column=0, rowspan=3, sticky="n")
        _text(row, r.name, 13, C["txt"], bold=True).grid(row=0, column=1, sticky="w")
        score = _text(row, f"{r.score:.0f}", 17, C["accent"], bold=True)
        score.grid(row=0, column=2, rowspan=2, sticky="e", padx=(8, 0))
        _text(row, r.reason, 11, C["txt2"], wraplength=380, justify="left").grid(row=1, column=1, sticky="w")
        chips = ctk.CTkFrame(row, fg_color="transparent")
        chips.grid(row=2, column=1, columnspan=2, sticky="w", pady=(2, 0))
        for text, tone in r.chips[:6]:
            bg, fg = CHIP_COLORS.get(tone, CHIP_COLORS[""])
            chip = ctk.CTkLabel(chips, text=text, font=ctk.CTkFont(size=10), fg_color=bg, text_color=fg,
                                corner_radius=4, padx=6, pady=1)
            chip.pack(side="left", padx=(0, 4), pady=1)
