# Prototipo original de un solo archivo. Superado por el paquete dota_config_sync/
# (ver main.py) — se conserva como referencia histórica, no es el entry point.

import concurrent.futures
import io
import json
import os
import re
import shutil
import threading
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import customtkinter as ctk
import requests
import tkinter as tk
from PIL import Image, ImageDraw
from tkinter import messagebox

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ── Configuración ────────────────────────────────────────────────────────────────

STEAM_API_KEY = os.environ.get("STEAM_API_KEY", "")  # https://steamcommunity.com/dev/apikey
PREFERRED_MAIN_ID64 = "76561198128824716"
FAVORITES_LIMIT = 15
RECENT_MATCHES_LIMIT = 20

C = {
    "bg": "#0d1117",
    "bg2": "#111820",
    "card": "#0a1018",
    "blue": "#1a7fd4",
    "blue2": "#0d5ea8",
    "green": "#28a060",
    "amber": "#f0a020",
    "red": "#e05050",
    "border": "#1e2d3d",
    "bsrc": "#1a5a8a",
    "bdst": "#1a5a2a",
    "txt": "#c9d6e3",
    "txt2": "#6a8aaa",
    "txt3": "#3a5a7a",
    "badge_src": "#0d3a5c",
    "badge_dst": "#1a2e1a",
}

MEDALS = {
    0: ("Sin rango", "#888888"),
    1: ("Heraldo", "#7a5c3a"),
    2: ("Guardián", "#9a9a9a"),
    3: ("Cruzado", "#c8a850"),
    4: ("Arcano", "#4a9ad4"),
    5: ("Leyenda", "#9060d0"),
    6: ("Ancestral", "#e04a50"),
    7: ("Divino", "#f0c030"),
    8: ("Inmortal", "#f07830"),
}

META_META_CONFIG = {
    "config_name": "Meta Meta",
    "categories": [
        {
            "category_name": "OFFLANE / TIER S - A  / POS 3",
            "x_position": 0.000000,
            "y_position": 0.000000,
            "width": 474.782623,
            "height": 259.130432,
            "hero_ids": [2, 135, 60, 104, 36, 14, 128],
        },
        {
            "category_name": "SOFT / POS 4",
            "x_position": 833.913086,
            "y_position": 0.000000,
            "width": 304.347839,
            "height": 295.652191,
            "hero_ids": [14, 71, 22, 105, 62, 88, 74],
        },
        {
            "category_name": "MID / POS 2",
            "x_position": 474.782623,
            "y_position": 0.000000,
            "width": 357.391327,
            "height": 169.565216,
            "hero_ids": [74, 128, 25, 13, 90, 34, 76],
        },
        {
            "category_name": "CARRY / POS 1",
            "x_position": 0.869565,
            "y_position": 300.000000,
            "width": 296.521759,
            "height": 163.478271,
            "hero_ids": [6, 41, 12, 8, 54, 93, 67],
        },
        {
            "category_name": "COMFORT",
            "x_position": 310.434784,
            "y_position": 286.086975,
            "width": 389.565216,
            "height": 253.913055,
            "hero_ids": [92, 23, 97, 38, 7, 98, 55, 107],
        },
        {
            "category_name": "SUPP / POS 5",
            "x_position": 832.173950,
            "y_position": 295.652191,
            "width": 335.652191,
            "height": 185.217392,
            "hero_ids": [14, 31, 87, 86, 27, 75, 128],
        },
    ],
}


# ── Steam / sistema ──────────────────────────────────────────────────────────────

def find_steam_path() -> Path | None:
    candidates = [
        Path("C:/Program Files (x86)/Steam"),
        Path("C:/Program Files/Steam"),
        Path("D:/Steam"),
        Path("D:/Program Files (x86)/Steam"),
    ]
    try:
        import winreg

        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")
        val, _ = winreg.QueryValueEx(key, "InstallPath")
        candidates.insert(0, Path(val))
    except Exception:
        pass

    for c in candidates:
        if c.exists() and (c / "userdata").exists():
            return c
    return None


def _read_localconfig_name(folder: Path) -> str | None:
    vdf = folder / "config" / "localconfig.vdf"
    if not vdf.exists():
        return None
    try:
        text = vdf.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r'"PersonaName"\s+"([^"]+)"', text, re.IGNORECASE)
        return m.group(1) if m else None
    except Exception:
        return None


def get_steam_accounts(steam_path: Path) -> list[dict]:
    accounts = []
    userdata = steam_path / "userdata"
    if not userdata.exists():
        return accounts

    for folder in userdata.iterdir():
        if not folder.is_dir():
            continue
        try:
            sid3 = int(folder.name)
        except ValueError:
            continue

        name = _read_localconfig_name(folder) or f"Cuenta #{sid3}"
        accounts.append(
            {
                "steam_id3": str(sid3),
                "steam_id64": str(76561197960265728 + sid3),
                "name": name,
                "path": folder,
                "has_dota": (folder / "570").exists(),
                "avatar_url": None,
                "avatar_img": None,
                "rank_tier": None,
                "total_hours": None,
                "competitive_winrate": None,
                "competitive_source": None,
                "last_hero": None,
                "last_result": None,
                "last_kda": None,
            }
        )

    accounts.sort(key=lambda a: a["name"].lower())
    return accounts


