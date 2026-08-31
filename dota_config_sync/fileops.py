"""Operaciones de archivos para el reemplazo de la carpeta 570 (con backup y progreso)."""

import logging
import shutil
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)


def list_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file()] if root.exists() else []


def backup_dir_name(now: datetime | None = None) -> str:
    now = now or datetime.now()
    return f"backup_570_{now.strftime('%Y%m%d_%H%M%S')}"


def replace_570(src_570: Path, dst_570: Path, backup_dir: Path,
                *, progress_fn=None, status_fn=None) -> int:
    """
    Reemplaza dst_570 por una copia de src_570, respaldando antes el destino.

    progress_fn(copied, total, rel_path) y status_fn(texto) son callbacks opcionales.
    Devuelve el número de archivos copiados. Propaga excepciones al llamador.
    """
    if dst_570.exists():
        if status_fn:
            status_fn("Creando backup del destino...")
        shutil.copytree(dst_570, backup_dir)
        shutil.rmtree(dst_570)

    files = list_files(src_570)
    total = len(files)
    copied = 0
    for src_file in files:
        rel = src_file.relative_to(src_570)
        dst_file = dst_570 / rel
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dst_file)
        copied += 1
        if progress_fn:
            progress_fn(copied, total, rel.as_posix())
    return copied
