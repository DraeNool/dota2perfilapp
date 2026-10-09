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
from typing import Any

from .paths import config_path

log = logging.getLogger(__name__)

# Plantilla fija "Meta Meta" usada por defecto si el usuario no la personaliza.
DEFAULT_META_META: dict[str, Any] = {
    "config_name": "Meta Meta",
    "categories": [
        # hero_ids por posición: snapshot del meta 7.41f (2026-10-03). Es solo el fallback
        # cuando Dota2ProTracker no responde; al generar se reemplazan por el ranking vivo.
        {"category_name": "OFFLANE / TIER S - A  / POS 3", "x_position": 0.0, "y_position": 0.0,
         "width": 474.782623, "height": 259.130432, "hero_ids": [33, 60, 55, 14, 2, 135]},
        {"category_name": "SOFT / POS 4", "x_position": 833.913086, "y_position": 0.0,
         "width": 304.347839, "height": 295.652191, "hero_ids": [62, 71, 86, 88, 9, 14]},
        {"category_name": "MID / POS 2", "x_position": 474.782623, "y_position": 0.0,
         "width": 357.391327, "height": 169.565216, "hero_ids": [74, 76, 49, 82, 106, 90]},
        {"category_name": "CARRY / POS 1", "x_position": 0.869565, "y_position": 300.0,
         "width": 296.521759, "height": 163.478271, "hero_ids": [12, 18, 93, 54, 67, 8]},
        {"category_name": "COMFORT", "x_position": 310.434784, "y_position": 286.086975,
         "width": 389.565216, "height": 253.913055, "hero_ids": [92, 23, 97, 38, 7, 98, 55, 107, 155]},
        {"category_name": "SUPP / POS 5", "x_position": 832.173950, "y_position": 295.652191,
         "width": 335.652191, "height": 185.217392, "hero_ids": [112, 50, 31, 131, 121, 87]},
    ],
}

DEFAULTS = {
    "steam_api_key": "",
    "stratz_api_token": "",            # también STRATZ_API_TOKEN en el entorno (prioridad)
    "preferred_main_id64": "76561198128824716",
    "favorites_limit": 15,
    "recent_matches_limit": 20,
    "cache_ttl_seconds": 600,          # perfiles / partidas recientes
    "hero_map_ttl_seconds": 86400,     # listado de héroes (cambia muy poco)
    "opendota_min_interval": 1.1,      # segundos entre llamadas a OpenDota (~55/min < límite 60)
    "meta_grids_ttl_seconds": 3600,    # grids Meta de Dota2ProTracker (cambian con el parche)
    "opendota_stats_ttl_seconds": 86400,  # heroStats y matchups (pesados, cambian poco)
    "ranked_matches_limit": 200,       # partidas ranked que lee la pestaña Rendimiento
    # Pesos del asistente de picks (se normalizan a suma 1). Con enemigos / sin enemigos (first pick).
    "picks_weights": {"meta": 0.30, "counters": 0.45, "position": 0.15, "personal": 0.10},
    "first_pick_weights": {"meta": 0.40, "counters": 0.35, "position": 0.15, "personal": 0.10},
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
    def stratz_api_token(self) -> str:
        return os.environ.get("STRATZ_API_TOKEN") or str(self._data.get("stratz_api_token") or "")

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
    def opendota_stats_ttl_seconds(self) -> int:
        return int(self._data.get("opendota_stats_ttl_seconds", 86400))

    @property
    def ranked_matches_limit(self) -> int:
        return int(self._data.get("ranked_matches_limit", 200))

    @property
    def picks_weights(self) -> dict:
        raw = self._data.get("picks_weights")
        return dict(raw) if isinstance(raw, dict) else {}

    @property
    def first_pick_weights(self) -> dict:
        raw = self._data.get("first_pick_weights")
        return dict(raw) if isinstance(raw, dict) else {}

    @property
    def meta_meta(self) -> dict:
        return self._data.get("meta_meta", DEFAULT_META_META)

    def _comfort_category(self) -> dict[str, Any] | None:
        categories: list[dict[str, Any]] = self.meta_meta.get("categories", [])
        for cat in categories:
            if "comfort" in str(cat.get("category_name", "")).lower():
                return cat
        return None

    @property
    def comfort_hero_ids(self) -> list[int]:
        cat = self._comfort_category()
        return list(cat.get("hero_ids", [])) if cat else []

    def set_comfort_hero_ids(self, hero_ids: list[int]) -> None:
        cat = self._comfort_category()
        if cat is None:
            template = next(c for c in DEFAULT_META_META["categories"] if c["category_name"] == "COMFORT")
            cat = deepcopy(template)
            self.meta_meta.setdefault("categories", []).append(cat)
        cat["hero_ids"] = list(hero_ids)
