"""
Asistente de picks, solo con estadísticas del bracket Divine/Immortal.

Un candidato suma: meta de su posición (winrate y cuota de picks), counters contra los enemigos
(o, sin enemigos, qué tan countereable es como first pick) y sinergia con los aliados ya elegidos.
Nada del historial del jugador entra en la decisión. Todo acá es puro: la red vive en `meta`.
"""

from dataclasses import dataclass, field

from .meta import BRACKET_LABEL, MetaSnapshot

# Con enemigos a la vista deciden los counters; el meta de la posición y la sinergia acompañan.
DEFAULT_WEIGHTS = {"meta": 0.35, "counters": 0.45, "synergy": 0.20}
# First pick: manda el meta; "safety" es cuánto NO se lo puede counterear.
FIRST_PICK_WEIGHTS = {"meta": 0.50, "safety": 0.30, "synergy": 0.20}

META_PRIOR_GAMES = 100          # winrate por posición: muestras de miles → prior chico
COUNTER_PRIOR_GAMES = 50
COUNTER_FULL_SCALE = 0.12       # ±6 % de delta medio contra los enemigos ya es counter (o countereado) a fondo
SYNERGY_FULL_SCALE = 0.12
POPULAR_MIN_GAMES = 30          # mínimo de partidas para que un matchup opine
COUNTER_WR = 0.52               # en pubs casi ningún matchup pasa de 53 %: con 52 % ya es un counter real
EXPOSURE_MAX = 0.25             # con 25 % del pool (ponderado por picks) countereándolo, safety = 0
MIN_COVERAGE = 20               # con menos matchups válidos, un héroe raro parecería "seguro" por falta de datos
MIN_POS_SHARE = 0.05            # por debajo, el héroe no se juega en esa posición: no es un pick para vos
MIN_POS_RATE = 0.005            # ...ni si ocupa menos del 0,5 % de los picks de la posición en el bracket
META_POOL_SHARE = 0.15          # first picks "del meta": héroes que se juegan de verdad en la posición...
META_POOL_SIZE = 40             # ...entre los más pickeados ahí
TOP_PICK_RATE = 0.06            # cuota de picks de la posición que ya cuenta como "muy jugado"


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
class DraftData:
    meta: MetaSnapshot = field(default_factory=MetaSnapshot)
    # Tabla propia de cada héroe bajado: {héroe: {rival: (partidas, victorias del héroe)}}.
    matchups: dict[int, dict[int, tuple[int, int]]] = field(default_factory=dict)
    # {héroe: {aliado: (partidas, victorias juntos)}} — solo con Stratz.
    synergy: dict[int, dict[int, tuple[int, int]]] = field(default_factory=dict)

    @property
    def hero_names(self) -> dict[int, str]:
        return self.meta.hero_names

    def name(self, hid: int) -> str:
        return self.meta.name(hid)


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


def _smooth(games: int, wins: int, prior: int) -> float:
    return (wins + 0.5 * prior) / (games + prior)


# ── Meta de la posición ──────────────────────────────────────────────────────
def position_fit(hero: int, pos: int, meta: MetaSnapshot) -> tuple[float, float, int] | None:
    """(cuota de sus partidas en `pos`, winrate suavizado ahí, partidas ahí); None sin datos del héroe."""
    by_pos = meta.position_stats.get(hero)
    if not by_pos:
        return None
    total = sum(r["games"] for r in by_pos.values())
    if total <= 0:
        return None
    row = by_pos.get(pos) or {"games": 0, "wins": 0}
    return row["games"] / total, _smooth(row["games"], row["wins"], META_PRIOR_GAMES), row["games"]


def position_totals(meta: MetaSnapshot) -> dict[int, int]:
    """Partidas por posición sumando todos los héroes (para la cuota de picks)."""
    totals: dict[int, int] = {}
    for by_pos in meta.position_stats.values():
        for pos, row in by_pos.items():
            totals[pos] = totals.get(pos, 0) + row["games"]
    return totals


def meta_rate(hero: int, pos: int | None, meta: MetaSnapshot,
              totals: dict[int, int] | None = None) -> tuple[float, int, float]:
    """
    (winrate suavizado, partidas, cuota de picks) del héroe en el bracket: en `pos` si hay stats por
    posición, si no en general. La cuota es relativa a los picks de esa posición (≈0.06 ya es top).
    """
    if pos and meta.position_stats:
        fit = position_fit(hero, pos, meta)
        if fit:
            _, wr, games = fit
            total = (totals or position_totals(meta)).get(pos, 0)
            return wr, games, games / total if total else 0.0
        return 0.5, 0, 0.0
    games, wins = meta.overall.get(hero, (0, 0))
    all_games = sum(g for g, _ in meta.overall.values())
    # Un héroe ocupa 1 de 10 slots por partida; una posición 2 de 10 → ×5 deja la cuota comparable.
    return _smooth(games, wins, META_PRIOR_GAMES), games, 5 * games / all_games if all_games else 0.0


