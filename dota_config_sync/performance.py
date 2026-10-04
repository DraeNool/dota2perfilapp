"""
Pestaña Rendimiento: ranked recientes desde OpenDota, resumen, MMR estimado e historial de medalla.

El MMR real no es público: se estima por el rango promedio del lobby de cada partida
(mismo enfoque que d2calibrate). El historial de medalla lo construye la app guardando
un punto por día en progress.json — OpenDota devuelve un solo punto.
"""

import json
import logging
from collections import defaultdict
from datetime import date, datetime

from . import http, opendota
from .paths import progress_path

log = logging.getLogger(__name__)

MATCHES_URL = "https://api.opendota.com/api/players/{steam_id3}/matches"
MMR_PER_RESULT = 25
ROLLING_WINDOW = 10

# (MMR base, MMR por estrella) por medalla principal. Inmortal no tiene estrellas.
_MEDAL_MMR: dict[int, tuple[int, int]] = {
    1: (0, 154), 2: (770, 154), 3: (1540, 154), 4: (2310, 154),
    5: (3080, 154), 6: (3850, 154), 7: (4620, 200), 8: (5620, 0),
}


def fetch_ranked_matches(steam_id3: str, limit: int, ttl: int) -> tuple[list[dict], str]:
    """Últimas `limit` ranked (lobby_type 7), más recientes primero."""
    try:
        status, body = http.cached_get_json(
            MATCHES_URL.format(steam_id3=steam_id3), ttl=ttl, timeout=25, rate_limited=True,
            params={"lobby_type": 7, "limit": limit, "significant": 0},
        )
    except http.requests.exceptions.Timeout:
        return [], "Timeout al conectar con OpenDota"
    except http.requests.RequestException as e:
        return [], f"Error de red: {e}"
    if status == 403:
        return [], "Perfil privado — activa Exposición de datos de partida"
    if status != 200 or not body:
        return [], f"OpenDota respondió {status}"
    matches = sorted(body, key=lambda m: int(m.get("start_time") or 0), reverse=True)
    return matches, f"{len(matches)} ranked"


# ── Lógica pura ──────────────────────────────────────────────────────────────
def mmr_from_rank_tier(rank_tier: int | None) -> int | None:
    """Punto medio de MMR para un rank_tier (p.ej. 75 = Divino 5 ≈ 5 520)."""
    if not rank_tier:
        return None
    main, stars = int(rank_tier) // 10, int(rank_tier) % 10
    if main not in _MEDAL_MMR:
        return None
    base, step = _MEDAL_MMR[main]
    if step == 0:
        return base
    return base + (max(stars, 1) - 1) * step + step // 2


def estimate_mmr_series(matches_newest_first: list[dict]) -> list[tuple[dict, int | None]]:
    """
    (partida, MMR estimado) de la más vieja a la más nueva.

    Ancla = MMR del rango promedio del lobby (se arrastra el último conocido);
    estimación = media móvil de las anclas + 25 por cada victoria neta de la ventana.
    """
    ordered = list(reversed(matches_newest_first))
    anchors: list[int | None] = []
    last: int | None = None
    for m in ordered:
        a = mmr_from_rank_tier(m.get("average_rank"))
        if a is not None:
            last = a
        anchors.append(last)

    out: list[tuple[dict, int | None]] = []
    for i, m in enumerate(ordered):
        window = [(ordered[j], anchors[j]) for j in range(max(0, i - ROLLING_WINDOW + 1), i + 1)]
        known = [a for _, a in window if a is not None]
        if not known:
            out.append((m, None))
            continue
        net = sum(1 if opendota.is_win(mm) else -1 for mm, _ in window if opendota.is_win(mm) is not None)
        out.append((m, round(sum(known) / len(known) + MMR_PER_RESULT * net)))
    return out


def _period(start_time: int) -> str:
    hour = datetime.fromtimestamp(start_time).hour
    if 6 <= hour < 13:
        return "mañana"
    if 13 <= hour < 19:
        return "tarde"
    return "noche"


def _rate(wins: int, games: int) -> float | None:
    return wins / games if games else None


def summarize(matches_newest_first: list[dict]) -> dict:
    """Winrate por ventana, racha, solo/party, horario y tabla por héroe."""
    ms = [m for m in matches_newest_first if opendota.is_win(m) is not None]
    wins = [bool(opendota.is_win(m)) for m in ms]

    windows = {}
    for n in (20, 50, 100):
        chunk = wins[:n]
        windows[n] = (sum(chunk), len(chunk) - sum(chunk), _rate(sum(chunk), len(chunk)))

    streak = 0
    for w in wins:
        if streak == 0:
            streak = 1 if w else -1
        elif (streak > 0) == w:
            streak += 1 if w else -1
        else:
            break

    def split(pred) -> tuple[int, int, float | None]:
        sel = [w for m, w in zip(ms, wins, strict=True) if pred(m)]
        return sum(sel), len(sel), _rate(sum(sel), len(sel))

    solo = split(lambda m: (m.get("party_size") or 1) <= 1)
    party = split(lambda m: (m.get("party_size") or 1) > 1)
    periods = {
        p: split(lambda m, p=p: _period(int(m.get("start_time") or 0)) == p)
        for p in ("mañana", "tarde", "noche")
    }

    per_hero: dict[int, list[tuple[bool, int, int, int]]] = defaultdict(list)
    for m, w in zip(ms, wins, strict=True):
        hid = opendota._coerce_hero_id(m.get("hero_id"))
        if hid is not None:
            per_hero[hid].append((w, int(m.get("kills") or 0), int(m.get("deaths") or 0), int(m.get("assists") or 0)))
    heroes = []
    for hid, rows in per_hero.items():
        g = len(rows)
        hw = sum(1 for r in rows if r[0])
        k, d, a = (sum(r[i] for r in rows) for i in (1, 2, 3))
        recent = rows[:5]
        older = rows[5:10]
        trend = "→"
        if len(recent) >= 3 and len(older) >= 3:
            diff = sum(1 for r in recent if r[0]) / len(recent) - sum(1 for r in older if r[0]) / len(older)
            trend = "↑" if diff > 0.15 else "↓" if diff < -0.15 else "→"
        heroes.append({
            "hero_id": hid, "games": g, "wins": hw, "wr": hw / g, "kda": (k + a) / max(d, 1), "trend": trend,
        })
    heroes.sort(key=lambda h: (h["games"], h["wr"]), reverse=True)

    return {
        "total": len(ms), "windows": windows, "streak": streak,
        "solo": solo, "party": party, "periods": periods, "heroes": heroes,
        "last_start": int(ms[0].get("start_time") or 0) if ms else None,
    }


# ── Historial de medalla (progress.json) ─────────────────────────────────────
def load_progress() -> dict[str, list[dict]]:
    path = progress_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as e:
        log.warning("progress.json ilegible (%s); se ignora", e)
        return {}


def record_snapshot(steam_id3: str, rank_tier: int | None, today: date | None = None) -> list[dict]:
    """Guarda (fecha, rank_tier) una vez por día por cuenta; devuelve el historial de la cuenta."""
    if not rank_tier:
        return load_progress().get(str(steam_id3), [])
    day = (today or date.today()).isoformat()
    data = load_progress()
    history = data.setdefault(str(steam_id3), [])
    if history and history[-1].get("date") == day:
        history[-1]["rank_tier"] = int(rank_tier)
    elif not history or history[-1].get("rank_tier") != int(rank_tier):
        history.append({"date": day, "rank_tier": int(rank_tier)})
    else:
        return history
    try:
        progress_path().write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError as e:
        log.warning("No se pudo guardar progress.json: %s", e)
    return history
