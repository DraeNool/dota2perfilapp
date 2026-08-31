"""Resolución de rutas de datos de la app (config y caché), válida para script y .exe."""

import sys
from pathlib import Path


def app_base_dir() -> Path:
    """
    Carpeta base donde viven config.json y la caché.

    - Ejecutable PyInstaller: junto al .exe.
    - Script: raíz del proyecto (carpeta padre de este paquete).
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_path(name: str) -> Path:
    """
    Ruta a un recurso empaquetado (icono, etc.).

    - Ejecutable PyInstaller: carpeta temporal de extracción (sys._MEIPASS).
    - Script: raíz del proyecto.
    """
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / name  # type: ignore[attr-defined]
    return app_base_dir() / name


def config_path() -> Path:
    return app_base_dir() / "config.json"


def cache_dir() -> Path:
    d = app_base_dir() / ".cache"
    d.mkdir(parents=True, exist_ok=True)
    return d
