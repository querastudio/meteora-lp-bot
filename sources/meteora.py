"""
sources/meteora.py — Ambil daftar pool DLMM Meteora + normalisasi field.

Endpoint gratis (no key), API baru per OpenAPI spec resmi Meteora:
  https://dlmm.datapi.meteora.ag/pools
  (endpoint lama dlmm-api.meteora.ag/pair/all_with_pagination sudah pensiun -> 404)

Catatan penting spesifikasi:
  - `page` 1-based (bukan 0-based)
  - `page_size` maksimal 1000 -> bisa ambil banyak pool dalam 1 call
  - `filter_by=is_blacklisted=false` : Meteora sendiri menandai pool blacklist,
    kita pakai ini sebagai lapisan keamanan gratis tambahan (di luar Stage 3).
  - `sort_by=volume_24h:desc` : kandidat fee bagus lebih dulu diproses.

Field respons (`data[]`) yang kita pakai (lihat _normalize):
  address, name, token_x.address, token_y.address, tvl,
  pool_config.bin_step, pool_config.base_fee_pct,
  cumulative_metrics.fees, volume.24h, fees.24h, is_blacklisted
"""

import logging
from typing import Any, Dict, List, Optional

from sources import http

log = logging.getLogger("meteora")

BASE = "https://dlmm.datapi.meteora.ag"
POOLS_URL = f"{BASE}/pools"


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _normalize(pair: Dict[str, Any]) -> Dict[str, Any]:
    """Seragamkan field pool ke bentuk internal yang stabil dipakai pipeline."""
    token_x = pair.get("token_x") or {}
    token_y = pair.get("token_y") or {}
    pool_cfg = pair.get("pool_config") or {}
    volume = pair.get("volume") or {}
    fees = pair.get("fees") or {}
    cumulative = pair.get("cumulative_metrics") or {}

    return {
        "address": pair.get("address") or "",
        "name": pair.get("name") or "",
        "mint_x": token_x.get("address") or "",
        "mint_y": token_y.get("address") or "",
        "tvl_usd": _to_float(pair.get("tvl")),
        "bin_step": int(_to_float(pool_cfg.get("bin_step"))),
        "base_fee_pct": _to_float(pool_cfg.get("base_fee_pct")),
        # total fee global sepanjang umur pool (dipakai gate 20 SOL)
        "cumulative_fee_usd": _to_float(cumulative.get("fees")),
        "volume_24h_usd": _to_float(volume.get("24h")),
        "fees_24h_usd": _to_float(fees.get("24h")),
        "is_blacklisted": bool(pair.get("is_blacklisted")),
        # simpan mentah untuk keperluan lanjutan (mis. token_x/y price, apr, tags)
        "_raw": pair,
    }


def _rows_from(data: Any) -> List[Dict[str, Any]]:
    """Ekstrak list pool dari respons `/pools` (key "data")."""
    if isinstance(data, dict):
        return data.get("data") or []
    if isinstance(data, list):
        return data
    return []


def fetch_pools(max_pools: int, page_size: int = 200) -> List[Dict[str, Any]]:
    """
    Ambil pool DLMM Meteora ter-normalisasi (maks `max_pools`), terurut volume 24h.

    `page` di API ini 1-based. `page_size` di-cap 1000 oleh server.
    filter_by=is_blacklisted=false membuang pool yang sudah ditandai Meteora
    sebagai bermasalah -- lapisan keamanan gratis tambahan di luar Stage 3.
    """
    pools: List[Dict[str, Any]] = []
    page = 1
    page_size = min(page_size, 1000)

    while len(pools) < max_pools:
        data = http.get_json(
            POOLS_URL,
            params={
                "page": page,
                "page_size": page_size,
                "sort_by": "volume_24h:desc",
                "filter_by": "is_blacklisted=false",
            },
        )
        if not data:
            break
        rows = _rows_from(data)
        if not rows:
            break
        for pair in rows:
            try:
                pools.append(_normalize(pair))
            except Exception as e:  # noqa: BLE001 — 1 pool rusak tak boleh crash run
                log.debug("skip pool malformed: %s", e)
            if len(pools) >= max_pools:
                break
        if len(rows) < page_size:
            break
        page += 1

    log.info("Meteora: %d pool diambil", len(pools))
    return pools


def fetch_pool_by_mint(mint: str, max_search: int = 3000) -> Optional[Dict[str, Any]]:
    """
    Cari SATU pool Meteora yang salah satu sisinya (mint_x/mint_y) = mint ini.
    Dipakai fitur "kirim CA, bot balas analisa" (main.py: analyze_by_mint) --
    bukan hot-path cron 5 menit, jadi boleh scan lebih dalam (max_search lebih
    besar drpd MAX_POOLS_PER_RUN biasa).

    Tak ada endpoint resmi "search pool by token mint" yang terverifikasi di
    /pools (dokumentasinya tak bisa diakses dari sandbox ini) -- daripada
    menebak nama parameter query yang berisiko 404/salah diam-diam, kita pakai
    fetch_pools() yang SUDAH terbukti jalan lalu cari mint-nya di sisi klien.
    Trade-off: pool yang volume-nya sangat kecil (di luar `max_search` pool
    teratas by volume) tak akan ketemu -- utk kasus itu dianggap "bukan pool
    Meteora aktif", bagian Kualitas LP di notif ditandai n/a (degrade
    gracefully, bukan error).
    """
    pools = fetch_pools(max_search)
    for pool in pools:
        if pool["mint_x"] == mint or pool["mint_y"] == mint:
            return pool
    return None
