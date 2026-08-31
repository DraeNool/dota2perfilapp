"""Construcción y guardado del hero_grid_config.json (Meta Meta + Favoritos)."""

import json
import logging
from pathlib import Path

from . import dota2protracker, opendota

log = logging.getLogger(__name__)


def build_hero_grid(steam_id3: str, *, meta_meta: dict, favorites_limit: int,
                    recent_limit: int, ttl: int, grids_ttl: int,
                    log_fn=None) -> tuple[dict, list[str], list[str], str]:
    """
    Construye el payload con:
      - Meta Meta (plantilla configurable)
      - Favoritos (performance + últimas 20 partidas)
      - Meta D2PT (grids públicos de Dota2ProTracker, scrapeados)

    Devuelve (payload, fav_perf_names, fav_recent_names, status_msg).
    """
    if log_fn:
        log_fn("  Consultando favoritos en OpenDota...")

    perf_ids, perf_msg = opendota.favorites_from_performance(steam_id3, favorites_limit, ttl)
    recent_ids, recent_msg = opendota.favorites_from_recent(steam_id3, recent_limit, ttl)

    hero_map = opendota.get_hero_map()
    perf_names = [hero_map.get(hid, f"Hero #{hid}") for hid in perf_ids]
    recent_names = [hero_map.get(hid, f"Hero #{hid}") for hid in recent_ids]

    if log_fn:
        log_fn(f"  Performance: {perf_msg}")
        log_fn(f"  Últimas 20: {recent_msg}")
        log_fn("  Descargando Meta de Dota2ProTracker...")

    d2pt_configs, d2pt_msg = dota2protracker.fetch_meta_hero_grids(grids_ttl)
    if log_fn:
        log_fn(f"  D2PT: {d2pt_msg}")

    payload = {
        "version": 3,
        "configs": [
            meta_meta,
            {
                "config_name": "Favoritos",
                "categories": [
                    {"category_name": "Favoritos por performance", "x_position": 0.0,
                     "y_position": 0.0, "width": 700.0, "height": 260.0, "hero_ids": perf_ids},
                    {"category_name": "Últimas 20 partidas", "x_position": 0.0,
                     "y_position": 290.0, "width": 700.0, "height": 260.0, "hero_ids": recent_ids},
                ],
            },
            *d2pt_configs,
        ],
    }
    status_msg = f"Performance: {perf_msg} | Recientes: {recent_msg} | D2PT: {d2pt_msg}"
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
        except OSError:
            try:
                backup.write_bytes(target.read_bytes())
                target.unlink(missing_ok=True)
            except OSError as e:
                log.warning("No se pudo respaldar el hero grid previo: %s", e)

    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return target
