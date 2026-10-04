"""Retratos de héroes (CDN de Steam) cacheados en disco y convertidos a CTkImage."""

import concurrent.futures
import logging
from collections.abc import Callable
from pathlib import Path

import customtkinter as ctk
from PIL import Image

from . import http
from .paths import cache_dir

log = logging.getLogger(__name__)

PORTRAIT_URL = "https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/heroes/{npc}.png"
_IMAGES: dict[tuple[str, int, int], ctk.CTkImage] = {}


def portrait_path(npc: str) -> Path:
    return cache_dir() / "heroes" / f"{npc}.png"


def download_portrait(npc: str) -> Path | None:
    path = portrait_path(npc)
    if path.exists():
        return path
    try:
        r = http.SESSION.get(PORTRAIT_URL.format(npc=npc), timeout=20)
        r.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(r.content)
        return path
    except (http.requests.RequestException, OSError) as e:
        log.debug("Retrato %s no disponible: %s", npc, e)
        return None


def ensure_portraits(npcs: list[str], on_ready: Callable[[str], None] | None = None, workers: int = 4) -> None:
    """Descarga los retratos que falten (en paralelo, sin limitador: es un CDN) y avisa por cada uno."""
    pending = [n for n in npcs if n and not portrait_path(n).exists()]
    if on_ready:
        for n in npcs:
            if n and n not in pending:
                on_ready(n)
    if not pending:
        return
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for npc, path in zip(pending, pool.map(download_portrait, pending), strict=True):
            if path and on_ready:
                on_ready(npc)


def portrait_image(npc: str, size: tuple[int, int] = (64, 36)) -> ctk.CTkImage | None:
    """CTkImage del retrato ya descargado (memoizada por tamaño); None si no está en disco."""
    key = (npc, *size)
    if key in _IMAGES:
        return _IMAGES[key]
    path = portrait_path(npc)
    if not path.exists():
        return None
    try:
        img = Image.open(path).convert("RGB").resize(size, Image.Resampling.LANCZOS)
    except OSError as e:
        log.debug("Retrato %s ilegible: %s", npc, e)
        return None
    _IMAGES[key] = ctk.CTkImage(light_image=img, dark_image=img, size=size)
    return _IMAGES[key]
