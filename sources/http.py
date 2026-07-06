"""
sources/http.py — Klien HTTP bersama: retry + backoff eksponensial + rate limit.

Semua modul source memakai fungsi ini supaya perilaku jaringan konsisten:
  - timeout jelas (biar cron 5 menit tak nyangkut),
  - retry pada error transien (5xx / 429 / timeout) dengan backoff,
  - throttle sederhana per-host (menghormati rate limit gratis).

Sengaja dibuat ringan (pakai requests) — cukup untuk beban GitHub Actions.
"""

import logging
import threading
import time
from typing import Any, Dict, Optional

import requests

import config

log = logging.getLogger("http")

# Session global (reuse koneksi TCP => lebih cepat di runner).
# Pakai User-Agent seperti browser: beberapa API (mis. Meteora di balik Cloudflare)
# membalas 404/403 untuk UA "bot" custom. UA browser lolos soft-block ini.
_session = requests.Session()
_session.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
    }
)

# Throttle per-host: simpan timestamp request terakhir agar tak menembak beruntun.
_last_call_lock = threading.Lock()
_last_call: Dict[str, float] = {}
# Jeda minimal antar-call per host (detik). Dexscreener ~300/min => ~0.2s aman.
_MIN_INTERVAL = {
    "api.dexscreener.com": 0.25,
    "dlmm.datapi.meteora.ag": 0.2,
    "mainnet.helius-rpc.com": 0.15,
    "trends.google.com": 1.0,
    "www.googleapis.com": 0.2,
    "news.google.com": 0.5,
    # Reddit: tak ada limit resmi dipublikasikan utk endpoint json publik ini;
    # jeda konservatif spy tak dianggap abuse.
    "www.reddit.com": 1.0,
    # GeckoTerminal free tier ~30 req/menit -> jeda >2s aman.
    "api.geckoterminal.com": 2.1,
    # Gemini API free tier (model flash-lite) ~15-30 req/menit tergantung
    # model -> jeda konservatif 3s aman utk semua varian free tier.
    "generativelanguage.googleapis.com": 3.0,
    # Groq free tier ~30 req/menit -> jeda >2s aman.
    "api.groq.com": 2.1,
    # LunarCrush (berbayar, tier Individual) -- jeda konservatif, blm ada
    # angka rate-limit resmi yg kita pakai jadi aman lebih lambat.
    "lunarcrush.com": 1.0,
    # Jupiter lite-api (gratis, no key) -- jeda konservatif, blm ada angka
    # rate-limit resmi yg kita pakai jadi aman lebih lambat.
    "lite-api.jup.ag": 0.5,
    "api.telegram.org": 0.3,
    # GMGN OpenAPI (gratis) -- dokumentasi sebut ~20 req/detik sustained,
    # jeda konservatif jauh di bawah itu.
    "openapi.gmgn.ai": 0.2,
}


def _throttle(host: str) -> None:
    """Tahan sebentar bila call ke host ini terlalu rapat dengan sebelumnya."""
    interval = _MIN_INTERVAL.get(host, 0.1)
    with _last_call_lock:
        now = time.monotonic()
        prev = _last_call.get(host, 0.0)
        wait = interval - (now - prev)
        if wait > 0:
            time.sleep(wait)
        _last_call[host] = time.monotonic()


def _host_of(url: str) -> str:
    try:
        return url.split("/")[2]
    except IndexError:
        return url


def request_json(
    method: str,
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: Optional[int] = None,
    max_retries: Optional[int] = None,
) -> Optional[Any]:
    """
    Lakukan request dan kembalikan JSON (dict/list) atau None bila gagal permanen.

    JANGAN pernah melempar exception ke pemanggil — return None supaya pipeline
    bisa degrade gracefully (satu API mati != seluruh run crash).
    """
    timeout = timeout or config.HTTP_TIMEOUT
    max_retries = max_retries if max_retries is not None else config.HTTP_MAX_RETRIES
    host = _host_of(url)

    for attempt in range(max_retries + 1):
        _throttle(host)
        try:
            resp = _session.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=headers,
                timeout=timeout,
            )
        except requests.RequestException as e:
            log.warning("HTTP %s %s gagal (attempt %d): %s", method, host, attempt, e)
            _sleep_backoff(attempt)
            continue

        # Rate limited / server error => retry dengan backoff.
        if resp.status_code in (429, 500, 502, 503, 504):
            body = ""
            try:
                body = resp.text[:300].replace("\n", " ")
            except Exception:  # noqa: BLE001
                pass
            log.warning("HTTP %s %s -> %d (attempt %d) body=%s", method, host, resp.status_code, attempt, body)
            # Hormati Retry-After bila ada.
            retry_after = resp.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                time.sleep(min(int(retry_after), 30))
            else:
                _sleep_backoff(attempt)
            continue

        if resp.status_code >= 400:
            # Error klien (4xx selain 429) biasanya permanen => jangan retry.
            # Log potongan body utk diagnosa (mis. kenapa 404/400).
            body = ""
            try:
                body = resp.text[:160].replace("\n", " ")
            except Exception:  # noqa: BLE001
                pass
            # Log header kunci utk deteksi Cloudflare (server/cf-ray/cf-mitigated).
            srv = resp.headers.get("server", "")
            cfray = resp.headers.get("cf-ray", "")
            cfmit = resp.headers.get("cf-mitigated", "")
            log.info(
                "HTTP %s %s -> %d (tidak di-retry) server=%s cf-ray=%s cf-mitigated=%s body=%s",
                method, host, resp.status_code, srv, cfray, cfmit, body,
            )
            return None

        try:
            return resp.json()
        except ValueError:
            log.warning("Respon non-JSON dari %s", host)
            return None

    log.warning("HTTP %s %s menyerah setelah %d percobaan", method, host, max_retries + 1)
    return None


def _sleep_backoff(attempt: int) -> None:
    """Backoff eksponensial: base^attempt (1.5, 2.25, 3.375, ...) detik."""
    delay = config.HTTP_BACKOFF_BASE ** attempt
    time.sleep(min(delay, 30))


def get_json(url: str, **kwargs) -> Optional[Any]:
    return request_json("GET", url, **kwargs)


def post_json(url: str, **kwargs) -> Optional[Any]:
    return request_json("POST", url, **kwargs)
