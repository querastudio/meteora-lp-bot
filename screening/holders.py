"""
screening/holders.py — Stage 4: distribusi holder (Helius, berat).

Hanya dijalankan untuk token yang sudah lolos Stage 1-3.

Hard gate:  top 10 holder < MAX_TOP10_SUPPLY_PCT (default 30%).
Hard gate:  cluster/bundle TERBESAR < MAX_CLUSTER_SUPPLY_PCT (default 25%).
Soft:       heuristik "wallet aneh" di top 20 (fresh wallet, wallet kosong).

Deteksi cluster (ala GMGN/DevsNightmare, versi gratis): wallet top holder yang
"lahir" (tx pertama terlihat) dalam jendela waktu sempit satu sama lain
dikelompokkan sbg 1 kemungkinan entitas. TANPA API call tambahan (pakai data
yg sudah diambil dari get_wallet_activity utk fresh-wallet check). Bukan exact
funding-source match spt GMGN (yang trace siapa danai wallet), tapi cukup
menangkap pola paling umum: banyak wallet baru dibuat berdekatan sesaat
sebelum/saat token diluncurkan. Filosofi user: bundler BOLEH ada, asal tak
kuasai mayoritas supply -- utk verifikasi visual lebih dalam, link GMGN/
Bubblemaps tetap disediakan (lihat notify.py).

Return dict skor 0-1 utk holder_health + flag utk notif.
"""

import logging
from typing import Any, Dict, List

import config
from sources import helius

log = logging.getLogger("holders")


def _cluster_by_time(wallets: List[Dict[str, Any]], window_sec: int) -> List[Dict[str, Any]]:
    """
    Kelompokkan wallet berdasarkan earliest_seen_ts yang berdekatan (proxy
    "dibuat sekitar waktu yang sama" -> kemungkinan 1 entitas/operator).

    wallets: [{"pct": float, "earliest_seen_ts": int|None}, ...]
    Return list cluster: [{"pct_total": float, "wallet_count": int, "ts_start": int}, ...]
    Wallet tanpa earliest_seen_ts (gagal diambil) diperlakukan sbg singleton
    terpisah (tak masuk cluster mana pun) -- konservatif, tak menuduh tanpa data.
    """
    timed = sorted(
        (w for w in wallets if w.get("earliest_seen_ts") is not None),
        key=lambda w: w["earliest_seen_ts"],
    )
    clusters: List[Dict[str, Any]] = []
    for w in timed:
        ts = w["earliest_seen_ts"]
        if clusters and (ts - clusters[-1]["_last_ts"]) <= window_sec:
            clusters[-1]["pct_total"] += w["pct"]
            clusters[-1]["wallet_count"] += 1
            clusters[-1]["_last_ts"] = ts
        else:
            clusters.append({"pct_total": w["pct"], "wallet_count": 1, "_last_ts": ts, "ts_start": ts})
    return clusters


def analyze(mint: str, sol_price: float) -> Dict[str, Any]:
    """
    Analisa distribusi holder. Return:
      {
        available: bool,
        top10_pct: float,
        top10_gate_pass: bool,        # HARD GATE
        fresh_count: int,             # jumlah fresh wallet di top20
        empty_count: int,             # jumlah wallet kosong di top20
        suspicious_pct: float,        # proporsi wallet mencurigakan di top20
        largest_cluster_pct: float,   # % supply cluster terbesar (proxy waktu)
        largest_cluster_wallets: int, # jumlah wallet di cluster terbesar
        cluster_gate_pass: bool,      # HARD GATE
        health_score: float,          # 0-1 utk soft scoring
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
        "largest_cluster_pct": 0.0,
        "largest_cluster_wallets": 0,
        "cluster_gate_pass": True,
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

    # Heuristik top 20: fresh wallet + wallet kosong + data utk cluster.
    inspect = holders[: config.TOP_N_HOLDERS_INSPECT]
    fresh = 0
    empty = 0
    inspected = 0
    wallets_for_cluster: List[Dict[str, Any]] = []
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
        wallets_for_cluster.append({"pct": h["pct"], "earliest_seen_ts": act.get("earliest_seen_ts")})

    out["fresh_count"] = fresh
    out["empty_count"] = empty

    # Wallet dianggap "mencurigakan" bila fresh ATAU kosong (union kasar).
    # Proporsi vs jumlah yang berhasil diinspeksi.
    suspicious = max(fresh, empty)  # konservatif: jangan double count agresif
    denom = max(inspected, 1)
    suspicious_pct = suspicious / denom * 100.0
    out["suspicious_pct"] = round(suspicious_pct, 1)

    # HARD GATE: cluster/bundle terbesar (proxy waktu pembuatan wallet).
    clusters = _cluster_by_time(wallets_for_cluster, config.CLUSTER_TIME_WINDOW_SECONDS)
    if clusters:
        largest = max(clusters, key=lambda c: c["pct_total"])
        out["largest_cluster_pct"] = round(largest["pct_total"], 1)
        out["largest_cluster_wallets"] = largest["wallet_count"]
    out["cluster_gate_pass"] = out["largest_cluster_pct"] < config.MAX_CLUSTER_SUPPLY_PCT

    # Health score (0-1): mulai dari top10 share, dikurangi penalti wallet aneh
    # + penalti cluster besar.
    base = 1.0
    base -= min(top10_pct / config.MAX_TOP10_SUPPLY_PCT, 1.0) * 0.4
    base -= min(suspicious_pct / 100.0, 1.0) * 0.3
    base -= min(out["largest_cluster_pct"] / config.MAX_CLUSTER_SUPPLY_PCT, 1.0) * 0.3
    out["health_score"] = round(max(base, 0.0), 2)

    notes = []
    if suspicious_pct > config.SUSPICIOUS_TOP20_PCT_THRESHOLD:
        notes.append(f"wallet mencurigakan {suspicious_pct:.0f}% di top20 (>{config.SUSPICIOUS_TOP20_PCT_THRESHOLD:.0f}%)")
    if out["largest_cluster_wallets"] >= 2:
        notes.append(
            f"cluster terbesar: {out['largest_cluster_pct']:.1f}% supply "
            f"({out['largest_cluster_wallets']} wallet dibuat berdekatan)"
        )
    out["note"] = "; ".join(notes)

    return out
