"""
Pestaña Picks: draft por slots (popup con grilla de retratos), first picks del meta y recomendados.

Tres bloques: el draft, los first picks del meta de tu posición y los recomendados con avisos.
Todo sale de stats Divine/Immortal (Stratz; OpenDota sin token): nada del historial del jugador.
"""

import concurrent.futures
import logging
import threading
import tkinter as tk

import customtkinter as ctk

from . import draft, hero_images, meta, opendota
from .theme import C
from .widgets import TabContext, card, section_label

log = logging.getLogger(__name__)

POS_LABELS = ["Pos 1", "Pos 2", "Pos 3", "Pos 4", "Pos 5"]
NO_POS = "—"
SIDES = ("Mi equipo", "Enemigos")
ATTR_ORDER = (("str", "FUERZA"), ("agi", "AGILIDAD"), ("int", "INTELIGENCIA"), ("all", "UNIVERSAL"))
TILE = (64, 36)
TILE_W, TILE_H, GAP, HEADER_H, PAD = 64, 36, 6, 22, 8
MAX_RECS, MAX_ALERTS, MAX_CHIPS, MAX_FIRST = 10, 6, 5, 5
MAX_TABLE_TRIES = 3
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
TIER_COLORS = {  # (fondo, texto) por tier
    "S+": ("#5a4300", "#ffd84d"), "S": ("#4a3800", "#f0c030"),
    "A+": ("#0f3d2e", "#34d399"), "A": ("#0f3328", "#2fbf8a"),
    "B+": ("#2a1a4a", "#c4b5fd"), "B": ("#241741", "#a78bfa"),
    "C+": ("#201a2e", "#a99fc9"), "C": ("#1a1526", "#6b6090"),
}


def _tier_badge(parent) -> ctk.CTkLabel:
    return ctk.CTkLabel(parent, text="", font=ctk.CTkFont(size=14, weight="bold"), corner_radius=6,
                        width=40, height=26, padx=6)


def _set_tier(badge: ctk.CTkLabel, score_lbl: ctk.CTkLabel, r: draft.Recommendation):
    bg, fg = TIER_COLORS.get(r.tier, TIER_COLORS["C"])
    badge.configure(text=r.tier or "—", fg_color=bg, text_color=fg)
    score_lbl.configure(text=f"{r.score:.0f}")


def virtual_screen_bounds(root) -> tuple[int, int, int, int]:
    """(x, y, ancho, alto) del escritorio virtual: todos los monitores. Fallback: el principal."""
    try:
        import ctypes

        m = ctypes.windll.user32.GetSystemMetrics
        x, y, w, h = m(76), m(77), m(78), m(79)   # SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CX/CYVIRTUALSCREEN
        if w > 0 and h > 0:
            return x, y, w, h
    except (AttributeError, OSError):
        pass
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def _text(parent, text: str, size: int = 12, color: str = C["txt2"], bold: bool = False, **kw) -> ctk.CTkLabel:
    font = ctk.CTkFont(size=size, weight="bold" if bold else "normal")
    kw.setdefault("anchor", "w")
    return ctk.CTkLabel(parent, text=text, font=font, text_color=color, **kw)


def _combo(parent, values: list[str], width: int, command) -> ctk.CTkComboBox:
    return ctk.CTkComboBox(parent, values=values, width=width, command=command,
                           font=ctk.CTkFont(size=12), **COMBO_STYLE)


def _chip(parent) -> ctk.CTkLabel:
    return ctk.CTkLabel(parent, text="", font=ctk.CTkFont(size=10), corner_radius=4, padx=6, pady=1)


def _set_chips(labels: list[ctk.CTkLabel], chips: list[tuple[str, str]]):
    for i, chip in enumerate(labels):
        if i < len(chips):
            text, tone = chips[i]
            bg, fg = CHIP_COLORS.get(tone, CHIP_COLORS[""])
            chip.configure(text=text, fg_color=bg, text_color=fg)
            chip.pack(side="left", padx=(0, 4), pady=1)
        else:
            chip.pack_forget()


