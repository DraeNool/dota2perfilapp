r"""
Generación de autoexec.cfg para Dota 2 a partir de perfiles (plantillas) gráficos.

A diferencia del hero grid, autoexec.cfg vive en la carpeta del JUEGO INSTALADO:
    ...\steamapps\common\dota 2 beta\game\dota\cfg\autoexec.cfg
no en userdata\<id>\570.

Los perfiles son archivos .cfg en la carpeta `autoexec_profiles/` (junto al script o .exe),
editables por el usuario. Solo deben contener ajustes gráficos/rendimiento — NO binds.
"""

import logging
import re
from datetime import datetime
from pathlib import Path

from .paths import app_base_dir

log = logging.getLogger(__name__)

DOTA_RELATIVE_CFG = Path("steamapps") / "common" / "dota 2 beta" / "game" / "dota" / "cfg"

# Plantillas por defecto que se crean si la carpeta está vacía. Son un punto de partida:
# el usuario las reemplaza por las suyas. Solo gráficos/rendimiento, sin binds.
_DEFAULT_PROFILES: dict[str, str] = {
    "bajo": (
        "// === autoexec.cfg — perfil BAJO (equipos modestos) ===\n"
        "// Solo ajustes graficos/rendimiento. Sin binds.\n"
        'con_enable "1"\n'
        'fps_max "120"\n'
        'mat_vsync "0"\n'
        'engine_no_focus_sleep "0"\n'
        "\n// >>> Pega aqui tu configuracion grafica para equipos bajos <<<\n\n"
        'echo ">> autoexec.cfg (perfil BAJO) cargado <<"\n'
    ),
    "medio": (
        "// === autoexec.cfg — perfil MEDIO ===\n"
        "// Solo ajustes graficos/rendimiento. Sin binds.\n"
        'con_enable "1"\n'
        'fps_max "144"\n'
        'mat_vsync "0"\n'
        "\n// >>> Pega aqui tu configuracion grafica media <<<\n\n"
        'echo ">> autoexec.cfg (perfil MEDIO) cargado <<"\n'
    ),
    "alto": (
        "// === autoexec.cfg — perfil ALTO (equipos potentes) ===\n"
        "// Solo ajustes graficos/rendimiento. Sin binds.\n"
        'con_enable "1"\n'
        'fps_max "0"\n'
        'mat_vsync "0"\n'
        "\n// >>> Pega aqui tu configuracion grafica para equipos altos <<<\n\n"
        'echo ">> autoexec.cfg (perfil ALTO) cargado <<"\n'
    ),
}


def profiles_dir() -> Path:
    return app_base_dir() / "autoexec_profiles"


def ensure_default_profiles() -> Path:
    """Crea la carpeta de perfiles con plantillas por defecto si está vacía."""
    d = profiles_dir()
    d.mkdir(parents=True, exist_ok=True)
    if not any(d.glob("*.cfg")):
        for name, content in _DEFAULT_PROFILES.items():
            (d / f"{name}.cfg").write_text(content, encoding="utf-8")
        log.info("Perfiles autoexec por defecto creados en %s", d)
    return d


def list_profiles() -> list[Path]:
    """Lista los .cfg disponibles (ordenados)."""
    ensure_default_profiles()
    return sorted(profiles_dir().glob("*.cfg"), key=lambda p: p.stem.lower())


def find_dota_cfg_dir(steam_path: Path) -> Path | None:
    """
    Localiza ...\\dota 2 beta\\game\\dota\\cfg recorriendo las bibliotecas de Steam.

    Lee libraryfolders.vdf para soportar Dota instalado en otro disco.
    """
    candidates: list[Path] = [steam_path]

    vdf = steam_path / "steamapps" / "libraryfolders.vdf"
    if vdf.exists():
        try:
            text = vdf.read_text(encoding="utf-8", errors="ignore")
            for m in re.finditer(r'"path"\s+"([^"]+)"', text):
                candidates.append(Path(m.group(1).replace("\\\\", "\\")))
        except OSError as e:
            log.debug("No se pudo leer libraryfolders.vdf: %s", e)

    seen = set()
    for base in candidates:
        if base in seen:
            continue
        seen.add(base)
        cfg = base / DOTA_RELATIVE_CFG
        if cfg.parent.exists():  # existe .../dota/ → es una instalación válida
            return cfg
    return None


def generate(profile_file: Path, cfg_dir: Path) -> Path:
    """
    Escribe autoexec.cfg en cfg_dir copiando el contenido del perfil.

    Respalda el autoexec.cfg previo como autoexec_backup_<timestamp>.cfg.
    Devuelve la ruta del autoexec.cfg escrito.
    """
    cfg_dir.mkdir(parents=True, exist_ok=True)
    target = cfg_dir / "autoexec.cfg"

    if target.exists():
        backup = cfg_dir / f"autoexec_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.cfg"
        try:
            backup.write_bytes(target.read_bytes())
            log.info("Backup de autoexec.cfg en %s", backup)
        except OSError as e:
            log.warning("No se pudo respaldar autoexec.cfg previo: %s", e)

    content = profile_file.read_text(encoding="utf-8", errors="ignore")
    target.write_text(content, encoding="utf-8")
    return target