# ── Counters y sinergia ──────────────────────────────────────────────────────
def hero_vs_enemy(hero: int, enemy: int, data: DraftData) -> tuple[int, float] | None:
    """
    (partidas, winrate suavizado de `hero` contra `enemy`), leído de la tabla del ENEMIGO:
    data.matchups[enemy][hero] = (partidas, victorias del enemigo). Alcanza con bajar la
    tabla de cada enemigo pickeado (≤5 llamadas) para puntuar a todos los candidatos.
    """
    row = data.matchups.get(enemy, {}).get(hero)
    if not row:
        return None
    games, enemy_wins = row
    return games, _smooth(games, games - enemy_wins, COUNTER_PRIOR_GAMES)


def counter_exposure(hero: int, data: DraftData) -> tuple[float, list[int]] | None:
    """
    Qué tan countereable es `hero` sin saber nada del rival: fracción del pool (ponderada por
    cuánto se pickea cada héroe en el bracket) que le gana ≥ COUNTER_WR. Usa la tabla propia
    data.matchups[hero]. (exposición 0..1, peores 3 counters) o None sin tabla o sin cobertura.
    """
    table = data.matchups.get(hero)
    if not table:
        return None
    exposed = total = 0.0
    covered = 0
    counters: list[tuple[float, int]] = []
    for other, (games, hero_wins) in table.items():
        if games < POPULAR_MIN_GAMES or other == hero:
            continue
        covered += 1
        weight = float(data.meta.overall.get(other, (1, 0))[0] or 1)
        wr = _smooth(games, hero_wins, COUNTER_PRIOR_GAMES)
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


def _counter_delta(hero: int, state: DraftState, data: DraftData) -> tuple[float, int | None, int]:
    """(delta medio vs enemigos, enemigo que mejor countereás, enemigos con dato). Tu línea pesa LANE_WEIGHT."""
    deltas: list[tuple[float, int, float]] = []
    for e in state.enemies:
        vs = hero_vs_enemy(hero, e, data)
        if vs:
            w = LANE_WEIGHT if is_lane_opponent(state.my_pos, state.enemy_pos.get(e)) else 1.0
            deltas.append((vs[1] - 0.5, e, w))
    if not deltas:
        return 0.0, None, 0
    best = max(deltas, key=lambda t: t[0] * t[2])
    weighted = sum(d * w for d, _, w in deltas) / sum(w for _, _, w in deltas)
    return weighted, (best[1] if best[0] > 0.03 else None), len(deltas)


def synergy_with(hero: int, ally: int, data: DraftData) -> tuple[int, float] | None:
    """(partidas, winrate suavizado jugando juntos) desde la tabla "with" de cualquiera de los dos."""
    row = data.synergy.get(ally, {}).get(hero) or data.synergy.get(hero, {}).get(ally)
    if not row or row[0] < POPULAR_MIN_GAMES:
        return None
    return row[0], _smooth(row[0], row[1], COUNTER_PRIOR_GAMES)


def _synergy_delta(hero: int, state: DraftState, data: DraftData) -> tuple[float, int, list[tuple[str, str]]]:
    deltas: list[float] = []
    chips: list[tuple[str, str]] = []
    for ally in state.allies:
        syn = synergy_with(hero, ally, data)
        if not syn:
            continue
        d = syn[1] - 0.5
        deltas.append(d)
        if abs(d) >= 0.02:
            chips.append((f"{d:+.0%} con {data.name(ally)}", "g" if d > 0 else "r"))
    if not deltas:
        return 0.0, 0, chips
    return sum(deltas) / len(deltas), len(deltas), chips


