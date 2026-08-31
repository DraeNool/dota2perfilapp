"""Detección de Steam, cuentas locales y datos de perfil vía Steam Web API."""

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from . import http

log = logging.getLogger(__name__)

DOTA_APPID = "570"
_STEAMID64_BASE = 76561197960265728


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
    except (OSError, ImportError) as e:
        log.debug("No se pudo leer InstallPath del registro: %s", e)

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
    except OSError as e:
        log.debug("No se pudo leer localconfig.vdf en %s: %s", folder, e)
        return None


def _read_login_names(steam_path: Path) -> dict[str, str]:
    """Lee config/loginusers.vdf: steamid64 -> AccountName (usuario de inicio de sesión)."""
    vdf = steam_path / "config" / "loginusers.vdf"
    if not vdf.exists():
        return {}
    try:
        text = vdf.read_text(encoding="utf-8", errors="ignore")
    except OSError as e:
        log.debug("No se pudo leer loginusers.vdf: %s", e)
        return {}
    out: dict[str, str] = {}
    for sid64, block in re.findall(r'"(\d{17})"\s*\{([^{}]*)\}', text):
        m = re.search(r'"AccountName"\s+"([^"]*)"', block, re.IGNORECASE)
        if m:
            out[sid64] = m.group(1)
    return out


def _read_config_accounts(steam_path: Path) -> dict[str, str]:
    """Lee config/config.vdf ("Accounts"): steamid64 -> AccountName.

    Respaldo de _read_login_names: Steam solo conserva en loginusers.vdf los
    inicios de sesión más recientes (los va podando si hay muchas cuentas),
    mientras que config.vdf guarda toda cuenta que alguna vez inició sesión
    en esta máquina.
    """
    vdf = steam_path / "config" / "config.vdf"
    if not vdf.exists():
        return {}
    try:
        text = vdf.read_text(encoding="utf-8", errors="ignore")
    except OSError as e:
        log.debug("No se pudo leer config.vdf: %s", e)
        return {}
    out: dict[str, str] = {}
    for name, sid64 in re.findall(r'"([^"]+)"\s*\{\s*"SteamID"\s*"(\d{17})"\s*\}', text):
        out[sid64] = name.strip()
    return out


def steam_id3_to_id64(sid3: int) -> str:
    return str(_STEAMID64_BASE + sid3)


def get_steam_accounts(steam_path: Path) -> list[dict]:
    accounts = []
    userdata = steam_path / "userdata"
    if not userdata.exists():
        return accounts

    login_names = _read_config_accounts(steam_path)
    login_names.update(_read_login_names(steam_path))

    for folder in userdata.iterdir():
        if not folder.is_dir():
            continue
        try:
            sid3 = int(folder.name)
        except ValueError:
            continue

        id64 = steam_id3_to_id64(sid3)
        name = _read_localconfig_name(folder) or f"Cuenta #{sid3}"
        accounts.append(
            {
                "steam_id3": str(sid3),
                "steam_id64": id64,
                "name": name,
                "login_name": login_names.get(id64),
                "path": folder,
                "has_dota": (folder / DOTA_APPID).exists(),
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


def fetch_steam_profile(steam_id64: str, api_key: str) -> dict:
    """Avatar + nombre desde Steam API. Devuelve {} si no hay key o falla."""
    if not api_key:
        return {}
    try:
        status, body = http.get_json(
            "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/",
            params={"key": api_key, "steamids": steam_id64},
            timeout=10,
        )
    except http.requests.RequestException as e:
        log.warning("Steam GetPlayerSummaries falló para %s: %s", steam_id64, e)
        return {}
    if status != 200 or not body:
        return {}
    players = body.get("response", {}).get("players", [])
    if not players:
        return {}
    out = {"avatar_url": players[0].get("avatarmedium")}
    if players[0].get("personaname"):
        out["name"] = players[0]["personaname"]
    return out


def fetch_steam_hours(steam_id64: str, api_key: str) -> float | None:
    """Horas jugadas en Dota 2 desde Steam API."""
    if not api_key:
        return None
    try:
        status, body = http.get_json(
            "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/",
            params={
                "key": api_key,
                "steamid": steam_id64,
                "include_appinfo": "false",
                "include_played_free_games": "true",
            },
            timeout=10,
        )
    except http.requests.RequestException as e:
        log.warning("Steam GetOwnedGames falló para %s: %s", steam_id64, e)
        return None
    if status != 200 or not body:
        return None
    for game in body.get("response", {}).get("games", []):
        if int(game.get("appid", -1)) == int(DOTA_APPID):
            minutes = game.get("playtime_forever")
            if minutes is not None:
                return float(minutes) / 60.0
    return None


def is_dota_running() -> bool:
    """True si dota2.exe está en ejecución (Windows). Evita que Steam Cloud pise los cambios."""
    tasklist = shutil.which("tasklist") or os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"), "System32", "tasklist.exe"
    )
    try:
        out = subprocess.run(  # noqa: S603 — argumentos constantes, sin entrada del usuario
            [tasklist, "/FI", "IMAGENAME eq dota2.exe", "/NH"],
            capture_output=True, text=True, timeout=8,
        )
        return "dota2.exe" in out.stdout.lower()
    except (OSError, subprocess.SubprocessError) as e:
        log.debug("No se pudo comprobar si Dota está abierto: %s", e)
        return False
