"""Pestaña Picks: draft con grilla de retratos, buscador y recomendaciones resaltadas."""

import logging
import threading

import customtkinter as ctk

from . import dota2protracker, hero_images, opendota, picks
from .theme import C
from .widgets import TabContext, card, section_label

log = logging.getLogger(__name__)

POS_LABELS = ["Pos 1", "Pos 2", "Pos 3", "Pos 4", "Pos 5"]
NO_POS = "—"
SIDES = ("Mi equipo", "Enemigos")
ATTR_ORDER = (("str", "FUERZA"), ("agi", "AGILIDAD"), ("int", "INTELIGENCIA"), ("all", "UNIVERSAL"))
TILE = (64, 36)
TILES_PER_ROW = 10
CHIP_COLORS = {
    "": (C["card"], C["txt2"]), "g": ("#10302a", C["green"]),
    "a": ("#3a2a10", C["amber"]), "r": ("#3a1a1f", C["red"]),
}
COMBO_STYLE = {
    "fg_color": C["card"], "border_color": C["border"], "button_color": C["accent2"],
    "button_hover_color": C["accent"], "text_color": C["txt"], "dropdown_fg_color": C["bg2"],
    "dropdown_hover_color": C["card"], "dropdown_text_color": C["txt"],
}
ENEMY_RED = "#6b2a3b"


def _text(parent, text: str, size: int = 12, color: str = C["txt2"], bold: bool = False, **kw) -> ctk.CTkLabel:
    font = ctk.CTkFont(size=size, weight="bold" if bold else "normal")
    kw.setdefault("anchor", "w")
    return ctk.CTkLabel(parent, text=text, font=font, text_color=color, **kw)


def _combo(parent, values: list[str], width: int, command) -> ctk.CTkComboBox:
    return ctk.CTkComboBox(parent, values=values, width=width, command=command,
                           font=ctk.CTkFont(size=12), **COMBO_STYLE)


class Slot(ctk.CTkFrame):
    """Un pick: retrato + nombre + (opcional) posición + quitar."""

    def __init__(self, master, on_remove, on_change, **kw):
        super().__init__(master, fg_color=C["card"], corner_radius=8, **kw)
        self.hero_id: int | None = None
        self.columnconfigure(1, weight=1)
        self.img = ctk.CTkLabel(self, text="", width=TILE[0], height=TILE[1], fg_color=C["bg2"], corner_radius=4)
        self.img.grid(row=0, column=0, padx=(6, 8), pady=5)
        self.name = _text(self, "vacío", 12, C["txt3"])
        self.name.grid(row=0, column=1, sticky="ew")
        self.pos = _combo(self, [NO_POS, *POS_LABELS], 82, lambda _v: on_change())
        self.pos.set(NO_POS)
        self.pos.grid(row=0, column=2, padx=(0, 6))
        self.pos.grid_remove()
        self.remove_btn = ctk.CTkButton(
            self, text="✕", width=26, height=26, fg_color="transparent", text_color=C["txt3"],
            hover_color=C["bg2"], font=ctk.CTkFont(size=12), command=on_remove,
        )
        self.remove_btn.grid(row=0, column=3, padx=(0, 4))

    def set_hero(self, hero_id: int, name: str, image: ctk.CTkImage | None):
        self.hero_id = hero_id
        self.name.configure(text=name, text_color=C["txt"])
        self.img.configure(image=image, text="" if image else name[:2].upper())

    def clear(self):
        self.hero_id = None
        self.name.configure(text="vacío", text_color=C["txt3"])
        self.img.configure(image=None, text="")
        self.pos.set(NO_POS)

    def show_pos(self, visible: bool):
        if visible:
            self.pos.grid()
        else:
            self.pos.grid_remove()

    def pos_value(self) -> int | None:
        return POS_LABELS.index(self.pos.get()) + 1 if self.pos.get() in POS_LABELS else None


