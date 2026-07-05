"""
sources/jupiter.py — Organic Score dari Jupiter Tokens API V2 (gratis, no key
via lite-api.jup.ag -- agregator DEX terbesar Solana).

BUKAN sinyal narasi/hype -- ini sinyal LEGITIMASI VOLUME (organic volume vs
bot/wash-trading), jadi konseptual paling dekat dgn Stage 4 (coordinated
trading) & Stage 6 (volatilitas) yang sudah kita bangun pakai heuristik
sendiri (fresh/empty/young wallet, konsistensi volume antar-window). Jupiter
liat SEMUA venue trading Solana jadi datanya lebih kuat drpd proxy kita.

Organic Score = komposit resmi Jupiter dari organic volume/holders/traders/
buyers (0-100) + label (high/medium/low). SOFT SCORE saja (bukan hard gate),
sama filosofinya dgn semua penambahan sesi ini -- degrade gracefully (skor
netral 0.5) kalau API berubah/gagal/token tak ketemu.

API masih "V2 (Beta)" per dokumentasi Jupiter -- skema bisa berubah, makanya
parsing di bawah defensif (coba beberapa nama field yang umum dipakai).
"""

import logging
from typing import Any, Dict, List, Optional

import config
from sources import http

log = logging.getLogger("jupiter")

BASE = "https://lite-api.jup.ag/tokens/v2"

_LABEL_SCORE_FALLBACK = {"high": 0.85, "medium": 0.55, "low": 0.2}


def _find_match(rows: List[Dict[str, Any]], mint: str) -> Optional[Dict[str, Any]]:
    for row in rows:
        if row.get("id") == mint or row.get("address") == mint or row.get("mint") == mint:
            return row
    return rows[0] if len(rows) == 1 else None


def organic_score(mint: str) -> Dict[str, Any]:
    """
    Return { available, organic_score, organic_label, organic_signal_score }.
    organic_signal_score (0-1) dipakai scoring.py sbg soft-score component.
    """
    out = {
        "available": False, "organic_score": 0.0, "organic_label": "",
        "organic_signal_score": 0.5,
    }
    if not config.JUPITER_ORGANIC_ENABLED or not mint:
        return out
    try:
        resp = http.get_json(f"{BASE}/search", params={"query": mint}, timeout=config.HTTP_TIMEOUT)
        if not resp:
            return out
        rows = resp if isinstance(resp, list) else resp.get("data") or []
        row = _find_match(rows, mint)
        if not row:
            return out

        score_raw = row.get("organicScore")
        label = str(row.get("organicScoreLabel", "") or "").lower()
        if score_raw is not None:
            score = float(score_raw)
        elif label in _LABEL_SCORE_FALLBACK:
            score = _LABEL_SCORE_FALLBACK[label] * 100.0
        else:
            return out

        out.update(
            {
                "available": True,
                "organic_score": round(score, 1),
                "organic_label": label,
                "organic_signal_score": max(0.0, min(score / 100.0, 1.0)),
            }
        )
        log.info("Jupiter Organic Score OK utk mint %s...: %.0f/100 (%s)", mint[:6], score, label or "?")
    except Exception as e:  # noqa: BLE001
        log.info("Jupiter Organic Score gagal utk mint %s...: %s (degrade)", mint[:6], e)
    return out
