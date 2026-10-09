"""Asistente de picks (draft.py + meta.py): puntuación pura con stats del bracket, sin historial del jugador."""

from dota_config_sync import draft, meta


def _pos(games, wins, k=5.0, d=5.0, a=10.0):
    return {"games": games, "wins": wins, "kills": k, "deaths": d, "assists": a}


def _snapshot() -> meta.MetaSnapshot:
    names = {1: "Anti-Mage", 2: "Axe", 3: "Bane", 12: "Phantom Lancer", 55: "Dark Seer", 93: "Slark", 86: "Rubick"}
    position_stats = {
        2: {3: _pos(12000, 6480), 1: _pos(300, 140)},            # Axe: offlaner, 54 %
        55: {3: _pos(9000, 4770), 2: _pos(400, 200)},            # Dark Seer: offlaner, 53 %
        93: {1: _pos(10000, 5000), 3: _pos(40, 20)},             # Slark: carry; casi nunca pos 3
        1: {1: _pos(9000, 4500)},
        12: {1: _pos(8000, 4080)},
        3: {5: _pos(3000, 1500), 3: _pos(900, 400)},             # Bane: support, a veces pos 3 (44 %)
        86: {4: _pos(7000, 3500)},
    }
    snap = meta.MetaSnapshot(patch="7.41f", hero_names=names, position_stats=position_stats,
                             roles={"pos 3": [2, 55, 93]}, source="stratz")
    snap.overall = meta.overall_from_positions(position_stats)
    return snap


def _data() -> draft.DraftData:
    # Tabla del ENEMIGO (PL, id 12): victorias de PL contra cada héroe.
    matchups = {12: {2: (2000, 800), 55: (2000, 900), 93: (2000, 1100), 1: (2000, 1000), 3: (2000, 1000)}}
    return draft.DraftData(meta=_snapshot(), matchups=matchups)


def test_overall_from_positions_and_opendota():
    assert meta.overall_from_positions({2: {3: _pos(100, 60), 1: _pos(10, 4)}}) == {2: (110, 64)}
    rows = {2: {"7_pick": 100, "7_win": 50, "8_pick": 50, "8_win": 30}, 3: {"7_pick": 0}}
    assert meta.overall_from_opendota(rows) == {2: (150, 80)}


def test_meta_rate_uses_position_stats_or_overall():
    snap = _snapshot()
    wr, games, rate = draft.meta_rate(2, 3, snap)
    assert games == 12000 and 0.535 < wr < 0.545
    assert rate == 12000 / (12000 + 9000 + 40 + 900)             # cuota de los picks de pos 3
    assert draft.meta_rate(86, 3, snap) == (0.5, 0, 0.0)          # Rubick nunca de pos 3
    wr_all, games_all, rate_all = draft.meta_rate(2, None, snap)
    assert games_all == 12300 and rate_all > 0
    snap.position_stats = {}
    assert draft.meta_rate(2, 3, snap) == (wr_all, games_all, rate_all)   # sin Stratz: general


def test_position_fit_and_plays_position():
    snap = _snapshot()
    share, wr, games = draft.position_fit(55, 3, snap)
    assert share > 0.95 and games == 9000 and wr > 0.52
    assert draft.position_fit(93, 3, snap)[0] < draft.MIN_POS_SHARE
    assert draft.position_fit(99, 3, snap) is None
    assert draft.plays_position(55, 3, snap) and not draft.plays_position(93, 3, snap)
    assert draft.plays_position(99, 3, snap)                      # sin datos no se excluye
    assert draft.plays_position(93, None, snap)
    snap.position_stats[99] = {3: _pos(50, 30)}                   # 100 % de SUS partidas, 0,2 % de los picks
    assert not draft.plays_position(99, 3, snap)
    assert 99 not in [r.hero_id for r in draft.recommend(draft.DraftState(my_pos=3), draft.DraftData(meta=snap))]


def test_recommend_ranks_counters_and_hides_heroes_off_position():
    data = _data()
    state = draft.DraftState(my_pos=3, allies=[86], enemies=[12], bans=[1])
    recs = draft.recommend(state, data, limit=5)
    order = [r.hero_id for r in recs]
    assert 1 not in order and 12 not in order and 86 not in order
    assert 93 not in order                                        # Slark no se juega de pos 3
    assert order[0] == 2                                          # Axe: 60 % vs PL y top del meta pos 3
    assert order.index(55) < order.index(3)                       # Dark Seer 55 % vs PL > Bane 50 %
    axe = recs[0]
    assert "counter de phantom lancer" in axe.reason.lower()
    assert axe.parts["counters"] > 0.8 and axe.parts["known_enemies"] == 1
    assert any(c.startswith("Meta pos 3") for c, _ in axe.chips)
    assert "personal" not in axe.parts and not any("Vos" in c for c, _ in axe.chips)


