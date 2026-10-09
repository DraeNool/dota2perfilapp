"""
Cliente de Stratz (GraphQL, bearer token): matchups por bracket, stats por posición y partidas con posición.

Solo se usa si hay token (config.stratz_api_token o STRATZ_API_TOKEN); sin él la app sigue con OpenDota.
Las respuestas se cachean en disco por consulta. Los parsers son puros (testeables sin red).
"""

import hashlib
import json
import logging

from . import cache, http

log = logging.getLogger(__name__)

URL = "https://api.stratz.com/graphql"
_LIMITER = http.RateLimiter(min_interval=0.3)   # Stratz: 250 req/min; sobra margen

# rank_tier // 10 → bracket "básico" de Stratz
BRACKET_ENUM = {1: "HERALD_GUARDIAN", 2: "HERALD_GUARDIAN", 3: "CRUSADER_ARCHON", 4: "CRUSADER_ARCHON",
                5: "LEGEND_ANCIENT", 6: "LEGEND_ANCIENT", 7: "DIVINE_IMMORTAL", 8: "DIVINE_IMMORTAL"}
POSITION_NUM = {f"POSITION_{i}": i for i in range(1, 6)}

Q_MATCHUPS = """
query($h: Short!, $b: [RankBracketBasicEnum]) {
  heroStats { heroVsHeroMatchup(heroId: $h, bracketBasicIds: $b) {
    advantage { heroId
      vs   { heroId2 winsAverage matchCount }
      with { heroId2 winsAverage matchCount } } } } }"""

Q_POSITION_STATS = """
query($b: [RankBracketBasicEnum]) {
  heroStats { stats(bracketBasicIds: $b, groupByPosition: true) { heroId position matchCount winCount } } }"""

PAGE = 100   # Stratz: take máximo por consulta

Q_PLAYER_MATCHES = """
query($id: Long!, $take: Int!, $skip: Int!) {
  player(steamAccountId: $id) {
    steamAccount { seasonRank }
    matches(request: { take: $take, skip: $skip, lobbyTypeIds: [7] }) {
      id didRadiantWin durationSeconds startDateTime averageRank rank
      players(steamAccountId: $id) {
        heroId isVictory position lane kills deaths assists goldPerMinute experiencePerMinute imp } } } }"""


def bracket_enum(bracket: int | None) -> str:
    return BRACKET_ENUM.get(bracket or 0, "ALL")


def query(gql: str, variables: dict, token: str, ttl: int) -> dict | None:
    """POST GraphQL con caché en disco por (consulta, variables). None si falla o trae errores."""
    key = "stratz:" + hashlib.sha1((gql + json.dumps(variables, sort_keys=True)).encode()).hexdigest()
    cached = cache.get(key, ttl)
    if cached is not None:
        return cached
    _LIMITER.wait()
    try:
        r = http.SESSION.post(
            URL, json={"query": gql, "variables": variables}, timeout=30,
            headers={"Authorization": f"Bearer {token}", "User-Agent": "STRATZ_API"},
        )
        body = r.json() if r.status_code == 200 else None
    except (http.requests.RequestException, ValueError) as e:
        log.warning("Stratz falló: %s", e)
        return None
    if not body or body.get("errors") or not body.get("data"):
        log.warning("Stratz respondió %s: %s", r.status_code, str(body)[:200])
        return None
    cache.set(key, body["data"])
    return body["data"]


# ── Parsers puros ────────────────────────────────────────────────────────────
def _table(rows: list[dict]) -> dict[int, tuple[int, int]]:
    out: dict[int, tuple[int, int]] = {}
    for row in rows or []:
        try:
            other, games = int(row["heroId2"]), int(row.get("matchCount") or 0)
            wins = round(games * float(row.get("winsAverage") or 0))
        except (KeyError, TypeError, ValueError):
            continue
        if games > 0:
            out[other] = (games, wins)
    return out