# ── Puntuación ───────────────────────────────────────────────────────────────
def score_hero(hero: int, state: DraftState, data: DraftData, weights: dict[str, float] = DEFAULT_WEIGHTS,
               totals: dict[int, int] | None = None) -> Recommendation:
    chips: list[tuple[str, str]] = []
    parts: dict[str, float] = {}
    meta = data.meta

    wr, games, pick_rate = meta_rate(hero, state.my_pos, meta, totals)
    popularity = _clamp(pick_rate / TOP_PICK_RATE)
    parts["meta"] = 0.7 * _clamp((wr - 0.42) / 0.16) + 0.3 * popularity
    where = f"pos {state.my_pos}" if state.my_pos and meta.position_stats else BRACKET_LABEL
    tone = "g" if wr >= 0.53 else "r" if wr <= 0.47 else ""
    chips.append((f"Meta {where}: {wr:.0%} · {pick_rate:.0%} de los picks", tone))

    delta, best_target, known = _counter_delta(hero, state, data)
    exposure: tuple[float, list[int]] | None = None
    if state.enemies:
        parts["counters"] = _clamp(0.5 + delta / COUNTER_FULL_SCALE) if known else 0.5
        parts["known_enemies"] = float(known)   # informativo: no entra en la suma ponderada
        tone = "g" if delta >= 0.03 else "r" if delta <= -0.03 else ""
        chips.append((f"{delta:+.0%} vs enemigos" if known else "sin dato vs enemigos", tone))
        for e in state.enemies:
            if not is_lane_opponent(state.my_pos, state.enemy_pos.get(e)):
                continue
            vs = hero_vs_enemy(hero, e, data)
            if vs:
                lane_tone = "g" if vs[1] >= 0.53 else "r" if vs[1] <= 0.47 else ""
                chips.append((f"{vs[1] - 0.5:+.0%} vs {data.name(e)} (tu línea)", lane_tone))
    else:
        # First pick: sin rival a la vista, vale más el que menos counters tiene.
        exposure = counter_exposure(hero, data)
        if exposure:
            parts["safety"] = _clamp(1 - exposure[0] / EXPOSURE_MAX)
            exp_tone = "g" if exposure[0] <= 0.08 else "r" if exposure[0] >= 0.20 else "a"
            chips.append((f"First pick: {exposure[0]:.0%} del pool lo counterea", exp_tone))
        else:
            parts["safety"] = 0.4            # sin datos no se premia: leve malus
            if hero in data.matchups:
                chips.append(("First pick: pocos matchups con muestra", "a"))

    syn_delta, syn_known, syn_chips = _synergy_delta(hero, state, data)
    parts["synergy"] = _clamp(0.5 + syn_delta / SYNERGY_FULL_SCALE) if syn_known else 0.5
    chips.extend(syn_chips)

    score = 100 * sum(weights[k] * parts.get(k, 0.5) for k in weights)

    reasons = []
    if best_target is not None:
        reasons.append(f"counter de {data.name(best_target)}")
    if exposure:
        if exposure[0] <= 0.08:
            reasons.append("first pick seguro: casi nadie lo countera")
        elif exposure[0] >= 0.20:
            reasons.append("arriesgado de first: lo counterean " + ", ".join(data.name(c) for c in exposure[1]))
    if syn_known and syn_delta >= 0.03:
        reasons.append("buena sinergia con tu equipo")
    if wr >= 0.53 and popularity >= 0.5:
        reasons.append(f"top del meta en {where}")
    elif wr >= 0.53:
        reasons.append(f"gana {wr:.0%} en {where}")
    if not reasons:
        reasons.append("sólido en el meta" if parts["meta"] >= 0.6 else "neutro contra este draft")
    reason = "; ".join(reasons)
    return Recommendation(hero, data.name(hero), round(score, 1), parts, chips, reason[0].upper() + reason[1:])


def weights_for(state: DraftState) -> dict[str, float]:
    """Sin enemigos a la vista se recomienda un first pick."""
    return DEFAULT_WEIGHTS if state.enemies else FIRST_PICK_WEIGHTS


def plays_position(hero: int, pos: int | None, meta: MetaSnapshot, totals: dict[int, int] | None = None) -> bool:
    """
    False solo cuando hay stats por posición y el héroe casi no se juega ahí: menos de MIN_POS_SHARE
    de sus propias partidas o menos de MIN_POS_RATE de los picks de la posición (un héroe raro
    con 100 % de sus 50 partidas en pos 3 tampoco es un pick de pos 3).
    """
    if not pos or not meta.position_stats:
        return True
    fit = position_fit(hero, pos, meta)
    if fit is None:
        return True
    total = (totals or position_totals(meta)).get(pos, 0)
    return fit[0] >= MIN_POS_SHARE and (not total or fit[2] / total >= MIN_POS_RATE)


def has_enough_sample(r: Recommendation, state: DraftState, data: DraftData,
                      totals: dict[int, int] | None = None) -> bool:
    """
    Sin muestra no se recomienda. First pick: tabla propia ya bajada pero sin cobertura → fuera
    (la que todavía no llegó se muestra mientras tanto). Con enemigos y sus tablas ya bajadas:
    fuera el héroe que no aparece contra ninguno de ellos.
    """
    if not plays_position(r.hero_id, state.my_pos, data.meta, totals):
        return False
    if not state.enemies:
        return r.hero_id not in data.matchups or counter_exposure(r.hero_id, data) is not None
    if all(e in data.matchups for e in state.enemies):
        return r.parts.get("known_enemies", 0) > 0
    return True