def make_avatar_placeholder(initials: str, color: str = "#1a4a70", size: int = 48) -> ctk.CTkImage:
    img = Image.new("RGB", (size, size), color=color)
    draw = ImageDraw.Draw(img)
    txt = (initials[:2]).upper() if initials else "?"
    bb = draw.textbbox((0, 0), txt)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    draw.text(((size - tw) / 2, (size - th) / 2 - 2), txt, fill="#c9d6e3")
    return ctk.CTkImage(light_image=img, dark_image=img, size=(size, size))


def download_avatar(url: str, size: int = 48) -> ctk.CTkImage | None:
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content)).convert("RGB").resize((size, size), Image.LANCZOS)
        return ctk.CTkImage(light_image=img, dark_image=img, size=(size, size))
    except Exception:
        return None


def format_kda(kills, deaths, assists) -> str:
    k, d, a = int(kills or 0), int(deaths or 0), int(assists or 0)
    ratio = (k + a) / max(d, 1)
    return f"{k}/{d}/{a} ({ratio:.2f})"


def _is_win(match: dict) -> bool | None:
    slot = match.get("player_slot")
    radiant_win = match.get("radiant_win")
    if slot is None or radiant_win is None:
        return None
    return (slot < 128 and radiant_win) or (slot >= 128 and not radiant_win)


# ── Hero map cache ──────────────────────────────────────────────────────────────

_HERO_MAP_CACHE: dict[int, str] | None = None
_HERO_MAP_LOCK = threading.Lock()


def get_hero_map() -> dict[int, str]:
    """Descarga el listado de héroes una sola vez y lo cachea."""
    global _HERO_MAP_CACHE
    with _HERO_MAP_LOCK:
        if _HERO_MAP_CACHE is not None:
            return _HERO_MAP_CACHE
        try:
            r = requests.get("https://api.opendota.com/api/heroes", timeout=15)
            r.raise_for_status()
            out = {}
            for hero in r.json():
                hid = hero.get("id")
                if hid is None:
                    continue
                out[int(hid)] = str(hero.get("localized_name") or hero.get("name") or f"Hero #{hid}")
            _HERO_MAP_CACHE = out
        except Exception:
            _HERO_MAP_CACHE = {}
        return _HERO_MAP_CACHE


def get_hero_id_lookup() -> dict[str, int]:
    """Mapa {nombre_normalizado: hero_id}."""
    hero_map = get_hero_map()
    lookup = {}
    for hid, name in hero_map.items():
        lookup[_normalize_hero_name(name)] = hid
    return lookup


