"""Datos públicos de Dota2ProTracker — hero grids y ranking Meta por posición (con caché)."""

import json
import logging
import re
from typing import cast

from . import cache, http

log = logging.getLogger(__name__)

HOME_URL = "https://dota2protracker.com/"
GRIDS_URL = "https://dota2protracker.com/meta-hero-grids"

_GRIDS_MARKER = "matches:{configs:"
_ROLES_MARKER = 'roles:[{position:"pos 1"'
_BAREWORD_KEY = re.compile(r'([{,])(\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*):')
_BARE_DECIMAL = re.compile(r'([:,\[])(-?)\.(\d)')
_POS_IN_NAME = re.compile(r'pos\s*([1-5])', re.IGNORECASE)


def _js_array_to_json(raw: str) -> list:
    """Convierte un array-literal de JS (keys sin comillas, decimales `.5`) a JSON válido."""
    fixed = _BAREWORD_KEY.sub(r'\1\2"\3"\4:', raw)
    fixed = _BARE_DECIMAL.sub(r'\1\g<2>0.\3', fixed)
    return cast(list, json.loads(fixed))


# ── Hero grids listos (Meta / Favoritos / etc de /meta-hero-grids) ─────────────
def parse_meta_hero_grids_html(html: str) -> list[dict]:
    """Convierte el HTML de /meta-hero-grids en la lista `configs` (formato hero_grid_config.json)."""
    start = html.find(_GRIDS_MARKER)
    if start == -1:
        raise ValueError("No se encontró el payload de hero grids en la página")
    start += len(_GRIDS_MARKER)
    if html[start] != "[":
        raise ValueError("Formato de payload inesperado (no empieza con '[')")
    depth = 0
    end = None
    for i in range(start, len(html)):
        if html[i] == "[":
            depth += 1
        elif html[i] == "]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        raise ValueError("No se pudo cerrar el array de configs (HTML truncado)")

    configs = _js_array_to_json(html[start:end])
    if not isinstance(configs, list) or not configs:
        raise ValueError("El payload de hero grids vino vacío")
    return configs


def fetch_meta_hero_grids(ttl: int) -> tuple[list[dict], str]:
    """Descarga (o reutiliza de caché) los hero grids Meta publicados por Dota2ProTracker."""
    cached = cache.get(GRIDS_URL, ttl)
    if cached is not None:
        return cached, f"{len(cached)} grids D2PT (caché)"

    try:
        status, html = http.get_text(GRIDS_URL, timeout=20)
    except http.requests.exceptions.Timeout:
        return [], "Timeout al conectar con Dota2ProTracker"
    except http.requests.RequestException as e:
        return [], f"Error de red: {e}"

    if status != 200 or not html:
        return [], f"Dota2ProTracker respondió {status}"

    try:
        configs = parse_meta_hero_grids_html(html)
    except ValueError as e:
        log.warning("No se pudo parsear meta-hero-grids: %s", e)
        return [], f"No se pudo leer el meta de Dota2ProTracker: {e}"

    cache.set(GRIDS_URL, configs)
    return configs, f"{len(configs)} grids D2PT"


# ── Ranking Meta por posición (home, para actualizar la plantilla Meta Meta) ───
def parse_meta_roles_html(html: str) -> dict[str, list[int]]:
    """Convierte el HTML de la home en {"pos 1": [hero_id, ...], ..., "pos 5": [...]}."""
    start = html.find(_ROLES_MARKER)
    if start == -1:
        raise ValueError("No se encontró el ranking por posición en la página")
    start = html.find("[", start)
    depth = 0
    end = None
    for i in range(start, len(html)):
        if html[i] == "[":
            depth += 1
        elif html[i] == "]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        raise ValueError("No se pudo cerrar el array de roles (HTML truncado)")

    roles = _js_array_to_json(html[start:end])
    if not isinstance(roles, list) or not roles:
        raise ValueError("El ranking por posición vino vacío")

    out: dict[str, list[int]] = {}
    for group in roles:
        pos = group.get("position")
        heroes = group.get("heroes")
        if not pos or not heroes:
            continue
        out[pos] = [h["hero_id"] for h in heroes if "hero_id" in h]
    if not out:
        raise ValueError("No se pudo leer ningún héroe del ranking por posición")
    return out


def fetch_meta_roles(ttl: int) -> tuple[dict[str, list[int]], str]:
    """Descarga (o reutiliza de caché) el top de héroes por posición ("pos 1".."pos 5")."""
    cached = cache.get(HOME_URL, ttl)
    if cached is not None:
        return cached, f"{sum(len(v) for v in cached.values())} héroes Meta (caché)"

    try:
        status, html = http.get_text(HOME_URL, timeout=20)
    except http.requests.exceptions.Timeout:
        return {}, "Timeout al conectar con Dota2ProTracker"
    except http.requests.RequestException as e:
        return {}, f"Error de red: {e}"

    if status != 200 or not html:
        return {}, f"Dota2ProTracker respondió {status}"

    try:
        roles = parse_meta_roles_html(html)
    except ValueError as e:
        log.warning("No se pudo parsear el ranking por posición: %s", e)
        return {}, f"No se pudo leer el Meta de Dota2ProTracker: {e}"

    cache.set(HOME_URL, roles)
    return roles, f"{sum(len(v) for v in roles.values())} héroes Meta"


def apply_role_heroes(meta_meta: dict, role_heroes: dict[str, list[int]]) -> dict:
    """
    Copia meta_meta reemplazando los hero_ids de las categorías cuyo nombre contiene
    "POS N" (N=1..5) por el ranking Meta actual de esa posición. Posiciones/tamaños de
    las cajas y categorías sin "POS N" en el nombre (p.ej. COMFORT) quedan intactas.
    """
    updated = {
        "config_name": meta_meta.get("config_name", "Meta Meta"),
        "categories": [dict(cat) for cat in meta_meta.get("categories", [])],
    }
    for cat in updated["categories"]:
        m = _POS_IN_NAME.search(cat.get("category_name", ""))
        if not m:
            continue
        pos_key = f"pos {m.group(1)}"
        if pos_key in role_heroes:
            cat["hero_ids"] = role_heroes[pos_key]
    return updated
