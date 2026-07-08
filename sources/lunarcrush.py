"""
sources/lunarcrush.py — Sinyal sosial (Galaxy Score, sentiment, kontributor)
via LunarCrush API (opsional). Dipakai krn X/Twitter adalah sumber narasi
utama dunia memecoin dan tak ada jalan resmi+gratis+ToS-aman lain utk
mengaksesnya (X API resmi $200+/bln, GMGN X Tracker tak ada API publik,
scraping X = ToS-violation & risiko banned -- lihat riwayat diskusi sesi ini).

STATUS TIER GRATIS: BELUM PASTI. Dokumentasi publik LunarCrush simpang siur
-- sebagian sumber bilang free tier "no social data or API access", sebagian
lain bilang free plan tetap bisa generate API key & akses data sosial
terbatas. Kita coba pakai key dari tier gratis dulu; kalau endpoint balas
401/403 (bukan 404 "belum ter-index"), berarti memang perlu upgrade ke tier
berbayar (~$24/bln Individual) -- status code aslinya akan kelihatan di log
warning bawaan sources/http.py (baris "HTTP GET lunarcrush.com -> 401/403"),
degrade gracefully spt biasa, TIDAK bikin run gagal.

KETERBATASAN LAIN (dikonfirmasi user via cek manual): LunarCrush TIDAK
meng-index token yang baru rilis hitungan jam -- cuma coin yang lebih mapan
atau topik lagi populer. Jadi utk kandidat paling fresh, endpoint ini nyaris
pasti 404/kosong -- itu WAJAR (bukan bug), degrade gracefully (skor netral
0.5) spt semua soft signal lain di bot ini, BUKAN hard gate.

API: https://github.com/lunarcrush/api (v4, topic endpoint).
"""

import logging
from typing import Any, Dict

import config
from sources import http

log = logging.getLogger("lunarcrush")

BASE = "https://lunarcrush.com/api4/public"

# Circuit breaker per-run: live log konfirmasi LunarCrush rate-limited
# (daily/minute) SEPANJANG run -- tiap kandidat retry penuh (default 3x
# retry + backoff, ~9 detik) padahal hasilnya PASTI gagal lagi (limit tak
# reset di tengah run). Setelah gagal SEKALI, skip sisa kandidat run ini
# tanpa panggil API sama sekali -- reset_run() dipanggil main.py tiap run
# baru mulai (limit bisa reset di run berikutnya).
_exhausted_this_run = False


def reset_run() -> None:
    global _exhausted_this_run
    _exhausted_this_run = False


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
    global _exhausted_this_run
    if not config.LUNARCRUSH_ENABLED or not config.LUNARCRUSH_API_KEY:
        return out
    if not symbol or symbol == "?":
        return out
    if _exhausted_this_run:
        return out
    try:
        topic = f"${symbol.lower()}"
        url = f"{BASE}/topic/{topic}/v1"
        headers = {"Authorization": f"Bearer {config.LUNARCRUSH_API_KEY}"}
        # max_retries=0: kalau gagal, jangan buang waktu retry di SINI --
        # kalau alasannya 429 (lihat status di bawah), circuit breaker yg
        # cegah KANDIDAT BERIKUTNYA coba lagi sepanjang run ini (limit
        # daily/minute tak reset di tengah run, retry cuma buang waktu).
        resp, status = http.get_json_with_status(url, headers=headers, timeout=config.HTTP_TIMEOUT, max_retries=0)
        if status == 429:
            # Rate limit (daily/minute) berlaku global, BUKAN token-spesifik --
            # aman diasumsikan kandidat lain jg bakal kena, skip sisa run.
            _exhausted_this_run = True
        if not resp:
            return out  # 404 (token blm ter-index) atau gagal lain -- wajar, degrade
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
