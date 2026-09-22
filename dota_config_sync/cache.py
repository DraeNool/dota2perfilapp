"""Caché en disco con TTL para respuestas de APIs (evita re-descargar en cada recarga)."""

import hashlib
import json
import logging
import threading
import time

from .paths import cache_dir

log = logging.getLogger(__name__)
_LOCK = threading.Lock()


def _key_to_file(key: str):
    # SHA1 solo para derivar un nombre de archivo de caché — no es uso criptográfico.
    digest = hashlib.sha1(key.encode("utf-8"), usedforsecurity=False).hexdigest()
    return cache_dir() / f"{digest}.json"


def get(key: str, ttl_seconds: int):
    """Devuelve el valor cacheado si existe y no ha expirado; si no, None."""
    path = _key_to_file(key)
    with _LOCK:
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    if time.time() - payload.get("ts", 0) > ttl_seconds:
        return None
    return payload.get("value")


def peek(key: str):
    """(valor, timestamp) aunque haya expirado; None si no existe o no se puede leer."""
    path = _key_to_file(key)
    with _LOCK:
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    return payload.get("value"), float(payload.get("ts", 0))


def set(key: str, value) -> None:
    """Guarda un valor JSON-serializable con marca de tiempo."""
    path = _key_to_file(key)
    with _LOCK:
        try:
            path.write_text(
                json.dumps({"ts": time.time(), "value": value}, ensure_ascii=False),
                encoding="utf-8",
            )
        except (OSError, TypeError) as e:
            log.debug("No se pudo cachear %s: %s", key, e)


def clear() -> int:
    """Borra toda la caché. Devuelve el número de archivos eliminados."""
    n = 0
    with _LOCK:
        for f in cache_dir().glob("*.json"):
            try:
                f.unlink()
                n += 1
            except OSError:
                pass
    return n