def parse_matchups(data: dict) -> tuple[dict[int, tuple[int, int]], dict[int, tuple[int, int]]]:
    """(vs: {rival: (partidas, victorias del héroe)}, with: {aliado: (partidas, victorias juntos)})."""
    adv = (((data or {}).get("heroStats") or {}).get("heroVsHeroMatchup") or {}).get("advantage") or []
    if not adv:
        return {}, {}
    return _table(adv[0].get("vs")), _table(adv[0].get("with"))


def parse_position_stats(data: dict) -> dict[int, dict[int, tuple[int, int]]]:
    """{hero_id: {posición 1..5: (partidas, victorias)}} en el bracket pedido."""
    out: dict[int, dict[int, tuple[int, int]]] = {}
    for row in ((data or {}).get("heroStats") or {}).get("stats") or []:
        pos = POSITION_NUM.get(str(row.get("position")))
        try:
            hid, games, wins = int(row["heroId"]), int(row.get("matchCount") or 0), int(row.get("winCount") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if pos and games > 0:
            out.setdefault(hid, {})[pos] = (games, wins)
    return out


def parse_player_matches(data: dict) -> tuple[list[dict], int | None]:
    """
    Partidas normalizadas a la forma que usa performance.summarize (más posición, línea, GPM, XPM, IMP),
    de la más reciente a la más vieja, y el rank_tier de temporada del jugador.
    """
    player = (data or {}).get("player") or {}
    rank_tier = (player.get("steamAccount") or {}).get("seasonRank")
    out = []
    for m in player.get("matches") or []:
        me = (m.get("players") or [{}])[0]
        if not me:
            continue
        avg = m.get("averageRank")
        out.append({
            "match_id": m.get("id"), "hero_id": me.get("heroId"),
            "radiant_win": bool(me.get("isVictory")), "player_slot": 0,   # is_win() → isVictory
            "start_time": int(m.get("startDateTime") or 0), "duration": int(m.get("durationSeconds") or 0),
            "kills": int(me.get("kills") or 0), "deaths": int(me.get("deaths") or 0),
            "assists": int(me.get("assists") or 0),
            "average_rank": int(avg) if isinstance(avg, int) and 10 <= avg <= 85 else None,
            "party_size": None, "lobby_type": 7,
            "position": POSITION_NUM.get(str(me.get("position"))), "lane": me.get("lane"),
            "gpm": me.get("goldPerMinute"), "xpm": me.get("experiencePerMinute"), "imp": me.get("imp"),
        })
    out.sort(key=lambda m: m["start_time"], reverse=True)
    return out, int(rank_tier) if rank_tier else None


# ── Fetchers ─────────────────────────────────────────────────────────────────
def fetch_hero_matchups(hero_id: int, bracket: int | None, token: str, ttl: int):
    data = query(Q_MATCHUPS, {"h": hero_id, "b": [bracket_enum(bracket)]}, token, ttl)
    return parse_matchups(data) if data else ({}, {})


def fetch_position_stats(bracket: int | None, token: str, ttl: int) -> dict[int, dict[int, tuple[int, int]]]:
    data = query(Q_POSITION_STATS, {"b": [bracket_enum(bracket)]}, token, ttl)
    return parse_position_stats(data) if data else {}


def fetch_player_matches(steam_id3: str, take: int, token: str, ttl: int) -> tuple[list[dict], int | None, str]:
    """Pagina de a PAGE (tope de Stratz) hasta `take` partidas ranked, más recientes primero."""
    matches: list[dict] = []
    rank_tier: int | None = None
    for skip in range(0, take, PAGE):
        data = query(Q_PLAYER_MATCHES, {"id": int(steam_id3), "take": min(PAGE, take - skip), "skip": skip},
                     token, ttl)
        if not data:
            if not matches:
                return [], None, "Stratz no respondió (¿token vencido?)"
            break
        page, tier = parse_player_matches(data)
        rank_tier = rank_tier or tier
        matches.extend(page)
        if len(page) < min(PAGE, take - skip):
            break
    matches.sort(key=lambda m: m["start_time"], reverse=True)
    return matches, rank_tier, f"{len(matches)} ranked (Stratz, con posición)"
