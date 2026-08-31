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
    'garbage... roles:[{position:"pos 1",roleName:"Carry",icon:"/x.svg",'
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
