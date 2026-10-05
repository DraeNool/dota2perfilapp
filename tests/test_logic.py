"""Tests de la lógica pura (sin red ni GUI)."""

import pytest

from dota_config_sync import autoexec, dota2protracker, opendota
from dota_config_sync.config import DEFAULTS, AppConfig
from dota_config_sync.steam import steam_id3_to_id64
from dota_config_sync.theme import medal_for_tier


# ── format_kda ────────────────────────────────────────────────────────────────
def test_format_kda_basic():
    assert opendota.format_kda(10, 5, 20) == "10/5/20 (6.00)"


def test_format_kda_zero_deaths_no_division_error():
    assert opendota.format_kda(3, 0, 1) == "3/0/1 (4.00)"


def test_format_kda_handles_none():
    assert opendota.format_kda(None, None, None) == "0/0/0 (0.00)"


# ── is_win ────────────────────────────────────────────────────────────────────
def test_is_win_radiant():
    assert opendota.is_win({"player_slot": 1, "radiant_win": True}) is True
    assert opendota.is_win({"player_slot": 1, "radiant_win": False}) is False


def test_is_win_dire():
    assert opendota.is_win({"player_slot": 130, "radiant_win": False}) is True
    assert opendota.is_win({"player_slot": 130, "radiant_win": True}) is False


def test_is_win_missing_data():
    assert opendota.is_win({"player_slot": None, "radiant_win": True}) is None
    assert opendota.is_win({"player_slot": 1, "radiant_win": None}) is None


# ── Favoritos por performance ─────────────────────────────────────────────────
def test_performance_score_prefers_volume_with_good_wr():
    high_volume = {"hero_id": 1, "games": 100, "win": 60}
    one_off = {"hero_id": 2, "games": 1, "win": 1}
    assert opendota.performance_score(high_volume) > opendota.performance_score(one_off)


def test_rank_favorites_by_performance_dedups_and_limits():
    rows = [
        {"hero_id": 1, "games": 50, "win": 30},
        {"hero_id": 2, "games": 40, "win": 25},
        {"hero_id": 1, "games": 50, "win": 30},  # duplicado
        {"hero_id": 3, "games": 0, "win": 0},     # sin partidas → descartado
    ]
    result = opendota.rank_favorites_by_performance(rows, limit=2)
    assert result == [1, 2]


def test_rank_favorites_by_performance_skips_invalid_ids():
    rows = [{"hero_id": None, "games": 10, "win": 5}, {"hero_id": "x", "games": 10, "win": 5}]
    assert opendota.rank_favorites_by_performance(rows, limit=5) == []


# ── Favoritos recientes ───────────────────────────────────────────────────────
def test_rank_favorites_recent_orders_by_recency():
    recent = [
        {"hero_id": 10, "start_time": 100},
        {"hero_id": 20, "start_time": 300},
        {"hero_id": 30, "start_time": 200},
    ]
    assert opendota.rank_favorites_recent(recent, limit=5) == [20, 30, 10]


def test_rank_favorites_recent_dedups():
    recent = [
        {"hero_id": 5, "start_time": 500},
        {"hero_id": 5, "start_time": 400},
        {"hero_id": 7, "start_time": 300},
    ]
    assert opendota.rank_favorites_recent(recent, limit=5) == [5, 7]


# ── SteamID ───────────────────────────────────────────────────────────────────
def test_steam_id3_to_id64():
    assert steam_id3_to_id64(168558988) == "76561198128824716"


# ── Medallas ──────────────────────────────────────────────────────────────────
def test_medal_for_tier_with_stars():
    label, _color, stars = medal_for_tier(55)  # Leyenda 5 estrellas
    assert label == "Leyenda"
    assert stars == 5


def test_medal_for_tier_none():
    label, _color, stars = medal_for_tier(None)
    assert label == "Sin rango"
    assert stars == 0


# ── Config ────────────────────────────────────────────────────────────────────
def test_config_defaults():
    cfg = AppConfig()
    assert cfg.favorites_limit == DEFAULTS["favorites_limit"]
    assert cfg.preferred_main_id64 == DEFAULTS["preferred_main_id64"]
    assert cfg.meta_meta["config_name"] == "Meta Meta"


