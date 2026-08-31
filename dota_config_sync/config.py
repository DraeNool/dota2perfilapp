"""
Configuración persistente de la app.

Orden de prioridad para la API key de Steam:
    1. Variable de entorno STEAM_API_KEY (útil en CI / no toca disco).
    2. Campo "steam_api_key" en config.json (lo que introduce el usuario en la UI).

config.json NO se versiona (está en .gitignore). config.example.json sí, como plantilla.
"""

import json
import logging
import os
from copy import deepcopy

from .paths import config_path

log = logging.getLogger(__name__)

# Plantilla fija "Meta Meta" usada por defecto si el usuario no la personaliza.
DEFAULT_META_META = {
    "config_name": "Meta Meta",
    "categories": [
        {"category_name": "OFFLANE / TIER S - A  / POS 3", "x_position": 0.0, "y_position": 0.0,
         "width": 474.782623, "height": 259.130432, "hero_ids": [2, 135, 60, 104, 36, 14, 128]},
        {"category_name": "SOFT / POS 4", "x_position": 833.913086, "y_position": 0.0,
         "width": 304.347839, "height": 295.652191, "hero_ids": [14, 71, 22, 105, 62, 88, 74]},
        {"category_name": "MID / POS 2", "x_position": 474.782623, "y_position": 0.0,
         "width": 357.391327, "height": 169.565216, "hero_ids": [74, 128, 25, 13, 90, 34, 76]},
        {"category_name": "CARRY / POS 1", "x_position": 0.869565, "y_position": 300.0,
         "width": 296.521759, "height": 163.478271, "hero_ids": [6, 41, 12, 8, 54, 93, 67]},
        {"category_name": "COMFORT", "x_position": 310.434784, "y_position": 286.086975,
         "width": 389.565216, "height": 253.913055, "hero_ids": [92, 23, 97, 38, 7, 98, 55, 107]},
        {"category_name": "SUPP / POS 5", "x_position": 832.173950, "y_position": 295.652191,
         "width": 335.652191, "height": 185.217392, "hero_ids": [14, 31, 87, 86, 27, 75, 128]},
    ],
}

DEFAULTS = {
    "steam_api_key": "",
    "preferred_main_id64": "76561198128824716",
    "favorites_limit": 15,
    "recent_matches_limit": 20,
    "cache_ttl_seconds": 600,          # perfiles / partidas recientes
    "hero_map_ttl_seconds": 86400,     # listado de héroes (cambia muy poco)
    "opendota_min_interval": 1.1,      # segundos entre llamadas a OpenDota (~55/min < límite 60)
    "meta_grids_ttl_seconds": 3600,    # grids Meta de Dota2ProTracker (cambian con el parche)
    "meta_meta": DEFAULT_META_META,
}


class AppConfig:
    """Carga/guarda config.json y expone los valores con defaults seguros."""

    def __init__(self, data: dict | None = None):
        self._data = deepcopy(DEFAULTS)
        if data:
            self._data.update({k: v for k, v in data.items() if k in DEFAULTS})

    @classmethod
    def load(cls) -> "AppConfig":
        path = config_path()
        if not path.exists():
            log.info("config.json no existe, usando valores por defecto: %s", path)
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(data)
        except (OSError, json.JSONDecodeError) as e:
            log.warning("No se pudo leer config.json (%s); usando defaults", e)
            return cls()

    def save(self) -> None:
        path = config_path()
        try:
            path.write_text(
                json.dumps(self._data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            log.info("config.json guardado en %s", path)
        except OSError as e:
            log.error("No se pudo guardar config.json: %s", e)

    # ── Accesores ────────────────────────────────────────────────────────────
    @property
    def steam_api_key(self) -> str:
        return os.environ.get("STEAM_API_KEY") or self._data.get("steam_api_key", "")

    def set_steam_api_key(self, key: str) -> None:
        self._data["steam_api_key"] = (key or "").strip()

    @property
    def preferred_main_id64(self) -> str:
        return self._data.get("preferred_main_id64", "")

    @property
    def favorites_limit(self) -> int:
        return int(self._data.get("favorites_limit", 15))

    @property
    def recent_matches_limit(self) -> int:
        return int(self._data.get("recent_matches_limit", 20))

    @property
    def cache_ttl_seconds(self) -> int:
        return int(self._data.get("cache_ttl_seconds", 600))

    @property
    def hero_map_ttl_seconds(self) -> int:
        return int(self._data.get("hero_map_ttl_seconds", 86400))

    @property
    def opendota_min_interval(self) -> float:
        return float(self._data.get("opendota_min_interval", 1.1))

    @property
    def meta_grids_ttl_seconds(self) -> int:
        return int(self._data.get("meta_grids_ttl_seconds", 3600))

    @property
    def meta_meta(self) -> dict:
        return self._data.get("meta_meta", DEFAULT_META_META)
