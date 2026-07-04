"""
screening/holders.py — Stage 4: distribusi holder (Helius, berat).

Hanya dijalankan untuk token yang sudah lolos Stage 1-3.

Hard gate:  top 10 holder < MAX_TOP10_SUPPLY_PCT (default 30%).
Soft:       heuristik "wallet aneh" di top 20 (fresh wallet, wallet kosong).
            Bundle sederhana: banyak top wallet didanai dari sumber sama -> ⚠️.
            (Deteksi funding-source penuh butuh telusur tx yang mahal; kita beri
             estimasi ringan + link Bubblemaps/GMGN utk verifikasi manual.)

Return dict skor 0-1 utk holder_health + flag utk notif.
"""

import logging
from typing import Any, Dict, List

import config
from sources import helius

log = logging.getLogger("holders")


def analyze(mint: str, sol_price: float) -> Dict[str, Any]:
    """
    Analisa distribusi holder. Return:
      {
        available: bool,
        top10_pct: float,
        top10_gate_pass: bool,      # HARD GATE
        fresh_count: int,           # jumlah fresh wallet di top20
        empty_count: int,           # jumlah wallet kosong di top20
        suspicious_pct: float,      # proporsi wallet mencurigakan di top20
        health_score: float,        # 0-1 utk soft scoring
        note: str
      }
    """
    out = {
        "available": False,
        "top10_pct": 0.0,
        "top10_gate_pass": False,
        "fresh_count": 0,
        "empty_count": 0,
        "suspicious_pct": 0.0,
        "health_score": 0.0,
        "note": "",
    }

    holders = helius.get_top_holders(mint, config.TOP_N_HOLDERS_FETCH)
    if not holders:
        out["note"] = "data holder tak tersedia"
        return out

    out["available"] = True

    # HARD GATE: top 10 supply share.
    top10_pct = sum(h["pct"] for h in holders[:10])
    out["top10_pct"] = round(top10_pct, 1)
    out["top10_gate_pass"] = top10_pct < config.MAX_TOP10_SUPPLY_PCT

    # Heuristik top 20: fresh wallet + wallet kosong.
    inspect = holders[: config.TOP_N_HOLDERS_INSPECT]
    fresh = 0
    empty = 0
    inspected = 0
    for h in inspect:
        owner = helius.get_token_account_owner(h["token_account"]) if h.get("token_account") else None
        if not owner:
            continue
        inspected += 1
        act = helius.get_wallet_activity(owner)
        if act.get("is_fresh"):
            fresh += 1
        bal = helius.get_sol_balance_usd(owner, sol_price)
        if bal is not None and bal < config.EMPTY_WALLET_SOL_USD:
            empty += 1

    out["fresh_count"] = fresh
    out["empty_count"] = empty

    # Wallet dianggap "mencurigakan" bila fresh ATAU kosong (union kasar).
    # Proporsi vs jumlah yang berhasil diinspeksi.
    suspicious = max(fresh, empty)  # konservatif: jangan double count agresif
    denom = max(inspected, 1)
    suspicious_pct = suspicious / denom * 100.0
    out["suspicious_pct"] = round(suspicious_pct, 1)

    # Health score (0-1): mulai dari top10 share, dikurangi penalti wallet aneh.
    # Semakin rendah top10 & semakin sedikit wallet aneh -> semakin tinggi.
    base = 1.0
    # penalti top10 (skala terhadap gate)
    base -= min(top10_pct / config.MAX_TOP10_SUPPLY_PCT, 1.0) * 0.5
    # penalti wallet mencurigakan
    base -= min(suspicious_pct / 100.0, 1.0) * 0.5
    out["health_score"] = round(max(base, 0.0), 2)

    if suspicious_pct > config.SUSPICIOUS_TOP20_PCT_THRESHOLD:
        out["note"] = f"wallet mencurigakan {suspicious_pct:.0f}% di top20 (>{config.SUSPICIOUS_TOP20_PCT_THRESHOLD:.0f}%)"

    return out