def test_config_env_var_overrides_key(monkeypatch):
    monkeypatch.setenv("STEAM_API_KEY", "ENV_KEY")
    cfg = AppConfig({"steam_api_key": "FILE_KEY"})
    assert cfg.steam_api_key == "ENV_KEY"


def test_config_set_key_persists_in_data():
    cfg = AppConfig()
    cfg.set_steam_api_key("  abc123  ")
    assert cfg._data["steam_api_key"] == "abc123"


def test_config_ignores_unknown_keys():
    cfg = AppConfig({"unknown": 1, "favorites_limit": 99})
    assert cfg.favorites_limit == 99
    assert "unknown" not in cfg._data


# ── Autoexec ──────────────────────────────────────────────────────────────────
def _make_dota_install(base):
    cfg = base / "steamapps" / "common" / "dota 2 beta" / "game" / "dota" / "cfg"
    cfg.parent.mkdir(parents=True)
    return cfg


def test_find_dota_cfg_dir_same_library(tmp_path):
    expected = _make_dota_install(tmp_path)
    assert autoexec.find_dota_cfg_dir(tmp_path) == expected


def test_find_dota_cfg_dir_other_library_via_vdf(tmp_path):
    steam = tmp_path / "Steam"
    (steam / "steamapps").mkdir(parents=True)
    other = tmp_path / "D_Library"
    expected = _make_dota_install(other)
    (steam / "steamapps" / "libraryfolders.vdf").write_text(
        f'"libraryfolders"{{ "0" {{ "path" "{other.as_posix()}" }} }}', encoding="utf-8"
    )
    assert autoexec.find_dota_cfg_dir(steam) == expected


def test_find_dota_cfg_dir_not_installed(tmp_path):
    (tmp_path / "steamapps").mkdir()
    assert autoexec.find_dota_cfg_dir(tmp_path) is None


def test_safe_profile_name():
    assert autoexec.safe_profile_name("  draenool ") == "draenool"
    assert autoexec.safe_profile_name("mi perfil/alto!") == "mi_perfil_alto"
    assert autoexec.safe_profile_name("///") == "perfil"


def test_export_autoexec_preserves_bytes(tmp_path):
    src = tmp_path / "autoexec.cfg"
    src.write_bytes(b"\xef\xbb\xbf// con BOM\r\nfps_max \"0\"\r\n")
    out = autoexec.export_autoexec(src, tmp_path / "salida" / "autoexec_draenool.cfg")
    assert out.read_bytes() == src.read_bytes()


def test_import_profile_copies_into_profiles_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(autoexec, "app_base_dir", lambda: tmp_path)
    src = tmp_path / "autoexec_draenool.cfg"
    src.write_text('fps_max "0"\n', encoding="utf-8")
    dest = autoexec.import_profile(src, "draenool")
    assert dest == tmp_path / "autoexec_profiles" / "draenool.cfg"
    assert dest.read_text(encoding="utf-8") == 'fps_max "0"\n'
    assert dest in autoexec.list_profiles()


def test_installed_autoexec_requires_file(tmp_path):
    cfg = _make_dota_install(tmp_path)
    assert autoexec.installed_autoexec(tmp_path) is None
    cfg.mkdir()
    (cfg / "autoexec.cfg").write_text("x", encoding="utf-8")
    assert autoexec.installed_autoexec(tmp_path) == cfg / "autoexec.cfg"


