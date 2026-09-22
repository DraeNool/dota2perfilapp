"""Ventana principal de Dota 2 Config Sync."""

import concurrent.futures
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from . import __version__, autoexec, dota2protracker, fileops, hero_grid, http, opendota, steam
from .config import AppConfig
from .paths import resource_path
from .theme import C, download_avatar
from .widgets import AccountCard, StatusBar

ICON_FILE = "DotaConfigSyncByDraenool.ico"

log = logging.getLogger(__name__)

LOG_MAX_LINES = 500


ALL_ACCOUNTS_LABEL = "  ★ Todas las cuentas con Dota 2"


def _names_preview(names: list[str], limit: int = 8) -> str:
    """Resumen legible de una lista de nombres (con '...' si se trunca)."""
    if not names:
        return "ninguno"
    return ", ".join(names[:limit]) + ("..." if len(names) > limit else "")


def _age_text(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return "hace un momento"
    if s < 3600:
        return f"hace {s // 60} min"
    if s < 86400:
        return f"hace {s // 3600} h"
    return f"hace {s // 86400} día(s)"


def fetch_account_profile(acc: dict, cfg: AppConfig) -> dict:
    """Reúne avatar, horas (Steam) y rango/winrate/último match (OpenDota)."""
    updates = {}
    updates.update(steam.fetch_steam_profile(acc["steam_id64"], cfg.steam_api_key))
    hours = steam.fetch_steam_hours(acc["steam_id64"], cfg.steam_api_key)
    if hours is not None:
        updates["total_hours"] = hours
    updates.update(opendota.fetch_profile(acc["steam_id3"], cfg.cache_ttl_seconds))
    return updates


class App(ctk.CTk):
    def __init__(self, cfg: AppConfig):
        super().__init__()
        self.cfg = cfg
        self.title("Dota 2 Config Sync")
        self.geometry("900x860")
        self.minsize(840, 740)
        self.configure(fg_color=C["bg"])

        self.steam_path = None
        self.accounts = []

        self._set_window_icon()
        self._build_ui()
        self._detect_steam()

    def _set_window_icon(self):
        ico = resource_path(ICON_FILE)
        if not ico.exists():
            return
        try:
            self.iconbitmap(str(ico))
        except Exception as e:  # noqa: BLE001 — un icono ausente no debe tumbar la app
            log.debug("No se pudo aplicar el icono de ventana: %s", e)

    # ── Construcción de UI ───────────────────────────────────────────────────
    def _build_ui(self):
        hdr = ctk.CTkFrame(self, fg_color=C["header"], corner_radius=0)
        hdr.pack(fill="x")

        lf = ctk.CTkFrame(hdr, fg_color="transparent")
        lf.pack(side="left", padx=20, pady=12)
        ctk.CTkLabel(lf, text="⚙", font=ctk.CTkFont(size=22), text_color=C["accent"]).pack(side="left", padx=(0, 8))
        ctk.CTkLabel(
            lf, text="Dota 2  Config Sync", font=ctk.CTkFont(size=16, weight="bold"), text_color=C["txt"],
        ).pack(side="left")
        ctk.CTkLabel(lf, text=f"  v{__version__}", font=ctk.CTkFont(size=11), text_color=C["txt3"]).pack(side="left")

        rf = ctk.CTkFrame(hdr, fg_color="transparent")
        rf.pack(side="right", padx=12, pady=10)
        ctk.CTkButton(
            rf, text="⟳  Recargar perfiles", width=145, height=30, fg_color="transparent",
            border_width=1, border_color=C["border"], text_color=C["txt2"], hover_color=C["bg2"],
            font=ctk.CTkFont(size=12), command=self._reload_profiles,
        ).pack(side="left", padx=(0, 8))

        self.scroll = ctk.CTkScrollableFrame(
            self, fg_color="transparent", scrollbar_button_color=C["border"],
            scrollbar_button_hover_color=C["accent2"],
        )
        self.scroll.pack(fill="both", expand=True, padx=20, pady=(16, 0))

        self._build_api_key_row()

        self._lbl(self.scroll, "CUENTAS STEAM")
        row = ctk.CTkFrame(self.scroll, fg_color="transparent")
        row.pack(fill="x", pady=(6, 0))
        row.columnconfigure(0, weight=1)
        row.columnconfigure(2, weight=1)

        self.card_src = AccountCard(row, role="source", on_change=self._on_src_change)
        self.card_src.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        arrow = ctk.CTkFrame(row, fg_color="transparent", width=54)
        arrow.grid(row=0, column=1)
        ctk.CTkLabel(arrow, text="→", font=ctk.CTkFont(size=26), text_color=C["accent"]).pack(pady=(50, 0))
        ctk.CTkLabel(
            arrow, text="copiar\n570", font=ctk.CTkFont(size=10), text_color=C["txt3"], justify="center",
        ).pack()

        self.card_dst = AccountCard(row, role="target", on_change=self._on_dst_change)
        self.card_dst.grid(row=0, column=2, sticky="ew", padx=(8, 0))

        self._lbl(self.scroll, "COPIAR CONFIGURACIÓN  (carpeta 570: origen → destino)", pady=(20, 6))
        sync_card = ctk.CTkFrame(
            self.scroll, fg_color=C["bg2"], corner_radius=12, border_width=1, border_color=C["src"],
        )
        sync_card.pack(fill="x")
        sync_inner = ctk.CTkFrame(sync_card, fg_color="transparent")
        sync_inner.pack(fill="x", padx=16, pady=16)

        self.op_desc = ctk.CTkLabel(
            sync_inner, text="Selecciona origen y destino para ver la operación",
            font=ctk.CTkFont(size=12), text_color=C["txt2"], anchor="w", wraplength=760, justify="left",
        )
        self.op_desc.pack(fill="x", pady=(0, 12))

        self.btn_replace = ctk.CTkButton(
            sync_inner, text="↻   Reemplazar carpeta 570", height=46, corner_radius=10,
            fg_color=C["accent2"], hover_color=C["accent3"], text_color="#ffffff",
            font=ctk.CTkFont(size=14, weight="bold"), command=self._do_replace_570,
        )
        self.btn_replace.pack(fill="x")

        self.progress = ctk.CTkProgressBar(sync_inner, progress_color=C["accent"], fg_color=C["card"])
        self.progress.set(0)

        self.sync_result = ctk.CTkLabel(
            sync_inner, text="", font=ctk.CTkFont(size=11), text_color=C["txt2"],
            anchor="w", wraplength=760, justify="left",
        )
        self.sync_result.pack(fill="x", pady=(10, 0))

        self._build_hero_grid_section()
        self._build_autoexec_section()

        log_hdr = ctk.CTkFrame(self.scroll, fg_color="transparent")
        log_hdr.pack(fill="x", pady=(20, 6))
        ctk.CTkLabel(
            log_hdr, text="REGISTRO DE ACTIVIDAD", font=ctk.CTkFont(size=10, weight="bold"),
            text_color=C["txt3"], anchor="w",
        ).pack(side="left")
        ctk.CTkButton(
            log_hdr, text="Copiar", width=70, height=24, fg_color="transparent",
            border_width=1, border_color=C["border"], text_color=C["txt2"], hover_color=C["bg2"],
            font=ctk.CTkFont(size=11), command=self._copy_log,
        ).pack(side="right")
        self.log = ctk.CTkTextbox(
            self.scroll, height=240, fg_color=C["card"], border_color=C["border"],
            border_width=1, corner_radius=10, font=ctk.CTkFont(size=11, family="Courier"),
            text_color=C["txt2"],
        )
        self.log.pack(fill="x", pady=(0, 16))
        self.log.configure(state="disabled")

        self.status = StatusBar(self)
        self.status.pack(fill="x", side="bottom")

    def _build_api_key_row(self):
        self._lbl(self.scroll, "STEAM API KEY  (avatares y horas reales)")
        box = ctk.CTkFrame(self.scroll, fg_color=C["bg2"], corner_radius=12, border_width=1, border_color=C["border"])
        box.pack(fill="x", pady=(6, 0))
        inner = ctk.CTkFrame(box, fg_color="transparent")
        inner.pack(fill="x", padx=14, pady=12)
        inner.columnconfigure(0, weight=1)

        self.api_entry = ctk.CTkEntry(
            inner, placeholder_text="Pega tu Steam Web API key (se guarda en config.json)",
            show="•", fg_color=C["card"], border_color=C["border"], text_color=C["txt"],
            font=ctk.CTkFont(size=12),
        )
        if self.cfg.steam_api_key:
            self.api_entry.insert(0, self.cfg.steam_api_key)
        self.api_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        self.api_show = ctk.CTkCheckBox(
            inner, text="Ver", width=20, font=ctk.CTkFont(size=11), text_color=C["txt2"],
            fg_color=C["accent2"], hover_color=C["accent3"], border_color=C["border"],
            command=self._toggle_api_visibility,
        )
        self.api_show.grid(row=0, column=1, padx=(0, 8))

        ctk.CTkButton(
            inner, text="Guardar", width=90, height=30, fg_color=C["accent2"], hover_color=C["accent"],
            text_color=C["txt"], font=ctk.CTkFont(size=12, weight="bold"), command=self._save_api_key,
        ).grid(row=0, column=2)

    def _toggle_api_visibility(self):
        self.api_entry.configure(show="" if self.api_show.get() else "•")

    def _save_api_key(self):
        key = self.api_entry.get().strip()
        self.cfg.set_steam_api_key(key)
        self.cfg.save()
        if key:
            self._log("API key guardada en config.json. Recargando perfiles...")
            self.status.set("API key guardada", "ok")
            self._reload_profiles()
        else:
            self._log("API key vacía; avatares y horas no estarán disponibles.")
            self.status.set("Sin API key", "info")

    # ── Autoexec ─────────────────────────────────────────────────────────────
    def _build_autoexec_section(self):
        self._lbl(self.scroll, "AUTOEXEC.CFG  (perfil gráfico de Dota 2)", pady=(20, 6))
        box = ctk.CTkFrame(self.scroll, fg_color=C["bg2"], corner_radius=12, border_width=1, border_color=C["dst"])
        inner = ctk.CTkFrame(box, fg_color="transparent")
        box.pack(fill="x")
        inner.pack(fill="x", padx=16, pady=16)
        inner.columnconfigure(0, weight=1)

        ctk.CTkLabel(
            inner,
            text="Genera un autoexec.cfg en tu instalación de Dota a partir de un perfil. Solo ajustes gráficos.",
            font=ctk.CTkFont(size=12), text_color=C["txt2"], anchor="w", wraplength=760, justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))

        profiles = autoexec.list_profiles()
        self.autoexec_profiles = {p.stem: p for p in profiles}
        names = list(self.autoexec_profiles) or ["(sin perfiles)"]

        self.autoexec_combo = ctk.CTkComboBox(
            inner, values=names, fg_color=C["card"], border_color=C["border"],
            button_color=C["accent2"], button_hover_color=C["accent"], text_color=C["txt"],
            dropdown_fg_color=C["bg2"], dropdown_hover_color=C["card"], dropdown_text_color=C["txt"],
            font=ctk.CTkFont(size=13),
        )
        self.autoexec_combo.set(names[0])
        self.autoexec_combo.grid(row=1, column=0, sticky="ew", padx=(0, 8))

        self.btn_autoexec = ctk.CTkButton(
            inner, text="⚡  Generar autoexec.cfg", width=200, height=34, corner_radius=10,
            fg_color="transparent", border_width=2, border_color=C["dst"],
            hover_color=C["badge_dst"], text_color=C["dst"],
            font=ctk.CTkFont(size=13, weight="bold"), command=self._do_generate_autoexec,
        )
        self.btn_autoexec.grid(row=1, column=1)

    def _do_generate_autoexec(self):
        name = self.autoexec_combo.get()
        profile = self.autoexec_profiles.get(name)
        if not profile:
            messagebox.showwarning("Sin perfil", "No hay perfiles en la carpeta autoexec_profiles.")
            return
        if not self.steam_path:
            messagebox.showerror("Steam no detectado", "No se encontró Steam, no puedo localizar Dota.")
            return

        cfg_dir = autoexec.find_dota_cfg_dir(self.steam_path)
        if not cfg_dir:
            messagebox.showerror(
                "Dota no encontrado",
                "No se localizó la instalación de Dota 2 (carpeta 'dota 2 beta').\n"
                "Verifica que Dota esté instalado.",
            )
            return

        if not messagebox.askyesno(
            "Generar autoexec.cfg",
            f"Perfil: {name}\nDestino: {cfg_dir / 'autoexec.cfg'}\n\n"
            f"Se respaldará el autoexec.cfg actual (si existe).\n¿Continuar?",
        ):
            return

        try:
            out = autoexec.generate(profile, cfg_dir)
        except OSError as e:
            self._log(f"✘ Error autoexec: {e}")
            messagebox.showerror("Error", f"No se pudo escribir autoexec.cfg:\n\n{e}")
            return

        self.status.set("✔ autoexec.cfg generado", "ok")
        self._log(f"✔ autoexec.cfg ({name}) escrito en {out}")
        messagebox.showinfo(
            "autoexec.cfg generado",
            f"Perfil '{name}' escrito en:\n{out}\n\n"
            f"Recuerda añadir la opción de lanzamiento -console en Dota.\n"
            f"El archivo anterior quedó como autoexec_backup_<fecha>.cfg.",
        )

    def _lbl(self, parent, text: str, pady=(12, 0)):
        ctk.CTkLabel(
            parent, text=text, font=ctk.CTkFont(size=10, weight="bold"),
            text_color=C["txt3"], anchor="w",
        ).pack(anchor="w", pady=pady)

    # ── Log (con truncado) ───────────────────────────────────────────────────
    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log.configure(state="normal")
        self.log.insert("end", f"[{ts}] {msg}\n")
        line_count = int(self.log.index("end-1c").split(".")[0])
        if line_count > LOG_MAX_LINES:
            self.log.delete("1.0", f"{line_count - LOG_MAX_LINES}.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _log_async(self, msg: str):
        self.after(0, self._log, msg)

    def _set_status_async(self, text: str, state: str = "ok"):
        self.after(0, self.status.set, text, state)

    # ── Detección de Steam ───────────────────────────────────────────────────
    def _detect_steam(self):
        self.status.set("Buscando instalación de Steam...", "loading")
        threading.Thread(target=self._detect_thread, daemon=True).start()

    def _detect_thread(self):
        path = steam.find_steam_path()
        self.after(0, self._on_steam_found, path)

    def _on_steam_found(self, steam_path: Path | None):
        if not steam_path:
            self.status.set("Steam no encontrado", "error")
            self._log("ERROR: Steam no encontrado.")
            return

        self.steam_path = steam_path
        self._log(f"Steam detectado: {steam_path}")

        accounts = steam.get_steam_accounts(steam_path)
        self.accounts = accounts
        self._log(f"{len(accounts)} cuenta(s) encontrada(s):")
        for a in accounts:
            self._log(f"  ► {a['name']}  (ID64: {a['steam_id64']})  {'✔ 570' if a['has_dota'] else '✘'}")

        self.card_src.load_accounts(accounts)
        self.card_dst.load_accounts(accounts)
        self._load_grid_targets()
        self._preload_hero_map()
        self.status.set(f"{len(accounts)} cuenta(s) encontradas", "ok")
        self.status.set_right(r"userdata\...\570")

        main_idx = next((i for i, a in enumerate(accounts) if a["steam_id64"] == self.cfg.preferred_main_id64), None)
        if main_idx is not None:
            self.card_src.select_by_index(main_idx)
        elif accounts:
            self.card_src.select_by_index(0)

        if len(accounts) >= 2:
            dst_idx = 1 if main_idx in (None, 0) else 0
            self.card_dst.select_by_index(dst_idx)

        self._update_op_desc()
        self._reload_profiles()

    # ── Perfiles ─────────────────────────────────────────────────────────────
    def _reload_profiles(self):
        if not self.accounts:
            self.status.set("Primero espera que carguen las cuentas", "info")
            return
        if not self.cfg.steam_api_key:
            self._log("Aviso: sin API key, avatares y horas no se cargarán (rango/winrate sí vía OpenDota).")
        self.status.set(f"Cargando {len(self.accounts)} perfil(es)...", "loading")
        self._log(f"Iniciando descarga de perfiles ({len(self.accounts)} cuentas)...")
        threading.Thread(target=self._profiles_thread, daemon=True).start()

    def _profiles_thread(self):
        def fetch_one(acc: dict):
            try:
                acc.update(fetch_account_profile(acc, self.cfg))
                if acc.get("avatar_url") and not acc.get("avatar_img"):
                    img = download_avatar(acc["avatar_url"])
                    if img:
                        acc["avatar_img"] = img
            except Exception as e:  # noqa: BLE001 — registramos en el panel, no abortamos la tanda
                log.exception("Error cargando perfil %s", acc.get("name"))
                self._log_async(f"  ⚠ Perfil {acc['name']}: {e}")
            self.after(0, self._on_account_loaded, acc)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(fetch_one, self.accounts))
        self.after(0, self._on_all_profiles_done)

    def _on_account_loaded(self, acc: dict):
        hours = acc.get("total_hours")
        winrate = acc.get("competitive_winrate")
        wr_txt = f"{winrate * 100:.1f}%" if winrate is not None else "N/D"
        hours_txt = f"{hours:.0f} h" if hours is not None else "N/D"
        self._log(
            f"  ✔ {acc['name']}  |  avatar={'sí' if acc.get('avatar_img') else 'no'}"
            f"  |  rango={acc.get('rank_tier', '?')}  |  wr={wr_txt}"
            f"  |  horas={hours_txt}  |  último={acc.get('last_hero') or 'N/D'}/{acc.get('last_result') or 'N/D'}"
            f"/KDA {acc.get('last_kda') or 'N/D'}"
        )
        self.card_src.refresh_selected()
        self.card_dst.refresh_selected()
        self._sync_combo_names()

    def _on_all_profiles_done(self):
        n = len(self.accounts)
        wa = sum(1 for a in self.accounts if a.get("avatar_img"))
        wr = sum(1 for a in self.accounts if a.get("rank_tier"))
        wh = sum(1 for a in self.accounts if a.get("total_hours") is not None)
        ww = sum(1 for a in self.accounts if a.get("competitive_winrate") is not None)
        wl = sum(1 for a in self.accounts if a.get("last_hero"))
        summary = (
            f"✔ {wa}/{n} avatares  •  {wr}/{n} rangos  •  {wh}/{n} horas"
            f"  •  {ww}/{n} winrate  •  {wl}/{n} último héroe"
        )
        self.status.set(summary, "ok")
        self._log("Carga de perfiles completada.")

    def _sync_combo_names(self):
        names = [f"  {a['name']}" for a in self.accounts]
        for card in (self.card_src, self.card_dst):
            card.accounts = self.accounts
            old = card.combo.get()
            card.combo.configure(values=names if names else ["  (sin cuentas)"])
            if card.selected:
                new_name = f"  {card.selected['name']}"
                card.combo.set(new_name if new_name in names else old)
        self._load_grid_targets()

    # ── Callbacks de selección ───────────────────────────────────────────────
    def _on_src_change(self, acc: dict):
        self._log(f"Origen → {acc['name']}")
        self._update_op_desc()

    def _on_dst_change(self, acc: dict):
        self._log(f"Destino → {acc['name']}")
        self._update_op_desc()

    def _update_op_desc(self):
        src, dst = self.card_src.selected, self.card_dst.selected
        if src and dst:
            if src["steam_id3"] == dst["steam_id3"]:
                self.op_desc.configure(text="⚠ La cuenta origen y destino son la misma.", text_color=C["amber"])
                return
            self.op_desc.configure(
                text=(
                    f'Se reemplazará TODO el contenido de la carpeta 570 de "{src["name"]}" '
                    f'en la cuenta "{dst["name"]}".\n'
                    f"Se hará backup de la 570 actual del destino antes de sobrescribir."
                ),
                text_color=C["txt"],
            )
        else:
            self.op_desc.configure(text="Selecciona origen y destino para ver la operación", text_color=C["txt2"])

    # ── Reemplazar 570 ───────────────────────────────────────────────────────
    def _do_replace_570(self):
        src, dst = self.card_src.selected, self.card_dst.selected
        if not src or not dst:
            messagebox.showwarning("Faltan cuentas", "Selecciona origen y destino.")
            return
        if src["steam_id3"] == dst["steam_id3"]:
            messagebox.showwarning("Misma cuenta", "Origen y destino son la misma cuenta.")
            return

        src_570 = src["path"] / "570"
        dst_570 = dst["path"] / "570"
        if not src_570.exists():
            messagebox.showerror("Sin carpeta 570", f"No existe:\n{src_570}")
            return

        total = len(fileops.list_files(src_570))
        if total == 0:
            messagebox.showwarning("Carpeta vacía", f"Sin archivos en:\n{src_570}")
            return

        if steam.is_dota_running():
            if not messagebox.askyesno(
                "Dota 2 está abierto",
                "Dota 2 parece estar en ejecución. Steam Cloud puede sobrescribir los "
                "cambios al cerrar el juego.\n\nSe recomienda cerrar Dota antes de continuar.\n\n"
                "¿Continuar de todos modos?",
            ):
                return

        backup_dir = dst["path"] / fileops.backup_dir_name()
        if not messagebox.askyesno(
            "Confirmar reemplazo total",
            f'ORIGEN:  {src["name"]}\n  {src_570}\n\n'
            f'DESTINO: {dst["name"]}\n  {dst_570}\n\n'
            f"Se reemplazará COMPLETAMENTE la carpeta 570 del destino.\n"
            f"Backup en: {backup_dir}\nArchivos aprox.: {total}\n\n¿Continuar?",
        ):
            return

        self.btn_replace.configure(state="disabled", text="⏳  Reemplazando...")
        self.btn_grid.configure(state="disabled")
        self.progress.pack(fill="x", pady=(12, 0))
        self.progress.set(0)
        self.status.set("Preparando reemplazo...", "loading")
        self.sync_result.configure(text="Iniciando proceso...", text_color=C["amber"])
        self._log(f'\n── Reemplazo 570: {src["name"]} → {dst["name"]} ──')

        threading.Thread(
            target=self._replace_570_worker, args=(src_570, dst_570, backup_dir, src, dst), daemon=True,
        ).start()

    def _replace_570_worker(self, src_570, dst_570, backup_dir, src, dst):
        def progress(copied, total, rel):
            self._log_async(f"  ✔ {copied}/{total}: {rel}")
            self._set_status_async(f"Copiando {copied}/{total}...", "loading")
            frac = copied / total if total else 0
            self.after(0, self._update_replace_progress, frac, copied, total, rel)

        try:
            copied = fileops.replace_570(
                src_570, dst_570, backup_dir,
                progress_fn=progress,
                status_fn=lambda t: self._set_status_async(t, "loading"),
            )
            self.after(0, self._on_replace_done, True, copied, copied, src, dst, backup_dir, "")
        except Exception as e:  # noqa: BLE001
            log.exception("Error en reemplazo 570")
            self.after(0, self._on_replace_done, False, 0, 0, src, dst, backup_dir, str(e))

    def _update_replace_progress(self, frac, copied, total, rel):
        self.progress.set(frac)
        self.sync_result.configure(text=f"Copiando {copied}/{total}: {rel}", text_color=C["txt2"])

    def _on_replace_done(self, ok, copied, total, src, dst, backup_dir, error):
        self.btn_replace.configure(state="normal", text="↻   Reemplazar carpeta 570")
        self.btn_grid.configure(state="normal")
        if ok:
            self.progress.set(1)
            self.status.set(f"✔ Reemplazo completado: {copied}/{total} archivos", "ok")
            self.sync_result.configure(
                text=f'Listo. {copied} archivos copiados de "{src["name"]}" → "{dst["name"]}".\nBackup: {backup_dir}',
                text_color=C["green"],
            )
            self._log(f"✔ Reemplazo completado: {copied}/{total} archivos.")
            messagebox.showinfo(
                "Completado",
                f'Reemplazo exitoso.\nOrigen: {src["name"]}\nDestino: {dst["name"]}\n'
                f"Archivos: {copied}\nBackup: {backup_dir}",
            )
        else:
            self.status.set("Error durante el reemplazo", "error")
            self.sync_result.configure(text=f"Error: {error}", text_color=C["red"])
            self._log(f"✘ Error: {error}")
            messagebox.showerror("Error", f"Error durante el reemplazo:\n\n{error}")
        self.after(1500, self.progress.pack_forget)

    # ── Hero Grid ────────────────────────────────────────────────────────────
    def _build_hero_grid_section(self):
        self._lbl(self.scroll, "HERO GRID  (Meta Meta + Favoritos + Meta D2PT)", pady=(20, 6))
        box = ctk.CTkFrame(self.scroll, fg_color=C["bg2"], corner_radius=12, border_width=1, border_color=C["accent2"])
        box.pack(fill="x")
        inner = ctk.CTkFrame(box, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=16)
        inner.columnconfigure(1, weight=1)

        ctk.CTkLabel(
            inner,
            text="Genera hero_grid_config.json con el meta actual y los favoritos de la cuenta elegida. "
                 "No depende de origen/destino.",
            font=ctk.CTkFont(size=12), text_color=C["txt2"], anchor="w", wraplength=760, justify="left",
        ).grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 10))

        ctk.CTkLabel(inner, text="Cuenta", font=ctk.CTkFont(size=12), text_color=C["txt2"]).grid(
            row=1, column=0, padx=(0, 8),
        )
        self.grid_combo = ctk.CTkComboBox(
            inner, values=["  (cargando cuentas...)"], fg_color=C["card"], border_color=C["border"],
            button_color=C["accent2"], button_hover_color=C["accent"], text_color=C["txt"],
            dropdown_fg_color=C["bg2"], dropdown_hover_color=C["card"], dropdown_text_color=C["txt"],
            font=ctk.CTkFont(size=13),
        )
        self.grid_combo.set("  (cargando cuentas...)")
        self.grid_combo.grid(row=1, column=1, sticky="ew", padx=(0, 8))

        self.btn_grid = ctk.CTkButton(
            inner, text="🧩  Generar Hero Grid", width=200, height=34, corner_radius=10,
            fg_color="transparent", border_width=2, border_color=C["accent2"],
            hover_color=C["badge_src"], text_color=C["accent"],
            font=ctk.CTkFont(size=13, weight="bold"), command=self._do_generate_hero_grid,
        )
        self.btn_grid.grid(row=1, column=2)

        self.meta_status = ctk.CTkLabel(
            inner, text="", font=ctk.CTkFont(size=11), text_color=C["txt3"], anchor="w",
        )
        self.meta_status.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(8, 0))

        self.grid_progress = ctk.CTkProgressBar(inner, progress_color=C["accent"], fg_color=C["card"])
        self.grid_progress.set(0)

        result_row = ctk.CTkFrame(inner, fg_color="transparent")
        result_row.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        result_row.columnconfigure(0, weight=1)
        self.grid_result = ctk.CTkLabel(
            result_row, text="", font=ctk.CTkFont(size=11), text_color=C["txt2"],
            anchor="w", wraplength=620, justify="left",
        )
        self.grid_result.grid(row=0, column=0, sticky="ew")
        self.btn_open_grid = ctk.CTkButton(
            result_row, text="📂  Abrir carpeta", width=130, height=30, fg_color="transparent",
            border_width=1, border_color=C["border"], text_color=C["txt2"], hover_color=C["bg2"],
            font=ctk.CTkFont(size=11), command=self._open_grid_folder, state="disabled",
        )
        self.btn_open_grid.grid(row=0, column=1, padx=(8, 0), sticky="n")

        ctk.CTkLabel(
            inner, text="HÉROES COMFORT  (tu lista fija dentro de Meta Meta — nombres separados por coma)",
            font=ctk.CTkFont(size=10, weight="bold"), text_color=C["txt3"], anchor="w",
        ).grid(row=5, column=0, columnspan=3, sticky="ew", pady=(16, 4))

        self.comfort_entry = ctk.CTkEntry(
            inner, placeholder_text="Cargando héroes...", fg_color=C["card"], border_color=C["border"],
            text_color=C["txt"], font=ctk.CTkFont(size=12),
        )
        self.comfort_entry.grid(row=6, column=0, columnspan=2, sticky="ew", padx=(0, 8))
        ctk.CTkButton(
            inner, text="Guardar comfort", width=200, height=30, fg_color=C["accent2"], hover_color=C["accent"],
            text_color=C["txt"], font=ctk.CTkFont(size=12, weight="bold"), command=self._save_comfort,
        ).grid(row=6, column=2)

        ctk.CTkLabel(inner, text="Agregar", font=ctk.CTkFont(size=12), text_color=C["txt2"]).grid(
            row=7, column=0, padx=(0, 8), pady=(6, 0),
        )
        self.comfort_combo = ctk.CTkComboBox(
            inner, values=["  (cargando héroes...)"], fg_color=C["card"], border_color=C["border"],
            button_color=C["accent2"], button_hover_color=C["accent"], text_color=C["txt"],
            dropdown_fg_color=C["bg2"], dropdown_hover_color=C["card"], dropdown_text_color=C["txt"],
            font=ctk.CTkFont(size=12),
        )
        self.comfort_combo.set("  (cargando héroes...)")
        self.comfort_combo.grid(row=7, column=1, sticky="ew", padx=(0, 8), pady=(6, 0))
        ctk.CTkButton(
            inner, text="＋  Añadir a la lista", width=200, height=30, fg_color="transparent",
            border_width=1, border_color=C["border"], text_color=C["txt2"], hover_color=C["bg2"],
            font=ctk.CTkFont(size=12), command=self._add_comfort_hero,
        ).grid(row=7, column=2, pady=(6, 0))

        self._grid_out_dir: Path | None = None
        self._refresh_meta_status()

    def _grid_accounts(self) -> list[dict]:
        return [a for a in self.accounts if a["has_dota"]]

    def _load_grid_targets(self):
        names = [f"  {a['name']}" for a in self._grid_accounts()]
        values = [ALL_ACCOUNTS_LABEL, *names] if names else ["  (sin cuentas con Dota 2)"]
        self.grid_combo.configure(values=values)
        if self.grid_combo.get() in values:
            return
        main = next((a for a in self._grid_accounts() if a["steam_id64"] == self.cfg.preferred_main_id64), None)
        self.grid_combo.set(f"  {main['name']}" if main else values[1] if names else values[0])

    def _grid_targets(self) -> list[dict]:
        value = self.grid_combo.get()
        if value == ALL_ACCOUNTS_LABEL:
            return self._grid_accounts()
        acc = next((a for a in self._grid_accounts() if a["name"] == value.strip()), None)
        return [acc] if acc else []

    def _refresh_meta_status(self):
        patch, age = dota2protracker.cached_meta_status()
        if age is None:
            self.meta_status.configure(
                text="Meta D2PT: todavía no descargado — se baja al generar.", text_color=C["txt3"],
            )
            return
        stale = age > 86400
        self.meta_status.configure(
            text=f"Meta D2PT: parche {patch or 'desconocido'}  •  descargado {_age_text(age)}"
                 + ("  •  se actualizará al generar" if stale else ""),
            text_color=C["amber"] if stale else C["txt3"],
        )

    def _do_generate_hero_grid(self):
        targets = self._grid_targets()
        if not targets:
            messagebox.showwarning("Sin cuenta", "Selecciona una cuenta con Dota 2 instalado.")
            return

        if steam.is_dota_running() and not messagebox.askyesno(
            "Dota 2 está abierto",
            "Dota 2 parece estar en ejecución. Steam Cloud puede sobrescribir el hero grid "
            "al cerrar el juego.\n\nSe recomienda cerrar Dota antes de continuar.\n\n"
            "¿Continuar de todos modos?",
        ):
            return

        self.btn_grid.configure(state="disabled", text="⏳  Generando...")
        self.btn_replace.configure(state="disabled")
        self.btn_open_grid.configure(state="disabled")
        self.grid_progress.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        self.grid_progress.set(0)
        self.status.set("Consultando OpenDota y Dota2ProTracker...", "loading")
        self.grid_result.configure(
            text=f"Generando {len(targets)} hero grid(s)...", text_color=C["amber"],
        )
        threading.Thread(target=self._hero_grid_worker, args=(targets,), daemon=True).start()

    def _hero_grid_worker(self, targets: list[dict]):
        results = []
        total = len(targets)
        for i, acc in enumerate(targets, 1):
            self._log_async(f'\n── Hero Grid {i}/{total}: {acc["name"]} ──')
            self._set_status_async(f'Hero Grid {i}/{total}: {acc["name"]}...', "loading")
            try:
                payload, perf_names, recent_names, status_msg = hero_grid.build_hero_grid(
                    acc["steam_id3"], meta_meta=self.cfg.meta_meta,
                    favorites_limit=self.cfg.favorites_limit, recent_limit=self.cfg.recent_matches_limit,
                    ttl=self.cfg.cache_ttl_seconds, grids_ttl=self.cfg.meta_grids_ttl_seconds,
                    log_fn=self._log_async,
                )
                out_path = hero_grid.save_hero_grid(acc["path"] / "570", payload)
                self._log_async(f"✔ Guardado en {out_path}")
                self._log_async(f'  Performance ({len(perf_names)}): {", ".join(perf_names) or "ninguno"}')
                self._log_async(f'  Últimas 20 ({len(recent_names)}): {", ".join(recent_names) or "ninguno"}')
                results.append({
                    "acc": acc, "out": out_path, "perf": perf_names, "recent": recent_names,
                    "status": status_msg, "error": None,
                })
            except Exception as e:  # noqa: BLE001 — una cuenta fallida no frena las demás
                log.exception("Error generando hero grid para %s", acc.get("name"))
                self._log_async(f'✘ {acc["name"]}: {e}')
                results.append({"acc": acc, "error": str(e)})
            self.after(0, self.grid_progress.set, i / total)
        self.after(0, self._on_hero_grid_done, results)

    def _on_hero_grid_done(self, results: list[dict]):
        self.btn_grid.configure(state="normal", text="🧩  Generar Hero Grid")
        self.btn_replace.configure(state="normal")
        self.after(1500, self.grid_progress.grid_forget)
        self._refresh_meta_status()

        ok = [r for r in results if not r["error"]]
        failed = [r for r in results if r["error"]]
        if ok:
            self._grid_out_dir = ok[-1]["out"].parent
            self.btn_open_grid.configure(state="normal")

        if len(results) == 1:
            r = results[0]
            if r["error"]:
                self.status.set("Error generando Hero Grid", "error")
                self.grid_result.configure(text=f"Error: {r['error']}", text_color=C["red"])
                messagebox.showerror("Error", f"No se pudo generar el Hero Grid:\n\n{r['error']}")
                return
            perf_txt, recent_txt = _names_preview(r["perf"]), _names_preview(r["recent"])
            self.status.set("✔ Hero Grid generado", "ok")
            self.grid_result.configure(
                text=f"Guardado en:\n{r['out']}\n\n{r['status']}\n\nPerformance: {perf_txt}\nÚltimas 20: {recent_txt}",
                text_color=C["green"],
            )
            messagebox.showinfo(
                "Hero Grid generado",
                f"hero_grid_config.json guardado en:\n{r['out']}\n\n{r['status']}\n\n"
                f"Favoritos por performance: {perf_txt}\nÚltimas 20 partidas: {recent_txt}\n\n"
                f"El archivo anterior quedó como hero_grid_config_backup.json.",
            )
            return

        lines = [f'✔ {r["acc"]["name"]}' for r in ok] + [f'✘ {r["acc"]["name"]}: {r["error"]}' for r in failed]
        summary = f"{len(ok)}/{len(results)} hero grids generados"
        self.status.set(("✔ " if not failed else "⚠ ") + summary, "ok" if not failed else "error")
        self.grid_result.configure(
            text=summary + "\n" + "\n".join(lines), text_color=C["green"] if not failed else C["amber"],
        )
        messagebox.showinfo("Hero Grids", summary + "\n\n" + "\n".join(lines))

    # ── Héroes comfort ───────────────────────────────────────────────────────
    def _preload_hero_map(self):
        def worker():
            opendota.get_hero_map()
            self.after(0, self._refresh_comfort_ui)

        threading.Thread(target=worker, daemon=True).start()

    def _refresh_comfort_ui(self):
        hero_map = opendota.get_hero_map()
        if not hero_map:
            self.comfort_entry.configure(placeholder_text="No se pudo descargar el listado de héroes (OpenDota)")
            return
        names = sorted(hero_map.values())
        self.comfort_combo.configure(values=[f"  {n}" for n in names])
        self.comfort_combo.set(f"  {names[0]}")
        current = ", ".join(hero_map.get(h, f"#{h}") for h in self.cfg.comfort_hero_ids)
        self.comfort_entry.delete(0, "end")
        self.comfort_entry.insert(0, current)

    def _add_comfort_hero(self):
        name = self.comfort_combo.get().strip()
        if not name or name.startswith("("):
            return
        current = self.comfort_entry.get().strip()
        if name.lower() in [t.strip().lower() for t in current.split(",")]:
            return
        self.comfort_entry.delete(0, "end")
        self.comfort_entry.insert(0, f"{current}, {name}" if current else name)

    def _save_comfort(self):
        ids, unknown = opendota.resolve_hero_names(self.comfort_entry.get())
        if unknown:
            messagebox.showwarning(
                "Héroes no reconocidos",
                "No encontré estos nombres:\n  " + "\n  ".join(unknown)
                + "\n\nUsá el desplegable para agregar con el nombre exacto.",
            )
            return
        if not ids:
            messagebox.showwarning("Lista vacía", "Agregá al menos un héroe a la lista comfort.")
            return
        self.cfg.set_comfort_hero_ids(ids)
        self.cfg.save()
        self._refresh_comfort_ui()
        self._log(f"Comfort guardado ({len(ids)}): {self.comfort_entry.get()}")
        self.status.set(f"Comfort guardado: {len(ids)} héroes", "ok")

    def _open_grid_folder(self):
        if self._grid_out_dir and self._grid_out_dir.exists():
            os.startfile(self._grid_out_dir)  # noqa: S606 — abre el Explorador sobre una ruta local propia

    def _copy_log(self):
        self.clipboard_clear()
        self.clipboard_append(self.log.get("1.0", "end-1c"))
        self.status.set("Registro copiado al portapapeles", "info")

    def on_close(self):
        self.destroy()


def _set_app_user_model_id():
    """Hace que Windows agrupe la ventana con su propio icono en la barra de tareas."""
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("draenool.dota.config.sync")
    except Exception as e:  # noqa: BLE001
        log.debug("No se pudo fijar AppUserModelID: %s", e)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    _set_app_user_model_id()
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    cfg = AppConfig.load()
    http.configure(cfg.opendota_min_interval)
    opendota.configure(cfg.hero_map_ttl_seconds)

    app = App(cfg)
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
