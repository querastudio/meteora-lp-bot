"""
screening/lp_quality.py — Stage 5: metrik kualitas LP (inti profitabilitas pasif).

Bukan hard gate, tapi bobot besar. Semua dihitung dari data pool Meteora +
metrik token Dexscreener yang sudah kita punya (0 call tambahan).

Metrik:
  - fee/TVL harian = fees_24h / TVL  -> yield fee riil (TERPENTING utk LP pasif)
  - vol/TVL ratio  = volume_24h / TVL -> velocity (target >= 2-3x)
  - konsentrasi LP  -> estimasi dari data pool bila tersedia (kalau tidak, netral)
  - umur pool       -> prefer yang sudah lewat fase peluncuran liar

Return dict dengan skor 0-1 per komponen + nilai mentah utk notif.
"""

import logging
import time
from typing import Any, Dict

import config

log = logging.getLogger("lp_quality")


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def analyze(pool: Dict[str, Any], token_metrics: Dict[str, Any]) -> Dict[str, Any]:
    tvl = pool["tvl_usd"] or 0.0

    # Fee 24h: utamakan dari pool Meteora; fallback estimasi dari volume*base_fee.
    fees_24h = pool.get("fees_24h_usd") or 0.0
    if fees_24h <= 0 and pool.get("volume_24h_usd", 0) > 0:
        # estimasi: volume * base_fee%. Tandai sebagai estimasi.
        fees_24h = pool["volume_24h_usd"] * (pool["base_fee_pct"] / 100.0)
        fee_estimated = True
    else:
        fee_estimated = False

    vol_24h = pool.get("volume_24h_usd") or token_metrics.get("volume_h24") or 0.0

    fee_tvl_daily_pct = (fees_24h / tvl * 100.0) if tvl > 0 else 0.0
    vol_tvl = (vol_24h / tvl) if tvl > 0 else 0.0

    # Skor fee/TVL: linear antara GOOD..GREAT, cap di 1.0.
    if fee_tvl_daily_pct >= config.FEE_TVL_DAILY_GREAT_PCT:
        fee_score = 1.0
    else:
        fee_score = _clip01(fee_tvl_daily_pct / config.FEE_TVL_DAILY_GREAT_PCT)

    if vol_tvl >= config.VOL_TVL_GREAT_RATIO:
        vol_score = 1.0
    else:
        vol_score = _clip01(vol_tvl / config.VOL_TVL_GREAT_RATIO)

    # Umur pool dari pairCreatedAt (ms) Dexscreener bila ada.
    pool_age_hours = None
    created_ms = token_metrics.get("pair_created_at")
    if created_ms:
        pool_age_hours = (time.time() * 1000 - created_ms) / 3_600_000.0
    if pool_age_hours is None:
        age_score = 0.5  # tak diketahui -> netral
    elif pool_age_hours >= config.POOL_MIN_AGE_HOURS_HEALTHY:
        age_score = 1.0
    else:
        # makin muda makin rendah (risiko sniper/bundle dump awal)
        age_score = _clip01(pool_age_hours / config.POOL_MIN_AGE_HOURS_HEALTHY)

    # Konsentrasi LP (bin occupancy / LP dominan): data granular per-bin tak
    # tersedia gratis-stabil. Proxy kasar: rasio TVL vs volume — TVL sangat tebal
    # relatif volume sering berarti kompetisi LP tinggi (share fee terdilusi).
    # Ini estimasi -> ditandai ⚠️ di notif. Skor netral-ke-baik.
    if vol_tvl >= config.VOL_TVL_GOOD_RATIO:
        lp_conc_score = 0.8  # velocity sehat -> fee terdistribusi wajar
    else:
        lp_conc_score = 0.5
    lp_conc_estimated = True

    return {
        "fee_tvl_daily_pct": round(fee_tvl_daily_pct, 2),
        "fee_estimated": fee_estimated,
        "vol_tvl": round(vol_tvl, 2),
        "pool_age_hours": round(pool_age_hours, 1) if pool_age_hours is not None else None,
        "fee_score": round(fee_score, 2),
        "vol_score": round(vol_score, 2),
        "age_score": round(age_score, 2),
        "lp_conc_score": round(lp_conc_score, 2),
        "lp_conc_estimated": lp_conc_estimated,
    }
