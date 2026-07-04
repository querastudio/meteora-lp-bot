"""
sources/meteora.py — Ambil daftar pool DLMM Meteora + normalisasi field.

Endpoint gratis (no key):
  https://dlmm-api.meteora.ag/pair/all_with_pagination

Field yang kita pakai (nama bisa berbeda antar versi API -> kita normalisasi):
  - address        : alamat pool (untuk link Meteora & dedup)
  - name           : "TOKEN-SOL" dsb
  - mint_x / mint_y: dua sisi pasangan
  - liquidity      : TVL (USD, string)
  - bin_step
  - base_fee_percentage
  - cumulative_fee_volume / fees   : total fee global (USD)
  - trade_volume_24h / volume      : volume 24h (USD)
  - fees_24h                       : fee 24h (USD) untuk fee/TVL Stage 5

Kita ambil pool terurut dari yang aktivitasnya tinggi, lalu screening di pipeline.
"""

import logging
import os
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlencode

from sources import http

log = logging.getLogger("meteora")

BASE = "https://dlmm-api.meteora.ag"
PAIR_PAGINATED = f"{BASE}/pair/all_with_pagination"
PAIR_ALL = f"{BASE}/pair/all"

# Beberapa API front-end memvalidasi Referer/Origin (menolak request "polos").
# Kirim seolah datang dari app resmi Meteora.
_MET_HEADERS = {
    "Referer": "https://app.meteora.ag/",
    "Origin": "https://app.meteora.ag",
}

# Cloudflare Meteora memblokir IP data-center (mis. runner GitHub Actions -> 404).
# Relay publik mengambil data dari IP-nya sendiri (tak diblokir) lalu meneruskan.
# Kita coba beberapa relay berantai karena relay gratis sering flaky/timeout.
# Override daftar via env METEORA_PROXY (pisah koma). Set METEORA_PROXY=off utk mematikan.
# {url} diganti URL Meteora lengkap yang sudah di-URL-encode.
_DEFAULT_PROXIES = [
    "https://api.codetabs.com/v1/proxy/?quest={url}",
    "https://corsproxy.io/?url={url}",
    "https://api.allorigins.win/raw?url={url}",
]


def _proxy_list() -> List[str]:
    env = os.getenv("METEORA_PROXY", "").strip()
    if env.lower() == "off":
        return []
    if env:
        return [p.strip() for p in env.split(",") if p.strip()]
    return _DEFAULT_PROXIES


def _get_meteora(base_url: str, params: Optional[Dict[str, Any]] = None) -> Optional[Any]:
    """
    Ambil JSON dari Meteora. Coba langsung dulu; kalau gagal (mis. Cloudflare 404
    karena IP CI diblokir), coba tiap relay berurutan sampai ada yang berhasil.
    """
    data = http.get_json(base_url, params=params, headers=_MET_HEADERS)
    if data:
        return data

    full = base_url + ("?" + urlencode(params) if params else "")
    encoded = quote(full, safe="")
    for tmpl in _proxy_list():
        proxied = tmpl.format(url=encoded)
        host = tmpl.split("/")[2] if "//" in tmpl else tmpl
        log.info("Meteora: request langsung gagal -> coba relay %s", host)
        # relay bisa lambat -> beri timeout lebih longgar, tanpa retry berlebihan
        data = http.get_json(proxied, timeout=25, max_retries=1)
        if data and _rows_from(data):
            log.info("Meteora: relay %s berhasil", host)
            return data
    return None


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _normalize(pair: Dict[str, Any]) -> Dict[str, Any]:
    """Seragamkan field pool ke bentuk internal yang stabil dipakai pipeline."""
    return {
        "address": pair.get("address") or pair.get("pool_address") or "",
        "name": pair.get("name") or "",
        "mint_x": pair.get("mint_x") or "",
        "mint_y": pair.get("mint_y") or "",
        "tvl_usd": _to_float(pair.get("liquidity")),
        "bin_step": int(_to_float(pair.get("bin_step"))),
        "base_fee_pct": _to_float(pair.get("base_fee_percentage")),
        # total fee global sepanjang umur pool (dipakai gate 20 SOL)
        "cumulative_fee_usd": _to_float(
            pair.get("cumulative_fee_volume") or pair.get("fees")
        ),
        "volume_24h_usd": _to_float(
            (pair.get("trade_volume_24h"))
            or (isinstance(pair.get("volume"), dict) and pair["volume"].get("h24"))
            or 0.0
        ),
        "fees_24h_usd": _to_float(
            (pair.get("fees_24h"))
            or (isinstance(pair.get("fees"), dict) and pair["fees"].get("h24"))
            or 0.0
        ),
        # simpan mentah untuk keperluan lanjutan (mis. reserve, umur)
        "_raw": pair,
    }


def _rows_from(data: Any) -> List[Dict[str, Any]]:
    """Ekstrak list pair dari berbagai bentuk respon (list langsung / {pairs|data:[...]})."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("pairs") or data.get("data") or data.get("groups") or []
    return []


def _fetch_paginated(max_pools: int, page_size: int) -> List[Dict[str, Any]]:
    """
    Endpoint utama: /pair/all_with_pagination.
    Param di-minimal-kan (hanya page & limit) karena kombinasi sort_key/order_by
    tertentu bisa memicu 404 di sisi Meteora. Sudah terurut aktivitas dari server.
    """
    pools: List[Dict[str, Any]] = []
    page = 0
    while len(pools) < max_pools:
        data = _get_meteora(PAIR_PAGINATED, params={"page": page, "limit": page_size})
        if not data:
            break
        rows = _rows_from(data)
        if not rows:
            break
        for pair in rows:
            try:
                pools.append(_normalize(pair))
            except Exception as e:  # noqa: BLE001
                log.debug("skip pool malformed: %s", e)
            if len(pools) >= max_pools:
                break
        if len(rows) < page_size:
            break
        page += 1
    return pools


def _fetch_all_fallback(max_pools: int) -> List[Dict[str, Any]]:
    """
    Fallback: /pair/all (tanpa paginasi, kembalikan semua). Bisa besar, jadi kita
    urutkan client-side by volume 24h desc lalu ambil top `max_pools`.
    """
    data = _get_meteora(PAIR_ALL)
    rows = _rows_from(data)
    if not rows:
        return []
    pools = []
    for pair in rows:
        try:
            pools.append(_normalize(pair))
        except Exception as e:  # noqa: BLE001
            log.debug("skip pool malformed: %s", e)
    pools.sort(key=lambda p: p.get("volume_24h_usd", 0.0), reverse=True)
    return pools[:max_pools]


def fetch_pools(max_pools: int, page_size: int = 100) -> List[Dict[str, Any]]:
    """
    Ambil pool DLMM Meteora ter-normalisasi (maks `max_pools`).

    Strategi tahan-banting: coba endpoint paginasi dulu; kalau gagal/kosong
    (mis. 404), jatuh ke /pair/all lalu urut client-side. Return list dict.
    """
    pools = _fetch_paginated(max_pools, page_size)
    if not pools:
        log.info("Meteora: paginasi kosong/gagal -> coba fallback /pair/all")
        pools = _fetch_all_fallback(max_pools)

    log.info("Meteora: %d pool diambil", len(pools))
    return pools