def test_lane_opponent_weighs_more_in_counters():
    data = _data()
    data.matchups[12][1] = (2000, 1200)        # Anti-Mage pierde 40 % vs PL
    data.matchups[3] = {1: (2000, 800)}        # ...pero gana 60 % vs Bane
    data.meta.position_stats[1][3] = _pos(2000, 1000)             # para que AM "juegue" pos 3
    base = draft.DraftState(my_pos=3, enemies=[12, 3])
    lane = draft.DraftState(my_pos=3, enemies=[12, 3], enemy_pos={12: 1})   # PL es el carry: tu línea
    assert draft.is_lane_opponent(3, 1) and not draft.is_lane_opponent(3, 2)
    d_base = draft.score_hero(1, base, data).parts["counters"]
    d_lane = draft.score_hero(1, lane, data).parts["counters"]
    assert d_lane < d_base
    assert any("(tu línea)" in c for c, _ in draft.score_hero(1, lane, data).chips)


def test_synergy_moves_score_only_with_sample():
    data = _data()
    state = draft.DraftState(my_pos=3, allies=[3], enemies=[12])
    base = draft.score_hero(55, state, data)
    assert base.parts["synergy"] == 0.5                           # sin tabla "with": neutro
    data.synergy = {3: {55: (500, 300)}}                          # Bane con Dark Seer: 60 %
    with_ally = draft.score_hero(55, state, data)
    assert with_ally.score > base.score and with_ally.parts["synergy"] > 0.9
    assert any("con Bane" in c for c, _ in with_ally.chips)
    data.synergy = {3: {55: (10, 9)}}                             # 10 partidas: no opina
    assert draft.score_hero(55, state, data).parts["synergy"] == 0.5


def test_first_pick_mode_uses_counter_exposure():
    data = _data()
    pool = dict.fromkeys(range(100, 125), (1000, 400))            # Axe pierde 40 % vs 25 héroes raros...
    pool.update(dict.fromkeys(range(200, 225), (1000, 650)))      # ...y gana 65 % vs otros 25
    data.matchups[2] = pool
    data.meta.overall.update(dict.fromkeys(range(100, 125), (50, 25)))   # los counters casi no se pickean
    data.meta.overall.update(dict.fromkeys(range(200, 225), (5000, 2500)))
    exposure, worst = draft.counter_exposure(2, data)
    assert exposure < 0.05 and len(worst) == 3 and all(100 <= w < 125 for w in worst)
    thin = draft.DraftData(meta=_snapshot(), matchups={55: {1: (1000, 400), 3: (1000, 400)}})
    assert draft.counter_exposure(55, thin) is None               # pocos matchups: no se opina
    state = draft.DraftState(my_pos=3)
    axe = draft.score_hero(2, state, data)
    assert "safety" in axe.parts and axe.parts["safety"] > 0.8 and "counters" not in axe.parts
    assert any(c.startswith("First pick:") for c, _ in axe.chips)
    assert draft.score_hero(55, state, data).parts["safety"] == 0.4   # sin tabla propia: leve malus
    assert draft.weights_for(state) is draft.FIRST_PICK_WEIGHTS
    assert draft.weights_for(draft.DraftState(enemies=[12])) is draft.DEFAULT_WEIGHTS


def test_heroes_without_sample_are_hidden():
    data = _data()
    ids = [r.hero_id for r in draft.recommend(draft.DraftState(my_pos=3, enemies=[12]), data)]
    assert 3 in ids                                               # Bane figura en la tabla de PL
    data.matchups[12].pop(3)
    ids = [r.hero_id for r in draft.recommend(draft.DraftState(my_pos=3, enemies=[12]), data)]
    assert 3 not in ids                                           # tabla bajada y no aparece: sin muestra
    data.matchups = {}
    ids = [r.hero_id for r in draft.recommend(draft.DraftState(my_pos=3, enemies=[12]), data)]
    assert 3 in ids                                               # tabla todavía no llegó: se muestra
    data.matchups = {55: {1: (1000, 400)}}                        # first pick: tabla propia sin cobertura
    assert 55 not in [r.hero_id for r in draft.recommend(draft.DraftState(my_pos=3), data)]
    assert 2 in [r.hero_id for r in draft.recommend(draft.DraftState(my_pos=3), data)]
    data.matchups = {12: {}, 55: {}}                              # tablas vacías = no se pudieron bajar
    assert 3 in [r.hero_id for r in draft.recommend(draft.DraftState(my_pos=3, enemies=[12]), data)]
    assert 55 in [r.hero_id for r in draft.recommend(draft.DraftState(my_pos=3), data)]


