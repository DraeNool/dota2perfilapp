"""
Foto del meta Divine/Immortal: stats por posición de Stratz, ranking por posición de D2PT y nombres.

Es lo único que Picks descarga al arrancar (una consulta a Stratz + la home de D2PT, ambas con caché).
Sin token de Stratz se cae a heroStats de OpenDota (brackets 7 y 8 sumados, sin posición).
Las tablas de matchups se bajan después, una por héroe pickeado, en `fetch_tables`.
"""

import logging
from dataclasses import dataclass, field

from . import dota2protracker, http, opendota, stratz

log = logging.getLogger(__name__)

BRACKET = "DIVINE_IMMORTAL"
BRACKET_LABEL = "Divine/Immortal"
OPENDOTA_BRACKETS = (7, 8)
HERO_STATS_URL = "https://api.opendota.com/api/heroStats"
MATCHUPS_URL = "https://api.opendota.com/api/heroes/{hero_id}/matchups"


@dataclass
class MetaSnapshot:
    patch: str | None = None
    hero_names: dict[int, str] = field(default_factory=dict)
    # Stratz: {hero: {pos: {games, wins, kills, deaths, assists}}} en el bracket.
    position_stats: dict[int, dict[int, dict]] = field(default_factory=dict)
    # {hero: (partidas, victorias)} en el bracket, sin posición (de Stratz sumado o de OpenDota).
    overall: dict[int, tuple[int, int]] = field(default_factory=dict)
    roles: dict[str, list[int]] = field(default_factory=dict)   # D2PT: "pos 1".."pos 5" → ranking
    source: str = ""                                            # "stratz" | "opendota" | ""

    def name(self, hid: int) -> str:
        return self.hero_names.get(hid, f"Hero #{hid}")

    @property
    def ready(self) -> bool:
        return bool(self.hero_names and self.overall)


# ── Puro ─────────────────────────────────────────────────────────────────────
def overall_from_positions(position_stats: dict[int, dict[int, dict]]) -> dict[int, tuple[int, int]]:
    return {hid: (sum(r["games"] for r in by_pos.values()), sum(r["wins"] for r in by_pos.values()))
            for hid, by_pos in position_stats.items() if by_pos}


def overall_from_opendota(rows: dict[int, dict],
                          brackets: tuple[int, ...] = OPENDOTA_BRACKETS) -> dict[int, tuple[int, int]]:
    out: dict[int, tuple[int, int]] = {}
    for hid, row in rows.items():
        games = sum(int(row.get(f"{b}_pick") or 0) for b in brackets)
        wins = sum(int(row.get(f"{b}_win") or 0) for b in brackets)
        if games > 0:
            out[hid] = (games, wins)
    return out


# ── Red ──────────────────────────────────────────────────────────────────────
def fetch_hero_stats_opendota(ttl: int) -> dict[int, dict]:
    """Filas de heroStats por hero_id (pick/win por bracket). {} si falla."""
    try:
        status, body = http.cached_get_json(HERO_STATS_URL, ttl=ttl, timeout=20, rate_limited=True)
    except http.requests.RequestException as e:
        log.warning("heroStats falló: %s", e)
        return {}
    if status != 200 or not body:
        return {}
    out: dict[int, dict] = {}
    for row in body:
        hid = opendota._coerce_hero_id(row.get("id"))
        if hid is not None:
            out[hid] = row
    return out


def fetch_matchups_opendota(hero_id: int, ttl: int) -> dict[int, tuple[int, int]]:
    """{rival: (partidas, victorias del héroe contra ese rival)} en todos los brackets."""
    try:
        status, body = http.cached_get_json(
            MATCHUPS_URL.format(hero_id=hero_id), ttl=ttl, timeout=20, rate_limited=True,
        )
    except http.requests.RequestException as e:
        log.warning("matchups %s falló: %s", hero_id, e)
        return {}
    if status != 200 or not body:
        return {}
    out: dict[int, tuple[int, int]] = {}
    for row in body:
        rival = opendota._coerce_hero_id(row.get("hero_id"))
        if rival is not None:
            out[rival] = (int(row.get("games_played") or 0), int(row.get("wins") or 0))
    return out


def position_stats(cfg) -> dict[int, dict[int, dict]]:
    """Stats por posición del bracket (Stratz, caché meta_ttl). {} sin token o si falla."""
    token = cfg.stratz_api_token
    if not token:
        return {}
    return stratz.fetch_position_stats_table(BRACKET, token, cfg.meta_ttl_seconds)


def load_snapshot(cfg) -> MetaSnapshot:
    snap = MetaSnapshot(hero_names=dict(opendota.get_hero_map()))
    roles, patch, _ = dota2protracker.fetch_meta_roles(cfg.meta_grids_ttl_seconds)
    snap.roles, snap.patch = roles, patch
    snap.position_stats = position_stats(cfg)
    if snap.position_stats:
        snap.overall = overall_from_positions(snap.position_stats)
        snap.source = "stratz"
    else:
        snap.overall = overall_from_opendota(fetch_hero_stats_opendota(cfg.opendota_stats_ttl_seconds))
        snap.source = "opendota" if snap.overall else ""
    return snap


def fetch_tables(hero_id: int, cfg) -> tuple[dict[int, tuple[int, int]], dict[int, tuple[int, int]]]:
    """
    (vs, with) del héroe. Con token: Stratz en el bracket; si llega vacío (respuesta rara cacheada o
    fallo transitorio) se reintenta una vez sin caché. Sin token: OpenDota (todos los brackets, solo vs).
    Con token no se cae a OpenDota: su tabla no distingue bracket y hoy puede tardar un minuto por héroe.
    """
    ttl = cfg.opendota_stats_ttl_seconds
    if cfg.stratz_api_token:
        vs, with_ = stratz.fetch_hero_matchups(hero_id, BRACKET, cfg.stratz_api_token, ttl)
        if not vs:
            vs, with_ = stratz.fetch_hero_matchups(hero_id, BRACKET, cfg.stratz_api_token, 0)
        return vs, with_
    return fetch_matchups_opendota(hero_id, ttl), {}