class PicksTab(ctk.CTkFrame):
    def __init__(self, master, ctx: TabContext, **kw):
        super().__init__(master, fg_color="transparent", **kw)
        self.ctx = ctx
        self.data = picks.PickData()
        self.catalog: dict[int, dict] = {}
        self.npc_to_id: dict[str, int] = {}
        self.tiles: dict[int, ctk.CTkButton] = {}
        self.attr_frames: dict[str, tuple[ctk.CTkLabel, ctk.CTkFrame]] = {}
        self.slots: dict[str, list[Slot]] = {}
        self.side = SIDES[0]
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
        top.columnconfigure(5, weight=1)
        _text(top, "Mi cuenta").grid(row=0, column=0, padx=(0, 8))
        self.acc_combo = _combo(top, ["  (cargando...)"], 200, lambda _v: self._on_account_change())
        self.acc_combo.grid(row=0, column=1, padx=(0, 14))
        _text(top, "Mi posición").grid(row=0, column=2, padx=(0, 8))
        self.pos_combo = _combo(top, POS_LABELS, 96, lambda _v: self._schedule_recompute())
        self.pos_combo.set("Pos 3")
        self.pos_combo.grid(row=0, column=3, padx=(0, 14))
        self.pos_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            top, text="Posiciones de los picks", variable=self.pos_var, command=self._toggle_positions,
            font=ctk.CTkFont(size=12), text_color=C["txt2"], fg_color=C["accent2"], hover_color=C["accent3"],
            border_color=C["border"], checkbox_width=18, checkbox_height=18,
        ).grid(row=0, column=4, padx=(0, 14))
        ctk.CTkButton(
            top, text="Limpiar draft", width=110, height=28, fg_color="transparent", border_width=1,
            border_color=C["border"], text_color=C["txt2"], hover_color=C["bg2"], font=ctk.CTkFont(size=12),
            command=self._clear_draft,
        ).grid(row=0, column=6, sticky="e")
        self.data_status = _text(top, "Cargando meta...", 11, C["txt3"])
        self.data_status.grid(row=1, column=0, columnspan=7, sticky="w", pady=(8, 0))

        teams = ctk.CTkFrame(self.scroll, fg_color="transparent")
        teams.pack(fill="x", pady=(12, 0))
        teams.columnconfigure((0, 1), weight=1, uniform="team")
        self.side_markers: dict[str, ctk.CTkLabel] = {}
        self._team_column(teams, 0, SIDES[0], C["accent"], C["accent2"])
        self._team_column(teams, 1, SIDES[1], C["red"], ENEMY_RED)

        gbox, gin = card(self.scroll)
        gbox.pack(fill="x", pady=(12, 0))
        head = ctk.CTkFrame(gin, fg_color="transparent")
        head.pack(fill="x")
        head.columnconfigure(1, weight=1)
        _text(head, "HÉROES · click para agregar a", 10, C["txt3"], bold=True).grid(row=0, column=0, padx=(0, 8))
        self.side_switch = ctk.CTkSegmentedButton(
            head, values=list(SIDES), command=self._set_side, font=ctk.CTkFont(size=12, weight="bold"),
            selected_color=C["accent2"], selected_hover_color=C["accent3"], unselected_color=C["card"],
            unselected_hover_color=C["bg2"], fg_color=C["bg2"], text_color=C["txt"],
        )
        self.side_switch.set(self.side)
        self.side_switch.grid(row=0, column=0, columnspan=2, sticky="w", padx=(190, 0))
        self.search = ctk.CTkEntry(
            head, placeholder_text="Buscar héroe…  (Enter agrega el primero)", width=280, fg_color=C["card"],
            border_color=C["border"], text_color=C["txt"], font=ctk.CTkFont(size=12),
        )
        self.search.grid(row=0, column=2, sticky="e")
        self.search.bind("<KeyRelease>", lambda _e: self._layout_grid())
        self.search.bind("<Return>", lambda _e: self._add_first_visible())
        self.hover = _text(gin, " ", 11, C["txt2"])
        self.hover.pack(fill="x", pady=(6, 0))
        self.grid_body = ctk.CTkFrame(gin, fg_color="transparent")
        self.grid_body.pack(fill="x")
        _text(self.grid_body, "Cargando héroes...", 11, C["txt3"]).pack(fill="x", pady=8)
        legend = ("Borde verde: top 3 recomendados · violeta: top 10 · teal: en tu equipo · rojo: enemigo")
        _text(gin, legend, 10, C["txt3"]).pack(fill="x", pady=(6, 0))

        results = ctk.CTkFrame(self.scroll, fg_color="transparent")
        results.pack(fill="x", pady=(12, 16))
        results.columnconfigure(0, weight=3, uniform="res")
        results.columnconfigure(1, weight=2, uniform="res")
        rbox, rin = card(results)
        rbox.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.rec_title = _text(rin, "RECOMENDADOS", 10, C["txt3"], bold=True)
        self.rec_title.pack(fill="x")
        _text(rin, "click en una fila para agregarlo a tu equipo", 10, C["txt3"]).pack(fill="x")
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

    def _team_column(self, parent, col: int, side: str, color: str, border: str):
        box, inner = card(parent, border)
        box.grid(row=0, column=col, sticky="nsew", padx=(0, 6) if col == 0 else (6, 0))
        head = ctk.CTkFrame(inner, fg_color="transparent")
        head.pack(fill="x", pady=(0, 6))
        title = _text(head, f"● {side.upper()}", 10, color, bold=True)
        title.pack(side="left")
        marker = _text(head, "← agregando acá", 10, C["dst"], bold=True, anchor="e")
        marker.pack(side="right")
        self.side_markers[side] = marker
        for wdg in (box, inner, head, title):
            wdg.bind("<Button-1>", lambda _e, s=side: self._set_side(s))
        slots = []
        for i in range(5):
            slot = Slot(inner, on_remove=lambda s=side, i=i: self._remove(s, i), on_change=self._schedule_recompute)
            slot.pack(fill="x", pady=2)
            slots.append(slot)
        self.slots[side] = slots
        self._refresh_markers()

    def _build_grid(self):
        for wdg in self.grid_body.winfo_children():
            wdg.destroy()
        for attr, label in ATTR_ORDER:
            lbl = _text(self.grid_body, label, 10, C["txt3"], bold=True)
            frame = ctk.CTkFrame(self.grid_body, fg_color="transparent")
            self.attr_frames[attr] = (lbl, frame)
        for hid, info in sorted(self.catalog.items(), key=lambda kv: kv[1]["name"]):
            _, frame = self.attr_frames.get(info["attr"], self.attr_frames["all"])
            tile = ctk.CTkButton(
                frame, text=info["name"][:2].upper(), width=TILE[0], height=TILE[1], corner_radius=4,
                fg_color=C["card"], hover_color=C["accent3"], border_width=1, border_color=C["border"],
                text_color=C["txt2"], font=ctk.CTkFont(size=10, weight="bold"),
                command=lambda h=hid: self._add(h),
            )
            tile.bind("<Enter>", lambda _e, n=info["name"]: self.hover.configure(text=n))
            self.tiles[hid] = tile
            self.npc_to_id[info["npc"]] = hid
        self._layout_grid()
        npcs = [info["npc"] for info in self.catalog.values()]
        threading.Thread(
            target=hero_images.ensure_portraits, args=(npcs, lambda n: self.after(0, self._set_tile_image, n)),
            daemon=True,
        ).start()

    def _set_tile_image(self, npc: str):
        hid = self.npc_to_id.get(npc)
        img = hero_images.portrait_image(npc, TILE)
        if hid in self.tiles and img:
            self.tiles[hid].configure(image=img, text="")
        for slots in self.slots.values():
            for slot in slots:
                if slot.hero_id == hid and img:
                    slot.img.configure(image=img, text="")

    def _layout_grid(self):
        query = opendota._normalize_hero_name(self.search.get())
        for attr, _ in ATTR_ORDER:
            lbl, frame = self.attr_frames[attr]
            lbl.pack_forget()
            frame.pack_forget()
            for tile in frame.winfo_children():
                tile.grid_forget()
            visible = [
                hid for hid, info in sorted(self.catalog.items(), key=lambda kv: kv[1]["name"])
                if info["attr"] == attr and (not query or query in opendota._normalize_hero_name(info["name"]))
            ]
            if not visible:
                continue
            lbl.pack(fill="x", pady=(6, 2))
            frame.pack(fill="x")
            for i, hid in enumerate(visible):
                self.tiles[hid].grid(row=i // TILES_PER_ROW, column=i % TILES_PER_ROW, padx=2, pady=2)

    def _visible_tiles(self) -> list[int]:
        out = []
        for attr, _ in ATTR_ORDER:
            _, frame = self.attr_frames[attr]
            out.extend(hid for hid, tile in self.tiles.items() if tile.master is frame and tile.winfo_manager())
        return out

    # ── Draft ────────────────────────────────────────────────────────────────
    def _set_side(self, side: str):
        self.side = side
        self.side_switch.set(side)
        self._refresh_markers()

    def _refresh_markers(self):
        for side, marker in self.side_markers.items():
            marker.configure(text="← agregando acá" if side == self.side else "")

    def _taken(self) -> set[int]:
        return {s.hero_id for slots in self.slots.values() for s in slots if s.hero_id is not None}

    def _add(self, hero_id: int, side: str | None = None):
        if hero_id in self._taken():
            return
        slots = self.slots[side or self.side]
        slot = next((s for s in slots if s.hero_id is None), None)
        if slot is None:
            self.ctx.status(f"{side or self.side}: los 5 picks ya están cargados", "info")
            return
        info = self.catalog.get(hero_id, {})
        slot.set_hero(hero_id, info.get("name", f"#{hero_id}"), hero_images.portrait_image(info.get("npc", ""), TILE))
        self._schedule_recompute()

    def _add_first_visible(self):
        visible = [h for h in self._visible_tiles() if h not in self._taken()]
        if visible:
            self._add(visible[0])
            self.search.delete(0, "end")
            self._layout_grid()

    def _remove(self, side: str, index: int):
        self.slots[side][index].clear()
        self._schedule_recompute()

    def _clear_draft(self):
        for slots in self.slots.values():
            for s in slots:
                s.clear()
        self._schedule_recompute()

    def _toggle_positions(self):
        show = bool(self.pos_var.get())
        for slots in self.slots.values():
            for s in slots:
                s.show_pos(show)
        self._schedule_recompute()

    def _state(self) -> picks.DraftState:
        pos_txt = self.pos_combo.get()
        my_pos = POS_LABELS.index(pos_txt) + 1 if pos_txt in POS_LABELS else None
        allies = [s.hero_id for s in self.slots[SIDES[0]] if s.hero_id is not None]
        enemies = [s.hero_id for s in self.slots[SIDES[1]] if s.hero_id is not None]
        enemy_pos: dict[int, int] = {}
        if self.pos_var.get():
            for s in self.slots[SIDES[1]]:
                if s.hero_id is not None and s.pos_value():
                    enemy_pos[s.hero_id] = s.pos_value()  # type: ignore[assignment]
        return picks.DraftState(my_pos=my_pos, allies=allies, enemies=enemies, enemy_pos=enemy_pos)

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
        self.catalog = opendota.get_hero_catalog()
        self.after(0, self._build_grid)

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

    # ── Recompute ────────────────────────────────────────────────────────────
    def _schedule_recompute(self):
        if self._pending_recompute:
            self.after_cancel(self._pending_recompute)
        self._pending_recompute = self.after(120, self._recompute)

    def _recompute(self):
        self._pending_recompute = None
        if not self.data.hero_names:
            return
        state = self._state()
        self._ensure_enemy_matchups(state.enemies)

        known = sum(1 for e in state.enemies if e in self.data.matchups)
        counters = f"counters {known}/{len(state.enemies)} enemigos" if state.enemies else "counters al pickear"
        pending = not self._loaded_static or known < len(state.enemies)
        bracket = picks.BRACKET_NAMES.get(self.data.bracket or 0, "?")
        meta_txt = f"Parche {self._patch or '?'}" if self._loaded_static else "Cargando meta..."
        self.data_status.configure(text=f"{meta_txt} · {counters} · bracket {bracket}",
                                   text_color=C["amber"] if pending else C["txt3"])

        recs = picks.recommend(state, self.data, limit=10)
        self._highlight(state, [r.hero_id for r in recs])
        title = f"RECOMENDADOS PARA {POS_LABELS[state.my_pos - 1].upper()}" if state.my_pos else "RECOMENDADOS"
        self.rec_title.configure(text=f"{title} · {len(state.enemies)} enemigo(s)")
        for wdg in self.rec_list.winfo_children():
            wdg.destroy()
        for i, r in enumerate(recs, 1):
            self._rec_row(i, r)

        for wdg in self.alert_list.winfo_children():
            wdg.destroy()
        alerts = picks.draft_alerts(state, self.data)
        if not alerts:
            _text(self.alert_list, "Cargá picks enemigos para ver avisos.", 11, C["txt3"]).pack(fill="x")
        for tone, text in alerts:
            row = ctk.CTkFrame(self.alert_list, fg_color=C["card"], corner_radius=8)
            row.pack(fill="x", pady=2)
            color = C["amber"] if tone == "warn" else C["green"]
            _text(row, ("⚠ " if tone == "warn" else "✔ ") + text, 11, color, wraplength=300,
                  justify="left").pack(fill="x", padx=8, pady=5)

    def _highlight(self, state: picks.DraftState, rec_ids: list[int]):
        allies, enemies = set(state.allies), set(state.enemies)
        for hid, tile in self.tiles.items():
            if hid in allies:
                tile.configure(border_color=C["dst"], border_width=2, state="disabled")
            elif hid in enemies:
                tile.configure(border_color=C["red"], border_width=2, state="disabled")
            elif hid in rec_ids[:3]:
                tile.configure(border_color=C["green"], border_width=3, state="normal")
            elif hid in rec_ids:
                tile.configure(border_color=C["accent"], border_width=2, state="normal")
            else:
                tile.configure(border_color=C["border"], border_width=1, state="normal")

    def _rec_row(self, rank: int, r: picks.Recommendation):
        row = ctk.CTkFrame(self.rec_list, fg_color="transparent")
        row.pack(fill="x", pady=(0, 6))
        row.columnconfigure(2, weight=1)
        info = self.catalog.get(r.hero_id, {})
        img = hero_images.portrait_image(info.get("npc", ""), TILE)
        _text(row, str(rank), 12, C["txt3"], bold=True, width=22).grid(row=0, column=0, rowspan=3, sticky="n")
        pic = ctk.CTkLabel(row, text="" if img else r.name[:2].upper(), image=img, width=TILE[0], height=TILE[1],
                           fg_color=C["card"], corner_radius=4)
        pic.grid(row=0, column=1, rowspan=3, padx=(0, 8), sticky="n")
        _text(row, r.name, 13, C["txt"], bold=True).grid(row=0, column=2, sticky="w")
        _text(row, f"{r.score:.0f}", 17, C["accent"], bold=True).grid(row=0, column=3, rowspan=2, sticky="e")
        _text(row, r.reason, 11, C["txt2"], wraplength=330, justify="left").grid(row=1, column=2, sticky="w")
        chips = ctk.CTkFrame(row, fg_color="transparent")
        chips.grid(row=2, column=2, columnspan=2, sticky="w", pady=(2, 0))
        for text, tone in r.chips[:6]:
            bg, fg = CHIP_COLORS.get(tone, CHIP_COLORS[""])
            ctk.CTkLabel(chips, text=text, font=ctk.CTkFont(size=10), fg_color=bg, text_color=fg,
                         corner_radius=4, padx=6, pady=1).pack(side="left", padx=(0, 4), pady=1)
        for wdg in (row, pic, *row.winfo_children()):
            wdg.bind("<Button-1>", lambda _e, h=r.hero_id: self._add(h, SIDES[0]))
