"""Pestaña Rendimiento: ranked recientes, MMR estimado, historial de medalla y tabla por héroe."""

import logging
import threading
import tkinter as tk
from datetime import datetime

import customtkinter as ctk

from . import opendota, performance, stratz
from .theme import C, medal_for_tier
from .widgets import TabContext, card, section_label

log = logging.getLogger(__name__)

COMBO_STYLE = {
    "fg_color": C["card"], "border_color": C["border"], "button_color": C["accent2"],
    "button_hover_color": C["accent"], "text_color": C["txt"], "dropdown_fg_color": C["bg2"],
    "dropdown_hover_color": C["card"], "dropdown_text_color": C["txt"],
}
MAX_HERO_ROWS = 12


def _text(parent, text: str, size: int = 12, color: str = C["txt2"], bold: bool = False, **kw) -> ctk.CTkLabel:
    font = ctk.CTkFont(size=size, weight="bold" if bold else "normal")
    kw.setdefault("anchor", "w")
    return ctk.CTkLabel(parent, text=text, font=font, text_color=color, **kw)


def _pct(rate: float | None) -> str:
    return f"{rate:.0%}" if rate is not None else "N/D"


def _rate_color(rate: float | None) -> str:
    if rate is None:
        return C["txt2"]
    return C["green"] if rate >= 0.55 else C["red"] if rate < 0.45 else C["txt"]


