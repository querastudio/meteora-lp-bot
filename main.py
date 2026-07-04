"""
main.py — Orkestrasi pipeline cascade (Stage 1 -> 7).

Alur per run (dipanggil cron GitHub Actions tiap 5 menit):
  1. Load state (ATH history + anti-duplikat) & harga SOL.
  2. Fetch pool Meteora, urut aktivitas.
  3. Cascade: Stage 1 (pool) -> Stage 2 (token+ATH) -> Stage 3 (keamanan)
     -> Stage 4 (holder) -> Stage 5 (LP) -> Stage 6 (volatilitas) -> Stage 7 (narasi).
     Gugur di stage awal = tak lanjut ke stage mahal (hemat rate limit).
  4. Scoring -> verdict. Kirim Telegram (anti-duplikat). Simpan state.

Prinsip: cepat, idempoten, degrade gracefully. Satu API mati != run crash.
"""

import logging
import sys
from typing import Any, Dict, List

import config
import notify
import scoring
import state as state_mod
from screening import hard_filters, holders, lp_quality, volatility
from sources import dexscreener, helius, meteora, narrative

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("main")


def run() -> int:
    log.info("=== Meteora LP screening run mulai ===")
    st = state_mod.load()
    sol_price = dexscreener.get_sol_price_usd()
    log.info("Harga SOL: $%.2f", sol_price)

    pools = meteora.fetch_pools(config.MAX_POOLS_PER_RUN)
    if not pools:
        log.warning("Tak ada pool dari Meteora, selesai.")
        return 0

    # ---- STAGE 1: hard filter pool (murah, 0 call) ----
    stage1_pass: List[Dict[str, Any]] = []
    for pool in pools:
        ok, quote_sym, reasons = hard_filters.stage1_pool(pool, sol_price)
        if not ok:
            log.debug("S1 gugur %s: %s", pool.get("name"), reasons)
            continue
        stage1_pass.append(pool)
    log.info("Stage 1: %d/%d pool lolos", len(stage1_pass), len(pools))

    # Batasi kandidat mahal per run (hemat Helius rate limit & waktu cron).
    candidates = stage1_pass[: config.MAX_EXPENSIVE_CANDIDATES]

    sent = 0
    for pool in candidates:
        try:
            result = _process_candidate(pool, st, sol_price)
            if result:
                sent += 1
        except Exception as e:  # noqa: BLE001 — 1 token error != crash run
            log.exception("Error proses pool %s: %s", pool.get("name"), e)

    # Simpan state (ATH history + notified) untuk run berikutnya.
    state_mod.save(st)
    log.info("=== Selesai. %d notifikasi terkirim. ===", sent)
    return sent


def _process_candidate(pool: Dict[str, Any], st: Dict[str, Any], sol_price: float):
    """Jalankan Stage 2-7 untuk satu pool kandidat. Return True bila dikirim."""
    mint = hard_filters.base_mint_of(pool)
    name = pool.get("name", "?")

    # ---- STAGE 2: token metrics + ATH ----
    metrics = dexscreener.get_token_metrics(mint)
    if not metrics:
        log.info("S2 skip %s: metrik token tak tersedia", name)
        return False

    symbol = metrics["symbol"]
    # Catat harga & ambil ATH tercatat SEBELUM update (utk proximity check).
    prev_ath = state_mod.record_price(st, mint, metrics["price_usd"], symbol)

    ok2, reasons2, ath_info = hard_filters.stage2_token(metrics, prev_ath)
    if not ok2:
        log.info("S2 gugur $%s: %s", symbol, reasons2)
        return False

    # ---- STAGE 3: keamanan kontrak (Helius) — paling kritis ----
    sec = helius.get_security_info(mint)
    ok3, hard3, warn3 = hard_filters.stage3_security(sec)
    if not ok3:
        log.info("S3 gugur $%s (SKIP keamanan): %s", symbol, hard3)
        if config.SEND_SKIP_AUDIT:
            _maybe_send_skip(mint, pool, symbol, hard3, st)
        return False

    warnings: List[str] = list(warn3)
    if ath_info.get("cold_start"):
        warnings.append("ATH: data baru dikumpulkan, belum ada riwayat penuh")

    # ---- STAGE 4: distribusi holder (hard gate top10 + soft heuristik) ----
    hold = holders.analyze(mint, sol_price)
    if hold.get("available") and not hold["top10_gate_pass"]:
        log.info("S4 gugur $%s: top10 %.1f%% >= %.0f%%", symbol, hold["top10_pct"], config.MAX_TOP10_SUPPLY_PCT)
        return False
    if not hold.get("available"):
        warnings.append("distribusi holder tak terverifikasi")
    if hold.get("note"):
        warnings.append(hold["note"])

    # ---- STAGE 5: kualitas LP ----
    lp = lp_quality.analyze(pool, metrics)

    # ---- STAGE 6: volatilitas turun-stabil (hard SKIP bila mati vertikal) ----
    history = state_mod.get_price_history(st, mint)
    vol = volatility.analyze(metrics, history)
    if vol["vertical_death"]:
        log.info("S6 gugur $%s: mati vertikal (ATH palsu / pump-dump)", symbol)
        return False

    # ---- STAGE 7: narasi viral (degrade gracefully) ----
    nar = narrative.evaluate_narrative(metrics.get("name", ""), symbol)

    # ---- SCORING & VERDICT ----
    scored = scoring.compute(lp, vol, hold, nar, warnings)
    verdict = scored["verdict"]
    log.info("$%s -> %s (skor %.0f) breakdown=%s", symbol, verdict, scored["score"], scored["breakdown"])

    if verdict == "SKIP":
        return False  # hard gate lolos tapi skor terlalu rendah -> tak menarik

    # Anti-duplikat.
    if not state_mod.should_notify(st, mint, verdict):
        log.info("$%s: sudah dinotif (%s), skip re-notif", symbol, verdict)
        return False

    links = notify.build_manual_links(mint, pool["address"], symbol)
    ctx = {
        "verdict": verdict,
        "score": scored["score"],
        "symbol": symbol,
        "mint": mint,
        "metrics": metrics,
        "pool_data": pool,
        "security": sec,
        "holders": hold,
        "lp": lp,
        "vol": vol,
        "narrative": nar,
        "ath_info": ath_info,
        "warnings": warnings,
        "links": links,
    }
    text = notify.format_message(ctx)
    if notify.send(text):
        state_mod.mark_notified(st, mint, verdict)
        return True
    return False


def _maybe_send_skip(mint, pool, symbol, reasons, st):
    """Kirim SKIP ringkas untuk audit (opsional, default off)."""
    if not state_mod.should_notify(st, mint, "SKIP"):
        return
    txt = f"🔴 <b>SKIP — ${symbol}</b>\n{pool.get('name','')}\nAlasan: " + "; ".join(reasons)
    if notify.send(txt):
        state_mod.mark_notified(st, mint, "SKIP")


if __name__ == "__main__":
    try:
        run()
    except Exception as e:  # noqa: BLE001
        log.exception("Run gagal total: %s", e)
        sys.exit(1)
