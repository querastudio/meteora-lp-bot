"""
scoring.py — Engine soft-score + verdict akhir.

Alur:
  1. Hard gates (Stage 1,2,3 + top10<30% Stage 4) sudah dievaluasi di pipeline.
     Kalau ada yang gagal -> verdict SKIP (tak masuk sini untuk STRONG/WATCH).
  2. Soft score menimbang komponen Stage 4(heuristik)/5/6/7 pakai WEIGHTS di config,
     dinormalisasi ke 0-100.
  3. Verdict:
       >= VERDICT_STRONG_MIN -> STRONG
       >= VERDICT_WATCH_MIN  -> WATCH
       else                  -> (lemah) tetap WATCH? Tidak: di bawah watch = tak kirim.
  4. Bila ada warning (mis. LP-lock tak terverifikasi) & DOWNGRADE_ON_WARN -> STRONG turun ke WATCH.

Bobot mencerminkan profil pasif-konservatif: fee/TVL, volatilitas turun-stabil,
dan holder health diberi porsi terbesar (lihat config.WEIGHTS).
"""

import logging
from typing import Any, Dict, List

import config

log = logging.getLogger("scoring")


def compute(
    lp: Dict[str, Any],
    vol: Dict[str, Any],
    holders: Dict[str, Any],
    narrative: Dict[str, Any],
    warnings: List[str],
    vwap: Dict[str, Any] = None,
    lunarcrush: Dict[str, Any] = None,
    jupiter: Dict[str, Any] = None,
    vol_organic: Dict[str, Any] = None,
    is_new_ath: bool = False,
) -> Dict[str, Any]:
    """
    Hitung skor & verdict. Return dict:
      { score: float(0-100), verdict: 'STRONG'|'WATCH'|'SKIP', breakdown: {...} }
    """
    w = config.WEIGHTS
    total_weight = sum(w.values())
    vwap = vwap or {}
    lunarcrush = lunarcrush or {}
    jupiter = jupiter or {}
    vol_organic = vol_organic or {}

    # volume_organic: rasio_target/rasio_actual (makin actual mendekati/di
    # bawah target 10.000:1 -> makin sehat -> makin dekat 1.0). Kalau data
    # fee 0 (ratio_actual None), netral 0.5 drpd 0 -- konsisten dgn soft
    # signal lain yg degrade ke netral, bukan skor terendah, saat data tipis.
    ratio_actual = vol_organic.get("ratio_actual")
    ratio_target = vol_organic.get("ratio_target", config.MCAP_TO_FEE_SOL_RATIO)
    if ratio_actual is None or ratio_actual <= 0:
        volume_organic_score = 0.5
    else:
        volume_organic_score = max(0.0, min(ratio_target / ratio_actual, 1.0))

    # Skor per komponen (masing-masing 0-1) x bobot.
    components = {
        "fee_tvl": lp.get("fee_score", 0.0),
        "vol_tvl": lp.get("vol_score", 0.0),
        "lp_concentration": lp.get("lp_conc_score", 0.5),
        "pool_age": lp.get("age_score", 0.5),
        "volatility": vol.get("vol_score", 0.0),
        "holder_health": holders.get("health_score", 0.0) if holders.get("available") else 0.3,
        "narrative": narrative.get("score", 0.0),
        "vwap_momentum": vwap.get("momentum_score", 0.5),
        "lunarcrush_social": lunarcrush.get("social_score", 0.5),
        "jupiter_organic": jupiter.get("organic_signal_score", 0.5),
        "volume_organic": volume_organic_score,
        "ath_momentum": 1.0 if is_new_ath else 0.5,
    }

    weighted = sum(components[k] * w[k] for k in components)
    score100 = (weighted / total_weight * 100.0) if total_weight > 0 else 0.0
    score100 = round(score100, 1)

    # Verdict dasar dari skor.
    if score100 >= config.VERDICT_STRONG_MIN:
        verdict = "STRONG"
    elif score100 >= config.VERDICT_WATCH_MIN:
        verdict = "WATCH"
    else:
        verdict = "SKIP"  # skor terlalu rendah walau hard gate lolos -> tak menarik

    # Downgrade STRONG->WATCH kalau ada warning material (mis. LP-lock ⚠️).
    if verdict == "STRONG" and warnings and config.DOWNGRADE_STRONG_TO_WATCH_ON_WARNING:
        verdict = "WATCH"
        log.info("Downgrade STRONG->WATCH karena warning: %s", warnings)

    return {
        "score": score100,
        "verdict": verdict,
        "breakdown": {k: round(components[k], 2) for k in components},
    }