def layout_tiles(catalog: dict[int, dict], query: str, columns: int) -> tuple[list[tuple], int]:
    """
    Posiciones en píxeles de la grilla: [("header", etiqueta, x, y) | ("tile", hero_id, x, y)], alto total.

    Agrupa por atributo, ordena por nombre y filtra por `query` (subcadena normalizada). Pura.
    """
    q = opendota._normalize_hero_name(query)
    columns = max(1, columns)
    items: list[tuple] = []
    y = PAD
    ordered = sorted(catalog.items(), key=lambda kv: kv[1]["name"])
    for attr, label in ATTR_ORDER:
        heroes = [hid for hid, info in ordered
                  if info.get("attr", "all") == attr and (not q or q in opendota._normalize_hero_name(info["name"]))]
        if not heroes:
            continue
        items.append(("header", label, PAD, y))
        y += HEADER_H
        for i, hid in enumerate(heroes):
            r, c = divmod(i, columns)
            items.append(("tile", hid, PAD + c * (TILE_W + GAP), y + r * (TILE_H + GAP)))
        rows = (len(heroes) + columns - 1) // columns
        y += rows * (TILE_H + GAP) + 6
    return items, y + PAD


class HeroGridCanvas(tk.Canvas):
    """Los 127 retratos en un solo widget: dibujar y resaltar cuesta milisegundos, no relayouts."""

    def __init__(self, master, on_pick, on_hover, **kw):
        super().__init__(master, bg=C["bg2"], highlightthickness=0, bd=0, height=240, cursor="arrow", **kw)
        self.on_pick, self.on_hover = on_pick, on_hover
        self.catalog: dict[int, dict] = {}
        self.query = ""
        self.photos: dict[int, tk.PhotoImage] = {}
        self.allies: set[int] = set()
        self.enemies: set[int] = set()
        self.recs: list[int] = []
        self.rects: list[tuple[int, int, int, int, int]] = []
        self._hover: int | None = None
        self._height = 0
        self.bind("<Configure>", lambda _e: self.redraw())
        self.bind("<Button-1>", self._click)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda _e: self._set_hover(None))

    def set_catalog(self, catalog: dict[int, dict]):
        self.catalog = catalog
        self.redraw()

    def set_filter(self, query: str):
        self.query = query
        self.redraw()

    def set_photo(self, hid: int, photo: tk.PhotoImage):
        self.photos[hid] = photo
        for item in self.find_withtag(f"img{hid}"):
            self.itemconfigure(item, image=photo)

    def visible_ids(self) -> list[int]:
        return [hid for _, _, _, _, hid in self.rects]

    def redraw(self):
        width = self.winfo_width()
        if width < TILE_W + 2 * PAD:
            return
        columns = (width - 2 * PAD + GAP) // (TILE_W + GAP)
        items, total = layout_tiles(self.catalog, self.query, columns)
        if total != self._height:
            self._height = total
            self.configure(height=total)
        self.delete("all")
        self.rects = []
        for item in items:
            if item[0] == "header":
                self.create_text(item[2], item[3] + 6, text=item[1], anchor="w", fill=C["txt3"],
                                 font=("Segoe UI", 9, "bold"))
                continue
            _, hid, x, y = item
            x2, y2 = x + TILE_W, y + TILE_H
            photo = self.photos.get(hid)
            if photo:
                self.create_image(x, y, image=photo, anchor="nw", tags=(f"img{hid}",))
            else:
                self.create_rectangle(x, y, x2, y2, fill=C["card"], outline="")
                self.create_text((x + x2) / 2, (y + y2) / 2, text=self.catalog[hid]["name"][:2].upper(),
                                 fill=C["txt2"], font=("Segoe UI", 9, "bold"))
            if hid in self.allies or hid in self.enemies:
                self.create_rectangle(x, y, x2, y2, fill=C["bg"], outline="", stipple="gray50")
            color, width_px = self._frame_for(hid)
            inset = width_px / 2
            self.create_rectangle(x + inset, y + inset, x2 - inset, y2 - inset, outline=color, width=width_px)
            if hid in self.recs[:3]:
                rank = self.recs.index(hid) + 1
                self.create_rectangle(x2 - 16, y, x2, y + 14, fill=C["green"], outline="")
                self.create_text(x2 - 8, y + 7, text=str(rank), fill="#06281c", font=("Segoe UI", 8, "bold"))
            self.rects.append((x, y, x2, y2, hid))

    def _frame_for(self, hid: int) -> tuple[str, int]:
        if hid in self.allies:
            return C["dst"], 2
        if hid in self.enemies:
            return C["red"], 2
        if hid in self.recs[:3]:
            return C["green"], 3
        if hid in self.recs:
            return C["accent"], 2
        return C["border"], 1

    def _hit(self, x: int, y: int) -> int | None:
        for x1, y1, x2, y2, hid in self.rects:
            if x1 <= x <= x2 and y1 <= y <= y2:
                return hid
        return None

    def _click(self, event):
        hid = self._hit(event.x, event.y)
        if hid is not None and hid not in self.allies and hid not in self.enemies:
            self.on_pick(hid)

    def _motion(self, event):
        self._set_hover(self._hit(event.x, event.y))

    def _set_hover(self, hid: int | None):
        if hid == self._hover:
            return
        self._hover = hid
        self.configure(cursor="hand2" if hid is not None else "arrow")
        self.on_hover(self.catalog[hid]["name"] if hid is not None else "")


