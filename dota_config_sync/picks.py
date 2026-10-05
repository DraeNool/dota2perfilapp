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

# Con enemigos a la vista deciden los counters y el meta de la posición; el historial solo desempata:
# un héroe "tuyo" no debe colarse arriba cuando el draft pide otra cosa.
DEFAULT_WEIGHTS = {"meta": 0.30, "counters": 0.45, "position": 0.15, "personal": 0.10}
COUNTER_FULL_SCALE = 0.12   # ±6 % de delta medio contra los enemigos ya es counter (o countereado) a fondo
# First pick: manda el meta; "counters" acá es la seguridad ante counters.
FIRST_PICK_WEIGHTS = {"meta": 0.40, "counters": 0.35, "position": 0.15, "personal": 0.10}


def normalize_weights(raw: dict | None, base: dict[str, float]) -> dict[str, float]:
    """Mezcla pesos del usuario sobre `base` y los normaliza a suma 1; ignora claves o valores inválidos."""
    out = dict(base)
    for key, value in (raw or {}).items():
        if key in out:
            try:
                out[key] = max(0.0, float(value))
            except (TypeError, ValueError):
                continue
    total = sum(out.values()) or 1.0
    return {k: v / total for k, v in out.items()}
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
    tier: str = ""


# Tier por percentil dentro de todos los candidatos (rank/total ≤ p) ...
TIER_PERCENTILES = (("S+", 0.02), ("S", 0.05), ("A+", 0.10), ("A", 0.20),
                    ("B+", 0.35), ("B", 0.50), ("C+", 0.70), ("C", 1.01))
TIER_ORDER = [t for t, _ in TIER_PERCENTILES]
# ... con techo por puntaje absoluto: un draft donde ni el mejor pick es bueno no muestra un S+ falso.
TIER_CAPS = ((70, "S+"), (60, "S"), (50, "A+"), (40, "B+"), (0, "C+"))


def tier_for(score: float, rank: int, total: int) -> str:
    p = rank / max(total, 1)
    tier = next(t for t, limit in TIER_PERCENTILES if p <= limit)
    cap = next(c for floor, c in TIER_CAPS if score >= floor)
    return tier if TIER_ORDER.index(tier) >= TIER_ORDER.index(cap) else cap


def assign_tiers(recs: list[Recommendation]) -> list[Recommendation]:
    """Asigna tier a una lista YA ordenada de mejor a peor (toda la pool, no solo el top)."""
    for i, r in enumerate(recs, 1):
        r.tier = tier_for(r.score, i, len(recs))
    return recs


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


POPULAR_MIN_GAMES = 30      # OpenDota cuenta una ventana corta: con 30 partidas y el prior de 50 ya hay señal
COUNTER_WR = 0.52           # en pubs casi ningún matchup pasa de 53 %: con 52 % ya es un counter real
EXPOSURE_MAX = 0.25         # con 25 % del pool (ponderado por picks) counteréandolo, safety = 0
MIN_COVERAGE = 20           # con menos matchups válidos, un héroe raro parecería "seguro" solo por falta de datos


def counter_exposure(hero: int, data: PickData) -> tuple[float, list[int]] | None:
    """
    Qué tan countereable es `hero` sin saber nada del rival: fracción del pool (ponderada por
    cuánto se pickea cada héroe en tu bracket) que le gana ≥ COUNTER_WR. Usa la tabla propia
    data.matchups[hero]. Devuelve (exposición 0..1, peores 3 counters), o None si no hay tabla
    o la cobertura es insuficiente para opinar.
    """
    table = data.matchups.get(hero)
    if not table:
        return None
    key = f"{data.bracket}_pick" if data.bracket else "pub_pick"
    exposed = total = 0.0
    covered = 0
    counters: list[tuple[float, int]] = []
    for other, (games, hero_wins) in table.items():
        if games < POPULAR_MIN_GAMES or other == hero:
            continue
        covered += 1
        weight = float((data.hero_stats.get(other) or {}).get(key) or 1)
        wr = matchup_winrate(games, hero_wins)
        total += weight
        if wr <= 1 - COUNTER_WR:
            exposed += weight
            counters.append((wr, other))
    if not total or covered < MIN_COVERAGE:
        return None
    counters.sort()
    return exposed / total, [o for _, o in counters[:3]]


# Con quién se cruza cada posición en la fase de líneas (safe vs offlane, mid vs mid).
LANE_OPPONENTS = {1: {3, 4}, 5: {3, 4}, 3: {1, 5}, 4: {1, 5}, 2: {2}}
LANE_WEIGHT = 1.5


def is_lane_opponent(my_pos: int | None, enemy_pos: int | None) -> bool:
    return bool(my_pos and enemy_pos and enemy_pos in LANE_OPPONENTS.get(my_pos, set()))