def _normalize_hero_name(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


# ── Steam API ───────────────────────────────────────────────────────────────────

def _fetch_steam_profile(steam_id64: str) -> dict:
    """Avatar + nombre desde Steam API."""
    result = {}
    if not STEAM_API_KEY:
        return result
    try:
        r = requests.get(
            "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/",
            params={"key": STEAM_API_KEY, "steamids": steam_id64},
            timeout=10,
        )
        r.raise_for_status()
        players = r.json().get("response", {}).get("players", [])
        if players:
            result["avatar_url"] = players[0].get("avatarmedium")
            if players[0].get("personaname"):
                result["name"] = players[0]["personaname"]
    except Exception:
        pass
    return result


def _fetch_steam_hours(steam_id64: str) -> float | None:
    """Horas jugadas en Dota 2 desde Steam API."""
    if not STEAM_API_KEY:
        return None
    try:
        r = requests.get(
            "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/",
            params={
                "key": STEAM_API_KEY,
                "steamid": steam_id64,
                "include_appinfo": "false",
                "include_played_free_games": "true",
            },
            timeout=10,
        )
        r.raise_for_status()
        for game in r.json().get("response", {}).get("games", []):
            if int(game.get("appid", -1)) == 570:
                minutes = game.get("playtime_forever")
                if minutes is not None:
                    return float(minutes) / 60.0
    except Exception:
        pass
    return None


# ── OpenDota ────────────────────────────────────────────────────────────────────

def _fetch_odota_profile(steam_id3: str) -> dict:
    """
    Obtiene:
      - rank_tier
      - winrate competitivo (estimado)
      - último héroe, resultado y KDA
    """
    data = {
        "rank_tier": None,
        "competitive_winrate": None,
        "competitive_source": None,
        "last_hero": None,
        "last_result": None,
        "last_kda": None,
    }

    try:
        r = requests.get(f"https://api.opendota.com/api/players/{steam_id3}", timeout=15)
        if r.status_code == 200:
            body = r.json()
            tier = body.get("rank_tier")
            if tier:
                data["rank_tier"] = int(tier)
    except Exception:
        pass

    recent = []
    try:
        r = requests.get(f"https://api.opendota.com/api/players/{steam_id3}/recentMatches", timeout=15)
        if r.status_code == 200:
            recent = r.json() or []
    except Exception:
        pass

    if recent:
        hero_map = get_hero_map()
        last = max(recent, key=lambda m: int(m.get("start_time") or 0))
        hid = last.get("hero_id")
        data["last_hero"] = hero_map.get(int(hid), f"Hero #{hid}") if hid is not None else "N/D"
        result = _is_win(last)
        data["last_result"] = "Ganó" if result is True else "Perdió" if result is False else "N/D"
        data["last_kda"] = format_kda(last.get("kills"), last.get("deaths"), last.get("assists"))

        ranked = [m for m in recent if m.get("lobby_type") == 7]
        pool = ranked if len(ranked) >= 5 else recent
        if pool:
            wins = sum(1 for m in pool if _is_win(m) is True)
            data["competitive_winrate"] = wins / len(pool)
            data["competitive_source"] = "ranked" if pool is ranked else "recent"

    if data["competitive_winrate"] is None:
        try:
            r = requests.get(f"https://api.opendota.com/api/players/{steam_id3}/wl", timeout=15)
            if r.status_code == 200:
                wl = r.json() or {}
                wins = int(wl.get("win", 0))
                losses = int(wl.get("lose", 0))
                total = wins + losses
                if total:
                    data["competitive_winrate"] = wins / total
                    data["competitive_source"] = "global"
        except Exception:
            pass

    return data


def _favorites_from_performance(steam_id3: str, limit: int = FAVORITES_LIMIT) -> tuple[list[int], str]:
    """
    Héroes favoritos por performance usando el histórico público de OpenDota (/heroes).
    """
    rows = []
    try:
        r = requests.get(f"https://api.opendota.com/api/players/{steam_id3}/heroes", timeout=20)
        if r.status_code == 403:
            return [], "Perfil privado — activa Exposición de datos de partido"
        if r.status_code == 200:
            rows = r.json() or []
    except requests.exceptions.Timeout:
        return [], "Timeout al conectar con OpenDota"
    except Exception as e:
        return [], f"Error de red: {e}"

    rows = [row for row in rows if int(row.get("games") or 0) > 0]
    if not rows:
        return [], "Sin datos de héroes"

    PRIOR_GAMES = 15
    PRIOR_WINS = PRIOR_GAMES * 0.5

    def score(row: dict) -> float:
        games = int(row.get("games") or 0)
        wins = int(row.get("win") or 0)
        adj_wr = (wins + PRIOR_WINS) / (games + PRIOR_GAMES)
        return games * (adj_wr ** 1.5)

    rows.sort(key=score, reverse=True)

    hero_ids = []
    for row in rows:
        hid = row.get("hero_id")
        if hid is None:
            continue
        try:
            hid = int(hid)
        except (ValueError, TypeError):
            continue
        if hid not in hero_ids:
            hero_ids.append(hid)
        if len(hero_ids) >= limit:
            break

    return hero_ids[:limit], f"{len(hero_ids)} héroes por performance"


def _favorites_from_recent_20(steam_id3: str, limit: int = RECENT_MATCHES_LIMIT) -> tuple[list[int], str]:
    """
    Héroes de las últimas 20 partidas, priorizando frecuencia y recencia.
    """
    recent = []
    try:
        r = requests.get(f"https://api.opendota.com/api/players/{steam_id3}/recentMatches", timeout=20)
        if r.status_code == 403:
            return [], "Perfil privado — no hay recentMatches públicos"
        if r.status_code == 200:
            recent = r.json() or []
    except requests.exceptions.Timeout:
        return [], "Timeout al conectar con OpenDota"
    except Exception as e:
        return [], f"Error de red: {e}"

    if not recent:
        return [], "Sin partidas recientes"

    hero_map = get_hero_map()
    ordered = []
    seen = set()
    for match in sorted(recent, key=lambda m: int(m.get("start_time") or 0), reverse=True)[:20]:
        hid = match.get("hero_id")
        if hid is None:
            continue
        try:
            hid = int(hid)
        except (ValueError, TypeError):
            continue
        if hid not in seen:
            seen.add(hid)
            ordered.append(hid)

    if len(ordered) < limit:
        # Completar con héroes más repetidos en esas 20 partidas
        counts = Counter()
        for match in recent[:20]:
            hid = match.get("hero_id")
            if hid is None:
                continue
            try:
                hid = int(hid)
            except (ValueError, TypeError):
                continue
            counts[hid] += 1

        for hid, _ in counts.most_common():
            if hid not in seen:
                ordered.append(hid)
                seen.add(hid)
            if len(ordered) >= limit:
                break

    return ordered[:limit], f"{len(ordered[:limit])} héroes de las últimas 20"


def fetch_account_profile(acc: dict) -> dict:
    """Reúne todos los datos de perfil de una cuenta."""
    updates = {}

    steam_data = _fetch_steam_profile(acc["steam_id64"])
    updates.update(steam_data)

    hours = _fetch_steam_hours(acc["steam_id64"])
    if hours is not None:
        updates["total_hours"] = hours

    od_data = _fetch_odota_profile(acc["steam_id3"])
    updates.update(od_data)

    return updates


def build_hero_grid_json(src_account: dict, log_fn=None) -> tuple[dict, list[str], list[str], str]:
    """
    Construye:
      - Meta Meta (fijo)
      - Favoritos (subcategorías: performance + últimas 20 partidas)

    Retorna (payload, fav_perf_names, fav_recent_names, status_msg).
    """
    if log_fn:
        log_fn(f'  Consultando favoritos de {src_account["name"]} en OpenDota...')

    perf_ids, perf_msg = _favorites_from_performance(src_account["steam_id3"], limit=FAVORITES_LIMIT)
    recent_ids, recent_msg = _favorites_from_recent_20(src_account["steam_id3"], limit=RECENT_MATCHES_LIMIT)

    hero_map = get_hero_map()
    perf_names = [hero_map.get(hid, f"Hero #{hid}") for hid in perf_ids]
    recent_names = [hero_map.get(hid, f"Hero #{hid}") for hid in recent_ids]

    if log_fn:
        log_fn(f"  Performance: {perf_msg}")
        log_fn(f"  Últimas 20: {recent_msg}")

    payload = {
        "version": 3,
        "configs": [
            META_META_CONFIG,
            {
                "config_name": "Favoritos",
                "categories": [
                    {
                        "category_name": "Favoritos por performance",
                        "x_position": 0.0,
                        "y_position": 0.0,
                        "width": 700.0,
                        "height": 260.0,
                        "hero_ids": perf_ids,
                    },
                    {
                        "category_name": "Últimas 20 partidas",
                        "x_position": 0.0,
                        "y_position": 290.0,
                        "width": 700.0,
                        "height": 260.0,
                        "hero_ids": recent_ids,
                    },
                ],
            },
        ],
    }
    status_msg = f"Performance: {perf_msg} | Recientes: {recent_msg}"
    return payload, perf_names, recent_names, status_msg


def save_hero_grid(dst_570: Path, payload: dict) -> Path:
    cfg_dir = dst_570 / "remote" / "cfg"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    target = cfg_dir / "hero_grid_config.json"
    backup = cfg_dir / "hero_grid_config_backup.json"

    if target.exists():
        try:
            if backup.exists():
                backup.unlink()
            target.replace(backup)
        except Exception:
            try:
                backup.write_bytes(target.read_bytes())
                target.unlink(missing_ok=True)
            except Exception:
                pass

    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return target


def list_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file()] if root.exists() else []