class PerformanceTab(ctk.CTkFrame):
    def __init__(self, master, ctx: TabContext, **kw):
        super().__init__(master, fg_color="transparent", **kw)
        self.ctx = ctx
        self._series: list[tuple[dict, int | None]] = []
        self._loading_for: str | None = None
        self._build()

    # ── UI ───────────────────────────────────────────────────────────────────
    def _build(self):
        self.scroll = ctk.CTkScrollableFrame(
            self, fg_color="transparent", scrollbar_button_color=C["border"],
            scrollbar_button_hover_color=C["accent2"],
        )
        self.scroll.pack(fill="both", expand=True, padx=20, pady=(12, 0))

        section_label(self.scroll, "CUENTA", pady=(4, 6))
        box, top = card(self.scroll, C["dst"])
        box.pack(fill="x")
        top.columnconfigure(2, weight=1)
        self.acc_combo = ctk.CTkComboBox(top, values=["  (cargando...)"], width=260, font=ctk.CTkFont(size=12),
                                         command=lambda _v: self._reload(), **COMBO_STYLE)
        self.acc_combo.grid(row=0, column=0, padx=(0, 10))
        ctk.CTkButton(
            top, text="⟳  Actualizar partidas", width=170, height=30, fg_color="transparent", border_width=2,
            border_color=C["dst"], text_color=C["dst"], hover_color=C["badge_dst"],
            font=ctk.CTkFont(size=12, weight="bold"), command=lambda: self._reload(force=True),
        ).grid(row=0, column=1, padx=(0, 10))
        self.sync_status = _text(top, "Esperando cuentas...", 11, C["txt3"], anchor="e")
        self.sync_status.grid(row=0, column=2, sticky="e")

        kpis = ctk.CTkFrame(self.scroll, fg_color="transparent")
        kpis.pack(fill="x", pady=(12, 0))
        kpis.columnconfigure((0, 1, 2, 3), weight=1, uniform="kpi")
        self.tiles = {
            key: self._tile(kpis, i, title)
            for i, (key, title) in enumerate((("medal", "MEDALLA"), ("mmr", "MMR ESTIMADO"),
                                               ("last20", "ÚLTIMAS 20"), ("streak", "RACHA")))
        }

        ibox, iin = card(self.scroll, C["accent2"])
        ibox.pack(fill="x", pady=(12, 0))
        _text(iin, "LECTURA · qué dicen tus números", 10, C["accent"], bold=True).pack(fill="x")
        self.insights_box = ctk.CTkFrame(iin, fg_color="transparent")
        self.insights_box.pack(fill="x", pady=(6, 0))
        _text(self.insights_box, "Se completa al cargar las partidas.", 11, C["txt3"]).pack(fill="x")

        mid = ctk.CTkFrame(self.scroll, fg_color="transparent")
        mid.pack(fill="x", pady=(12, 0))
        mid.columnconfigure(0, weight=5, uniform="mid")
        mid.columnconfigure(1, weight=3, uniform="mid")

        cbox, cin = card(mid)
        cbox.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        head = ctk.CTkFrame(cin, fg_color="transparent")
        head.pack(fill="x")
        self.chart_title = _text(head, "MMR ESTIMADO · RANKED RECIENTES", 10, C["txt3"], bold=True)
        self.chart_title.pack(side="left")
        _text(head, "● victoria   ● derrota", 10, C["txt3"]).pack(side="right")
        self.canvas = tk.Canvas(cin, bg=C["card"], height=190, highlightthickness=0, bd=0)
        self.canvas.pack(fill="x", pady=(6, 0))
        self.canvas.bind("<Configure>", lambda _e: self._draw_chart())

        sbox, self.side = card(mid)
        sbox.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        _text(self.side, "Elegí una cuenta.", 11, C["txt3"]).pack(fill="x")

        hbox, hin = card(self.scroll)
        hbox.pack(fill="x", pady=(12, 16))
        _text(hin, "HÉROES EN RANKED · ÚLTIMAS PARTIDAS", 10, C["txt3"], bold=True).pack(fill="x")
        self.hero_table = ctk.CTkFrame(hin, fg_color="transparent")
        self.hero_table.pack(fill="x", pady=(6, 0))

    def _tile(self, parent, col: int, title: str) -> tuple[ctk.CTkLabel, ctk.CTkLabel]:
        tile = ctk.CTkFrame(parent, fg_color=C["card"], corner_radius=10, border_width=1, border_color=C["border"])
        tile.grid(row=0, column=col, sticky="nsew", padx=(0, 8) if col < 3 else 0)
        _text(tile, title, 10, C["txt3"], bold=True).pack(fill="x", padx=12, pady=(10, 0))
        value = _text(tile, "—", 20, C["txt"], bold=True)
        value.pack(fill="x", padx=12)
        sub = _text(tile, "", 11, C["txt2"])
        sub.pack(fill="x", padx=12, pady=(0, 10))
        return value, sub

    # ── Datos ────────────────────────────────────────────────────────────────
    def set_accounts(self):
        accounts = self.ctx.accounts()
        names = [f"  {a['name']}" for a in accounts] or ["  (sin cuentas)"]
        self.acc_combo.configure(values=names)
        main = self.ctx.main_account()
        self.acc_combo.set(f"  {main['name']}" if main else names[0])
        self._reload()

    def _selected_account(self) -> dict | None:
        name = self.acc_combo.get().strip()
        return next((a for a in self.ctx.accounts() if a["name"] == name), None)

    def _reload(self, force: bool = False):
        acc = self._selected_account()
        if not acc:
            return
        if not force and acc["steam_id3"] == self._loading_for:
            return
        self._loading_for = acc["steam_id3"]
        self.sync_status.configure(text=f"Descargando ranked de {acc['name']}...", text_color=C["amber"])
        threading.Thread(target=self._load, args=(acc, force), daemon=True).start()

    def _load(self, acc: dict, force: bool):
        cfg = self.ctx.cfg
        ttl = 0 if force else cfg.cache_ttl_seconds
        rank_tier = acc.get("rank_tier")
        matches: list[dict] = []
        msg = ""
        if cfg.stratz_api_token:
            # Stratz: posición jugada por partida, GPM/XPM/IMP. Si falla, OpenDota.
            matches, tier, msg = stratz.fetch_player_matches(acc["steam_id3"], cfg.ranked_matches_limit,
                                                             cfg.stratz_api_token, ttl)
            rank_tier = rank_tier or tier
            if matches:
                # Stratz no trae rango promedio del lobby ni tamaño de party: se completan desde OpenDota.
                od, _ = performance.fetch_ranked_matches(acc["steam_id3"], cfg.ranked_matches_limit, ttl)
                extra = {m.get("match_id"): m for m in od}
                for m in matches:
                    src = extra.get(m["match_id"])
                    if src:
                        m["average_rank"] = m["average_rank"] or src.get("average_rank")
                        m["party_size"] = src.get("party_size")
        if not matches:
            matches, msg = performance.fetch_ranked_matches(acc["steam_id3"], cfg.ranked_matches_limit, ttl)
        if not rank_tier:
            rank_tier = opendota.fetch_profile(acc["steam_id3"], cfg.cache_ttl_seconds).get("rank_tier")
        history = performance.record_snapshot(acc["steam_id3"], rank_tier)
        summary = performance.summarize(matches)
        series = performance.estimate_mmr_series(matches[:50])
        self.ctx.log(f"Rendimiento {acc['name']}: {msg}")
        self.after(0, self._render, acc, rank_tier, history, summary, series, msg)

    # ── Render ───────────────────────────────────────────────────────────────
    def _render(self, acc: dict, rank_tier, history: list[dict], summary: dict,
                series: list[tuple[dict, int | None]], msg: str):
        self.sync_status.configure(text=msg, text_color=C["txt3"] if summary["total"] else C["red"])

        label, color, stars = medal_for_tier(rank_tier)
        self.tiles["medal"][0].configure(text=f"{label}{' ★' * stars}", text_color=color)
        self.tiles["medal"][1].configure(text=f"rank_tier {rank_tier or '?'} · OpenDota")

        est = [e for _, e in series if e is not None]
        self.tiles["mmr"][0].configure(text=f"≈ {est[-1]:,}".replace(",", " ") if est else "N/D")
        nm = performance.next_medal(rank_tier, est[-1] if est else None)
        if nm and nm["missing_mmr"] is not None:
            target, _, tstars = medal_for_tier(nm["target_tier"])
            self.tiles["mmr"][1].configure(
                text=f"→ {target}{' ★' * tstars}: faltan ≈ {nm['missing_mmr']} MMR ({nm['net_wins']} victorias netas)",
            )
        else:
            self.tiles["mmr"][1].configure(text="por rango promedio del lobby")

        for wdg in self.insights_box.winfo_children():
            wdg.destroy()
        found = performance.insights(summary, self.ctx.hero_map())
        if not found:
            _text(self.insights_box, "Todavía no hay suficientes partidas para leer una tendencia.", 11,
                  C["txt3"]).pack(fill="x")
        for tone, text in found:
            color = {"ok": C["green"], "warn": C["amber"]}.get(tone, C["txt"])
            mark = {"ok": "✔", "warn": "⚠"}.get(tone, "•")
            row = ctk.CTkFrame(self.insights_box, fg_color=C["card"], corner_radius=8)
            row.pack(fill="x", pady=2)
            _text(row, f"{mark}  {text}", 12, color, wraplength=760, justify="left").pack(fill="x", padx=10, pady=6)

        w, lose, rate = summary["windows"][20]
        self.tiles["last20"][0].configure(text=_pct(rate), text_color=_rate_color(rate))
        _, _, r50 = summary["windows"][50]
        self.tiles["last20"][1].configure(text=f"{w} – {lose} · últimas 50: {_pct(r50)}")

        streak = summary["streak"]
        self.tiles["streak"][0].configure(text=f"{streak:+d}" if streak else "—",
                                          text_color=C["green"] if streak > 0 else C["red"] if streak < 0 else C["txt"])
        streak_txt = "victorias seguidas" if streak > 0 else "derrotas seguidas" if streak else ""
        self.tiles["streak"][1].configure(text=streak_txt)

        self._series = series
        self.chart_title.configure(text=f"MMR ESTIMADO · ÚLTIMAS {len(series)} RANKED")
        self._draw_chart()

        for wdg in self.side.winfo_children():
            wdg.destroy()
        _text(self.side, "PROGRESO · medalla guardada en cada apertura", 10, C["txt3"], bold=True).pack(fill="x")
        for snap in history[-5:]:
            lbl, col, st = medal_for_tier(snap.get("rank_tier"))
            day = datetime.fromisoformat(snap["date"]).strftime("%d %b %Y")
            self._kv(day, f"{lbl}{' ★' * st}", col)
        _text(self.side, "SOLO VS PARTY", 10, C["txt3"], bold=True).pack(fill="x", pady=(10, 0))
        for name, (_sw, sg, sr) in (("Solo", summary["solo"]), ("Party", summary["party"])):
            self._kv(f"{name} ({sg} pj)", _pct(sr), _rate_color(sr))
        _text(self.side, "POR HORARIO", 10, C["txt3"], bold=True).pack(fill="x", pady=(10, 0))
        for name, (_pw, pg, pr) in summary["periods"].items():
            self._kv(f"{name.capitalize()} ({pg} pj)", _pct(pr), _rate_color(pr))
        positions = {p: v for p, v in (summary.get("positions") or {}).items() if v[1]}
        if positions:
            _text(self.side, "POR POSICIÓN (Stratz)", 10, C["txt3"], bold=True).pack(fill="x", pady=(10, 0))
            for pos, (_w, g, r) in sorted(positions.items(), key=lambda kv: -kv[1][1]):
                self._kv(f"Pos {pos} ({g} pj)", _pct(r), _rate_color(r))

        for wdg in self.hero_table.winfo_children():
            wdg.destroy()
        hero_map = self.ctx.hero_map()
        with_gpm = any(h.get("gpm") for h in summary["heroes"])
        heads = ("HÉROE", "PARTIDAS", "WINRATE", "KDA") + (("GPM", "XPM") if with_gpm else ()) + ("TENDENCIA",)
        for c, h in enumerate(heads):
            _text(self.hero_table, h, 10, C["txt3"], bold=True).grid(row=0, column=c, sticky="w", padx=(0, 18))
        self.hero_table.columnconfigure(0, weight=1)
        for r, h in enumerate(summary["heroes"][:MAX_HERO_ROWS], 1):
            cells = [
                (hero_map.get(h["hero_id"], f"#{h['hero_id']}"), C["txt"]), (str(h["games"]), C["txt2"]),
                (_pct(h["wr"]), _rate_color(h["wr"])), (f"{h['kda']:.1f}", C["txt2"]),
            ]
            if with_gpm:
                cells += [(f"{h['gpm']:.0f}" if h.get("gpm") else "—", C["txt2"]),
                          (f"{h['xpm']:.0f}" if h.get("xpm") else "—", C["txt2"])]
            trend_color = C["green"] if h["trend"] == "↑" else C["red"] if h["trend"] == "↓" else C["txt2"]
            cells.append((h["trend"], trend_color))
            for c, (txt, col) in enumerate(cells):
                _text(self.hero_table, txt, 12, col).grid(row=r, column=c, sticky="w", padx=(0, 18), pady=1)
        if not summary["heroes"]:
            _text(self.hero_table, "Sin partidas ranked visibles. ¿Activaste 'Exponer datos de partida' en Dota?",
                  11, C["txt3"]).grid(row=1, column=0, columnspan=5, sticky="w")

    def _kv(self, key: str, value: str, color: str = C["txt"]):
        row = ctk.CTkFrame(self.side, fg_color="transparent")
        row.pack(fill="x")
        _text(row, key, 11, C["txt2"]).pack(side="left")
        _text(row, value, 11, color, bold=True, anchor="e").pack(side="right")

    def _draw_chart(self):
        cv = self.canvas
        cv.delete("all")
        pts = [(m, e) for m, e in self._series if e is not None]
        width, height = cv.winfo_width(), int(cv["height"])
        if len(pts) < 2 or width < 80:
            cv.create_text(width // 2, height // 2, text="Sin datos suficientes", fill=C["txt3"], font=("Segoe UI", 11))
            return
        values = [e for _, e in pts]
        lo = (min(values) // 100) * 100 - 50
        hi = (max(values) // 100 + 1) * 100 + 50
        left, right, top, bottom = 48, 14, 12, 26
        x = lambda i: left + i * (width - left - right) / (len(pts) - 1)  # noqa: E731
        y = lambda v: top + (1 - (v - lo) / (hi - lo)) * (height - top - bottom)  # noqa: E731
        step = 100 if hi - lo <= 600 else 200
        v = lo + 50
        while v <= hi:
            cv.create_line(left, y(v), width - right, y(v), fill=C["border"])
            cv.create_text(left - 6, y(v), text=f"{v:,}".replace(",", " "), anchor="e",
                           fill=C["txt3"], font=("Segoe UI", 9))
            v += step
        poly = [(x(i), y(e)) for i, (_, e) in enumerate(pts)]
        cv.create_polygon(*poly, (x(len(pts) - 1), y(lo)), (x(0), y(lo)),
                          fill=C["accent3"], outline="", stipple="gray25")
        cv.create_line(*poly, fill=C["accent"], width=2, smooth=False)
        for i, (m, e) in enumerate(pts):
            win = opendota.is_win(m)
            r = 4 if i == len(pts) - 1 else 2.5
            cv.create_oval(x(i) - r, y(e) - r, x(i) + r, y(e) + r, fill=C["green"] if win else C["red"], outline="")
        for n in sorted({1, len(pts) // 2, len(pts)}):
            cv.create_text(x(n - 1), height - 10, text=f"#{n}", fill=C["txt3"], font=("Segoe UI", 9))
        last = values[-1]
        cv.create_text(x(len(pts) - 1) - 6, y(last) - 10, text=f"≈ {last:,}".replace(",", " "), anchor="e",
                       fill=C["txt"], font=("Segoe UI", 10, "bold"))
