"""
Asistente de picks: meta por bracket, counters, sinergias de D2PT y héroes propios.

Toda la lógica de puntuación es pura (testeable sin red); la red vive en las
funciones fetch_* que cachean en disco con TTL largo — en draft no se espera red.
"""

import logging
from dataclasses import dataclass, field

from . import http, opendota

log = logging.getLogger(__name__)

HERO_STATS_URL = "https://api.opendota.com/api/heroStats"
MATCHUPS_URL = "https://api.opendota.com/api/heroes/{hero_id}/matchups"
PLAYER_HEROES_URL = "https://api.opendota.com/api/players/{steam_id3}/heroes"

DEFAULT_WEIGHTS = {"meta": 0.35, "counters": 0.35, "personal": 0.20, "position": 0.10}
META_PRIOR_GAMES = 200
COUNTER_PRIOR_GAMES = 50
PERSONAL_PRIOR_GAMES = 15
SYNERGY_POINTS = 2.5
MAX_SYNERGY_POINTS = 6.0

BRACKET_NAMES = {
    1: "Heraldo", 2: "Guardián", 3: "Cruzado", 4: "Arcano",
    5: "Leyenda", 6: "Ancestral", 7: "Divino", 8: "Inmortal",
}


# ── Datos ────────────────────────────────────────────────────────────────────
def bracket_from_rank_tier(rank_tier: int | None) -> int | None:
    if not rank_tier:
        return None
    main = int(rank_tier) // 10
    return main if 1 <= main <= 8 else None


def fetch_hero_stats(ttl: int) -> dict[int, dict]:
    """Pick/win por bracket (1..8) y pro, por héroe. {} si falla."""
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


def fetch_matchups(hero_id: int, ttl: int) -> dict[int, tuple[int, int]]:
    """{enemigo: (partidas, victorias del héroe contra ese enemigo)}."""
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
        enemy = opendota._coerce_hero_id(row.get("hero_id"))
        if enemy is not None:
            out[enemy] = (int(row.get("games_played") or 0), int(row.get("wins") or 0))
    return out


def fetch_player_heroes(steam_id3: str, ttl: int) -> dict[int, dict]:
    """Filas de /players/{id}/heroes por hero_id (games, win, with_*, against_*)."""
    try:
        status, body = http.cached_get_json(
            PLAYER_HEROES_URL.format(steam_id3=steam_id3), ttl=ttl, timeout=20, rate_limited=True,
        )
    except http.requests.RequestException as e:
        log.warning("players/heroes %s falló: %s", steam_id3, e)
        return {}
    if status != 200 or not body:
        return {}
    out: dict[int, dict] = {}
    for row in body:
        hid = opendota._coerce_hero_id(row.get("hero_id"))
        if hid is not None:
            out[hid] = row
    return out


@dataclass
class D2ptRelations:
    """Best/Worst with/against por héroe, extraídos de los grids por posición de D2PT."""

    best_with: dict[int, set[int]] = field(default_factory=dict)
    worst_with: dict[int, set[int]] = field(default_factory=dict)
    best_against: dict[int, set[int]] = field(default_factory=dict)
    worst_against: dict[int, set[int]] = field(default_factory=dict)


def parse_d2pt_relations(configs: list[dict]) -> D2ptRelations:
    """
    En cada config por posición, la primera categoría es "Top Heroes Pos N" y después
    vienen 4 categorías por héroe (Best with, Worst with, Best against, Worst against),
    en filas a la misma altura (y_position). Se agrupan por fila para no depender del orden.
    """
    rel = D2ptRelations()
    for cfg in configs:
        cats = cfg.get("categories", [])
        top = next((c for c in cats if str(c.get("category_name", "")).lower().startswith("top heroes")), None)
        if not top:
            continue
        heroes = list(top.get("hero_ids", []))
        rows: dict[float, list[dict]] = {}
        for c in cats:
            if c is top:
                continue
            rows.setdefault(round(float(c.get("y_position", 0))), []).append(c)
        for idx, y in enumerate(sorted(rows)):
            if idx >= len(heroes):
                break
            hero = heroes[idx]
            for c in rows[y]:
                name = str(c.get("category_name", "")).lower()
                ids = set(c.get("hero_ids", []))
                target = {
                    "best with": rel.best_with, "worst with": rel.worst_with,
                    "best against": rel.best_against, "worst against": rel.worst_against,
                }.get(name)
                if target is not None:
                    target.setdefault(hero, set()).update(ids)
    return rel


