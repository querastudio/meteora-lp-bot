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
from typing import Any, Dict, List

from sources import http

log = logging.getLogger("meteora")

BASE = "https://dlmm-api.meteora.ag"
PAIR_PAGINATED = f"{BASE}/pair/all_with_pagination"


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


def fetch_pools(max_pools: int, page_size: int = 100) -> List[Dict[str, Any]]:
    """
    Ambil pool DLMM (paginasi) sampai terkumpul `max_pools` pool ter-normalisasi.

    Diurutkan server berdasarkan aktivitas; kita berhenti begitu cukup supaya
    run cepat. Return list of dict ter-normalisasi.
    """
    pools: List[Dict[str, Any]] = []
    page = 0
    while len(pools) < max_pools:
        data = http.get_json(
            PAIR_PAGINATED,
            params={
                "page": page,
                "limit": page_size,
                # urut dari volume 24h tertinggi -> kandidat fee bagus duluan
                "sort_key": "volume",
                "order_by": "desc",
            },
        )
        if not data:
            break

        # API kadang bungkus list di key "pairs" / "data", kadang list langsung.
        rows = data.get("pairs") if isinstance(data, dict) else data
        if not rows:
            break

        for pair in rows:
            try:
                pools.append(_normalize(pair))
            except Exception as e:  # noqa: BLE001 — jangan biarkan 1 baris rusak crash run
                log.debug("skip pool malformed: %s", e)
            if len(pools) >= max_pools:
                break

        # Kalau halaman lebih kecil dari page_size, sudah habis.
        if len(rows) < page_size:
            break
        page += 1

    log.info("Meteora: %d pool diambil", len(pools))
    return pools