def rank_all(state: DraftState, data: DraftData, weights: dict[str, float] | None = None) -> list[Recommendation]:
    """Toda la pool (sin los ya pickeados) puntuada, filtrada por muestra, ordenada y con tier."""
    weights = weights or weights_for(state)
    taken = state.taken()
    totals = position_totals(data.meta)
    recs = [score_hero(h, state, data, weights, totals) for h in data.hero_names if h not in taken]
    recs = [r for r in recs if has_enough_sample(r, state, data, totals)]
    recs.sort(key=lambda r: r.score, reverse=True)
    return assign_tiers(recs)


def recommend(state: DraftState, data: DraftData, weights: dict[str, float] | None = None,
              limit: int = 10) -> list[Recommendation]:
    return rank_all(state, data, weights)[:limit]


def meta_pool(pos: int, meta: MetaSnapshot) -> set[int]:
    """Los héroes "del meta" de la posición: ranking D2PT más los que más se juegan ahí en el bracket."""
    pool = set(meta.roles.get(f"pos {pos}", []))
    if meta.position_stats:
        at_pos = {h: position_fit(h, pos, meta) for h in meta.hero_names}
        played = [(f[2], h) for h, f in at_pos.items() if f and f[0] >= META_POOL_SHARE]
        pool |= {h for _, h in sorted(played, reverse=True)[:META_POOL_SIZE]}
    return pool


def meta_first_picks(state: DraftState, data: DraftData, limit: int = 5,
                     weights: dict[str, float] | None = None) -> list[Recommendation]:
    """
    Los héroes del meta de TU posición ordenados como first pick: meta y cuán countereables son.
    Ignora a los enemigos cargados a propósito: responde "¿qué firsteo?" antes del draft.
    """
    if not state.my_pos:
        return []
    pool = meta_pool(state.my_pos, data.meta) - state.taken()
    blind = DraftState(my_pos=state.my_pos, allies=state.allies)
    # El tier sale del ranking de TODA la pool a ciegas, no del puñado de héroes de la posición.
    ranked = rank_all(blind, data, weights or FIRST_PICK_WEIGHTS)
    return [r for r in ranked if r.hero_id in pool][:limit]


def best_answers(enemy: int, state: DraftState, data: DraftData, limit: int = 2) -> list[tuple[int, float]]:
    """Héroes de tu posición (no pickeados) que mejor le ganan a `enemy`, [(héroe, winrate)]."""
    taken = state.taken()
    pool = meta_pool(state.my_pos, data.meta) if state.my_pos else set(data.hero_names)
    found = []
    for hero in pool - taken:
        vs = hero_vs_enemy(hero, enemy, data)
        if vs and vs[0] >= POPULAR_MIN_GAMES and vs[1] >= 0.53:
            found.append((hero, vs[1]))
    found.sort(key=lambda t: -t[1])
    return found[:limit]


def draft_alerts(state: DraftState, data: DraftData, limit: int = 6,
                 recs: list[Recommendation] | None = None) -> list[tuple[str, str]]:
    """
    [(tono "warn"|"ok"|"info", texto)]: qué dicen las tablas del bracket sobre este draft.

    Con enemigos: la mejor respuesta de tu posición a cada uno, enemigos que counterean a tus
    aliados y tu rival de línea. Sin enemigos: qué candidatos son first picks seguros o arriesgados.
    Siempre: parejas de aliados con sinergia (o anti-sinergia) real.
    """
    warns: list[tuple[str, str]] = []
    goods: list[tuple[str, str]] = []

    if state.enemies:
        for e in state.enemies:
            answers = best_answers(e, state, data)
            if answers:
                txt = ", ".join(f"{data.name(h)} {wr:.0%}" for h, wr in answers)
                goods.append(("ok", f"Contra {data.name(e)}: {txt}."))
        lane = [e for e in state.enemies if is_lane_opponent(state.my_pos, state.enemy_pos.get(e))]
        if lane:
            goods.append(("info", "Tu línea: " + ", ".join(f"{data.name(e)} (pos {state.enemy_pos[e]})" for e in lane)
                          + ". Sus matchups pesan más."))
        for ally in state.allies:
            for e in state.enemies:
                vs = hero_vs_enemy(ally, e, data)
                if vs and vs[0] >= POPULAR_MIN_GAMES and vs[1] <= 0.46:
                    warns.append(("warn", f"{data.name(e)} countera a tu aliado {data.name(ally)} ({1 - vs[1]:.0%})."))
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
            syn = synergy_with(a, b, data)
            if syn and syn[1] >= 0.55:
                goods.append(("ok", f"{data.name(a)} + {data.name(b)}: ganan {syn[1]:.0%} juntos."))
            elif syn and syn[1] <= 0.45:
                warns.append(("warn", f"{data.name(a)} + {data.name(b)}: pierden {1 - syn[1]:.0%} juntos."))

    merged = warns + goods
    if not merged and state.enemies:
        merged.append(("info", "Todavía sin tablas de los enemigos (o sin matchups fuertes)."))
    return merged[:limit]