def test_generate_backs_up_existing(tmp_path):
    profile = tmp_path / "alto.cfg"
    profile.write_text('fps_max "0"\n', encoding="utf-8")
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    (cfg_dir / "autoexec.cfg").write_text("// viejo\n", encoding="utf-8")

    out = autoexec.generate(profile, cfg_dir)
    assert out.read_text(encoding="utf-8") == 'fps_max "0"\n'
    backups = list(cfg_dir.glob("autoexec_backup_*.cfg"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "// viejo\n"


# ── picks (puntuación pura) ────────────────────────────────────────────────────
def _pick_data():
    from dota_config_sync import picks

    names = {1: "Anti-Mage", 2: "Axe", 3: "Bane", 12: "Phantom Lancer", 55: "Dark Seer", 93: "Slark"}
    stats = {
        2: {"7_pick": 10000, "7_win": 5300, "pub_pick": 100000, "pub_win": 50000},
        55: {"7_pick": 8000, "7_win": 4400, "pub_pick": 80000, "pub_win": 40000},
        93: {"7_pick": 9000, "7_win": 4300, "pub_pick": 90000, "pub_win": 45000},
        1: {"7_pick": 9000, "7_win": 4500},
        3: {"7_pick": 3000, "7_win": 1500},
    }
    # Tabla del ENEMIGO (PL, id 12): victorias de PL contra cada héroe.
    matchups = {
        12: {
            2: (2000, 800),      # Axe gana 60 % vs PL
            55: (2000, 900),     # Dark Seer 55 % vs PL
            93: (2000, 1100),    # Slark 45 % vs PL
            1: (2000, 1000),
        },
    }
    player = {
        55: {"hero_id": "55", "games": 31, "win": 20, "against_games": 10, "against_win": 3},
        93: {"hero_id": "93", "games": 14, "win": 6},
        12: {"hero_id": "12", "games": 2, "win": 1, "against_games": 12, "against_win": 4},
    }
    rel = picks.D2ptRelations(best_with={55: {3}}, best_against={2: {12}})
    return picks.PickData(
        hero_names=names, hero_stats=stats, matchups=matchups, player_heroes=player,
        roles={"pos 3": [2, 55, 93]}, relations=rel, bracket=7,
    )


def test_layout_tiles_groups_by_attribute_filters_and_wraps():
    from dota_config_sync.ui_picks import GAP, HEADER_H, PAD, TILE_H, TILE_W, layout_tiles

    catalog = {
        2: {"name": "Axe", "attr": "str"}, 155: {"name": "Largo", "attr": "str"},
        1: {"name": "Anti-Mage", "attr": "agi"}, 55: {"name": "Dark Seer", "attr": "int"},
        128: {"name": "Dark Willow", "attr": "all"},
    }
    items, total = layout_tiles(catalog, "", columns=1)
    headers = [it[1] for it in items if it[0] == "header"]
    tiles = [it for it in items if it[0] == "tile"]
    assert headers == ["FUERZA", "AGILIDAD", "INTELIGENCIA", "UNIVERSAL"]
    assert [t[1] for t in tiles] == [2, 155, 1, 55, 128]           # orden por nombre dentro de cada atributo
    axe, largo = tiles[0], tiles[1]
    assert axe[2] == PAD and axe[3] == PAD + HEADER_H
    assert largo[3] == axe[3] + TILE_H + GAP                      # columns=1: segunda fila
    assert total > largo[3] + TILE_H
    filtered, _ = layout_tiles(catalog, "dark", columns=3)
    assert [it[1] for it in filtered if it[0] == "tile"] == [55, 128]
    assert [it[1] for it in filtered if it[0] == "header"] == ["INTELIGENCIA", "UNIVERSAL"]
    two_cols, _ = layout_tiles(catalog, "", columns=2)
    largo_x = next(it[2] for it in two_cols if it[0] == "tile" and it[1] == 155)
    assert largo_x == PAD + TILE_W + GAP                           # misma fila, segunda columna


def test_bracket_from_rank_tier():
    from dota_config_sync.picks import bracket_from_rank_tier

    assert bracket_from_rank_tier(75) == 7
    assert bracket_from_rank_tier(None) is None
    assert bracket_from_rank_tier(80) == 8


def test_recommend_ranks_counter_and_own_hero_first_and_skips_taken():
    from dota_config_sync import picks

    state = picks.DraftState(my_pos=3, allies=[3], enemies=[12], bans=[1])
    recs = picks.recommend(state, _pick_data(), limit=5)
    order = [r.hero_id for r in recs]
    assert 1 not in order and 12 not in order and 3 not in order
    assert order[:2] in ([55, 2], [2, 55])            # Dark Seer (propio+sinergia) y Axe (counter) arriba
    assert order.index(93) > order.index(55)           # Slark pierde vs PL y tiene mal winrate propio
    dark_seer = next(r for r in recs if r.hero_id == 55)
    assert any("Best with Bane" in c for c, _ in dark_seer.chips)
    assert "counter de phantom lancer" in dark_seer.reason.lower()


def test_lane_opponent_weighs_more_in_counters():
    from dota_config_sync import picks

    data = _pick_data()
    data.matchups[12][1] = (2000, 1200)        # Anti-Mage pierde 40 % vs PL
    data.matchups[3] = {1: (2000, 800)}        # ...pero gana 60 % vs Bane
    base = picks.DraftState(my_pos=3, enemies=[12, 3])
    lane = picks.DraftState(my_pos=3, enemies=[12, 3], enemy_pos={12: 1})   # PL es el carry: tu línea
    assert picks.is_lane_opponent(3, 1) and not picks.is_lane_opponent(3, 2)
    d_base = picks.score_hero(1, base, data).parts["counters"]
    d_lane = picks.score_hero(1, lane, data).parts["counters"]
    assert d_lane < d_base                      # el mal matchup de línea pesa más
    assert any("(tu línea)" in c for c, _ in picks.score_hero(1, lane, data).chips)


def test_next_medal_thresholds():
    from dota_config_sync.performance import next_medal

    nm = next_medal(75, 5032)
    assert nm == {"target_tier": 81, "threshold": 5620, "missing_mmr": 588, "net_wins": 24}
    assert next_medal(73, 5300)["target_tier"] == 74
    assert next_medal(80, 6000) is None
    assert next_medal(None, 5000) is None


def test_insights_read_the_numbers():
    from dota_config_sync.performance import insights

    summary = {
        "total": 60,
        "windows": {20: (13, 7, 0.65), 50: (28, 22, 0.56), 100: (0, 0, None)},
        "streak": -3,
        "solo": (20, 30, 2 / 3), "party": (9, 20, 0.45),
        "periods": {"mañana": (10, 16, 0.625), "tarde": (5, 20, 0.25), "noche": (6, 10, 0.6)},
        "heroes": [{"hero_id": 55, "games": 19, "wins": 12, "wr": 12 / 19, "kda": 5.9, "trend": "↑"},
                   {"hero_id": 93, "games": 14, "wins": 6, "wr": 6 / 14, "kda": 2.1, "trend": "↓"}],
        "avg_minutes_win": 36.0, "avg_minutes_loss": 43.0,
    }
    found = insights(summary, {55: "Dark Seer", 93: "Slark"})
    text = " | ".join(t for _, t in found)
    assert "Vas en subida" in text
    assert "mejor solo" in text
    assert "mejor horario es la mañana" in text
    assert "Dark Seer 63% (19)" in text and "Slark 43% (14)" in text
    assert "3 derrotas seguidas" in text
    assert "derrotas duran 7 min más" in text


def test_meta_first_picks_only_role_heroes_minus_taken_sorted():
    from dota_config_sync import picks

    data = _pick_data()                                   # roles: pos 3 -> [2, 55, 93]
    state = picks.DraftState(my_pos=3, allies=[93], enemies=[12])
    recs = picks.meta_first_picks(state, data)
    assert [r.hero_id for r in recs] == [55, 2] or [r.hero_id for r in recs] == [2, 55]
    assert recs[0].score >= recs[1].score
    assert all("vs enemigos" not in c for r in recs for c, _ in r.chips)   # ignora el draft enemigo
    assert picks.meta_first_picks(picks.DraftState(my_pos=None), data) == []


def test_first_pick_weights_favor_meta_over_personal_history():
    from dota_config_sync import picks

    data = _pick_data()
    # Dos héroes de pos 3 con tablas iguales y sin counters: Axe es meta (53 %) y nunca jugado;
    # Slark es flojo en el meta (48 %) pero el usuario lo juega mucho y bien.
    data.hero_stats[93] = {"7_pick": 9000, "7_win": 4300}
    data.player_heroes[93] = {"hero_id": "93", "games": 60, "win": 39}
    neutral = dict.fromkeys(range(900, 925), (1000, 500))
    data.matchups[2] = dict(neutral)
    data.matchups[93] = dict(neutral)
    state = picks.DraftState(my_pos=3)
    assert picks.weights_for(state) is picks.FIRST_PICK_WEIGHTS
    assert picks.weights_for(picks.DraftState(my_pos=3, enemies=[12])) is picks.DEFAULT_WEIGHTS
    first = {r.hero_id: r.score for r in picks.meta_first_picks(state, data)}
    assert first[2] > first[93]                                   # manda el meta
    with_history = {r.hero_id: r.score for r in picks.recommend(state, data)}
    assert with_history[2] > with_history[93]
    # ...pero el historial sigue desempatando: el mismo Slark sin partidas puntúa menos.
    data.player_heroes.pop(93)
    assert picks.meta_first_picks(state, data)[-1].score < first[93]


def test_normalize_weights_merges_and_sums_to_one():
    from dota_config_sync import picks

    w = picks.normalize_weights({"personal": 0, "counters": "0.8", "bogus": 9, "meta": "x"}, picks.DEFAULT_WEIGHTS)
    assert set(w) == set(picks.DEFAULT_WEIGHTS)
    assert w["personal"] == 0 and abs(sum(w.values()) - 1) < 1e-9
    assert w["counters"] > w["meta"] > w["position"]
    assert picks.normalize_weights(None, picks.FIRST_PICK_WEIGHTS) == picks.FIRST_PICK_WEIGHTS
    cfg = AppConfig({"picks_weights": {"personal": 0.3}})
    assert cfg.picks_weights == {"personal": 0.3}
    assert AppConfig({"picks_weights": "nope"}).picks_weights == {}
    assert picks.DEFAULT_WEIGHTS["personal"] == 0.10                   # el historial solo desempata


def test_my_heroes_in_draft_are_strong_heroes_scored_against_draft():
    from dota_config_sync import picks

    data = _pick_data()                                   # historial: Dark Seer 31 pj, Slark 14 pj, PL 2 pj
    state = picks.DraftState(my_pos=3, enemies=[12])
    mine = picks.my_heroes_in_draft(state, data)
    ids = [r.hero_id for r in mine]
    assert ids == [55, 93]                                # PL tiene 2 pj (<5) y además es enemigo
    assert mine[0].score >= mine[1].score and all(r.tier for r in mine)
    assert any("vs enemigos" in c for c, _ in mine[0].chips)
    assert picks.my_heroes_in_draft(state, picks.PickData(hero_names=data.hero_names)) == []


def test_tier_for_percentile_with_absolute_cap():
    from dota_config_sync.picks import tier_for

    assert tier_for(80, 1, 127) == "S+"
    assert tier_for(80, 6, 127) == "S"          # 4.7 %
    assert tier_for(80, 12, 127) == "A+"        # 9.4 %
    assert tier_for(80, 25, 127) == "A"
    assert tier_for(80, 63, 127) == "B"         # 49.6 %
    assert tier_for(80, 127, 127) == "C"
    assert tier_for(58, 1, 127) == "A+"         # el mejor del draft, pero con 58 no es S
    assert tier_for(44, 1, 127) == "B+"
    assert tier_for(30, 1, 127) == "C+"
    assert tier_for(65, 1, 127) == "S"


def test_recommend_and_first_picks_carry_tiers():
    from dota_config_sync import picks

    data = _pick_data()
    state = picks.DraftState(my_pos=3, enemies=[12])
    recs = picks.recommend(state, data)
    assert all(r.tier for r in recs)
    assert recs[0].tier == picks.tier_for(recs[0].score, 1, len(picks.rank_all(state, data)))
    order = picks.TIER_ORDER
    assert all(order.index(a.tier) <= order.index(b.tier) for a, b in zip(recs, recs[1:], strict=False))
    firsts = picks.meta_first_picks(picks.DraftState(my_pos=3), data)
    assert firsts and all(f.tier for f in firsts)


def test_heroes_without_sample_are_hidden():
    from dota_config_sync import picks

    data = _pick_data()
    # Con enemigos y sus tablas bajadas: Bane (3) no aparece en la tabla de PL → fuera de la lista.
    ids = [r.hero_id for r in picks.recommend(picks.DraftState(my_pos=3, enemies=[12]), data)]
    assert 3 not in ids and 55 in ids
    # Tabla del enemigo todavía no bajada: nadie se oculta.
    data2 = _pick_data()
    data2.matchups = {}
    assert 3 in [r.hero_id for r in picks.recommend(picks.DraftState(my_pos=3, enemies=[12]), data2)]
    # First pick: tabla propia bajada pero sin cobertura → fuera; sin tabla todavía → se muestra.
    data3 = _pick_data()
    data3.matchups[55] = {1: (1000, 400)}          # Dark Seer: 1 matchup, cobertura insuficiente
    first_ids = [r.hero_id for r in picks.recommend(picks.DraftState(my_pos=3), data3)]
    assert 55 not in first_ids and 2 in first_ids
    assert 55 not in [r.hero_id for r in picks.meta_first_picks(picks.DraftState(my_pos=3), data3)]


def test_meta_winrate_falls_back_to_pub_when_bracket_missing():
    from dota_config_sync.picks import meta_winrate

    wr, picks_n = meta_winrate({"pub_pick": 1000, "pub_win": 600}, bracket=7)
    assert picks_n == 1000 and 0.55 < wr < 0.6
    assert meta_winrate(None, 7) == (0.5, 0)


def test_draft_alerts_warn_on_weak_matchup_and_history():
    from dota_config_sync import picks

    state = picks.DraftState(my_pos=3, allies=[55, 3], enemies=[12])
    alerts = picks.draft_alerts(state, _pick_data())
    texts = [t for _, t in alerts]
    assert any("castiga a tu Slark: pierde 55%" in t for t in texts)
    assert any("Vos perdés 67% cuando enfrentás a Phantom Lancer" in t for t in texts)
    assert any("countera a tu aliado Dark Seer" in t for t in texts) is False   # Dark Seer le gana 55 % a PL
    assert any("Dark Seer + Bane" in t for t in texts)


def test_first_pick_mode_uses_counter_exposure():
    from dota_config_sync import picks

    data = _pick_data()
    # Tabla propia de Axe: Anti-Mage le gana 60 % (9000 picks en el bracket), Bane 48 %, Dark Seer 50 %,
    # más 17 héroes de relleno al 50 % (peso 1) para superar la cobertura mínima.
    data.matchups[2] = {1: (1000, 400), 3: (1000, 520), 55: (1000, 500)}
    data.matchups[2].update({900 + i: (1000, 500) for i in range(17)})
    data.hero_stats[1]["7_pick"] = 9000
    data.hero_stats[3]["7_pick"] = 3000
    data.hero_stats[55]["7_pick"] = 8000
    exposure, worst = picks.counter_exposure(2, data)
    assert worst == [1]
    assert abs(exposure - 9000 / (9000 + 3000 + 8000 + 17)) < 1e-9   # ponderado por picks del bracket
    thin = picks.PickData(matchups={5: {1: (1000, 400), 3: (1000, 400)}}, hero_names={5: "x"})
    assert picks.counter_exposure(5, thin) is None                   # pocos matchups: no se opina
    state = picks.DraftState(my_pos=3)                                # sin enemigos: first pick
    axe = picks.score_hero(2, state, data)
    assert axe.parts["counters"] == 0.0                               # 45 % del pool lo countera → nada seguro
    assert any("First pick: 45%" in c for c, _ in axe.chips)
    assert "arriesgado de first" in axe.reason.lower() and "Anti-Mage" in axe.reason
    unknown = picks.score_hero(93, state, data)             # sin tabla propia: leve malus, no se premia
    assert unknown.parts["counters"] == 0.4
    recs = picks.recommend(state, data)
    alerts = picks.draft_alerts(state, data, recs=recs)
    assert any("Axe de first es arriesgado" in t for _, t in alerts)


def test_parse_d2pt_relations_groups_rows_by_y_position():
    from dota_config_sync import picks

    cfg = {"categories": [
        {"category_name": "Top Heroes Pos 1", "y_position": 0, "x_position": 0, "hero_ids": [11, 48]},
        {"category_name": "Best with", "y_position": 20, "x_position": 75, "hero_ids": [62, 90]},
        {"category_name": "Worst against", "y_position": 20, "x_position": 945, "hero_ids": [128]},
        {"category_name": "Best with", "y_position": 95, "x_position": 75, "hero_ids": [83]},
    ]}
    rel = picks.parse_d2pt_relations([cfg])
    assert rel.best_with == {11: {62, 90}, 48: {83}}
    assert rel.worst_against == {11: {128}}


# ── performance (resumen y MMR estimado, sin red) ──────────────────────────────
def _match(win, hero=55, rank=75, start=1_790_000_000, party=1, k=5, d=3, a=10):
    return {"player_slot": 1, "radiant_win": win, "hero_id": hero, "average_rank": rank,
            "start_time": start, "party_size": party, "kills": k, "deaths": d, "assists": a}


def test_mmr_from_rank_tier():
    from dota_config_sync.performance import mmr_from_rank_tier

    assert mmr_from_rank_tier(75) == 4620 + 4 * 200 + 100
    assert mmr_from_rank_tier(11) == 77
    assert mmr_from_rank_tier(80) == 5620
    assert mmr_from_rank_tier(None) is None


def test_estimate_mmr_series_anchors_and_results():
    from dota_config_sync.performance import estimate_mmr_series

    newest_first = [_match(True, rank=75), _match(False, rank=None), _match(True, rank=74)]
    series = estimate_mmr_series(newest_first)
    assert [m["average_rank"] for m, _ in series] == [74, None, 75]  # orden cronológico
    assert series[0][1] == 4620 + 3 * 200 + 100 + 25
    assert series[1][1] is not None  # ancla arrastrada de la partida anterior


def test_summarize_windows_streak_party_and_heroes():
    from dota_config_sync.performance import summarize

    ms = [_match(True), _match(True, party=3), _match(False, hero=93), _match(True, hero=93)]
    s = summarize(ms)
    assert s["total"] == 4
    assert s["windows"][20] == (3, 1, 0.75)
    assert s["streak"] == 2
    assert s["party"] == (1, 1, 1.0) and s["solo"] == (2, 3, 2 / 3)
    heroes = {h["hero_id"]: h for h in s["heroes"]}
    assert heroes[55]["games"] == 2 and heroes[55]["wr"] == 1.0
    assert heroes[93]["wr"] == 0.5 and heroes[93]["kda"] == 5.0


def test_record_snapshot_once_per_day(monkeypatch, tmp_path):
    from datetime import date

    from dota_config_sync import performance

    monkeypatch.setattr(performance, "progress_path", lambda: tmp_path / "progress.json")
    h = performance.record_snapshot("1", 74, date(2026, 9, 1))
    h = performance.record_snapshot("1", 75, date(2026, 9, 1))   # mismo día: reemplaza
    h = performance.record_snapshot("1", 75, date(2026, 9, 2))   # sin cambio: no agrega
    h = performance.record_snapshot("1", 76, date(2026, 9, 3))
    assert h == [{"date": "2026-09-01", "rank_tier": 75}, {"date": "2026-09-03", "rank_tier": 76}]
    assert performance.load_progress()["1"] == h


# ── dota2protracker (parseo del payload embebido, sin red) ─────────────────────
_D2PT_FIXTURE = (
    'garbage before... "data":{grids:{matches:{configs:'
    '[{categories:[{width:455,height:75,hero_ids:[11,48,12],x_position:0,'
    'y_position:0,category_name:"Carry"}],config_name:"Dota2ProTracker 7.41e - All Roles"}]'
    '},"otherField":{"nested":[1,2,3]}} ...trailing'
)


def test_parse_meta_hero_grids_html_extracts_configs():
    configs = dota2protracker.parse_meta_hero_grids_html(_D2PT_FIXTURE)
    assert len(configs) == 1
    assert configs[0]["config_name"] == "Dota2ProTracker 7.41e - All Roles"
    cat = configs[0]["categories"][0]
    assert cat["category_name"] == "Carry"
    assert cat["hero_ids"] == [11, 48, 12]


def test_parse_meta_hero_grids_html_missing_marker_raises():
    with pytest.raises(ValueError):
        dota2protracker.parse_meta_hero_grids_html("<html>no payload here</html>")


def test_parse_meta_hero_grids_html_unbalanced_brackets_raises():
    broken = _D2PT_FIXTURE.split("],config_name")[0]  # corta el array a la mitad
    with pytest.raises(ValueError):
        dota2protracker.parse_meta_hero_grids_html(broken)


_D2PT_ROLES_FIXTURE = (
    'garbage... patch:{version:"7.41e",released_at:1785457681,period:"8"},'
    'roles:[{position:"pos 1",roleName:"Carry",icon:"/x.svg",'
    'heroes:[{hero_id:21,hero_name:"Windranger",win_rate:.527},'
    '{hero_id:8,hero_name:"Juggernaut",win_rate:-.518}],hasMore:false},'
    '{position:"pos 4",roleName:"Support",icon:"/y.svg",'
    'heroes:[{hero_id:62,hero_name:"Bounty Hunter",win_rate:.564}],hasMore:false}'
    '] ...trailing'
)


def test_parse_meta_roles_html_extracts_positions_and_fixes_bare_decimals():
    roles = dota2protracker.parse_meta_roles_html(_D2PT_ROLES_FIXTURE)
    assert roles == {"pos 1": [21, 8], "pos 4": [62]}


def test_parse_meta_roles_html_missing_marker_raises():
    with pytest.raises(ValueError):
        dota2protracker.parse_meta_roles_html("<html>no roles here</html>")


def test_parse_patch_version():
    assert dota2protracker.parse_patch_version(_D2PT_ROLES_FIXTURE) == "7.41e"
    assert dota2protracker.parse_patch_version("<html>sin parche</html>") is None


def test_cached_meta_status_reads_patch_and_age(monkeypatch, tmp_path):
    from dota_config_sync import cache

    monkeypatch.setattr(cache, "cache_dir", lambda: tmp_path)
    assert dota2protracker.cached_meta_status() == (None, None)
    cache.set(dota2protracker.HOME_URL, {"roles": {"pos 1": [1]}, "patch": "7.41f"})
    patch, age = dota2protracker.cached_meta_status()
    assert patch == "7.41f"
    assert age is not None and 0 <= age < 5


def test_resolve_hero_names_accepts_names_ids_and_reports_unknown(monkeypatch):
    monkeypatch.setattr(opendota, "get_hero_map", lambda: {55: "Dark Seer", 155: "Largo", 14: "Pudge"})
    ids, unknown = opendota.resolve_hero_names("dark seer, Largo; 14, Pudge, Nadie, 999")
    assert ids == [55, 155, 14]
    assert unknown == ["Nadie", "999"]


def test_config_comfort_roundtrip_keeps_layout_and_other_categories():
    cfg = AppConfig()
    before = {c["category_name"]: dict(c) for c in cfg.meta_meta["categories"]}
    cfg.set_comfort_hero_ids([1, 2, 3])
    assert cfg.comfort_hero_ids == [1, 2, 3]
    after = {c["category_name"]: c for c in cfg.meta_meta["categories"]}
    assert after["COMFORT"]["x_position"] == before["COMFORT"]["x_position"]
    assert after["CARRY / POS 1"]["hero_ids"] == before["CARRY / POS 1"]["hero_ids"]
    from dota_config_sync.config import DEFAULT_META_META

    assert 1 not in next(c for c in DEFAULT_META_META["categories"] if c["category_name"] == "COMFORT")["hero_ids"]


def test_age_text_buckets():
    from dota_config_sync.app import _age_text

    assert _age_text(5) == "hace un momento"
    assert _age_text(150) == "hace 2 min"
    assert _age_text(7200) == "hace 2 h"
    assert _age_text(3 * 86400) == "hace 3 día(s)"


def test_apply_role_heroes_appends_patch_to_config_name():
    meta_meta = {"config_name": "Meta Meta", "categories": []}
    assert dota2protracker.apply_role_heroes(meta_meta, {}, "7.41f")["config_name"] == "Meta Meta 7.41f"
    assert dota2protracker.apply_role_heroes(meta_meta, {}, None)["config_name"] == "Meta Meta"


def test_apply_role_heroes_replaces_pos_categories_keeps_comfort_and_layout():
    meta_meta = {
        "config_name": "Meta Meta",
        "categories": [
            {"category_name": "CARRY / POS 1", "x_position": 1.0, "y_position": 2.0,
             "width": 3.0, "height": 4.0, "hero_ids": [999]},
            {"category_name": "COMFORT", "x_position": 5.0, "y_position": 6.0,
             "width": 7.0, "height": 8.0, "hero_ids": [111, 222]},
        ],
    }
    role_heroes = {"pos 1": [21, 8, 67]}

    updated = dota2protracker.apply_role_heroes(meta_meta, role_heroes)

    carry, comfort = updated["categories"]
    assert carry["hero_ids"] == [21, 8, 67]
    assert carry["x_position"] == 1.0 and carry["width"] == 3.0  # layout intacto
    assert comfort["hero_ids"] == [111, 222]  # COMFORT nunca se toca
    # el original no se muta
    assert meta_meta["categories"][0]["hero_ids"] == [999]