def test_fetch_tables_retries_stratz_without_cache_and_skips_opendota(monkeypatch):
    calls = []

    def fake(hero_id, bracket, token, ttl):
        calls.append(ttl)
        return ({2: (100, 50)}, {}) if ttl == 0 else ({}, {})

    monkeypatch.setattr(meta.stratz, "fetch_hero_matchups", fake)
    monkeypatch.setattr(meta, "fetch_matchups_opendota", lambda *_a: (_ for _ in ()).throw(AssertionError("OpenDota")))

    class Cfg:
        stratz_api_token = "t"
        opendota_stats_ttl_seconds = 86400

    assert meta.fetch_tables(12, Cfg()) == ({2: (100, 50)}, {})
    assert calls == [86400, 0]                                    # caché primero, luego sin caché
    Cfg.stratz_api_token = ""
    monkeypatch.setattr(meta, "fetch_matchups_opendota", lambda h, ttl: {h: (1, 1)})
    assert meta.fetch_tables(12, Cfg()) == ({12: (1, 1)}, {})


def test_meta_first_picks_only_pool_heroes_minus_taken_sorted():
    data = _data()
    assert draft.meta_pool(3, data.meta) == {2, 55, 93, 3}        # D2PT + los que se juegan ahí (≥15 %)
    state = draft.DraftState(my_pos=3, allies=[93], enemies=[12])
    firsts = draft.meta_first_picks(state, data)
    ids = [r.hero_id for r in firsts]
    assert 93 not in ids and 12 not in ids and set(ids) <= {2, 55, 3}
    assert ids[0] in (2, 55) and ids[-1] == 3                      # Bane: 44 % de pos 3, al fondo
    assert all("counters" not in r.parts for r in firsts)         # a ciegas: ignora a los enemigos
    assert draft.meta_first_picks(draft.DraftState(my_pos=None), data) == []


def test_tier_for_percentile_with_absolute_cap():
    assert draft.tier_for(80, 1, 100) == "S+"
    assert draft.tier_for(80, 10, 100) == "A+"
    assert draft.tier_for(80, 100, 100) == "C"
    assert draft.tier_for(65, 1, 100) == "S"                      # top 2 % pero puntaje bajo: techo S
    assert draft.tier_for(55, 1, 100) == "A+"
    assert draft.tier_for(45, 1, 100) == "B+"
    assert draft.tier_for(30, 1, 100) == "C+"
    recs = draft.recommend(draft.DraftState(my_pos=3, enemies=[12]), _data())
    assert all(r.tier in draft.TIER_ORDER for r in recs)
    assert draft.TIER_ORDER.index(recs[0].tier) <= draft.TIER_ORDER.index(recs[-1].tier)


def test_normalize_weights_merges_and_sums_to_one():
    w = draft.normalize_weights({"personal": 0.5, "counters": "0.8", "bogus": 9, "meta": "x"}, draft.DEFAULT_WEIGHTS)
    assert set(w) == set(draft.DEFAULT_WEIGHTS) and "personal" not in w
    assert abs(sum(w.values()) - 1) < 1e-9 and w["counters"] > w["meta"]
    assert draft.normalize_weights(None, draft.FIRST_PICK_WEIGHTS) == draft.FIRST_PICK_WEIGHTS
    assert "personal" not in draft.DEFAULT_WEIGHTS and "personal" not in draft.FIRST_PICK_WEIGHTS


def test_draft_alerts_are_stats_only():
    data = _data()
    data.matchups[12][86] = (2000, 1150)                          # PL le gana 57,5 % a Rubick
    state = draft.DraftState(my_pos=3, allies=[86, 3], enemies=[12], enemy_pos={12: 1})
    alerts = draft.draft_alerts(state, data)
    text = " | ".join(t for _, t in alerts)
    assert "Contra Phantom Lancer: Axe 60%, Dark Seer 55%" in text
    assert "countera a tu aliado Rubick" in text
    assert "Tu línea: Phantom Lancer (pos 1)." in text
    assert "Vos" not in text and "tu " not in text.replace("tu aliado", "").replace("Tu línea", "")
    data.synergy = {86: {3: (400, 160)}}                          # Rubick + Bane: 40 % juntos
    assert any("pierden" in t for _, t in draft.draft_alerts(state, data))
    first = draft.draft_alerts(draft.DraftState(my_pos=3), data, recs=draft.recommend(draft.DraftState(my_pos=3), data))
    assert first and first[0][0] == "info"                        # sin tablas propias: "cargando"