# ── Widgets ─────────────────────────────────────────────────────────────────────

class StatusBar(ctk.CTkFrame):
    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        self.configure(fg_color="#090d12", corner_radius=0, border_width=0)
        self.dot = ctk.CTkLabel(self, text="●", font=ctk.CTkFont(size=10), text_color=C["green"], width=16)
        self.dot.pack(side="left", padx=(12, 4), pady=6)
        self.msg = ctk.CTkLabel(self, text="Iniciando...", font=ctk.CTkFont(size=11), text_color=C["txt3"], anchor="w")
        self.msg.pack(side="left", fill="x", expand=True)
        self.right = ctk.CTkLabel(self, text="", font=ctk.CTkFont(size=10), text_color=C["txt3"], anchor="e")
        self.right.pack(side="right", padx=12)

    def set(self, text: str, state: str = "ok"):
        colors = {"ok": C["green"], "loading": C["amber"], "error": C["red"], "info": C["blue"]}
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
            fg_color=C["bg2"],
            corner_radius=12,
            border_width=1,
            border_color=C["bsrc"] if role == "source" else C["bdst"],
        )
        self._build()

    def _build(self):
        badge_txt = "● Cuenta principal" if self.role == "source" else "● Cuenta secundaria"
        badge_bg = C["badge_src"] if self.role == "source" else C["badge_dst"]
        badge_fg = "#4a9fd4" if self.role == "source" else "#4a9a4a"
        ctk.CTkLabel(
            self,
            text=badge_txt,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=badge_bg,
            text_color=badge_fg,
            corner_radius=20,
            padx=10,
            pady=3,
        ).pack(anchor="w", padx=14, pady=(12, 6))

        self.combo = ctk.CTkComboBox(
            self,
            values=["  Seleccionar cuenta..."],
            command=self._on_select,
            fg_color=C["card"],
            border_color=C["border"],
            button_color=C["blue2"],
            button_hover_color=C["blue"],
            text_color=C["txt"],
            dropdown_fg_color=C["bg2"],
            dropdown_hover_color=C["card"],
            dropdown_text_color=C["txt"],
            font=ctk.CTkFont(size=13),
            width=240,
        )
        self.combo.set("  Seleccionar cuenta...")
        self.combo.pack(fill="x", padx=14, pady=(0, 10))

        preview = ctk.CTkFrame(self, fg_color=C["card"], corner_radius=10, border_width=1, border_color=C["border"])
        preview.pack(fill="x", padx=14, pady=(0, 14))
        preview.columnconfigure(1, weight=1)

        self.av_lbl = ctk.CTkLabel(
            preview,
            text="?",
            width=48,
            height=48,
            fg_color=C["bg2"],
            corner_radius=8,
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=C["txt2"],
        )
        self.av_lbl.grid(row=0, column=0, rowspan=4, padx=12, pady=12, sticky="n")

        self.name_lbl = ctk.CTkLabel(
            preview,
            text="Ninguna cuenta seleccionada",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=C["txt2"],
            anchor="w",
        )
        self.name_lbl.grid(row=0, column=1, sticky="sw", padx=(0, 12), pady=(12, 0))

        self.id_lbl = ctk.CTkLabel(preview, text="", font=ctk.CTkFont(size=11), text_color=C["txt3"], anchor="w")
        self.id_lbl.grid(row=1, column=1, sticky="w", padx=(0, 12))

        self.rank_lbl = ctk.CTkLabel(preview, text="", font=ctk.CTkFont(size=11), text_color=C["txt2"], anchor="w")
        self.rank_lbl.grid(row=2, column=1, sticky="nw", padx=(0, 12), pady=(0, 4))

        self.stats_lbl = ctk.CTkLabel(
            preview,
            text="",
            font=ctk.CTkFont(size=11),
            text_color=C["txt2"],
            anchor="w",
            justify="left",
            wraplength=250,
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
        self.id_lbl.configure(text=f"SteamID64: {acc['steam_id64']}")

        tier = acc.get("rank_tier") or 0
        main = tier // 10 if tier > 9 else tier
        stars = tier % 10 if tier > 9 else 0
        label, color = MEDALS.get(main, ("Desconocido", "#888888"))
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
        self.stats_lbl.configure(text="\n".join(l for l in lines if l), text_color=C["txt2"])

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


# ── App principal ───────────────────────────────────────────────────────────────

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Dota 2 Config Sync")
        self.geometry("900x800")
        self.minsize(840, 700)
        self.configure(fg_color=C["bg"])

        self.steam_path = None
        self.accounts = []

        self._build_ui()
        self._detect_steam()

    def _build_ui(self):
        hdr = ctk.CTkFrame(self, fg_color="#090d12", corner_radius=0)
        hdr.pack(fill="x")

        lf = ctk.CTkFrame(hdr, fg_color="transparent")
        lf.pack(side="left", padx=20, pady=12)
        ctk.CTkLabel(lf, text="⚙", font=ctk.CTkFont(size=22), text_color=C["blue"]).pack(side="left", padx=(0, 8))
        ctk.CTkLabel(lf, text="Dota 2  Config Sync", font=ctk.CTkFont(size=16, weight="bold"), text_color=C["txt"]).pack(side="left")
        ctk.CTkLabel(lf, text="  v4.0", font=ctk.CTkFont(size=11), text_color=C["txt3"]).pack(side="left")

        rf = ctk.CTkFrame(hdr, fg_color="transparent")
        rf.pack(side="right", padx=12, pady=10)

        ctk.CTkButton(
            rf,
            text="⟳  Recargar perfiles",
            width=145,
            height=30,
            fg_color="transparent",
            border_width=1,
            border_color=C["border"],
            text_color=C["txt2"],
            hover_color=C["bg2"],
            font=ctk.CTkFont(size=12),
            command=self._reload_profiles,
        ).pack(side="left", padx=(0, 8))

        self.scroll = ctk.CTkScrollableFrame(
            self,
            fg_color="transparent",
            scrollbar_button_color=C["border"],
            scrollbar_button_hover_color=C["blue2"],
        )
        self.scroll.pack(fill="both", expand=True, padx=20, pady=(16, 0))

        self._lbl(self.scroll, "CUENTAS STEAM")

        row = ctk.CTkFrame(self.scroll, fg_color="transparent")
        row.pack(fill="x", pady=(6, 0))
        row.columnconfigure(0, weight=1)
        row.columnconfigure(2, weight=1)

        self.card_src = AccountCard(row, role="source", on_change=self._on_src_change)
        self.card_src.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        arrow = ctk.CTkFrame(row, fg_color="transparent", width=54)
        arrow.grid(row=0, column=1)
        ctk.CTkLabel(arrow, text="→", font=ctk.CTkFont(size=26), text_color=C["blue"]).pack(pady=(50, 0))
        ctk.CTkLabel(arrow, text="copiar\n570", font=ctk.CTkFont(size=10), text_color=C["txt3"], justify="center").pack()

        self.card_dst = AccountCard(row, role="target", on_change=self._on_dst_change)
        self.card_dst.grid(row=0, column=2, sticky="ew", padx=(8, 0))

        self._lbl(self.scroll, "ACCIÓN", pady=(20, 6))

        sync_card = ctk.CTkFrame(self.scroll, fg_color=C["bg2"], corner_radius=12, border_width=1, border_color=C["bsrc"])
        sync_card.pack(fill="x")

        sync_inner = ctk.CTkFrame(sync_card, fg_color="transparent")
        sync_inner.pack(fill="x", padx=16, pady=16)

        self.op_desc = ctk.CTkLabel(
            sync_inner,
            text="Selecciona las dos cuentas para ver la operación",
            font=ctk.CTkFont(size=12),
            text_color=C["txt2"],
            anchor="w",
            wraplength=760,
            justify="left",
        )
        self.op_desc.pack(fill="x", pady=(0, 12))

        self.btn_replace = ctk.CTkButton(
            sync_inner,
            text="↻  REEMPLAZAR CARPETA 570 COMPLETA",
            height=48,
            fg_color=C["blue2"],
            hover_color=C["blue"],
            text_color=C["txt"],
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._do_replace_570,
        )
        self.btn_replace.pack(fill="x")

        self.btn_grid = ctk.CTkButton(
            sync_inner,
            text="🧩  GENERAR HERO GRID  (Meta Meta + Favoritos)",
            height=48,
            fg_color=C["green"],
            hover_color=C["blue"],
            text_color=C["txt"],
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._do_generate_hero_grid,
        )
        self.btn_grid.pack(fill="x", pady=(10, 0))

        self.sync_result = ctk.CTkLabel(
            sync_inner,
            text="",
            font=ctk.CTkFont(size=11),
            text_color=C["txt2"],
            anchor="w",
            wraplength=760,
            justify="left",
        )
        self.sync_result.pack(fill="x", pady=(10, 0))

        self._lbl(self.scroll, "REGISTRO DE ACTIVIDAD", pady=(20, 6))

        self.log = ctk.CTkTextbox(
            self.scroll,
            height=240,
            fg_color=C["card"],
            border_color=C["border"],
            border_width=1,
            corner_radius=10,
            font=ctk.CTkFont(size=11, family="Courier"),
            text_color=C["txt2"],
        )
        self.log.pack(fill="x", pady=(0, 16))
        self.log.configure(state="disabled")

        self.status = StatusBar(self)
        self.status.pack(fill="x", side="bottom")

    def _lbl(self, parent, text: str, pady=(12, 0)):
        ctk.CTkLabel(
            parent,
            text=text,
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=C["txt3"],
            anchor="w",
        ).pack(anchor="w", pady=pady)

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log.configure(state="normal")
        self.log.insert("end", f"[{ts}] {msg}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _log_async(self, msg: str):
        self.after(0, self._log, msg)

    def _set_status_async(self, text: str, state: str = "ok"):
        self.after(0, self.status.set, text, state)

    def _set_sync_text(self, text: str, color: str):
        self.sync_result.configure(text=text, text_color=color)

    # ── Steam detect ─────────────────────────────────────────────────────────────

    def _detect_steam(self):
        self.status.set("Buscando instalación de Steam...", "loading")
        threading.Thread(target=self._detect_thread, daemon=True).start()

    def _detect_thread(self):
        steam = find_steam_path()
        self.after(0, self._on_steam_found, steam)

    def _on_steam_found(self, steam: Path | None):
        if not steam:
            self.status.set("Steam no encontrado", "error")
            self._log("ERROR: Steam no encontrado.")
            return

        self.steam_path = steam
        self._log(f"Steam detectado: {steam}")

        accounts = get_steam_accounts(steam)
        self.accounts = accounts
        self._log(f"{len(accounts)} cuenta(s) encontrada(s):")
        for a in accounts:
            self._log(f"  ► {a['name']}  (ID64: {a['steam_id64']})  {'✔ 570' if a['has_dota'] else '✘'}")

        self.card_src.load_accounts(accounts)
        self.card_dst.load_accounts(accounts)
        self.status.set(f"{len(accounts)} cuenta(s) encontradas", "ok")
        self.status.set_right(r"userdata\...\570")

        main_idx = next((i for i, a in enumerate(accounts) if a["steam_id64"] == PREFERRED_MAIN_ID64), None)
        if main_idx is not None:
            self.card_src.select_by_index(main_idx)
        elif accounts:
            self.card_src.select_by_index(0)

        if len(accounts) >= 2:
            dst_idx = 1 if main_idx in (None, 0) else 0
            self.card_dst.select_by_index(dst_idx)

        self._update_op_desc()
        self._reload_profiles()

    # ── Perfiles ────────────────────────────────────────────────────────────────

    def _reload_profiles(self):
        if not self.accounts:
            self.status.set("Primero espera que carguen las cuentas", "info")
            return
        self.status.set(f"Cargando {len(self.accounts)} perfil(es)...", "loading")
        self._log(f"Iniciando descarga de perfiles ({len(self.accounts)} cuentas)...")
        threading.Thread(target=self._profiles_thread, daemon=True).start()

    def _profiles_thread(self):
        def fetch_one(acc: dict):
            try:
                updates = fetch_account_profile(acc)
                acc.update(updates)
                if acc.get("avatar_url") and not acc.get("avatar_img"):
                    img = download_avatar(acc["avatar_url"])
                    if img:
                        acc["avatar_img"] = img
            except Exception as e:
                self._log_async(f"  ⚠ Perfil {acc['name']}: {e}")
            self.after(0, self._on_account_loaded, acc)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(fetch_one, self.accounts))
        self.after(0, self._on_all_profiles_done)

    def _on_account_loaded(self, acc: dict):
        hours = acc.get("total_hours")
        winrate = acc.get("competitive_winrate")
        last_hero = acc.get("last_hero") or "N/D"
        last_res = acc.get("last_result") or "N/D"
        kda = acc.get("last_kda") or "N/D"
        wr_txt = f"{winrate * 100:.1f}%" if winrate is not None else "N/D"
        hours_txt = f"{hours:.0f} h" if hours is not None else "N/D"
        self._log(
            f"  ✔ {acc['name']}  |  avatar={'sí' if acc.get('avatar_img') else 'no'}"
            f"  |  rango={acc.get('rank_tier', '?')}  |  wr={wr_txt}"
            f"  |  horas={hours_txt}  |  último={last_hero}/{last_res}/KDA {kda}"
        )
        self.card_src.refresh_selected()
        self.card_dst.refresh_selected()
        self._sync_combo_names()

    def _on_all_profiles_done(self):
        wa = sum(1 for a in self.accounts if a.get("avatar_img"))
        wr = sum(1 for a in self.accounts if a.get("rank_tier"))
        wh = sum(1 for a in self.accounts if a.get("total_hours") is not None)
        ww = sum(1 for a in self.accounts if a.get("competitive_winrate") is not None)
        wl = sum(1 for a in self.accounts if a.get("last_hero"))
        n = len(self.accounts)
        self.status.set(
            f"✔ {wa}/{n} avatares  •  {wr}/{n} rangos  •  {wh}/{n} horas  •  {ww}/{n} winrate  •  {wl}/{n} último héroe",
            "ok",
        )
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

    # ── Callbacks ────────────────────────────────────────────────────────────────

    def _on_src_change(self, acc: dict):
        self._log(f"Origen → {acc['name']}")
        self._update_op_desc()

    def _on_dst_change(self, acc: dict):
        self._log(f"Destino → {acc['name']}")
        self._update_op_desc()

    def _update_op_desc(self):
        src = self.card_src.selected
        dst = self.card_dst.selected
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
            self.op_desc.configure(
                text="Selecciona las dos cuentas para ver la operación",
                text_color=C["txt2"],
            )

    # ── Reemplazar 570 ──────────────────────────────────────────────────────────

    def _do_replace_570(self):
        src = self.card_src.selected
        dst = self.card_dst.selected
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

        total = len(list_files(src_570))
        if total == 0:
            messagebox.showwarning("Carpeta vacía", f"Sin archivos en:\n{src_570}")
            return

        backup_dir = dst["path"] / f"backup_570_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        if not messagebox.askyesno(
            "Confirmar reemplazo total",
            f'ORIGEN:  {src["name"]}\n  {src_570}\n\n'
            f'DESTINO: {dst["name"]}\n  {dst_570}\n\n'
            f"Se reemplazará COMPLETAMENTE la carpeta 570 del destino.\n"
            f"Backup en: {backup_dir}\n"
            f"Archivos aprox.: {total}\n\n¿Continuar?",
        ):
            return

        self.btn_replace.configure(state="disabled", text="⏳  Reemplazando...")
        self.btn_grid.configure(state="disabled")
        self.status.set("Preparando reemplazo...", "loading")
        self.sync_result.configure(text="Iniciando proceso...", text_color=C["amber"])
        self._log(f'\n── Reemplazo 570: {src["name"]} → {dst["name"]} ──')

        threading.Thread(
            target=self._replace_570_worker,
            args=(src_570, dst_570, backup_dir, src, dst),
            daemon=True,
        ).start()

    def _replace_570_worker(self, src_570, dst_570, backup_dir, src, dst):
        try:
            if dst_570.exists():
                self._set_status_async("Creando backup...", "loading")
                self._log_async("Creando backup del destino...")
                shutil.copytree(dst_570, backup_dir)
                self._log_async(f"✔ Backup: {backup_dir}")
                shutil.rmtree(dst_570)

            total = len(list_files(src_570))
            copied = 0
            for src_file in list_files(src_570):
                rel = src_file.relative_to(src_570)
                dst_file = dst_570 / rel
                dst_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dst_file)
                copied += 1
                self._log_async(f"  ✔ {copied}/{total}: {rel.as_posix()}")
                self._set_status_async(f"Copiando {copied}/{total}...", "loading")
                self.after(
                    0,
                    lambda c=copied, t=total, r=rel.as_posix(): self.sync_result.configure(
                        text=f"Copiando {c}/{t}: {r}",
                        text_color=C["txt2"],
                    ),
                )

            self.after(0, self._on_replace_done, True, copied, total, src, dst, backup_dir, "")
        except Exception as e:
            self.after(0, self._on_replace_done, False, 0, 0, src, dst, backup_dir, str(e))

    def _on_replace_done(self, ok, copied, total, src, dst, backup_dir, error):
        self.btn_replace.configure(state="normal", text="↻  REEMPLAZAR CARPETA 570 COMPLETA")
        self.btn_grid.configure(state="normal")
        if ok:
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

    # ── Hero Grid ───────────────────────────────────────────────────────────────

    def _do_generate_hero_grid(self):
        src = self.card_src.selected
        dst = self.card_dst.selected

        if not src or not dst:
            messagebox.showwarning("Faltan cuentas", "Selecciona origen y destino.")
            return

        # favorites y archivo se calculan/guardan en la cuenta secundaria seleccionada
        self.btn_grid.configure(state="disabled", text="⏳  Generando Hero Grid...")
        self.btn_replace.configure(state="disabled")
        self.status.set("Consultando OpenDota...", "loading")
        self.sync_result.configure(
            text="Generando config: Meta Meta + Favoritos por performance + Últimas 20 partidas...",
            text_color=C["amber"],
        )
        self._log(f'\n── Hero Grid: fuente de favoritos {dst["name"]} ──')

        threading.Thread(target=self._hero_grid_worker, args=(dst,), daemon=True).start()

    def _hero_grid_worker(self, dst: dict):
        try:
            payload, fav_perf_names, fav_recent_names, status_msg = build_hero_grid_json(dst, log_fn=self._log_async)
            out_path = save_hero_grid(dst["path"] / "570", payload)
            self._log_async("✔ Meta Meta: plantilla fija conservada.")
            self._log_async(f'✔ Favoritos performance ({len(fav_perf_names)}): {", ".join(fav_perf_names) if fav_perf_names else "ninguno"}')
            self._log_async(f'✔ Últimas 20 ({len(fav_recent_names)}): {", ".join(fav_recent_names) if fav_recent_names else "ninguno"}')
            self.after(0, self._on_hero_grid_done, out_path, fav_perf_names, fav_recent_names, status_msg)
        except Exception as e:
            self.after(0, self._on_hero_grid_error, str(e))

    def _on_hero_grid_done(self, out_path: Path, fav_perf_names: list[str], fav_recent_names: list[str], status_msg: str):
        self.btn_grid.configure(state="normal", text="🧩  GENERAR HERO GRID  (Meta Meta + Favoritos)")
        self.btn_replace.configure(state="normal")
        self.status.set("✔ Hero Grid generado", "ok")

        perf_txt = ", ".join(fav_perf_names[:8]) + ("..." if len(fav_perf_names) > 8 else "") if fav_perf_names else "ninguno"
        recent_txt = ", ".join(fav_recent_names[:8]) + ("..." if len(fav_recent_names) > 8 else "") if fav_recent_names else "ninguno"

        self.sync_result.configure(
            text=(
                f"Hero Grid guardado en:\n{out_path}\n\n"
                f"{status_msg}\n\n"
                f"Performance: {perf_txt}\n"
                f"Últimas 20: {recent_txt}"
            ),
            text_color=C["green"],
        )
        self._log(f"✔ Hero Grid guardado en {out_path}")
        messagebox.showinfo(
            "Hero Grid generado",
            f"hero_grid_config.json guardado en:\n{out_path}\n\n"
            f"{status_msg}\n\n"
            f"Favoritos por performance: {perf_txt}\n"
            f"Últimas 20 partidas: {recent_txt}\n\n"
            f"El archivo anterior quedó como hero_grid_config_backup.json.",
        )

    def _on_hero_grid_error(self, error: str):
        self.btn_grid.configure(state="normal", text="🧩  GENERAR HERO GRID  (Meta Meta + Favoritos)")
        self.btn_replace.configure(state="normal")
        self.status.set("Error generando Hero Grid", "error")
        self.sync_result.configure(text=f"Error: {error}", text_color=C["red"])
        self._log(f"✘ Error Hero Grid: {error}")
        messagebox.showerror("Error", f"No se pudo generar el Hero Grid:\n\n{error}")

    def on_close(self):
        self.destroy()


if __name__ == "__main__":
    app = App()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
