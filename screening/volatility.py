"""
screening/volatility.py — Stage 6: volatilitas "turun stabil" vs "mati vertikal".

Cocokkan dengan strategi range -90%:
  - "turun stabil + volume tahan" = SURGA fee (skor tinggi).
  - "spike lalu mati" = SKIP walau sempat kena ATH (menyaring ATH palsu/pump-dump).

Keterbatasan gratis: Dexscreener tak beri OHLC historis lengkap tanpa key.
Kita pakai proxy dari window yang tersedia (h1/h6/h24 price change + volume) plus
riwayat harga yang kita simpan sendiri di state (beberapa run terakhir).

Sinyal:
  - drawdown 24h ekstrem + volume h1 mengering (relatif h24) = mati vertikal -> hard SKIP.
  - volume konsisten antar window (h6*4 ~ h24) = volume tahan -> skor tinggi.
"""

import logging
from typing import Any, Dict, List

import config

log = logging.getLogger("volatility")


def analyze(token_metrics: Dict[str, Any], price_history: List[float]) -> Dict[str, Any]:
    """
    Return:
      {
        vertical_death: bool,   # True -> SKIP (hard)
        volume_sustained: bool,
        days_volume: int,       # estimasi "hari volume tahan" (0-4+)
        vol_score: float,       # 0-1
        note: str
      }
    price_history = list harga historis token (dari state, terlama->terbaru).
    """
    chg_h24 = token_metrics.get("price_change_h24", 0.0)
    chg_h1 = token_metrics.get("price_change_h1", 0.0)
    vol_h24 = token_metrics.get("volume_h24", 0.0)
    vol_h6 = token_metrics.get("volume_h6", 0.0)
    vol_h1 = token_metrics.get("volume_h1", 0.0)

    note_parts: List[str] = []

    # --- Deteksi "mati vertikal" ---
    # Drawdown 24h ekstrem DAN aktivitas jam terakhir mengering = harga menuju nol.
    drawdown = -chg_h24 if chg_h24 < 0 else 0.0
    # volume h1 yang diproyeksikan ke 24h; jika << volume 24h -> aktivitas mengering
    projected_daily_from_h1 = vol_h1 * 24
    drying = vol_h24 > 0 and projected_daily_from_h1 < 0.2 * vol_h24

    vertical_death = drawdown >= config.VERTICAL_DEATH_DRAWDOWN_PCT and drying
    if vertical_death:
        note_parts.append(f"mati vertikal (dd -{drawdown:.0f}%, volume kering)")

    # --- Volume tahan lama ---
    # Konsistensi: bandingkan volume h6*4 dgn h24. Kalau seimbang -> volume merata.
    projected_daily_from_h6 = vol_h6 * 4
    consistent = vol_h24 > 0 and projected_daily_from_h6 >= 0.5 * vol_h24
    volume_sustained = consistent and vol_h24 >= config.MIN_VOLUME_H24_USD * 0.5

    # Estimasi "hari volume tahan" dari panjang price_history yang non-trivial.
    # (setiap run cron 5 menit menambah 1 titik; 288 titik ~ 1 hari. Kita
    #  approx kasar: >0 harga tercatat & harga tak kolaps.)
    days_volume = _estimate_sustained_days(price_history)

    # Skor volatilitas: tinggi bila turun-stabil (fluktuasi moderat) + volume tahan.
    vol_score = 0.5
    if vertical_death:
        vol_score = 0.0
    else:
        if volume_sustained:
            vol_score += 0.3
            note_parts.append("volume tahan")
        # penurunan bertahap (zona nyaman range -90%): drawdown moderat, bukan ekstrem
        if 0 < drawdown < config.VERTICAL_DEATH_DRAWDOWN_PCT:
            vol_score += 0.1
            note_parts.append("turun bertahap")
        elif chg_h24 >= 0:
            vol_score += 0.1
        vol_score = min(vol_score, 1.0)

    return {
        "vertical_death": vertical_death,
        "volume_sustained": volume_sustained,
        "days_volume": days_volume,
        "vol_score": round(vol_score, 2),
        "note": ", ".join(note_parts) if note_parts else "netral",
    }


def _estimate_sustained_days(price_history: List[float]) -> int:
    """
    Estimasi berapa 'hari' token bertahan (harga tak kolaps ke ~0).
    Kasar: hitung berdasarkan jumlah titik & apakah harga terakhir masih > 20%
    dari harga puncak dalam riwayat.
    """
    if not price_history:
        return 0
    peak = max(price_history) or 1.0
    last = price_history[-1]
    if last < 0.2 * peak:
        return 0  # sudah kolaps
    # 288 titik (@5 menit) ~ 1 hari. Cap di 7.
    return min(len(price_history) // 288 + 1, 7)
