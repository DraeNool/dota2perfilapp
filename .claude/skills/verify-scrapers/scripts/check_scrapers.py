"""
Verifica contra el sitio real que los parsers de dota2protracker sigan andando.

Descarga en vivo (salteando la caché en disco, que puede tener una respuesta
buena de hace horas) y corre las funciones puras de parseo. Sale con código 1
si alguna fuente se rompió, para poder usarlo en CI.
"""

import sys
from pathlib import Path


def _project_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").exists():
            return parent
    raise SystemExit("No se encontró la raíz del proyecto (pyproject.toml)")


sys.path.insert(0, str(_project_root()))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dota_config_sync import dota2protracker as d2pt  # noqa: E402
from dota_config_sync import http  # noqa: E402


def _summarize_roles(roles: dict) -> list[str]:
    return [f"{pos}: {len(ids)} héroes -> {ids}" for pos, ids in sorted(roles.items())]


def _summarize_grids(configs: list) -> list[str]:
    return [
        f"{c.get('config_name', '?')}: {len(c.get('categories', []))} categorías"
        for c in configs
    ]


CHECKS = [
    ("Meta por posición (alimenta la grilla Meta Meta)", d2pt.HOME_URL,
     d2pt.parse_meta_roles_html, _summarize_roles, "_ROLES_MARKER"),
    ("Hero grids publicados por D2PT", d2pt.GRIDS_URL,
     d2pt.parse_meta_hero_grids_html, _summarize_grids, "_GRIDS_MARKER"),
]


def main() -> int:
    failures = 0
    for name, url, parse, summarize, marker in CHECKS:
        print(f"\n=== {name} ===\n{url}")
        try:
            status, html = http.get_text(url, timeout=30)
        except http.requests.RequestException as e:
            print(f"  FALLO DE RED: {e}")
            failures += 1
            continue

        if status != 200 or not html:
            print(f"  FALLO: HTTP {status}")
            failures += 1
            continue

        marker_value = getattr(d2pt, marker)
        print(f"  HTTP 200, {len(html):,} bytes")
        print(f"  marcador {marker} = {marker_value!r}: "
              f"{'PRESENTE' if marker_value in html else 'AUSENTE'}")

        try:
            parsed = parse(html)
        except ValueError as e:
            print(f"  PARSER ROTO: {e}")
            failures += 1
            continue

        print("  OK")
        if marker == "_ROLES_MARKER":
            print(f"    parche: {d2pt.parse_patch_version(html) or 'NO DETECTADO'}")
        for line in summarize(parsed):
            print(f"    {line}")

    print("\n" + ("TODO OK" if not failures else f"{failures} fuente(s) rota(s)"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
