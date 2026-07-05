"""
sources/lunarcrush.py — Sinyal sosial (Galaxy Score, sentiment, kontributor)
via LunarCrush API. BERBAYAR (~$24/bulan tier Individual) -- BEDA dari semua
sumber lain di bot ini yang gratis. User pilih tetap pakai ini utk sinyal
X/sosial yang tak bisa didapat gratis dgn aman (X API resmi $200+/bln, GMGN
X Tracker tak ada API publik, CoinMarketCap/LunarCrush free tier tak include
data sosial, scraping X = ToS-violation & risiko banned -- lihat diskusi sesi
ini). LunarCrush resmi, ToS-compliant, dibangun khusus utk social intelligence
crypto (bukan scraping pihak kita sendiri).

KETERBATASAN PENTING (dikonfirmasi user via cek manual dulu sebelum bayar):
LunarCrush TIDAK meng-index token yang baru rilis hitungan jam -- cuma coin
yang lebih mapan atau topik lagi populer. Jadi utk kandidat paling fresh,
endpoint ini nyaris pasti 404/kosong -- itu WAJAR (bukan bug), degrade
gracefully (skor netral 0.5) spt semua soft signal lain di bot ini, BUKAN
hard gate.

API: https://github.com/lunarcrush/api (v4, topic endpoint).
"""

import logging
from typing import Any, Dict

import config
from sources import http

log = logging.getLogger("lunarcrush")

BASE = "https://lunarcrush.com/api4/public"


def social_signal(symbol: str) -> Dict[str, Any]:
    """
    Return { available, galaxy_score, sentiment_pct, num_contributors,
             num_posts, interactions_24h, social_score }.
    social_score (0-1) = galaxy_score/100 -- galaxy_score sudah komposit
    resmi LunarCrush (sentiment+momentum+aktivitas sosial), tak perlu
    diracik ulang. Dipakai scoring.py sbg soft-score component.
    """
    out = {
        "available": False, "galaxy_score": 0.0, "sentiment_pct": 0.0,
        "num_contributors": 0, "num_posts": 0, "interactions_24h": 0,
        "social_score": 0.5,
    }
    if not config.LUNARCRUSH_ENABLED or not config.LUNARCRUSH_API_KEY:
        return out
    if not symbol or symbol == "?":
        return out
    try:
        topic = f"${symbol.lower()}"
        url = f"{BASE}/topic/{topic}/v1"
        headers = {"Authorization": f"Bearer {config.LUNARCRUSH_API_KEY}"}
        resp = http.get_json(url, headers=headers, timeout=config.HTTP_TIMEOUT)
        if not resp:
            return out  # 404 (belum ter-index) atau API gagal -- wajar, degrade
        data = resp.get("data") or {}
        if not data:
            return out
        galaxy_score = float(data.get("galaxy_score", 0) or 0)
        out.update(
            {
                "available": True,
                "galaxy_score": galaxy_score,
                "sentiment_pct": float(data.get("sentiment", 0) or 0),
                "num_contributors": int(data.get("num_contributors", 0) or 0),
                "num_posts": int(data.get("num_posts", 0) or 0),
                "interactions_24h": int(data.get("interactions_24h", 0) or 0),
                "social_score": max(0.0, min(galaxy_score / 100.0, 1.0)),
            }
        )
        log.info(
            "LunarCrush OK utk $%s: galaxy_score=%.0f sentiment=%.0f%% (%d kontributor, %d post)",
            symbol, galaxy_score, out["sentiment_pct"], out["num_contributors"], out["num_posts"],
        )
    except Exception as e:  # noqa: BLE001
        log.info("LunarCrush gagal/tak terindex utk $%s: %s (degrade)", symbol, e)
    return out
