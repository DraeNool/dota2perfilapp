"""Paleta de colores, medallas e imágenes de avatar para la UI."""

import io
import logging

import customtkinter as ctk
import requests
from PIL import Image, ImageDraw

from . import http

log = logging.getLogger(__name__)

# Tema único: dark mode violeta + negro.
C = {
    # Superficies (negro → violeta muy oscuro)
    "bg": "#0b0710",        # fondo de la app (negro con tinte violeta)
    "bg2": "#150f24",       # tarjetas
    "card": "#1d1633",      # superficie interior
    "header": "#070409",    # barra superior y de estado

    # Acento violeta
    "accent": "#a78bfa",    # violeta claro (acentos, hover, texto destacado)
    "accent2": "#7c3aed",   # violeta primario (botones)
    "accent3": "#5b21b6",   # violeta profundo (botón secundario, scrollbar)

    # Estados
    "green": "#34d399",     # ok / Dota instalado
    "amber": "#fbbf24",     # cargando / aviso
    "red": "#f87171",       # error

    "border": "#2a2142",
    "src": "#7c3aed",       # glow tarjeta origen (violeta)
    "dst": "#22d3a6",       # glow tarjeta destino (teal, para diferenciar)

    "txt": "#ece8f7",       # texto principal (lavanda casi blanco)
    "txt2": "#a99fc9",      # secundario
    "txt3": "#6b6090",      # labels / terciario

    "badge_src": "#2a1a4a",
    "badge_dst": "#10302a",
}

MEDALS = {
    0: ("Sin rango", "#888888"), 1: ("Heraldo", "#7a5c3a"), 2: ("Guardián", "#9a9a9a"),
    3: ("Cruzado", "#c8a850"), 4: ("Arcano", "#4a9ad4"), 5: ("Leyenda", "#9060d0"),
    6: ("Ancestral", "#e04a50"), 7: ("Divino", "#f0c030"), 8: ("Inmortal", "#f07830"),
}


def medal_for_tier(rank_tier: int | None) -> tuple[str, str, int]:
    """Devuelve (etiqueta, color, estrellas) para un rank_tier de OpenDota."""
    tier = rank_tier or 0
    main = tier // 10 if tier > 9 else tier
    stars = tier % 10 if tier > 9 else 0
    label, color = MEDALS.get(main, ("Desconocido", "#888888"))
    return label, color, stars


def make_avatar_placeholder(initials: str, color: str = "#3a2a5a", size: int = 48) -> ctk.CTkImage:
    img = Image.new("RGB", (size, size), color=color)
    draw = ImageDraw.Draw(img)
    txt = (initials[:2]).upper() if initials else "?"
    bb = draw.textbbox((0, 0), txt)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    draw.text(((size - tw) / 2, (size - th) / 2 - 2), txt, fill="#c9d6e3")
    return ctk.CTkImage(light_image=img, dark_image=img, size=(size, size))


def download_avatar(url: str, size: int = 48) -> ctk.CTkImage | None:
    try:
        r = http.SESSION.get(url, timeout=10)
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content)).convert("RGB").resize((size, size), Image.LANCZOS)
        return ctk.CTkImage(light_image=img, dark_image=img, size=(size, size))
    except (requests.RequestException, OSError) as e:
        log.debug("No se pudo descargar avatar %s: %s", url, e)
        return None
