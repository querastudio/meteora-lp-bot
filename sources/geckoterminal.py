"""
sources/geckoterminal.py — Sinyal momentum VWAP dari riwayat candle OHLCV (gratis, no key).

Dexscreener API gratis TIDAK menyediakan riwayat harga (cuma harga sekarang +
persen perubahan h1/h6/h24), jadi VWAP "sejak pool dibuat" (yang user pantau
manual di GMGN sbg VWAP-hlc3-Century, konsisten di semua timeframe candle
karena anchor-nya tetap sejak awal, bukan reset harian) tak bisa dihitung dari
Dexscreener saja. GeckoTerminal (produk CoinGecko utk data on-chain) sediakan
candle OHLCV gratis tanpa API key, cakup Solana.

Konsep sinyal:
  harga sekarang > VWAP  -> pembeli rata-rata sejak pool dibuat masih untung,
  minat beli belum mereda ("market masih incar" ala pengamatan user).
  TAPI makin JAUH di atas VWAP bukan berarti makin aman -- itu justru ciri
  khas euforia/blow-off-top (rawan reversal). Jadi skornya tak linear naik
  tanpa batas, lihat _vwap_score().

Ini SOFT SIGNAL (nambah skor), BUKAN hard gate -- pelajaran dari gate ATH-
proximity yang dihapus: gate keras berbasis histori harga gampang bentrok dgn
gate mcap/volume dan bikin nyaris tak ada token lolos. Degrade gracefully
(available=False, skor netral 0.5) kalau API gagal/pool belum terindeks.
"""

import logging
from typing import Any, Dict, List

from sources import http

log = logging.getLogger("geckoterminal")

BASE = "https://api.geckoterminal.com/api/v2"
# 'hour' x limit 1000 -> ~41 hari histori dlm 1 call. Kandidat yg sampai
# sejauh ini di funnel biasanya baru berumur jam-hari (lihat Stage 1 gate
# MIN_CUMULATIVE_FEE_SOL & Stage 5 pool_age), jadi ini cukup mendekati
# "sejak pool dibuat". Pool yg lebih tua dari 41 hari -> VWAP dihitung dari
# histori 41 hari terakhir saja (estimasi, bukan sejak awal mutlak).
_TIMEFRAME = "hour"
_LIMIT = 1000


def _fetch_ohlcv(pool_address: str, network: str = "solana") -> List[List[Any]]:
    """Ambil candle [timestamp, open, high, low, close, volume], lama->baru."""
    url = f"{BASE}/networks/{network}/pools/{pool_address}/ohlcv/{_TIMEFRAME}"
    data = http.get_json(url, params={"aggregate": 1, "limit": _LIMIT, "currency": "usd"})
    if not data:
        return []
    try:
        candles: List[Any] = data["data"]["attributes"]["ohlcv_list"]
    except (KeyError, TypeError):
        return []
    return list(reversed(candles))  # API balikin baru->lama


def _vwap_score(ratio: float) -> float:
    """
    Skor 0-1 dari rasio price/VWAP. Di atas VWAP = bagus (minat msh ada),
    tapi TERLALU jauh di atas = euforia/rawan blow-off-top -> skor tak naik
    tanpa batas, malah turun lagi kalau rasio ekstrem.
    """
    if ratio < 1.0:
        return 0.3  # di bawah VWAP -> minat rata-rata sudah mereda
    if ratio <= 1.5:
        return round(0.7 + (ratio - 1.0) * 0.6, 2)  # 1.0->0.7 ... 1.5->1.0
    if ratio <= 3.0:
        return 1.0  # zona momentum kuat & masih dianggap sehat
    return round(max(0.5, 1.0 - (ratio - 3.0) * 0.1), 2)  # ekstrem -> mulai turun


def vwap_signal(pool_address: str, current_price: float, network: str = "solana") -> Dict[str, Any]:
    """
    Return { available, vwap, ratio_pct, above_vwap, momentum_score, candle_count }.

    ratio_pct = seberapa persen harga sekarang di atas(+)/bawah(-) VWAP.
    momentum_score dipakai scoring.py (soft, bukan gate).
    """
    out = {
        "available": False, "vwap": 0.0, "ratio_pct": 0.0,
        "above_vwap": False, "momentum_score": 0.5, "candle_count": 0,
    }
    if not pool_address or current_price <= 0:
        return out
    try:
        rows = _fetch_ohlcv(pool_address, network)
        if not rows:
            return out
        cum_pv = 0.0
        cum_v = 0.0
        for row in rows:
            if len(row) < 6:
                continue
            _, _o, hi, lo, cl, vol = row[:6]
            vol = float(vol or 0)
            if vol <= 0:
                continue
            hlc3 = (float(hi) + float(lo) + float(cl)) / 3.0
            cum_pv += hlc3 * vol
            cum_v += vol
        if cum_v <= 0:
            return out
        vwap = cum_pv / cum_v
        if vwap <= 0:
            return out
        ratio = current_price / vwap
        out.update(
            {
                "available": True,
                "vwap": round(vwap, 12),
                "ratio_pct": round((ratio - 1.0) * 100.0, 1),
                "above_vwap": current_price >= vwap,
                "momentum_score": _vwap_score(ratio),
                "candle_count": len(rows),
            }
        )
    except Exception as e:  # noqa: BLE001
        log.info("VWAP gagal utk pool %s: %s (degrade)", pool_address, e)
    return out
