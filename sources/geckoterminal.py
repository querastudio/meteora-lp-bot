"""
sources/geckoterminal.py — ATH sungguhan dari riwayat candle OHLCV (gratis, no key).

Dexscreener API publik/gratis TIDAK menyediakan riwayat harga (cuma harga saat
ini + persen perubahan h1/h6/h24) -- ATH di website Dexscreener dihitung dari
data chart internal yang tak dipublikasikan sbg API gratis.

GeckoTerminal (produk CoinGecko utk data on-chain) menyediakan candle OHLCV
harian gratis tanpa API key, hingga ~6 bulan ke belakang, mencakup Solana.
Kita ambil candle harian sejak pool dibuat, ambil `high` tertinggi = ATH
sungguhan (dalam batas window data yang tersedia).

Degrade gracefully: kalau pool belum terindeks / API gagal, return None -->
main.py akan pakai riwayat state kita sendiri sbg fallback (lihat state.py).
"""

import logging
from typing import Any, List, Optional

import config
from sources import http

log = logging.getLogger("geckoterminal")

BASE = "https://api.geckoterminal.com/api/v2"


def get_pool_ath(pool_address: str, network: str = "solana") -> Optional[float]:
    """
    Return harga `high` tertinggi dari candle harian pool (ATH sungguhan dalam
    window data GeckoTerminal, ~6 bulan). None kalau tak tersedia/gagal.
    """
    if not config.GECKOTERMINAL_ATH_ENABLED:
        return None

    url = f"{BASE}/networks/{network}/pools/{pool_address}/ohlcv/day"
    data = http.get_json(url, params={"aggregate": 1, "limit": 1000, "currency": "usd"})
    if not data:
        return None

    try:
        candles: List[Any] = data["data"]["attributes"]["ohlcv_list"]
    except (KeyError, TypeError):
        return None

    if not candles:
        return None

    try:
        # Format tiap candle: [timestamp, open, high, low, close, volume]
        highs = [float(c[2]) for c in candles if len(c) >= 3]
        return max(highs) if highs else None
    except (TypeError, ValueError, IndexError) as e:
        log.debug("gagal parse candle GeckoTerminal utk %s: %s", pool_address, e)
        return None
