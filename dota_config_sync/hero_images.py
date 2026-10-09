"""Retratos de héroes (CDN de Steam) cacheados en disco y convertidos a CTkImage."""

import concurrent.futures
import logging
from collections.abc import Callable
from pathlib import Path

import customtkinter as ctk
from PIL import Image, ImageTk

from . import http
from .paths import cache_dir
from .theme import C

log = logging.getLogger(__name__)

PORTRAIT_URL = "https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/heroes/{npc}.png"
_IMAGES: dict[tuple[str, int, int], ctk.CTkImage] = {}
_PHOTOS: dict[tuple[str, int, int], ImageTk.PhotoImage] = {}
_PIL: dict[tuple[str, int, int], Image.Image] = {}       # retratos ya abiertos y escalados (hilo aparte)
_BLANKS: dict[tuple[int, int], ctk.CTkImage] = {}


def blank_image(size: tuple[int, int] = (64, 36)) -> ctk.CTkImage:
    """Imagen vacía del color de tarjeta: CustomTkinter no borra un image=None, hay que reemplazarlo."""
    if size not in _BLANKS:
        img = Image.new("RGB", size, C["bg2"])
        _BLANKS[size] = ctk.CTkImage(light_image=img, dark_image=img, size=size)
    return _BLANKS[size]


def _open_resized(npc: str, size: tuple[int, int]) -> Image.Image | None:
    """PIL del retrato escalado, memoizada: abrir y escalar 127 PNG cuesta ~1 s, mejor fuera del hilo de Tk."""
    key = (npc, *size)
    if key in _PIL:
        return _PIL[key]
    path = portrait_path(npc)
    if not path.exists():
        return None
    try:
        img = Image.open(path).convert("RGB").resize(size, Image.Resampling.LANCZOS)
    except OSError as e:
        log.debug("Retrato %s ilegible: %s", npc, e)
        return None
    _PIL[key] = img
    return img


def portrait_photo(npc: str, size: tuple[int, int] = (64, 36)) -> ImageTk.PhotoImage | None:
    """PhotoImage para dibujar en un tk.Canvas (memoizada). Llamar desde el hilo de Tk."""
    key = (npc, *size)
    if key not in _PHOTOS:
        img = _open_resized(npc, size)
        if img is None:
            return None
        _PHOTOS[key] = ImageTk.PhotoImage(img)
    return _PHOTOS[key]


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


def ensure_portraits(npcs: list[str], on_ready: Callable[[str], None] | None = None, workers: int = 4,
                     size: tuple[int, int] | None = None) -> None:
    """
    Descarga los retratos que falten (en paralelo, sin limitador: es un CDN), los deja abiertos y
    escalados a `size` si se pide, y avisa por cada uno. Pensado para un hilo de fondo.
    """
    def prepare(npc: str) -> bool:
        if download_portrait(npc) is None:
            return False
        if size:
            _open_resized(npc, size)
        return True

    valid = [n for n in npcs if n]
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for npc, ok in zip(valid, pool.map(prepare, valid), strict=True):
            if ok and on_ready:
                on_ready(npc)


def portrait_image(npc: str, size: tuple[int, int] = (64, 36)) -> ctk.CTkImage | None:
    """CTkImage del retrato ya descargado (memoizada por tamaño); None si no está en disco."""
    key = (npc, *size)
    if key not in _IMAGES:
        img = _open_resized(npc, size)
        if img is None:
            return None
        _IMAGES[key] = ctk.CTkImage(light_image=img, dark_image=img, size=size)
    return _IMAGES[key]
