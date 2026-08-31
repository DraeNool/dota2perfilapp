"""Cliente HTTP compartido: sesión con reintentos + limitador de tasa para OpenDota."""

import logging
import threading
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import cache

log = logging.getLogger(__name__)


class RateLimiter:
    """Garantiza un intervalo mínimo entre llamadas (thread-safe)."""

    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            sleep_for = self._next_allowed - now
            if sleep_for > 0:
                time.sleep(sleep_for)
                now = time.monotonic()
            self._next_allowed = now + self.min_interval


def build_session() -> requests.Session:
    """Sesión con reintentos automáticos en 429 y 5xx (con backoff)."""
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": "DotaConfigSync/5.0"})
    return session


# Sesión y limitador globales (OpenDota free: ~60 req/min).
SESSION = build_session()
OPENDOTA_LIMITER = RateLimiter(min_interval=1.1)


def configure(opendota_min_interval: float) -> None:
    """Ajusta el intervalo del limitador de OpenDota desde la config."""
    OPENDOTA_LIMITER.min_interval = max(0.0, float(opendota_min_interval))


def get_json(url, *, params=None, timeout=15, rate_limited=False):
    """
    GET que devuelve (status_code, json|None).

    Lanza requests.RequestException si la red falla; el llamador decide el fallback.
    """
    if rate_limited:
        OPENDOTA_LIMITER.wait()
    resp = SESSION.get(url, params=params, timeout=timeout)
    body = None
    if resp.status_code == 200:
        try:
            body = resp.json()
        except ValueError:
            body = None
    return resp.status_code, body


def get_text(url, *, timeout=15):
    """GET que devuelve (status_code, texto|None). Lanza requests.RequestException si la red falla."""
    resp = SESSION.get(url, timeout=timeout)
    return resp.status_code, (resp.text if resp.status_code == 200 else None)


def cached_get_json(url, *, ttl, params=None, timeout=15, rate_limited=False):
    """
    Como get_json pero con caché en disco (solo cachea respuestas 200).

    Devuelve (status_code, json|None). En cache hit devuelve (200, valor).
    """
    cache_key = url if not params else f"{url}?{sorted(params.items())}"
    cached = cache.get(cache_key, ttl)
    if cached is not None:
        return 200, cached
    status, body = get_json(url, params=params, timeout=timeout, rate_limited=rate_limited)
    if status == 200 and body is not None:
        cache.set(cache_key, body)
    return status, body
