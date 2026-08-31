"""Hero grids Meta (D2PT) — extraídos de la página pública de Dota2ProTracker (con caché)."""

import json
import logging
import re

from . import cache, http

log = logging.getLogger(__name__)

GRIDS_URL = "https://dota2protracker.com/meta-hero-grids"

_PAYLOAD_MARKER = '"data":{grids:{matches:{configs:'
_BAREWORD_KEY = re.compile(r'([{,])(\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*):')


def _extract_configs_array(html: str) -> str:
    """Aísla el literal `[...]` de `configs` embebido en el HTML (JS, no JSON aún)."""
    start = html.find(_PAYLOAD_MARKER)
    if start == -1:
        raise ValueError("No se encontró el payload de hero grids en la página")
    start += len(_PAYLOAD_MARKER)
    if html[start] != "[":
        raise ValueError("Formato de payload inesperado (no empieza con '[')")

    depth = 0
    for i in range(start, len(html)):
        ch = html[i]
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return html[start:i + 1]
    raise ValueError("No se pudo cerrar el array de configs (HTML truncado)")


def parse_meta_hero_grids_html(html: str) -> list[dict]:
    """Convierte el HTML de /meta-hero-grids en la lista `configs` (formato hero_grid_config.json)."""
    raw_array = _extract_configs_array(html)
    json_array = _BAREWORD_KEY.sub(r'\1\2"\3"\4:', raw_array)
    configs = json.loads(json_array)
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