# ── Modelo ───────────────────────────────────────────────────────────────────
@dataclass
class DraftState:
    my_pos: int | None = None
    allies: list[int] = field(default_factory=list)
    enemies: list[int] = field(default_factory=list)
    enemy_pos: dict[int, int] = field(default_factory=dict)
    bans: list[int] = field(default_factory=list)

    def taken(self) -> set[int]:
        return set(self.allies) | set(self.enemies) | set(self.bans)


@dataclass
class PickData:
    hero_names: dict[int, str] = field(default_factory=dict)
    hero_stats: dict[int, dict] = field(default_factory=dict)
    matchups: dict[int, dict[int, tuple[int, int]]] = field(default_factory=dict)
    player_heroes: dict[int, dict] = field(default_factory=dict)
    roles: dict[str, list[int]] = field(default_factory=dict)
    relations: D2ptRelations = field(default_factory=D2ptRelations)
    bracket: int | None = None

    def name(self, hid: int) -> str:
        return self.hero_names.get(hid, f"Hero #{hid}")


@dataclass
class Recommendation:
    hero_id: int
    name: str
    score: float
    parts: dict[str, float]
    chips: list[tuple[str, str]]  # (texto, tono: "", "g", "a", "r")
    reason: str


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def meta_winrate(row: dict | None, bracket: int | None) -> tuple[float, int]:
    """(winrate suavizado, partidas) en el bracket; cae a pub si no hay bracket."""
    if not row:
        return 0.5, 0
    key = f"{bracket}_" if bracket and row.get(f"{bracket}_pick") else "pub_"
    picks = int(row.get(f"{key}pick") or 0)
    wins = int(row.get(f"{key}win") or 0)
    return (wins + 0.5 * META_PRIOR_GAMES) / (picks + META_PRIOR_GAMES), picks


def matchup_winrate(games: int, wins: int) -> float:
    return (wins + 0.5 * COUNTER_PRIOR_GAMES) / (games + COUNTER_PRIOR_GAMES)


def personal_rate(row: dict | None) -> tuple[float, float, int]:
    """(winrate ajustado, confianza 0..1, partidas) del jugador con ese héroe."""
    if not row:
        return 0.5, 0.0, 0
    games = int(row.get("games") or 0)
    wins = int(row.get("win") or 0)
    adj = (wins + 0.5 * PERSONAL_PRIOR_GAMES) / (games + PERSONAL_PRIOR_GAMES)
    return adj, games / (games + PERSONAL_PRIOR_GAMES), games


def hero_vs_enemy(hero: int, enemy: int, data: PickData) -> tuple[int, float] | None:
    """
    (partidas, winrate suavizado de `hero` contra `enemy`), leído de la tabla del ENEMIGO:
    data.matchups[enemy][hero] = (partidas, victorias del enemigo). Así alcanza con bajar
    la tabla de cada enemigo pickeado (≤5 llamadas) para puntuar a los 127 candidatos.
    """
    row = data.matchups.get(enemy, {}).get(hero)
    if not row:
        return None
    games, enemy_wins = row
    return games, matchup_winrate(games, games - enemy_wins)


def _counter_delta(hero: int, enemies: list[int], data: PickData) -> tuple[float, int | None, int]:
    """(delta medio vs enemigos, enemigo que mejor countereás, enemigos con dato)."""
    deltas: list[tuple[float, int]] = []
    for e in enemies:
        d = 0.0
        known = False
        vs = hero_vs_enemy(hero, e, data)
        if vs:
            d += vs[1] - 0.5
            known = True
        if e in data.relations.best_against.get(hero, ()):
            d += 0.02
            known = True
        if hero in data.relations.best_against.get(e, ()):
            d -= 0.02
            known = True
        if known:
            deltas.append((d, e))
    if not deltas:
        return 0.0, None, 0
    best = max(deltas)
    return sum(d for d, _ in deltas) / len(deltas), (best[1] if best[0] > 0.03 else None), len(deltas)


