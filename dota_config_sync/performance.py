"""
Pestaña Rendimiento: ranked recientes, resumen, MMR estimado, historial de medalla y K/D/A contra el bracket.

El MMR real no es público: se estima por el rango promedio del lobby de cada partida
(mismo enfoque que d2calibrate). El historial de medalla lo construye la app guardando
un punto por día en progress.json — OpenDota devuelve un solo punto.
"""

import json
import logging
from collections import defaultdict
from datetime import date

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


def _rate(wins: int, games: int) -> float | None:
    return wins / games if games else None


def summarize(matches_newest_first: list[dict]) -> dict:
    """Winrate por ventana, racha, tabla por héroe (posición y K/D/A medios) y winrate por posición."""
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

    per_hero: dict[int, list[tuple[bool, dict]]] = defaultdict(list)
    for m, w in zip(ms, wins, strict=True):
        hid = opendota._coerce_hero_id(m.get("hero_id"))
        if hid is not None:
            per_hero[hid].append((w, m))
    heroes = []
    for hid, rows in per_hero.items():
        g = len(rows)
        hw = sum(1 for w, _ in rows if w)
        k, d, a = (sum(int(m.get(key) or 0) for _, m in rows) for key in ("kills", "deaths", "assists"))
        recent = [w for w, _ in rows[:5]]
        older = [w for w, _ in rows[5:10]]
        trend = "→"
        if len(recent) >= 3 and len(older) >= 3:
            diff = sum(recent) / len(recent) - sum(older) / len(older)
            trend = "↑" if diff > 0.15 else "↓" if diff < -0.15 else "→"
        played = [int(m["position"]) for _, m in rows if m.get("position")]
        row: dict = {
            "hero_id": hid, "games": g, "wins": hw, "wr": hw / g, "kda": (k + a) / max(d, 1), "trend": trend,
            "kills": k / g, "deaths": d / g, "assists": a / g,
            "position": max(set(played), key=played.count) if played else None,
        }
        with_gpm = [m for _, m in rows if m.get("gpm")]
        if with_gpm:
            row["gpm"] = sum(int(m["gpm"]) for m in with_gpm) / len(with_gpm)
            row["xpm"] = sum(int(m.get("xpm") or 0) for m in with_gpm) / len(with_gpm)
        heroes.append(row)
    heroes.sort(key=lambda h: (h["games"], h["wr"]), reverse=True)

    def avg_minutes(pred) -> float | None:
        durs = [int(m.get("duration") or 0) for m, w in zip(ms, wins, strict=True) if pred(w)]
        return sum(durs) / len(durs) / 60 if durs else None

    # Solo con Stratz: posición jugada por partida.
    positions: dict[int, tuple[int, int, float | None]] = {}
    if any(m.get("position") for m in ms):
        for pos in range(1, 6):
            positions[pos] = split(lambda m, p=pos: m.get("position") == p)

    return {
        "total": len(ms), "windows": windows, "streak": streak, "heroes": heroes, "positions": positions,
        "avg_minutes_win": avg_minutes(lambda w: w), "avg_minutes_loss": avg_minutes(lambda w: not w),
        "last_start": int(ms[0].get("start_time") or 0) if ms else None,
    }


BENCH_MIN_GAMES = 100   # partidas del bracket en ese héroe+posición para que el promedio valga


def benchmark_heroes(heroes: list[dict], position_stats: dict[int, dict[int, dict]]) -> list[dict]:
    """
    Agrega a cada fila de héroe el promedio del bracket para ese héroe EN la posición que jugaste
    (K/D/A y KDA, de Stratz): h["bench"] = {kills, deaths, assists, kda}. Sin dato, no agrega nada.
    """
    for h in heroes:
        pos = h.get("position")
        row = (position_stats.get(h["hero_id"]) or {}).get(pos) if pos else None
        if not row or row.get("games", 0) < BENCH_MIN_GAMES:
            continue
        k, d, a = float(row["kills"]), float(row["deaths"]), float(row["assists"])
        h["bench"] = {"kills": k, "deaths": d, "assists": a, "kda": (k + a) / max(d, 0.1)}
    return heroes