class HeroPickerPopup(ctk.CTkToplevel):
    """Ventana para elegir el héroe de un slot: buscador arriba, grilla abajo, Enter o click eligen."""

    def __init__(self, master, title: str, catalog: dict[int, dict], photos: dict[int, tk.PhotoImage],
                 allies: set[int], enemies: set[int], recs: list[int], on_pick):
        super().__init__(master, fg_color=C["bg"])
        self.title(title)
        root = master.winfo_toplevel()
        self.geometry(self._centered_geometry(root, 860, 720))
        self.minsize(640, 480)
        self.transient(root)
        self._on_pick = on_pick
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(14, 6))
        _text(top, title, 13, C["txt"], bold=True).pack(side="left")
        self.search = ctk.CTkEntry(
            top, placeholder_text="Buscar héroe…  (Enter elige el primero · Esc cierra)", width=340,
            fg_color=C["card"], border_color=C["border"], text_color=C["txt"], font=ctk.CTkFont(size=12),
        )
        self.search.pack(side="right")
        self.hover = _text(self, " ", 11, C["txt2"])
        self.hover.pack(fill="x", padx=16)
        body = ctk.CTkScrollableFrame(self, fg_color=C["bg2"], corner_radius=12,
                                      scrollbar_button_color=C["border"], scrollbar_button_hover_color=C["accent2"])
        body.pack(fill="both", expand=True, padx=16, pady=(4, 6))
        self.grid = HeroGridCanvas(body, on_pick=self._pick, on_hover=lambda n: self.hover.configure(text=n or " "))
        self.grid.photos = photos
        self.grid.allies, self.grid.enemies, self.grid.recs = allies, enemies, recs
        self.grid.pack(fill="x", padx=6, pady=6)
        self.grid.set_catalog(catalog)
        legend = "Verde: top 3 recomendados · violeta: top 10 · teal: en tu equipo · rojo: enemigo"
        _text(self, legend, 10, C["txt3"]).pack(fill="x", padx=16, pady=(0, 12))
        self.search.bind("<KeyRelease>", lambda _e: self.grid.set_filter(self.search.get()))
        self.search.bind("<Return>", lambda _e: self._pick_first())
        self.bind("<Escape>", lambda _e: self.destroy())
        self.after(120, self._focus)

    @staticmethod
    def _centered_geometry(root, width: int, height: int) -> str:
        """
        Centrado sobre la ventana principal, esté en el monitor que esté.

        winfo_screenwidth() es solo el monitor principal: acotar con eso arrastra el popup
        a la pantalla 1. Se acota al escritorio virtual (todos los monitores) vía Win32.
        """
        root.update_idletasks()
        rx, ry, rw, rh = root.winfo_rootx(), root.winfo_rooty(), root.winfo_width(), root.winfo_height()
        vx, vy, vw, vh = virtual_screen_bounds(root)
        width, height = min(width, vw - 20), min(height, vh - 60)
        x = max(vx, min(rx + (rw - width) // 2, vx + vw - width))
        y = max(vy, min(ry + (rh - height) // 2, vy + vh - height - 40))
        return f"{width}x{height}+{x}+{y}"

    def _focus(self):
        self.lift()
        self.focus_force()
        self.search.focus_set()
        try:
            self.grab_set()
        except tk.TclError:
            pass

    def _pick_first(self):
        taken = self.grid.allies | self.grid.enemies
        visible = [h for h in self.grid.visible_ids() if h not in taken]
        if visible:
            self._pick(visible[0])

    def _pick(self, hero_id: int):
        self._on_pick(hero_id)
        self.destroy()


class Slot(ctk.CTkFrame):
    """Un pick: retrato + nombre + (opcional) posición + quitar. Click abre el selector."""

    def __init__(self, master, on_remove, on_change, on_click, **kw):
        super().__init__(master, fg_color=C["card"], corner_radius=8, cursor="hand2", **kw)
        self.hero_id: int | None = None
        self.columnconfigure(1, weight=1)
        self.img = ctk.CTkLabel(self, text="", image=hero_images.blank_image(TILE), width=TILE[0], height=TILE[1])
        self.img.grid(row=0, column=0, padx=(6, 8), pady=5)
        self.name = _text(self, "vacío · click para elegir", 12, C["txt3"])
        self.name.grid(row=0, column=1, sticky="ew")
        for wdg in (self, self.img, self.name):
            wdg.bind("<Button-1>", lambda _e: on_click())
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
        self.img.configure(image=image or hero_images.blank_image(TILE), text="" if image else name[:2].upper())

    def clear(self):
        self.hero_id = None
        self.name.configure(text="vacío · click para elegir", text_color=C["txt3"])
        self.img.configure(image=hero_images.blank_image(TILE), text="")
        self.pos.set(NO_POS)

    def show_pos(self, visible: bool):
        if visible:
            self.pos.grid()
        else:
            self.pos.grid_remove()

    def pos_value(self) -> int | None:
        return POS_LABELS.index(self.pos.get()) + 1 if self.pos.get() in POS_LABELS else None


class RecRow(ctk.CTkFrame):
    """Fila de recomendación reutilizable: se actualiza, no se recrea."""

    def __init__(self, master, on_click, **kw):
        super().__init__(master, fg_color="transparent", cursor="hand2", **kw)
        self.hero_id: int | None = None
        self.columnconfigure(2, weight=1)
        self.rank = _text(self, "", 12, C["txt3"], bold=True, width=22)
        self.rank.grid(row=0, column=0, rowspan=3, sticky="n")
        self.pic = ctk.CTkLabel(self, text="", image=hero_images.blank_image(TILE), width=TILE[0], height=TILE[1])
        self.pic.grid(row=0, column=1, rowspan=3, padx=(0, 8), sticky="n")
        self.name = _text(self, "", 13, C["txt"], bold=True)
        self.name.grid(row=0, column=2, sticky="w")
        self.tier = _tier_badge(self)
        self.tier.grid(row=0, column=3, sticky="e")
        self.score = _text(self, "", 10, C["txt3"], anchor="e")
        self.score.grid(row=1, column=3, sticky="e")
        self.reason = _text(self, "", 11, C["txt2"], wraplength=330, justify="left")
        self.reason.grid(row=1, column=2, sticky="w")
        self.chips = ctk.CTkFrame(self, fg_color="transparent")
        self.chips.grid(row=2, column=2, columnspan=2, sticky="w", pady=(2, 0))
        self.chip_labels = [_chip(self.chips) for _ in range(MAX_CHIPS)]
        for wdg in (self, self.rank, self.pic, self.name, self.tier, self.score, self.reason, self.chips,
                    *self.chip_labels):
            wdg.bind("<Button-1>", lambda _e: on_click(self.hero_id))

    def update_rec(self, rank: int, r: draft.Recommendation, image: ctk.CTkImage | None):
        self.hero_id = r.hero_id
        self.rank.configure(text=str(rank))
        self.pic.configure(image=image or hero_images.blank_image(TILE), text="" if image else r.name[:2].upper())
        self.name.configure(text=r.name)
        _set_tier(self.tier, self.score, r)
        self.reason.configure(text=r.reason)
        _set_chips(self.chip_labels, r.chips)


class FirstPickTile(ctk.CTkFrame):
    """Tarjeta compacta de un first pick del meta: retrato, nombre, tier y un chip. Click lo agrega."""

    def __init__(self, master, on_click, **kw):
        super().__init__(master, fg_color=C["card"], corner_radius=10, cursor="hand2", **kw)
        self.hero_id: int | None = None
        self.pic = ctk.CTkLabel(self, text="", image=hero_images.blank_image(TILE), width=TILE[0], height=TILE[1])
        self.pic.pack(pady=(10, 4))
        self.name = _text(self, "", 12, C["txt"], bold=True, anchor="center")
        self.name.pack(fill="x", padx=8)
        badge_row = ctk.CTkFrame(self, fg_color="transparent")
        badge_row.pack(pady=(2, 0))
        self.tier = _tier_badge(badge_row)
        self.tier.pack(side="left")
        self.score = _text(badge_row, "", 10, C["txt3"])
        self.score.pack(side="left", padx=(6, 0))
        self.chips = ctk.CTkFrame(self, fg_color="transparent")
        self.chips.pack(pady=(4, 10))
        self.chip_labels = [_chip(self.chips) for _ in range(1)]
        for wdg in (self, self.pic, self.name, badge_row, self.tier, self.score, self.chips, *self.chip_labels):
            wdg.bind("<Button-1>", lambda _e: on_click(self.hero_id))

    def update_rec(self, r: draft.Recommendation, image: ctk.CTkImage | None):
        self.hero_id = r.hero_id
        self.pic.configure(image=image or hero_images.blank_image(TILE), text="" if image else r.name[:2].upper())
        self.name.configure(text=r.name)
        _set_tier(self.tier, self.score, r)
        compact: list[tuple[str, str]] = []
        for text, tone in r.chips:                              # la tarjeta es angosta: un chip, el de first pick
            if text.startswith("First pick: pocos"):
                compact.append(("sin muestra", tone))
            elif text.startswith("First pick:"):
                compact.append((text.split(":")[1].split("del")[0].strip() + " countereable", tone))
        _set_chips(self.chip_labels, compact[:1] or r.chips[:1])


class PicksTab(ctk.CTkFrame):
    def __init__(self, master, ctx: TabContext, **kw):
        super().__init__(master, fg_color="transparent", **kw)
        self.ctx = ctx
        self.data = draft.DraftData()
        self.catalog: dict[int, dict] = {}
        self.photos: dict[int, tk.PhotoImage] = {}
        self.slots: dict[str, list[Slot]] = {}
        self._last_recs: list[int] = []
        self._tables_in_flight: set[int] = set()
        self._table_tries: dict[int, int] = {}
        self._pending_recompute: str | None = None
        self._build()
        # El hilo arranca recién dentro del mainloop: un after() desde otro hilo antes de que el
        # mainloop corra lanza "main thread is not in main loop" y mata el hilo en silencio.
        self.after(0, lambda: threading.Thread(target=self._load_meta, name="picks-meta", daemon=True).start())

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
        _text(top, "Mi posición").grid(row=0, column=0, padx=(0, 8))
        self.pos_combo = _combo(top, POS_LABELS, 96, lambda _v: self._schedule_recompute())
        self.pos_combo.set("Pos 3")
        self.pos_combo.grid(row=0, column=1, padx=(0, 14))
        self.pos_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            top, text="Posiciones de los picks", variable=self.pos_var, command=self._toggle_positions,
            font=ctk.CTkFont(size=12), text_color=C["txt2"], fg_color=C["accent2"], hover_color=C["accent3"],
            border_color=C["border"], checkbox_width=18, checkbox_height=18,
        ).grid(row=0, column=2, padx=(0, 14))
        self.data_status = _text(top, "Cargando meta...", 11, C["txt3"], anchor="e")
        self.data_status.grid(row=0, column=3, sticky="e", padx=(0, 12))
        ctk.CTkButton(
            top, text="Limpiar", width=90, height=28, fg_color="transparent", border_width=1,
            border_color=C["border"], text_color=C["txt2"], hover_color=C["bg2"], font=ctk.CTkFont(size=12),
            command=self._clear_draft,
        ).grid(row=0, column=4, sticky="e")

        teams = ctk.CTkFrame(top, fg_color="transparent")
        teams.grid(row=1, column=0, columnspan=5, sticky="ew", pady=(12, 0))
        teams.columnconfigure((0, 1), weight=1, uniform="team")
        self._team_column(teams, 0, SIDES[0], C["accent"])
        self._team_column(teams, 1, SIDES[1], C["red"])

        section_label(self.scroll, "FIRST PICKS DEL META", pady=(14, 6))
        fbox, fin = card(self.scroll, C["dst"])
        fbox.pack(fill="x")
        self.first_title = _text(fin, "Héroes del meta de tu posición que menos se counterean · click agrega",
                                 11, C["txt2"])
        self.first_title.pack(fill="x")
        strip = ctk.CTkFrame(fin, fg_color="transparent")
        strip.pack(fill="x", pady=(8, 0))
        strip.columnconfigure(tuple(range(MAX_FIRST)), weight=1, uniform="fp")
        self.first_tiles = []
        for i in range(MAX_FIRST):
            tile = FirstPickTile(strip, on_click=lambda h: self._add(h, SIDES[0]))
            tile.grid(row=0, column=i, sticky="nsew", padx=(0, 8) if i < MAX_FIRST - 1 else 0)
            self.first_tiles.append(tile)
        self.first_empty = _text(fin, "Cargando el meta de tu posición...", 11, C["txt3"])

        self.rec_section = section_label(self.scroll, "RECOMENDADOS", pady=(14, 6))
        results = ctk.CTkFrame(self.scroll, fg_color="transparent")
        results.pack(fill="x", pady=(0, 16))
        results.columnconfigure(0, weight=3, uniform="res")
        results.columnconfigure(1, weight=2, uniform="res")
        rbox, rin = card(results)
        rbox.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.rec_title = _text(rin, "click en una fila para agregarlo a tu equipo", 11, C["txt2"])
        self.rec_title.pack(fill="x")
        rec_list = ctk.CTkFrame(rin, fg_color="transparent")
        rec_list.pack(fill="x", pady=(6, 0))
        self.rec_rows = [RecRow(rec_list, on_click=lambda h: self._add(h, SIDES[0])) for _ in range(MAX_RECS)]
        abox, ain = card(results)
        abox.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        _text(ain, "AVISOS", 10, C["txt3"], bold=True).pack(fill="x")
        alert_list = ctk.CTkFrame(ain, fg_color="transparent")
        alert_list.pack(fill="x", pady=(6, 0))
        self.alert_rows = []
        for _ in range(MAX_ALERTS):
            row = ctk.CTkFrame(alert_list, fg_color=C["card"], corner_radius=8)
            lbl = _text(row, "", 11, C["txt2"], wraplength=300, justify="left")
            lbl.pack(fill="x", padx=8, pady=5)
            self.alert_rows.append((row, lbl))
        self.alert_empty = _text(alert_list, "Cargá picks enemigos para ver avisos.", 11, C["txt3"])
        self.alert_empty.pack(fill="x")
        w = draft.normalize_weights(self.ctx.cfg.picks_weights, draft.DEFAULT_WEIGHTS)
        f = draft.normalize_weights(self.ctx.cfg.first_pick_weights, draft.FIRST_PICK_WEIGHTS)
        weights = (f"Pesos · con enemigos: counters {w['counters']:.0%}, meta {w['meta']:.0%}, "
                   f"sinergia {w['synergy']:.0%} · first pick: meta {f['meta']:.0%}, seguridad {f['safety']:.0%}, "
                   f"sinergia {f['synergy']:.0%} (config.json).")
        _text(ain, weights, 10, C["txt3"], wraplength=300, justify="left").pack(fill="x", pady=(10, 0))

    def _team_column(self, parent, col: int, side: str, color: str):
        inner = ctk.CTkFrame(parent, fg_color="transparent")
        inner.grid(row=0, column=col, sticky="nsew", padx=(0, 6) if col == 0 else (6, 0))
        _text(inner, f"● {side.upper()}", 10, color, bold=True).pack(fill="x", pady=(0, 4))
        slots = []
        for i in range(5):
            slot = Slot(inner, on_remove=lambda s=side, i=i: self._remove(s, i), on_change=self._schedule_recompute,
                        on_click=lambda s=side, i=i: self._open_picker(s, i))
            slot.pack(fill="x", pady=2)
            slots.append(slot)
        self.slots[side] = slots

    def _install_catalog(self):
        """Retratos: descarga y escalado en un hilo; la PhotoImage (hilo de Tk) se crea al avisar."""
        npc_to_id = {info["npc"]: hid for hid, info in self.catalog.items()}

        def ready(npc: str):
            hid = npc_to_id.get(npc)
            photo = hero_images.portrait_photo(npc, TILE)
            if hid is not None and photo:
                self.photos[hid] = photo

        threading.Thread(
            target=hero_images.ensure_portraits,
            args=([info["npc"] for info in self.catalog.values()], lambda n: self.after(0, ready, n)),
            kwargs={"size": TILE}, daemon=True,
        ).start()

    # ── Draft ────────────────────────────────────────────────────────────────
    def _taken(self) -> set[int]:
        return {s.hero_id for slots in self.slots.values() for s in slots if s.hero_id is not None}

    def _add(self, hero_id: int | None, side: str):
        if hero_id is None or hero_id in self._taken():
            return
        slot = next((s for s in self.slots[side] if s.hero_id is None), None)
        if slot is None:
            self.ctx.status(f"{side}: los 5 picks ya están cargados", "info")
            return
        self._fill_slot(slot, hero_id)

    def _fill_slot(self, slot: Slot, hero_id: int):
        info = self.catalog.get(hero_id, {})
        slot.set_hero(hero_id, info.get("name", f"#{hero_id}"), hero_images.portrait_image(info.get("npc", ""), TILE))
        self._schedule_recompute()

    def _open_picker(self, side: str, index: int):
        if not self.catalog:
            return
        slot = self.slots[side][index]
        state = self._state()

        def chosen(hero_id: int):
            if hero_id in self._taken() and hero_id != slot.hero_id:
                return
            self._fill_slot(slot, hero_id)

        HeroPickerPopup(
            self, title=f"Elegir héroe · {side} · slot {index + 1}", catalog=self.catalog, photos=self.photos,
            allies=set(state.allies) - {slot.hero_id}, enemies=set(state.enemies) - {slot.hero_id},
            recs=self._last_recs, on_pick=chosen,
        )

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

    def _state(self) -> draft.DraftState:
        pos_txt = self.pos_combo.get()
        my_pos = POS_LABELS.index(pos_txt) + 1 if pos_txt in POS_LABELS else None
        allies = [s.hero_id for s in self.slots[SIDES[0]] if s.hero_id is not None]
        enemies = [s.hero_id for s in self.slots[SIDES[1]] if s.hero_id is not None]
        enemy_pos: dict[int, int] = {}
        if self.pos_var.get():
            for s in self.slots[SIDES[1]]:
                pos = s.pos_value()
                if s.hero_id is not None and pos:
                    enemy_pos[s.hero_id] = pos
        return draft.DraftState(my_pos=my_pos, allies=allies, enemies=enemies, enemy_pos=enemy_pos)

    # ── Datos ────────────────────────────────────────────────────────────────
    def _load_meta(self):
        """Al arrancar: catálogo de héroes (24 h), meta Divine/Immortal (6 h) y ranking D2PT (1 h)."""
        try:
            self.catalog = opendota.get_hero_catalog()
            self.after(0, self._install_catalog)
            snap = meta.load_snapshot(self.ctx.cfg)
        except Exception as e:  # noqa: BLE001 — se muestra en la pestaña en vez de morir en silencio
            log.exception("Picks: no se pudo cargar el meta")
            self.ctx.log(f"Picks: no se pudo cargar el meta: {e}")
            self.after(0, self._set_status, f"No se pudo cargar el meta: {e}", C["red"])
            return
        self.data.meta = snap
        if not snap.ready:
            why = "sin token de Stratz y OpenDota no respondió" if not snap.overall else "sin nombres de héroes"
            self.ctx.log(f"Picks: meta incompleto ({why})")
            self.after(0, self._set_status, f"Meta incompleto: {why}", C["red"])
            return
        src = {"stratz": "Stratz", "opendota": "OpenDota (sin posición: falta token de Stratz)"}.get(snap.source, "?")
        self.ctx.log(f"Picks: meta {snap.patch or '?'} · {len(snap.overall)} héroes {meta.BRACKET_LABEL} vía {src}")
        self.after(0, self._schedule_recompute)

    def _set_status(self, text: str, color: str):
        self.data_status.configure(text=text, text_color=color)

    def _ensure_tables(self, heroes: list[int]):
        """
        Baja (o lee de caché) la tabla de cada héroe nuevo, hasta 4 en paralelo; recompute al llegar cada una.
        Una tabla que llega vacía no se guarda: se reintenta en el próximo recompute (hasta MAX_TABLE_TRIES).
        """
        missing = [h for h in dict.fromkeys(heroes)
                   if h not in self.data.matchups and h not in self._tables_in_flight
                   and self._table_tries.get(h, 0) < MAX_TABLE_TRIES]
        if not missing:
            return
        self._tables_in_flight.update(missing)
        cfg = self.ctx.cfg

        def one(h: int):
            try:
                vs, with_ = meta.fetch_tables(h, cfg)
            except Exception:  # noqa: BLE001 — una tabla fallida no debe tumbar las demás
                log.exception("Picks: tabla de %s falló", h)
                vs, with_ = {}, {}
            self._table_tries[h] = self._table_tries.get(h, 0) + 1
            if vs:
                self.data.matchups[h] = vs
            else:
                self.ctx.log(f"Picks: sin tabla de matchups para {self.data.name(h)} (intento {self._table_tries[h]})")
            if with_:
                self.data.synergy[h] = with_
            self._tables_in_flight.discard(h)
            self.after(0, self._schedule_recompute)

        def worker():
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(one, missing))

        threading.Thread(target=worker, name="picks-tables", daemon=True).start()

    # ── Recompute ────────────────────────────────────────────────────────────
    def _schedule_recompute(self):
        if self._pending_recompute:
            self.after_cancel(self._pending_recompute)
        self._pending_recompute = self.after(120, self._recompute)

    def _recompute(self):
        self._pending_recompute = None
        snap = self.data.meta
        if not snap.ready:
            return
        state = self._state()
        w_enemy = draft.normalize_weights(self.ctx.cfg.picks_weights, draft.DEFAULT_WEIGHTS)
        w_first = draft.normalize_weights(self.ctx.cfg.first_pick_weights, draft.FIRST_PICK_WEIGHTS)
        recs = draft.recommend(state, self.data, weights=w_enemy if state.enemies else w_first, limit=MAX_RECS)
        firsts = draft.meta_first_picks(state, self.data, limit=MAX_FIRST, weights=w_first)
        self._last_recs = [r.hero_id for r in recs]

        # Tablas: la de cada enemigo (counters), la de cada aliado (sinergia) y, sin enemigos,
        # la propia de cada candidato (qué tan countereable es de first pick).
        needed = list(state.enemies) + list(state.allies)
        if not state.enemies:
            needed += [r.hero_id for r in recs]
        needed += [r.hero_id for r in firsts]
        self._ensure_tables(needed)
        needed = list(dict.fromkeys(needed))
        known = sum(1 for h in needed if h in self.data.matchups)
        failed = sum(1 for h in needed if h not in self.data.matchups
                     and self._table_tries.get(h, 0) >= MAX_TABLE_TRIES)
        pending = known + failed < len(needed)
        src = "Stratz" if snap.source == "stratz" else "OpenDota"
        status = f"Parche {snap.patch or '?'} · {meta.BRACKET_LABEL} · {src}"
        if pending:
            status += f" · tablas {known}/{len(needed)}"
        elif failed:
            status += f" · {failed} tabla(s) sin respuesta"
        color = C["amber"] if pending else C["red"] if failed else C["txt3"]
        self.data_status.configure(text=status, text_color=color)

        pos_txt = POS_LABELS[state.my_pos - 1] if state.my_pos else ""
        self.first_title.configure(text=f"Meta de {pos_txt.lower()} ordenado por lo poco que se counterea · "
                                        "click agrega a tu equipo")
        for i, tile in enumerate(self.first_tiles):
            if i < len(firsts):
                r = firsts[i]
                tile.update_rec(r, hero_images.portrait_image(self.catalog.get(r.hero_id, {}).get("npc", ""), TILE))
                tile.grid()
            else:
                tile.grid_remove()
        if firsts:
            self.first_empty.pack_forget()
        else:
            self.first_empty.pack(fill="x", pady=(6, 0))

        if state.enemies:
            self.rec_section.configure(text=f"RECOMENDADOS PARA {pos_txt.upper()} · {len(state.enemies)} ENEMIGO(S)")
            self.rec_title.configure(text="Counters a lo que sacaron, meta de tu posición y sinergia con tu equipo · "
                                          "click agrega")
        else:
            self.rec_section.configure(text=f"RECOMENDADOS PARA {pos_txt.upper()} · FIRST PICK")
            self.rec_title.configure(text="Sin enemigos a la vista: meta de tu posición y seguridad ante counters · "
                                          "click agrega")
        for i, row in enumerate(self.rec_rows):
            if i < len(recs):
                r = recs[i]
                npc = self.catalog.get(r.hero_id, {}).get("npc", "")
                row.update_rec(i + 1, r, hero_images.portrait_image(npc, TILE))
                row.pack(fill="x", pady=(0, 6))
            else:
                row.pack_forget()

        alerts = draft.draft_alerts(state, self.data, limit=MAX_ALERTS, recs=recs)
        if alerts:
            self.alert_empty.pack_forget()
        else:
            self.alert_empty.pack(fill="x")
        for i, (row, lbl) in enumerate(self.alert_rows):
            if i < len(alerts):
                tone, text = alerts[i]
                mark = {"warn": "⚠ ", "ok": "✔ "}.get(tone, "• ")
                color = {"warn": C["amber"], "ok": C["green"]}.get(tone, C["txt2"])
                lbl.configure(text=mark + text, text_color=color)
                row.pack(fill="x", pady=2)
            else:
                row.pack_forget()