def score_hero(hero: int, state: DraftState, data: PickData,
               weights: dict[str, float] = DEFAULT_WEIGHTS) -> Recommendation:
    chips: list[tuple[str, str]] = []
    parts: dict[str, float] = {}

    wr, picks = meta_winrate(data.hero_stats.get(hero), data.bracket)
    parts["meta"] = _clamp((wr - 0.42) / 0.16)
    bracket_txt = BRACKET_NAMES.get(data.bracket or 0, "pub")
    chips.append((f"Meta {wr:.0%} · {bracket_txt}", "g" if wr >= 0.53 else "r" if wr <= 0.47 else ""))

    delta, best_target, known = _counter_delta(hero, state.enemies, data)
    parts["counters"] = _clamp(0.5 + delta / 0.16) if known else 0.5
    if state.enemies:
        tone = "g" if delta >= 0.03 else "r" if delta <= -0.03 else ""
        chips.append((f"{delta:+.0%} vs enemigos" if known else "sin dato vs enemigos", tone))

    adj, conf, games = personal_rate(data.player_heroes.get(hero))
    parts["personal"] = _clamp(0.5 + conf * (adj - 0.5) / 0.15) if games else 0.42
    if games:
        chips.append((f"Vos {adj:.0%} · {games} pj", "g" if adj >= 0.55 and games >= 8 else "a" if games < 5 else ""))
    else:
        chips.append(("Sin partidas tuyas", "a"))

    pos_rank: int | None = None
    if state.my_pos:
        ranked = data.roles.get(f"pos {state.my_pos}", [])
        pos_rank = ranked.index(hero) if hero in ranked else None
        parts["position"] = (1.0 - 0.08 * pos_rank) if pos_rank is not None else 0.35
        if pos_rank is not None:
            chips.append((f"D2PT #{pos_rank + 1} pos {state.my_pos}", "g" if pos_rank < 3 else ""))
    else:
        parts["position"] = 0.5

    synergy = 0.0
    for ally in state.allies:
        if ally in data.relations.best_with.get(hero, ()) or hero in data.relations.best_with.get(ally, ()):
            synergy += SYNERGY_POINTS
            chips.append((f"Best with {data.name(ally)}", "g"))
        if ally in data.relations.worst_with.get(hero, ()) or hero in data.relations.worst_with.get(ally, ()):
            synergy -= SYNERGY_POINTS
            chips.append((f"Worst with {data.name(ally)}", "r"))
    synergy = max(-MAX_SYNERGY_POINTS, min(MAX_SYNERGY_POINTS, synergy))

    score = 100 * sum(weights[k] * parts[k] for k in weights) + synergy

    reasons = []
    if best_target is not None:
        reasons.append(f"counter de {data.name(best_target)}")
    if pos_rank is not None and pos_rank < 3:
        reasons.append(f"#{pos_rank + 1} del meta en pos {state.my_pos}")
    if games >= 8 and adj >= 0.55:
        reasons.append(f"tu héroe fuerte ({games} pj)")
    elif games == 0:
        reasons.append("nunca lo jugaste")
    if not reasons:
        reasons.append("sólido en el meta" if parts["meta"] >= 0.6 else "neutro contra este draft")
    reason = "; ".join(reasons)
    return Recommendation(hero, data.name(hero), round(score, 1), parts, chips, reason[0].upper() + reason[1:])


def recommend(state: DraftState, data: PickData, weights: dict[str, float] = DEFAULT_WEIGHTS,
              limit: int = 10) -> list[Recommendation]:
    taken = state.taken()
    pool = [h for h in data.hero_names if h not in taken]
    recs = [score_hero(h, state, data, weights) for h in pool]
    recs.sort(key=lambda r: r.score, reverse=True)
    return recs[:limit]


def draft_alerts(state: DraftState, data: PickData, limit: int = 6) -> list[tuple[str, str]]:
    """[(tono "warn"|"ok", texto)] sobre tus héroes fuertes vs el draft enemigo y sinergias aliadas."""
    alerts: list[tuple[str, str]] = []
    strong = sorted(
        (r for r in data.player_heroes.values() if int(r.get("games") or 0) >= 5),
        key=opendota.performance_score, reverse=True,
    )[:8]
    for row in strong:
        hero = opendota._coerce_hero_id(row.get("hero_id"))
        if hero is None or hero in state.taken():
            continue
        for e in state.enemies:
            vs = hero_vs_enemy(hero, e, data)
            if not vs:
                continue
            g, wr = vs
            if g >= 200 and wr < 0.46:
                alerts.append((
                    "warn", f"{data.name(e)} en enemigo: tu {data.name(hero)} pierde {1 - wr:.0%} contra él.",
                ))
    for e in state.enemies:
        hist = data.player_heroes.get(e)
        if hist and int(hist.get("against_games") or 0) >= 8:
            ag, aw = int(hist["against_games"]), int(hist.get("against_win") or 0)
            if aw / ag < 0.42:
                alerts.append((
                    "warn", f"Históricamente perdés {1 - aw / ag:.0%} cuando enfrentás a {data.name(e)} ({ag} pj).",
                ))
    allies = state.allies
    for i, a in enumerate(allies):
        for b in allies[i + 1:]:
            if b in data.relations.best_with.get(a, ()) or a in data.relations.best_with.get(b, ()):
                alerts.append(("ok", f"{data.name(a)} + {data.name(b)}: \"Best with\" en D2PT esta semana."))
    return alerts[:limit]
