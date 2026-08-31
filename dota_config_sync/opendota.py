"""Datos públicos de OpenDota: héroes, rango, winrate y favoritos (con caché + rate-limit)."""

import logging
import re
import threading
from collections import Counter

from . import http

log = logging.getLogger(__name__)

_HERO_MAP_CACHE: dict[int, str] | None = None
_HERO_MAP_LOCK = threading.Lock()
_HERO_MAP_TTL = 86400


# ── Utilidades puras ─────────────────────────────────────────────────────────
def format_kda(kills, deaths, assists) -> str:
    k, d, a = int(kills or 0), int(deaths or 0), int(assists or 0)
    ratio = (k + a) / max(d, 1)
    return f"{k}/{d}/{a} ({ratio:.2f})"


def is_win(match: dict) -> bool | None:
    slot = match.get("player_slot")
    radiant_win = match.get("radiant_win")
    if slot is None or radiant_win is None:
        return None
    return (slot < 128 and radiant_win) or (slot >= 128 and not radiant_win)


def _normalize_hero_name(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def _coerce_hero_id(value):
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


# ── Hero map ─────────────────────────────────────────────────────────────────
def configure(hero_map_ttl_seconds: int) -> None:
    global _HERO_MAP_TTL
    _HERO_MAP_TTL = int(hero_map_ttl_seconds)


def get_hero_map() -> dict[int, str]:
    """Descarga el listado de héroes (cacheado en memoria y en disco) una sola vez."""
    global _HERO_MAP_CACHE
    with _HERO_MAP_LOCK:
        if _HERO_MAP_CACHE is not None:
            return _HERO_MAP_CACHE
        out: dict[int, str] = {}
        try:
            status, body = http.cached_get_json(
                "https://api.opendota.com/api/heroes", ttl=_HERO_MAP_TTL,
                timeout=15, rate_limited=True,
            )
            if status == 200 and body:
                for hero in body:
                    hid = _coerce_hero_id(hero.get("id"))
                    if hid is None:
                        continue
                    out[hid] = str(hero.get("localized_name") or hero.get("name") or f"Hero #{hid}")
        except http.requests.RequestException as e:
            log.warning("No se pudo descargar el hero map: %s", e)
        _HERO_MAP_CACHE = out
        return _HERO_MAP_CACHE


def hero_id_lookup() -> dict[str, int]:
    return {_normalize_hero_name(name): hid for hid, name in get_hero_map().items()}


# ── Perfil ───────────────────────────────────────────────────────────────────
def fetch_profile(steam_id3: str, ttl: int) -> dict:
    """Rango, winrate competitivo estimado, último héroe/resultado/KDA."""
    data = {
        "rank_tier": None, "competitive_winrate": None, "competitive_source": None,
        "last_hero": None, "last_result": None, "last_kda": None,
    }

    try:
        status, body = http.cached_get_json(
            f"https://api.opendota.com/api/players/{steam_id3}", ttl=ttl,
            timeout=15, rate_limited=True,
        )
        if status == 200 and body and body.get("rank_tier"):
            data["rank_tier"] = int(body["rank_tier"])
    except http.requests.RequestException as e:
        log.warning("OpenDota player %s falló: %s", steam_id3, e)

    recent = []
    try:
        status, body = http.cached_get_json(
            f"https://api.opendota.com/api/players/{steam_id3}/recentMatches", ttl=ttl,
            timeout=15, rate_limited=True,
        )
        if status == 200 and body:
            recent = body
    except http.requests.RequestException as e:
        log.warning("OpenDota recentMatches %s falló: %s", steam_id3, e)

    if recent:
        hero_map = get_hero_map()
        last = max(recent, key=lambda m: int(m.get("start_time") or 0))
        hid = _coerce_hero_id(last.get("hero_id"))
        data["last_hero"] = hero_map.get(hid, f"Hero #{hid}") if hid is not None else "N/D"
        result = is_win(last)
        data["last_result"] = "Ganó" if result is True else "Perdió" if result is False else "N/D"
        data["last_kda"] = format_kda(last.get("kills"), last.get("deaths"), last.get("assists"))

        ranked = [m for m in recent if m.get("lobby_type") == 7]
        pool = ranked if len(ranked) >= 5 else recent
        if pool:
            wins = sum(1 for m in pool if is_win(m) is True)
            data["competitive_winrate"] = wins / len(pool)
            data["competitive_source"] = "ranked" if pool is ranked else "recent"

    if data["competitive_winrate"] is None:
        try:
            status, body = http.cached_get_json(
                f"https://api.opendota.com/api/players/{steam_id3}/wl", ttl=ttl,
                timeout=15, rate_limited=True,
            )
            if status == 200 and body:
                wins, losses = int(body.get("win", 0)), int(body.get("lose", 0))
                if wins + losses:
                    data["competitive_winrate"] = wins / (wins + losses)
                    data["competitive_source"] = "global"
        except http.requests.RequestException as e:
            log.warning("OpenDota wl %s falló: %s", steam_id3, e)

    return data


# ── Favoritos ────────────────────────────────────────────────────────────────
PRIOR_GAMES = 15
PRIOR_WINS = PRIOR_GAMES * 0.5


def performance_score(row: dict) -> float:
    """Score bayesiano: penaliza muestras pequeñas con un prior de 15 partidas al 50%."""
    games = int(row.get("games") or 0)
    wins = int(row.get("win") or 0)
    adj_wr = (wins + PRIOR_WINS) / (games + PRIOR_GAMES)
    return games * (adj_wr ** 1.5)


def rank_favorites_by_performance(rows: list[dict], limit: int) -> list[int]:
    """Lógica pura de ranking (separada para poder testearla sin red)."""
    rows = [r for r in rows if int(r.get("games") or 0) > 0]
    rows.sort(key=performance_score, reverse=True)
    hero_ids: list[int] = []
    for row in rows:
        hid = _coerce_hero_id(row.get("hero_id"))
        if hid is None or hid in hero_ids:
            continue
        hero_ids.append(hid)
        if len(hero_ids) >= limit:
            break
    return hero_ids[:limit]


def favorites_from_performance(steam_id3: str, limit: int, ttl: int) -> tuple[list[int], str]:
    try:
        status, body = http.cached_get_json(
            f"https://api.opendota.com/api/players/{steam_id3}/heroes", ttl=ttl,
            timeout=20, rate_limited=True,
        )
    except http.requests.exceptions.Timeout:
        return [], "Timeout al conectar con OpenDota"
    except http.requests.RequestException as e:
        return [], f"Error de red: {e}"

    if status == 403:
        return [], "Perfil privado — activa Exposición de datos de partido"
    rows = body or []
    if not rows or not any(int(r.get("games") or 0) > 0 for r in rows):
        return [], "Sin datos de héroes"

    hero_ids = rank_favorites_by_performance(rows, limit)
    return hero_ids, f"{len(hero_ids)} héroes por performance"


def rank_favorites_recent(recent: list[dict], limit: int) -> list[int]:
    """Héroes recientes por recencia, completados por frecuencia (lógica pura)."""
    ordered: list[int] = []
    seen: set[int] = set()
    for match in sorted(recent, key=lambda m: int(m.get("start_time") or 0), reverse=True)[:20]:
        hid = _coerce_hero_id(match.get("hero_id"))
        if hid is None or hid in seen:
            continue
        seen.add(hid)
        ordered.append(hid)

    if len(ordered) < limit:
        counts = Counter()
        for match in recent[:20]:
            hid = _coerce_hero_id(match.get("hero_id"))
            if hid is not None:
                counts[hid] += 1
        for hid, _ in counts.most_common():
            if hid not in seen:
                ordered.append(hid)
                seen.add(hid)
            if len(ordered) >= limit:
                break

    return ordered[:limit]


def favorites_from_recent(steam_id3: str, limit: int, ttl: int) -> tuple[list[int], str]:
    try:
        status, body = http.cached_get_json(
            f"https://api.opendota.com/api/players/{steam_id3}/recentMatches", ttl=ttl,
            timeout=20, rate_limited=True,
        )
    except http.requests.exceptions.Timeout:
        return [], "Timeout al conectar con OpenDota"
    except http.requests.RequestException as e:
        return [], f"Error de red: {e}"

    if status == 403:
        return [], "Perfil privado — no hay recentMatches públicos"
    recent = body or []
    if not recent:
        return [], "Sin partidas recientes"

    ordered = rank_favorites_recent(recent, limit)
    return ordered, f"{len(ordered)} héroes de las últimas 20"