def _counter_delta(hero: int, state: DraftState, data: PickData) -> tuple[float, int | None, int]:
    """
    (delta medio vs enemigos, enemigo que mejor countereás, enemigos con dato).

    El rival de tu línea pesa LANE_WEIGHT: ahí se decide la partida temprana.
    """
    deltas: list[tuple[float, int, float]] = []
    for e in state.enemies:
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
            w = LANE_WEIGHT if is_lane_opponent(state.my_pos, state.enemy_pos.get(e)) else 1.0
            deltas.append((d, e, w))
    if not deltas:
        return 0.0, None, 0
    best = max(deltas, key=lambda t: t[0] * t[2])
    weighted = sum(d * w for d, _, w in deltas) / sum(w for _, _, w in deltas)
    return weighted, (best[1] if best[0] > 0.03 else None), len(deltas)


def score_hero(hero: int, state: DraftState, data: PickData,
               weights: dict[str, float] = DEFAULT_WEIGHTS) -> Recommendation:
    chips: list[tuple[str, str]] = []
    parts: dict[str, float] = {}

    wr, picks = meta_winrate(data.hero_stats.get(hero), data.bracket)
    parts["meta"] = _clamp((wr - 0.42) / 0.16)
    bracket_txt = BRACKET_NAMES.get(data.bracket or 0, "pub")
    chips.append((f"Meta {wr:.0%} · {bracket_txt}", "g" if wr >= 0.53 else "r" if wr <= 0.47 else ""))

    delta, best_target, known = _counter_delta(hero, state, data)
    exposure: tuple[float, list[int]] | None = None
    if state.enemies:
        parts["counters"] = _clamp(0.5 + delta / COUNTER_FULL_SCALE) if known else 0.5
        parts["known_enemies"] = float(known)   # informativo: no entra en la suma ponderada
        tone = "g" if delta >= 0.03 else "r" if delta <= -0.03 else ""
        chips.append((f"{delta:+.0%} vs enemigos" if known else "sin dato vs enemigos", tone))
        lane = [e for e in state.enemies if is_lane_opponent(state.my_pos, state.enemy_pos.get(e))]
        for e in lane:
            vs = hero_vs_enemy(hero, e, data)
            if vs:
                lane_tone = "g" if vs[1] >= 0.53 else "r" if vs[1] <= 0.47 else ""
                chips.append((f"{vs[1] - 0.5:+.0%} vs {data.name(e)} (tu línea)", lane_tone))
    else:
        # First pick: sin rival a la vista, vale más el que menos counters tiene.
        exposure = counter_exposure(hero, data)
        if exposure:
            parts["counters"] = _clamp(1 - exposure[0] / EXPOSURE_MAX)
            exp_tone = "g" if exposure[0] <= 0.08 else "r" if exposure[0] >= 0.20 else "a"
            chips.append((f"First pick: {exposure[0]:.0%} del pool lo counterea", exp_tone))
        else:
            # Sin datos no se premia: leve malus, como el historial desconocido.
            parts["counters"] = 0.4
            if hero in data.matchups:
                chips.append(("First pick: pocos matchups con muestra", "a"))

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
    if exposure:
        if exposure[0] <= 0.08:
            reasons.append("first pick seguro: casi nadie lo countera")
        elif exposure[0] >= 0.20:
            reasons.append("arriesgado de first: lo counterean " + ", ".join(data.name(c) for c in exposure[1]))
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


def weights_for(state: DraftState) -> dict[str, float]:
    """Sin enemigos a la vista se recomienda un first pick: pesa el meta, no el historial."""
    return DEFAULT_WEIGHTS if state.enemies else FIRST_PICK_WEIGHTS


def has_enough_sample(r: Recommendation, state: DraftState, data: PickData) -> bool:
    """
    Sin muestra no se recomienda. First pick: tabla propia ya bajada pero sin cobertura → fuera
    (la que todavía no llegó se muestra mientras tanto). Con enemigos y sus tablas ya bajadas:
    fuera el héroe que no aparece contra ninguno de ellos.
    """
    if not state.enemies:
        return r.hero_id not in data.matchups or counter_exposure(r.hero_id, data) is not None
    if all(e in data.matchups for e in state.enemies):
        return r.parts.get("known_enemies", 0) > 0
    return True


def rank_all(state: DraftState, data: PickData, weights: dict[str, float] | None = None) -> list[Recommendation]:
    """Toda la pool (sin los ya pickeados) puntuada, filtrada por muestra, ordenada y con tier."""
    weights = weights or weights_for(state)
    taken = state.taken()
    recs = [score_hero(h, state, data, weights) for h in data.hero_names if h not in taken]
    recs = [r for r in recs if has_enough_sample(r, state, data)]
    recs.sort(key=lambda r: r.score, reverse=True)
    return assign_tiers(recs)


def recommend(state: DraftState, data: PickData, weights: dict[str, float] | None = None,
              limit: int = 10) -> list[Recommendation]:
    return rank_all(state, data, weights)[:limit]


