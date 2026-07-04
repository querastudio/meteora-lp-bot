"""
sources/dexscreener.py — Metrik token (mcap, volume, harga, perubahan) + harga SOL.

Endpoint gratis (no key, ~300 req/min):
  https://api.dexscreener.com/latest/dex/tokens/{mint}

Kembalikan ringkasan pair Solana paling likuid untuk mint tsb, karena satu token
bisa punya banyak pair. Kita pilih pair dengan likuiditas USD tertinggi.

Cache in-memory per run (TTL) supaya tak double-call token yang sama.
"""

import logging
import time
from typing import Any, Dict, Optional

import config
from sources import http

log = logging.getLogger("dexscreener")

TOKENS_URL = "https://api.dexscreener.com/latest/dex/tokens/{mint}"

# Cache sederhana: {mint: (timestamp, data)}. TTL cukup panjang untuk 1 run.
_CACHE: Dict[str, Any] = {}
_CACHE_TTL = 300  # detik


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _pick_best_pair(pairs: list) -> Optional[Dict[str, Any]]:
    """Pilih pair Solana dengan likuiditas USD tertinggi (paling representatif)."""
    sol_pairs = [p for p in pairs if p.get("chainId") == "solana"]
    candidates = sol_pairs or pairs
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda p: _to_float((p.get("liquidity") or {}).get("usd")),
    )


def get_token_metrics(mint: str) -> Optional[Dict[str, Any]]:
    """
    Ambil metrik token ter-normalisasi:
      mcap, volume h24/h6/h1, price_usd, price_change h24/h6/h1, liquidity_usd,
      pair_created_at (ms), symbol, name.

    Return None kalau token tak ditemukan / API mati.
    """
    cached = _CACHE.get(mint)
    if cached and (time.time() - cached[0]) < _CACHE_TTL:
        return cached[1]

    data = http.get_json(TOKENS_URL.format(mint=mint))
    if not data:
        return None

    pairs = data.get("pairs") or []
    best = _pick_best_pair(pairs)
    if not best:
        return None

    vol = best.get("volume") or {}
    chg = best.get("priceChange") or {}
    liq = best.get("liquidity") or {}
    base = best.get("baseToken") or {}

    metrics = {
        "mint": mint,
        "symbol": base.get("symbol") or "?",
        "name": base.get("name") or "",
        "price_usd": _to_float(best.get("priceUsd")),
        "market_cap": _to_float(best.get("marketCap") or best.get("fdv")),
        "fdv": _to_float(best.get("fdv")),
        "volume_h24": _to_float(vol.get("h24")),
        "volume_h6": _to_float(vol.get("h6")),
        "volume_h1": _to_float(vol.get("h1")),
        "price_change_h24": _to_float(chg.get("h24")),
        "price_change_h6": _to_float(chg.get("h6")),
        "price_change_h1": _to_float(chg.get("h1")),
        "liquidity_usd": _to_float(liq.get("usd")),
        "pair_created_at": best.get("pairCreatedAt"),  # ms epoch atau None
        "url": best.get("url") or "",
        # Alamat pair yg harganya kita pakai (bisa BEDA dari pool Meteora kita,
        # mis. token juga trading di Raydium dgn likuiditas lebih besar). Dipakai
        # utk pastikan lookup ATH GeckoTerminal konsisten dgn sumber harga ini.
        "pair_address": best.get("pairAddress") or "",
        "dex_id": best.get("dexId") or "",
        "_raw": best,
    }
    _CACHE[mint] = (time.time(), metrics)
    return metrics


# Harga SOL: ambil sekali per run, cache.
_SOL_PRICE_CACHE: Dict[str, Any] = {"ts": 0.0, "price": 0.0}


def get_sol_price_usd() -> float:
    """
    Harga SOL (USD) untuk konversi threshold '20 SOL' -> USD.
    Sumber utama Dexscreener (via WSOL mint); fallback CoinGecko free.
    """
    now = time.time()
    if _SOL_PRICE_CACHE["price"] > 0 and (now - _SOL_PRICE_CACHE["ts"]) < _CACHE_TTL:
        return _SOL_PRICE_CACHE["price"]

    price = 0.0
    m = get_token_metrics(config.SOL_MINT)
    if m and m["price_usd"] > 0:
        price = m["price_usd"]
    else:
        # Fallback CoinGecko (gratis, no key).
        cg = http.get_json(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "solana", "vs_currencies": "usd"},
        )
        if cg and "solana" in cg:
            price = _to_float(cg["solana"].get("usd"))

    if price > 0:
        _SOL_PRICE_CACHE.update({"ts": now, "price": price})
    else:
        log.warning("Gagal ambil harga SOL, pakai fallback konservatif 150")
        price = 150.0  # fallback aman biar gate tetap ketat
    return price
