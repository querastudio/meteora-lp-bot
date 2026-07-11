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


def fetch_pools(max_pools: int, page_size: int = 200, sort_by: str = "volume_24h:desc") -> List[Dict[str, Any]]:
    """
    Ambil pool DLMM Meteora ter-normalisasi (maks `max_pools`), default
    terurut volume 24h (lihat fetch_newest_pools() utk terurut kebaruan).

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
                "sort_by": sort_by,
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

    log.info("Meteora: %d pool diambil (sort_by=%s)", len(pools), sort_by)
    return pools


# Kandidat nama field "urut dari yang paling baru dibuat" -- BELUM
# terverifikasi resmi (docs Meteora 403 diakses dari sandbox sesi ini,
# endpoint lama /pair/all_with_pagination jg pernah diam2 pensiun tanpa
# ganti versi -- lihat catatan atas file). Coba berturut2, pakai yg PERTAMA
# hasilnya non-kosong; SEMUA gagal -> degrade ke [] (fetch_pools() volume
# biasa tetap jalan spt biasa, cuma newest-pool discovery yg skip run ini).
_NEWEST_SORT_CANDIDATES = ["created_at:desc", "createdAt:desc", "activation_point:desc", "pool_created_at:desc"]


def fetch_newest_pools(max_pools: int, page_size: int = 200) -> List[Dict[str, Any]]:
    """
    Ambil pool PALING BARU dibuat -- BEDA dari fetch_pools() (default
    volume_24h:desc) yg jadi akar bug (dilaporkan user, 11 Juli 2026): pool
    baru volume-nya masih ~0, KALAH ranking TERUS drpd pool yg udah rame,
    jadi tak PERNAH kebagian slot MAX_EXPENSIVE_CANDIDATES yg dibatasi --
    bukti nyata dari log: $TRIPLET tak pernah muncul sama sekali di log
    manapun sepanjang ~35 jam (kalah rank volume terus), $GHOSTI baru lolos
    gate mcap PAS harga sudah lewat puncak (bounce sesaat di atas $300rb
    baru ke-notice, padahal sudah beberapa kali gagal sebelumnya).

    Field sort_by di sini masih DUGAAN (lihat _NEWEST_SORT_CANDIDATES) --
    log raw pair PERTAMA sekali biar bisa diverifikasi dari live run apakah
    kandidat yg kepakai itu BENERAN ngurutin dari baru (bukan API diam2
    ignore sort_by tak dikenal & balik urutan default-nya sendiri).
    """
    for sort_by in _NEWEST_SORT_CANDIDATES:
        pools = fetch_pools(max_pools, page_size, sort_by=sort_by)
        if pools:
            log.info(
                "Meteora newest-pool sort_by='%s' dipakai (%d pool). Cek log raw pair "
                "pertama utk verifikasi ini beneran terurut kebaruan: %s",
                sort_by, len(pools), str(pools[0].get("_raw"))[:600],
            )
            return pools
        log.warning("Meteora newest-pool sort_by='%s' gagal/kosong, coba kandidat berikutnya", sort_by)
    log.error("Meteora: SEMUA kandidat sort_by newest-pool gagal -- discovery pool baru TAK jalan run ini")
    return []


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


def fetch_pool_by_address(address: str, max_search: int = 3000) -> Optional[Dict[str, Any]]:
    """
    Cari SATU pool Meteora dari alamat POOL-nya sendiri (bukan mint token) --
    dipakai position_monitor.py (/start <pool_address>). Sama pola dgn
    fetch_pool_by_mint(): tak ada endpoint resmi "get pool by address"
    terverifikasi di /pools (lihat catatan fetch_pool_by_mint), jadi pakai
    fetch_pools() yg SUDAH terbukti jalan lalu cari address-nya di sisi
    klien. Trade-off sama: pool volume sangat kecil di luar `max_search`
    teratas tak akan ketemu.
    """
    pools = fetch_pools(max_search)
    for pool in pools:
        if pool["address"] == address:
            return pool
    return None