def meta_first_picks(state: DraftState, data: PickData, limit: int = 5,
                     weights: dict[str, float] | None = None) -> list[Recommendation]:
    """
    Los héroes del meta de TU posición (ranking D2PT) ordenados como first pick: cuán
    countereables son, su winrate en tu bracket y tu historial con ellos. Ignora a los
    enemigos cargados a propósito: es la respuesta a "¿qué firsteo?" antes del draft.
    """
    if not state.my_pos:
        return []
    pool = set(data.roles.get(f"pos {state.my_pos}", [])) - state.taken()
    blind = DraftState(my_pos=state.my_pos, allies=state.allies)
    # El tier sale del ranking de TODA la pool a ciegas, no del puñado de héroes de la posición.
    ranked = rank_all(blind, data, weights or FIRST_PICK_WEIGHTS)
    return [r for r in ranked if r.hero_id in pool][:limit]


def strong_heroes(data: PickData, limit: int = 8) -> list[int]:
    """Tus héroes con mejor score bayesiano (≥5 partidas), de mejor a peor."""
    rows = sorted(
        (r for r in data.player_heroes.values() if int(r.get("games") or 0) >= 5),
        key=opendota.performance_score, reverse=True,
    )
    out = []
    for row in rows:
        hid = opendota._coerce_hero_id(row.get("hero_id"))
        if hid is not None:
            out.append(hid)
    return out[:limit]


def draft_alerts(state: DraftState, data: PickData, limit: int = 6,
                 recs: list[Recommendation] | None = None) -> list[tuple[str, str]]:
    """
    [(tono "warn"|"ok"|"info", texto)]: qué te conviene y qué te amenaza en este draft.

    Con enemigos: tus héroes fuertes que ganan o pierden contra ellos, enemigos que
    counterean a tus aliados ya pickeados, tu historial contra cada enemigo y sinergias.
    Sin enemigos (first pick): qué candidatos son seguros y cuáles arriesgados.
    """
    warns: list[tuple[str, str]] = []
    goods: list[tuple[str, str]] = []
    taken = state.taken()

    if state.enemies:
        for hero in strong_heroes(data):
            if hero in taken:
                continue
            for e in state.enemies:
                vs = hero_vs_enemy(hero, e, data)
                if not vs or vs[0] < POPULAR_MIN_GAMES:
                    continue
                if vs[1] <= 0.47:
                    warns.append(("warn", f"{data.name(e)} castiga a tu {data.name(hero)}: pierde {1 - vs[1]:.0%}."))
                elif vs[1] >= 0.55:
                    goods.append(("ok", f"Tu {data.name(hero)} le gana {vs[1]:.0%} a {data.name(e)}."))
        for ally in state.allies:
            for e in state.enemies:
                vs = hero_vs_enemy(ally, e, data)
                if vs and vs[0] >= POPULAR_MIN_GAMES and vs[1] <= 0.46:
                    warns.append(("warn", f"{data.name(e)} countera a tu aliado {data.name(ally)} ({1 - vs[1]:.0%})."))
        for e in state.enemies:
            hist = data.player_heroes.get(e)
            if hist and int(hist.get("against_games") or 0) >= 8:
                ag, aw = int(hist["against_games"]), int(hist.get("against_win") or 0)
                if aw / ag < 0.45:
                    warns.append(("warn", f"Vos perdés {1 - aw / ag:.0%} cuando enfrentás a {data.name(e)} ({ag} pj)."))
                elif aw / ag > 0.58:
                    goods.append(("ok", f"Vos le ganás {aw / ag:.0%} a {data.name(e)} ({ag} pj)."))
    elif recs:
        rated = [(r, counter_exposure(r.hero_id, data)) for r in recs[:8]]
        safe = [r for r, ex in rated if ex and ex[0] <= 0.08]
        risky = [(r, ex) for r, ex in rated if ex and ex[0] >= 0.20]
        if safe:
            goods.append(("ok", "First picks seguros: " + ", ".join(r.name for r in safe[:3]) + "."))
        for r, ex in risky[:2]:
            names = ", ".join(data.name(c) for c in ex[1])
            warns.append(("warn", f"{r.name} de first es arriesgado: {ex[0]:.0%} del pool lo countera ({names})."))
        if not rated or all(ex is None for _, ex in rated):
            goods.append(("info", "Cargando tablas de counters de los candidatos para evaluar el first pick..."))

    allies = state.allies
    for i, a in enumerate(allies):
        for b in allies[i + 1:]:
            if b in data.relations.best_with.get(a, ()) or a in data.relations.best_with.get(b, ()):
                goods.append(("ok", f"{data.name(a)} + {data.name(b)}: \"Best with\" en D2PT esta semana."))
            if b in data.relations.worst_with.get(a, ()) or a in data.relations.worst_with.get(b, ()):
                warns.append(("warn", f"{data.name(a)} + {data.name(b)}: \"Worst with\" en D2PT."))

    merged = warns + goods
    if not merged and state.enemies:
        merged.append(("info", "Ningún matchup fuerte a favor ni en contra con tus héroes habituales."))
    return merged[:limit]