# ── Lectura: de los números a frases que dicen qué hacer ──────────────────────
def next_medal(rank_tier: int | None, est_mmr: int | None) -> dict | None:
    """Siguiente medalla/estrella, MMR que falta (según la estimación) y victorias netas a +25."""
    if not rank_tier:
        return None
    main, stars = int(rank_tier) // 10, int(rank_tier) % 10
    if main not in _MEDAL_MMR or main >= 8:
        return None
    base, step = _MEDAL_MMR[main]
    if stars < 5:
        target_tier, threshold = main * 10 + stars + 1, base + stars * step
    else:
        target_tier, threshold = (main + 1) * 10 + 1, _MEDAL_MMR[main + 1][0]
    missing = max(0, threshold - est_mmr) if est_mmr is not None else None
    wins = -(-missing // MMR_PER_RESULT) if missing is not None else None
    return {"target_tier": target_tier, "threshold": threshold, "missing_mmr": missing, "net_wins": wins}


def insights(summary: dict, hero_names: dict[int, str] | None = None) -> list[tuple[str, str]]:
    """[(tono "ok"|"warn"|"info", frase)] con lo que los números dicen que hagas."""
    names = hero_names or {}
    out: list[tuple[str, str]] = []
    pct = lambda r: f"{r:.0%}"  # noqa: E731

    _, _, r20 = summary["windows"][20]
    _, _, r50 = summary["windows"][50]
    if r20 is not None and r50 is not None and summary["total"] >= 30:
        if r20 - r50 >= 0.07:
            out.append(("ok", f"Vas en subida: {pct(r20)} en las últimas 20 contra {pct(r50)} en las últimas 50."))
        elif r50 - r20 >= 0.07:
            out.append(("warn", f"Vas en bajada: {pct(r20)} en las últimas 20 contra {pct(r50)} en las últimas 50. "
                                "Revisá qué cambió: héroes nuevos, posición, racha."))
        else:
            out.append(("info", f"Estable: {pct(r20)} en las últimas 20, {pct(r50)} en 50."))

    pos_rows = [(p, g, r) for p, (_, g, r) in (summary.get("positions") or {}).items() if g >= 10 and r is not None]
    if len(pos_rows) >= 2:
        best = max(pos_rows, key=lambda t: t[2])
        worst = min(pos_rows, key=lambda t: t[2])
        if best[2] - worst[2] >= 0.08:
            out.append(("info", f"Rendís mejor de pos {best[0]} ({pct(best[2])}, {best[1]} pj) que de pos {worst[0]} "
                                f"({pct(worst[2])}, {worst[1]} pj)."))

    good = [h for h in summary["heroes"] if h["games"] >= 8 and h["wr"] >= 0.55]
    bad = [h for h in summary["heroes"] if h["games"] >= 8 and h["wr"] < 0.45]
    if good:
        top = ", ".join(f"{names.get(h['hero_id'], h['hero_id'])} {pct(h['wr'])} ({h['games']})" for h in good[:3])
        out.append(("ok", f"Tus héroes que suman: {top}. Spamearlos es la vía más corta a subir."))
    if bad:
        low = ", ".join(f"{names.get(h['hero_id'], h['hero_id'])} {pct(h['wr'])} ({h['games']})" for h in bad[:2])
        out.append(("warn", f"Te restan: {low}. Practicalos en unranked antes de volver a pickearlos."))

    bench_lines = 0
    for h in [h for h in summary["heroes"] if h.get("bench") and h["games"] >= 5]:
        if bench_lines >= 2:
            break
        name, b = names.get(h["hero_id"], h["hero_id"]), h["bench"]
        if h["deaths"] >= 1.3 * b["deaths"] and h["deaths"] - b["deaths"] >= 1.5:
            out.append(("warn", f"Con {name} morís {h['deaths']:.1f} veces por partida; el promedio Divine/Immortal "
                                f"de pos {h['position']} es {b['deaths']:.1f}. Jugá más atrás: el resto lo tenés."))
            bench_lines += 1
        elif h["kda"] >= 1.25 * b["kda"] and h["games"] >= 8:
            out.append(("ok", f"Con {name} tu KDA ({h['kda']:.1f}) supera al del bracket en pos {h['position']} "
                              f"({b['kda']:.1f}): lo jugás mejor que la media de tu rango."))
            bench_lines += 1

    streak = summary["streak"]
    if streak <= -3:
        out.append(("warn", f"Racha de {-streak} derrotas seguidas: cortá la sesión. "
                            "Después de 3 el tilt pesa más que el skill."))
    elif streak >= 3:
        out.append(("ok", f"Racha de {streak} victorias: buen momento para seguir con tus héroes seguros."))

    mw, ml = summary.get("avg_minutes_win"), summary.get("avg_minutes_loss")
    if mw and ml:
        if ml - mw >= 5:
            out.append(("info", f"Tus derrotas duran {ml - mw:.0f} min más que tus victorias ({ml:.0f} vs {mw:.0f}): "
                                "perdés las largas. Cerrá antes o pickeá héroes que escalen."))
        elif mw - ml >= 5:
            out.append(("info", f"Ganás las largas ({mw:.0f} min) y perdés rápido ({ml:.0f} min): "
                                "cuando la línea sale mal, defendé y estirá."))
    return out


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
